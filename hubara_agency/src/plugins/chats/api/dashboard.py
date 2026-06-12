from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
import asyncio
import json
import os
import time
from pathlib import Path

from loguru import logger

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.events import DashboardEvent, get_dashboard_event_bus
from src.platform.media import resolve_media_file

router = APIRouter()


# Reason por la que el agente escala una venta para que un humano verifique el
# pago (lo escribe `escalate_to_human(reason_category="PAYMENT_VERIFICATION_PENDING")`).
_PAYMENT_PENDING_REASON = "PAYMENT_VERIFICATION_PENDING"


def _compute_pending_payment_order_id(data: dict) -> str | None:
    """¿Esta sesión tiene un pedido esperando que un humano confirme el pago?

    Devuelve el `order_id` (id backend de Medusa) a confirmar, o ``None``.

    El agente, al cerrar una venta, registra el pedido en Medusa y escala con
    `escalate_to_human(reason_category="PAYMENT_VERIFICATION_PENDING")` — eso
    deja en el `metadata.json` del chat: ``active_route="humano"``,
    ``escalation_reason="PAYMENT_VERIFICATION_PENDING"`` y el
    ``registered_order`` exitoso. Esta función reconoce ese estado para que el
    frontend muestre el botón "Confirmar pago" en el chat (mismo endpoint que
    el tablero de orders).

    Cuando el humano confirma el pago (desde el chat o desde orders), el command
    `confirm_payment` reescribe este mismo `metadata.json`: ``tag`` pasa a
    ``COMPRA_EXITOSA`` y el episodio del pedido recibe ``payment_confirmed_at_ms``.
    Cualquiera de esas marcas hace que esta función devuelva ``None`` → el botón
    desaparece solo en el próximo tick del SSE. Idéntico para ``RECHAZO`` (cancel).
    """
    if data.get("active_route") != "humano":
        return None
    if data.get("escalation_reason") != _PAYMENT_PENDING_REASON:
        return None
    # tag terminal → ya resuelto (confirmado o rechazado).
    if data.get("tag") in ("COMPRA_EXITOSA", "RECHAZO"):
        return None
    registered = data.get("registered_order")
    if not isinstance(registered, dict) or registered.get("success") is not True:
        return None
    order_id = registered.get("order_id")
    if not isinstance(order_id, str) or not order_id:
        return None
    # Doble chequeo: si el episodio de este pedido ya tiene la marca de pago
    # confirmado (la escribe `apply_payment_confirmation_to_chat_metadata`), no
    # está pendiente — aunque el tag no se haya actualizado por algún flujo raro.
    for episode in data.get("episodes") or []:
        if (
            isinstance(episode, dict)
            and episode.get("order_id") == order_id
            and episode.get("payment_confirmed_at_ms")
        ):
            return None
    return order_id


# Note: the liveness probe for the whole FastAPI app is `GET /` (defined in
# src/main.py:24). The frontend pipeline polls that endpoint before invoking
# Playwright. We intentionally do NOT add a duplicate `/api/dashboard/health`
# here — two probes mean two truths to keep in sync.


# ── Eventos del dashboard — SSE multiplexado (F1, auditoría 2026-06-10) ────
#
# Reemplaza al viejo `/stream`, que empujaba el snapshot COMPLETO cada 2.5s
# POR CLIENTE hubiera o no cambios (polling server-side disfrazado). Ahora:
#
#   - UN sampler de fondo (compartido entre todos los clientes) stat-ea los
#     metadata.json del vault cada 2.5s y solo emite CUANDO ALGO CAMBIÓ.
#   - `/events` multiplexa dominios en un solo stream:
#       chats.sessions_snapshot  (payload = lista completa, para setQueryData)
#       chats.session_updated    (id = sesión que cambió → invalidar detalle)
#       orders.changed / eta.changed (derivados del diff de metadata — cubre
#         mutaciones de los WORKERS vía el volumen compartido del vault; las
#         mutaciones del propio API las publican los routers de orders/catalog
#         directo al bus, ver src/platform/events)
#   - heartbeat (`: ping`) cada 25s para que proxies/webviews no corten.
#
# El sampler arranca lazy con el primer suscriptor y vive lo que viva el
# proceso (un solo proceso uvicorn — ver doc del bus).

_SAMPLE_INTERVAL_S = 2.5
_HEARTBEAT_S = 25.0
_sampler_task: asyncio.Task | None = None


def _session_signature(meta_file: Path) -> tuple[str, str]:
    """(orders_sig, eta_sig) — proyecciones de metadata que disparan eventos
    de otros dominios cuando las escriben los workers (register_order del
    agente Sales, eta_tracking del agente ETA)."""
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # UnicodeDecodeError NO es JSONDecodeError: una lectura a mitad de
        # escritura puede cortar un codepoint UTF-8 multibyte (los metadata
        # traen emojis en motivo/nombres — premortem 2026-06-11).
        return ("", "")
    orders_sig = json.dumps(
        [data.get("registered_order"), data.get("failed_order_registrations")],
        sort_keys=True,
        default=str,
    )
    eta_sig = json.dumps(data.get("eta_tracking"), sort_keys=True, default=str)
    return (orders_sig, eta_sig)


def _sample_vault_state(
    vault_dir: Path,
    prev: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Estado observado del vault, por sesión. Barato: stat() siempre; el
    re-parse del metadata.json solo cuando su mtime cambió (cache en `prev`)."""
    prev = prev or {}
    state: dict[str, dict] = {}
    if not vault_dir.exists():
        return state
    for entry in os.listdir(vault_dir):
        session_path = vault_dir / entry
        if not (session_path.is_dir() and entry.startswith("wa_")):
            continue
        meta_file = session_path / "metadata.json"
        history_file = session_path / "sessions" / f"{entry}.jsonl"
        # TOCTOU-safe: stat directo con fallback (la sesión puede borrarse
        # entre el listdir y acá — premortem 2026-06-11).
        try:
            meta_stat = meta_file.stat()
            meta_mtime, meta_size = meta_stat.st_mtime, meta_stat.st_size
        except OSError:
            meta_mtime, meta_size = 0.0, 0
        try:
            hist_mtime = history_file.stat().st_mtime
        except OSError:
            hist_mtime = 0.0
        cached = prev.get(entry)
        # La clave del cache incluye el size: dos writes dentro del mismo
        # quantum de mtime del filesystem no se pierden si cambió el tamaño.
        if (
            cached is not None
            and cached["meta_mtime"] == meta_mtime
            and cached.get("meta_size") == meta_size
        ):
            orders_sig, eta_sig = cached["orders_sig"], cached["eta_sig"]
        elif meta_mtime > 0.0:
            orders_sig, eta_sig = _session_signature(meta_file)
        else:
            orders_sig, eta_sig = ("", "")
        state[entry] = {
            "meta_mtime": meta_mtime,
            "meta_size": meta_size,
            "hist_mtime": hist_mtime,
            "orders_sig": orders_sig,
            "eta_sig": eta_sig,
        }
    return state


def _diff_to_events(
    prev: dict[str, dict],
    curr: dict[str, dict],
) -> tuple[list[str], bool, bool]:
    """(sesiones cambiadas, orders_changed, eta_changed) entre dos muestras."""
    changed_ids: list[str] = []
    orders_changed = False
    eta_changed = False
    for sid in set(prev) | set(curr):
        p, c = prev.get(sid), curr.get(sid)
        if p == c:
            continue
        changed_ids.append(sid)
        if (p or {}).get("orders_sig") != (c or {}).get("orders_sig"):
            orders_changed = True
        if (p or {}).get("eta_sig") != (c or {}).get("eta_sig"):
            eta_changed = True
    return (sorted(changed_ids), orders_changed, eta_changed)


async def _sampler_loop() -> None:
    bus = get_dashboard_event_bus()
    # El baseline se toma DENTRO del loop blindado: si el primer sample
    # lanzara fuera del try, la task moriría en silencio con los generators
    # SSE vivos mandando heartbeats — "Tiempo real conectado" sin eventos
    # para siempre (premortem 2026-06-11, MEDIUM).
    prev: dict[str, dict] | None = None
    while True:
        # Sleep incondicional: también marca el paso de los REINTENTOS si el
        # baseline falla (sin esto, un fallo persistente sería un hot-loop).
        await asyncio.sleep(_SAMPLE_INTERVAL_S)
        try:
            curr = await asyncio.to_thread(
                _sample_vault_state, WORKSPACE_VAULT_DIR, prev
            )
            if prev is None:
                prev = curr
                continue
            changed_ids, orders_changed, eta_changed = _diff_to_events(prev, curr)
            prev = curr
            if not changed_ids:
                continue
            snapshot = await list_dashboard_sessions()
            bus.publish("chats", "sessions_snapshot", payload=snapshot)
            for sid in changed_ids:
                bus.publish("chats", "session_updated", id=sid)
            if orders_changed:
                bus.publish("orders", "changed")
            if eta_changed:
                bus.publish("eta", "changed")
        except Exception:
            # El sampler NUNCA muere por un tick malo (vault a medio escribir,
            # JSON corrupto, etc.) — loguea y sigue.
            logger.exception("dashboard events sampler: tick failed")


def _ensure_sampler() -> None:
    global _sampler_task
    if _sampler_task is None or _sampler_task.done():
        _sampler_task = asyncio.get_running_loop().create_task(_sampler_loop())


async def dashboard_events_generator():
    _ensure_sampler()
    bus = get_dashboard_event_bus()
    queue = bus.subscribe()
    try:
        # Snapshot inicial: el cliente pinta al conectar (o RE-conectar tras un
        # corte) sin esperar el primer cambio del vault.
        snapshot = await list_dashboard_sessions()
        yield DashboardEvent(
            domain="chats",
            type="sessions_snapshot",
            payload=snapshot,
            ts_ms=int(time.time() * 1000),
        ).to_sse()
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_S)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield event.to_sse()
    finally:
        bus.unsubscribe(queue)


@router.get("/events")
async def stream_dashboard_events():
    """SSE multiplexado del dashboard — eventos por dominio, no snapshots ciegos."""
    return StreamingResponse(
        dashboard_events_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@router.get("/sessions")
async def list_dashboard_sessions():
    """
    Returns a realtime list of all active or past WhatsApp sessions, 
    complete with their assigned tags, route handlers, and metadata.
    """
    if not WORKSPACE_VAULT_DIR.exists():
        # Vault doesn't exist yet, return empty
        return {"sessions": []}
        
    sessions = []
    
    for entry in os.listdir(WORKSPACE_VAULT_DIR):
        session_path = WORKSPACE_VAULT_DIR / entry
        if session_path.is_dir() and entry.startswith("wa_"):
            metadata_file = session_path / "metadata.json"
            tag = "NO_ETIQUETADO"
            motivo = "Sin diagnóstico todavía"
            active_route = "ventas"
            phone_number_id = None
            pending_payment_order_id = None

            if metadata_file.exists():
                try:
                    data = json.loads(metadata_file.read_text(encoding="utf-8"))
                    tag = data.get("tag", tag)
                    motivo = data.get("motivo", motivo)
                    active_route = data.get("active_route", active_route)
                    phone_number_id = data.get("phone_number_id")
                    pending_payment_order_id = _compute_pending_payment_order_id(data)
                except json.JSONDecodeError:
                    pass
            
            # Buscamos el timestamp de la ultima conversacion
            last_updated = 0
            history_file = session_path / "sessions" / f"{entry}.jsonl"
            if history_file.exists():
                last_updated = history_file.stat().st_mtime
            else:
                last_updated = session_path.stat().st_mtime
            
            sessions.append({
                "session_id": entry,
                "phone_number": entry.replace("wa_", ""),
                "tag": tag,
                "motivo": motivo,
                "active_agent_route": active_route,
                "phone_number_id": phone_number_id,
                "pending_payment_order_id": pending_payment_order_id,
                "last_updated_timestamp": last_updated
            })
            
    # Sort from most recent to oldest
    sessions.sort(key=lambda x: x["last_updated_timestamp"], reverse=True)
    return {"sessions": sessions}
    

@router.get("/sessions/{session_id}")
async def get_session_history(session_id: str):
    """
    Returns the raw historical chat events from exoclaw-temporal JSONL.
    """
    session_path = WORKSPACE_VAULT_DIR / session_id
    if not session_path.exists() or not session_path.is_dir():
        raise HTTPException(status_code=404, detail="Session not found in Vault")
        
    history_file = session_path / "sessions" / f"{session_id}.jsonl"
    if not history_file.exists():
        return {"session_id": session_id, "messages": []}
        
    messages = []
    
    with open(history_file, 'r', encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg_obj = json.loads(line)
                
                # Clasificador de eventos para facilitar el frontend.
                # `sender=="human"` (mensajes del operador humano via dashboard
                # handoff) se proyecta como `human_message` para que el bubble
                # se renderice distinto y se diferencie del agente.
                role = msg_obj.get("role")
                if role == "user":
                    msg_obj["ui_type"] = "user_message"
                elif role == "tool":
                    msg_obj["ui_type"] = "tool_execution_result"
                elif role == "assistant":
                    if msg_obj.get("sender") == "human":
                        msg_obj["ui_type"] = "human_message"
                    elif msg_obj.get("tool_calls"):
                        msg_obj["ui_type"] = "agent_tool_call"
                    else:
                        msg_obj["ui_type"] = "agent_message"
                else:
                    msg_obj["ui_type"] = "system_event"
                
                messages.append(msg_obj)
            except json.JSONDecodeError:
                continue
                
    tag = "NO_ETIQUETADO"
    motivo = "Sin diagnóstico todavía"
    active_route = "ventas"
    phone_number_id = None
    status_history = []
    pending_payment_order_id = None

    metadata_file = session_path / "metadata.json"
    if metadata_file.exists():
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            tag = data.get("tag", tag)
            motivo = data.get("motivo", motivo)
            active_route = data.get("active_route", active_route)
            phone_number_id = data.get("phone_number_id")
            status_history = data.get("status_history", [])
            pending_payment_order_id = _compute_pending_payment_order_id(data)
        except json.JSONDecodeError:
            pass

    memory_content = None
    memory_file = session_path / "memory" / "MEMORY.md"
    if memory_file.exists():
        memory_content = memory_file.read_text(encoding="utf-8")

    return {
        "session_id": session_id,
        "phone_number": session_id.replace("wa_", ""),
        "tag": tag,
        "motivo": motivo,
        "memory_content": memory_content,
        "active_agent_route": active_route,
        "phone_number_id": phone_number_id,
        "pending_payment_order_id": pending_payment_order_id,
        "status_history": status_history,
        "messages": messages
    }


@router.get("/media/{session_id}/{filename}")
async def get_session_media(session_id: str, filename: str):
    """Sirve una imagen inbound persistida de una sesión de WhatsApp.

    El cliente manda fotos por WhatsApp (típicamente comprobantes de pago); el
    ingest las descarga de Meta y las persiste en ``<vault>/<session_id>/media/``
    (ver ``platform/media``). El JSONL del chat referencia cada una con
    ``image_url=/api/dashboard/media/<session_id>/<filename>`` y el frontend la
    pinta en la burbuja para que el operador humano la pueda ver.

    ``resolve_media_file`` valida ambos segmentos (anti path-traversal) y que el
    archivo exista dentro del directorio de media de la sesión; si no, 404.
    """
    path = resolve_media_file(session_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(path)
