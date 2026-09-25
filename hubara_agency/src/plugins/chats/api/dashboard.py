from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.media import resolve_media_file
from src.plugins.chats.api.session_guard import require_valid_session_id
from src.plugins.chats.shared.chat_events import (
    annotate_touched_buttons,
    detect_chat_event,
)
from src.plugins.chats.shared import turn_traces
from src.plugins.chats.shared.origin import session_origin, with_ad_names
from src.plugins.chats.shared.turn_view import annotate_turn_keys
from src.sdk.connectorkit import fetch_meta_ad_names, meta_marketing_token
from src.sdk.dashboardkit import DashboardEvent, get_dashboard_event_bus
from src.sdk.messagingkit import postponed_view

router = APIRouter()


# ── Origen real de la conversación (campaña de Meta) ─────────────────────────
#
# El inspector mostraba un origen hardcodeado ("Meta Ads · velas"). El ingest
# persiste el referral CTWA (source_id = ad id) y Graph resuelve el nombre real
# de la campaña con UN GET batch (mismo resolver que el tablero de ads). Cache
# TTL en proceso: el listado se sirve seguido (SSE + fallback) y Graph no
# tiene por qué recibir un call por request. Best-effort: sin token / Graph
# caído → {} y el dashboard degrada al headline del referral.
_AD_NAMES_TTL_S = 15 * 60
_ad_names_cache: dict[str, tuple[float, dict[str, dict[str, str | None]]]] = {}


def _resolve_ad_names(ad_ids: list[str]) -> dict[str, dict[str, str | None]]:
    ids = sorted({i for i in ad_ids if i})
    if not ids:
        return {}
    token = meta_marketing_token()
    if not token:
        return {}
    key = ",".join(ids)
    now = time.monotonic()
    hit = _ad_names_cache.get(key)
    if hit and now - hit[0] < _AD_NAMES_TTL_S:
        return hit[1]
    names = fetch_meta_ad_names(ids, token=token)
    _ad_names_cache[key] = (now, names)
    return names


def _origins_with_names(
    origins: dict[str, dict | None],
) -> dict[str, dict | None]:
    """{session_id: origin crudo} → mismo dict con campaign_name/ad_name."""
    ad_ids = [
        o["source_id"] for o in origins.values() if o and o.get("source_id")
    ]
    names = _resolve_ad_names(ad_ids)
    return {sid: with_ad_names(o, names) for sid, o in origins.items()}


# Reason por la que el agente escala una venta para que un humano verifique el
# pago (lo escribe `escalate_to_human(reason_category="PAYMENT_VERIFICATION_PENDING")`).
_PAYMENT_PENDING_REASON = "PAYMENT_VERIFICATION_PENDING"


def _compute_pending_payment_order_id(data: dict, order_facts=None) -> str | None:
    """¿Esta sesión tiene un pedido esperando que un humano confirme el pago?

    Devuelve el `order_id` (id backend de Medusa) a confirmar, o ``None``.

    El agente, al cerrar una venta, registra el pedido en Medusa y escala con
    `escalate_to_human(reason_category="PAYMENT_VERIFICATION_PENDING")` — eso
    deja en el `metadata.json` del chat: ``active_route="humano"``,
    ``escalation_reason="PAYMENT_VERIFICATION_PENDING"`` y el
    ``registered_order`` exitoso. Esta función reconoce ese estado para que el
    frontend muestre el botón "Confirmar pago" en el chat (mismo endpoint que
    el tablero de orders).

    ``order_facts`` (`OrderFactsSnapshot`): cuando trae el pedido, MANDA EL
    PEDIDO y no las marcas del chat — pagado (y no cancelado) esconde el
    botón aunque el chat siga en ``HUMANO`` (el operador registró el pago en
    Medusa Admin), y sin pagar lo muestra aunque el chat diga
    ``COMPRA_EXITOSA`` (pedido #32: confirmado por error y reembolsado). Un
    pedido cancelado nunca ofrece pagar. La RUTA no se toca: esto sigue
    siendo solo para chats en la bandeja humana escalados por verificación
    de pago.

    Sin dato del pedido (Medusa caído, o caller legacy sin snapshot) vale la
    regla vieja: las marcas del chat (``COMPRA_EXITOSA`` / ``RECHAZO`` /
    ``payment_confirmed_at_ms``) cierran el caso.
    """
    if data.get("active_route") != "humano":
        return None
    if data.get("escalation_reason") != _PAYMENT_PENDING_REASON:
        return None
    registered = data.get("registered_order")
    if not isinstance(registered, dict) or registered.get("success") is not True:
        return None
    order_id = registered.get("order_id")
    if not isinstance(order_id, str) or not order_id:
        return None

    fact = None
    if order_facts is not None and order_id not in order_facts.unresolved:
        fact = order_facts.facts.get(order_id)
        if fact is None:
            return None  # el pedido ya no existe en Medusa
        if fact.stage == "cancelled":
            return None
        return None if fact.counts_as_revenue else order_id

    # Legacy (sin datos del pedido): las marcas del chat cierran el caso.
    if data.get("tag") in ("COMPRA_EXITOSA", "RECHAZO"):
        return None
    for episode in data.get("episodes") or []:
        if (
            isinstance(episode, dict)
            and episode.get("order_id") == order_id
            and episode.get("payment_confirmed_at_ms")
        ):
            return None
    return order_id


def _pending_payment_order_id_from_metadata(data: dict) -> str | None:
    """Candidato a pedido con pago por verificar, SIN consultar Medusa.

    Sirve para juntar los ids de todo el vault en UNA sola lectura de
    `OrderFacts` antes de decidir con el estado real (lista del inbox).
    """
    return _compute_pending_payment_order_id(data, None)


async def _pending_payment_facts(order_ids: set[str]):
    """Estado canónico de los pedidos del inbox (botón "Confirmar pago" + chip
    de orden de la fila), en UNA lectura.

    Falla / Medusa caído → snapshot `unresolved`: cada sesión cae a la regla
    vieja por etiquetas (el operador sigue viendo el botón como antes).
    """
    from src.sdk.connectorkit import OrderFactsSnapshot, get_order_facts_port

    if not order_ids:
        return OrderFactsSnapshot()
    try:
        return await get_order_facts_port().get_facts(order_ids)
    except Exception:  # noqa: BLE001 — el inbox nunca 500-ea por Medusa
        logger.warning("dashboard: OrderFacts no disponible para el inbox")
        return OrderFactsSnapshot(unresolved=frozenset(order_ids), stale=True)


def _order_ref_candidate_ids(data: dict) -> set[str]:
    """Pedidos EXITOSOS de la sesión (el vigente + el historial): los ids que
    el listado junta para leer `OrderFacts` en UN batch. El vault solo aporta
    este vínculo conversación → `order_id` (regla 13); qué ES cada pedido lo
    dice `OrderFacts`."""
    ids: set[str] = set()
    registered = data.get("registered_order")
    if isinstance(registered, dict) and registered.get("success") is True:
        order_id = registered.get("order_id")
        if isinstance(order_id, str) and order_id:
            ids.add(order_id)
    if not ids:
        return ids  # sin pedido vigente no hay chip — el historial no aplica
    for h in data.get("registered_orders_history") or []:
        if isinstance(h, dict) and h.get("success") is True:
            order_id = h.get("order_id")
            if isinstance(order_id, str) and order_id:
                ids.add(order_id)
    return ids


def _real_order_number(fact) -> str | None:
    """Número de orden PELADO ("32") de un pedido que ya es una orden real, o
    ``None``.

    * Un **draft** todavía no es una orden: el bot registró la venta pero
      nadie agendó la entrega (que es lo que lo convierte, conservando el id).
    * `OrderFacts.display_id` viene formateado para la vista Orders ("#32").
      Se pela acá y el "#" lo pone UNA sola vez el frontend — la primera
      versión del chip pintaba "##32".
    * Sin `display_id` numérico el mapper de Medusa cae a "#<últimos 6 del
      id>": un id interno que al operador no le dice nada. Eso no es un número
      de orden → sin chip.
    """
    if fact is None or fact.is_draft:
        return None
    number = str(fact.display_id or "").lstrip("#").strip()
    return number if number.isdigit() else None


def _compute_order_ref(data: dict, order_facts) -> dict | None:
    """¿Esta conversación ya pertenece a una ORDEN? ¿Cuál, y cómo va el pago?

    Devuelve ``{order_id, display_id, payment, count}`` o ``None``. Enciende
    el chip de la fila de la bandeja: el operador ve de un vistazo qué chats
    del filtro "Asignadas al humano" ya son una orden (y cuál) y cuáles siguen
    en proceso. **Solo órdenes reales con número** — un draft, un pedido que
    Medusa no conoce o uno sin número de orden no pintan nada.

    Todo lo que el chip dice sale de ``order_facts`` (`OrderFactsSnapshot`,
    regla 13 del repo): draft/orden, número, pago y etapa vienen del mismo
    store que alimenta la vista Orders, leído en UNA pasada para toda la
    bandeja. Del vault sale solo el vínculo conversación → `order_id`. Sin dato
    del pedido (snapshot ausente, Medusa caído y pedido nunca visto) no hay
    chip: no se sabe si es draft u orden, y un chip inventado es peor que
    ninguno.

      * ``pending``   — orden sin pago confirmado (también tras un reembolso).
      * ``confirmed`` — pagada y no cancelada (`counts_as_revenue`).
      * ``cancelled`` — orden cancelada.

    ``count`` = cuántas órdenes REALES lleva el cliente en esta sesión (el
    chip muestra "#33 +1"); los drafts viejos no cuentan.
    """
    if order_facts is None:
        return None
    registered = data.get("registered_order")
    if not isinstance(registered, dict) or registered.get("success") is not True:
        return None
    order_id = registered.get("order_id")
    if not isinstance(order_id, str) or not order_id:
        return None

    fact = order_facts.facts.get(order_id)
    number = _real_order_number(fact)
    if number is None:
        return None

    if fact.stage == "cancelled":
        payment = "cancelled"
    else:
        payment = "confirmed" if fact.counts_as_revenue else "pending"

    real_orders = sum(
        1
        for candidate in _order_ref_candidate_ids(data)
        if _real_order_number(order_facts.facts.get(candidate)) is not None
    )
    return {
        "order_id": order_id,
        "display_id": number,
        "payment": payment,
        "count": real_orders,
    }


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

# ── Último mensaje del CLIENTE (sonido de "mensaje nuevo" en el dashboard) ──
#
# `last_updated_timestamp` es el mtime del JSONL: se mueve también con cada
# turno del bot / del operador. El dashboard necesita el último `role: "user"`
# para sonar SOLO cuando escribe el cliente. Se lee el JSONL desde el final
# por bloques (el inbound casi siempre está en los últimos KB) y se cachea por
# (mtime, size): el listado se recalcula en cada tick del sampler y no tiene
# por qué releer historiales que no cambiaron.
_TAIL_CHUNK_BYTES = 64 * 1024
_last_inbound_cache: dict[Path, tuple[float, int, int | None]] = {}


def _inbound_ms_from_line(raw: bytes) -> int | None:
    # Pre-filtro barato: casi todas las líneas del final son del bot.
    if b'"user"' not in raw:
        return None
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None  # escritura a medias / codepoint cortado
    if not isinstance(event, dict) or event.get("role") != "user":
        return None
    ts = event.get("timestamp")
    if not isinstance(ts, str):
        return None  # inbound legacy sin timestamp: no sirve para comparar
    try:
        return int(datetime.fromisoformat(ts).timestamp() * 1000)
    except ValueError:
        return None


def _scan_last_inbound_ms(history_file: Path) -> int | None:
    with history_file.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        carry = b""  # primera línea (posiblemente incompleta) del bloque previo
        while pos > 0:
            step = min(_TAIL_CHUNK_BYTES, pos)
            pos -= step
            f.seek(pos)
            lines = (f.read(step) + carry).split(b"\n")
            # lines[0] puede estar cortada si no llegamos al inicio del archivo.
            carry = lines.pop(0) if pos > 0 else b""
            for raw in reversed(lines):
                found = _inbound_ms_from_line(raw)
                if found is not None:
                    return found
        return _inbound_ms_from_line(carry) if carry else None


def _last_inbound_ms(history_file: Path) -> int | None:
    try:
        st = history_file.stat()
        cached = _last_inbound_cache.get(history_file)
        if cached is not None and cached[:2] == (st.st_mtime, st.st_size):
            return cached[2]
        value = _scan_last_inbound_ms(history_file)
    except OSError:
        return None
    _last_inbound_cache[history_file] = (st.st_mtime, st.st_size, value)
    return value


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
    
    # `metadata.json` por sesion candidata - el estado real de esos pedidos se
    # resuelve DESPUES, en una sola lectura de OrderFacts (no N por sesion).
    session_data: dict[str, dict] = {}

    for entry in os.listdir(WORKSPACE_VAULT_DIR):
        session_path = WORKSPACE_VAULT_DIR / entry
        if session_path.is_dir() and entry.startswith("wa_"):
            metadata_file = session_path / "metadata.json"
            tag = "NO_ETIQUETADO"
            motivo = "Sin diagnóstico todavía"
            active_route = "ventas"
            phone_number_id = None
            pending_payment_order_id = None
            order_ref = None
            origin = None
            postponed = None

            if metadata_file.exists():
                try:
                    data = json.loads(metadata_file.read_text(encoding="utf-8"))
                    tag = data.get("tag", tag)
                    motivo = data.get("motivo", motivo)
                    active_route = data.get("active_route", active_route)
                    phone_number_id = data.get("phone_number_id")
                    pending_payment_order_id = (
                        _pending_payment_order_id_from_metadata(data)
                    )
                    session_data[entry] = data
                    origin = session_origin(data)
                    # Filtro "Pospuestos": dijo cuándo retoma y aún no quedó
                    # SIN_RESPUESTA (la regla vive en messagingkit).
                    postponed = postponed_view(data, int(time.time() * 1000))
                except json.JSONDecodeError:
                    pass
            
            # Buscamos el timestamp de la ultima conversacion
            last_updated = 0
            last_inbound_ms = None
            history_file = session_path / "sessions" / f"{entry}.jsonl"
            if history_file.exists():
                last_updated = history_file.stat().st_mtime
                last_inbound_ms = _last_inbound_ms(history_file)
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
                "order_ref": order_ref,
                "last_updated_timestamp": last_updated,
                "last_inbound_ms": last_inbound_ms,
                "origin": origin,
                "postponed": postponed,
            })

    # Estado real de los pedidos del inbox, en UNA lectura: los que esperan
    # verificación de pago (botón) + todo pedido vinculado a una sesión (chip
    # de orden). El store de OrderFacts es una caché compartida con la vista
    # Orders — lo ya visto sale al instante.
    order_ids = {
        s["pending_payment_order_id"] for s in sessions if s["pending_payment_order_id"]
    }
    for data_ in session_data.values():
        order_ids |= _order_ref_candidate_ids(data_)
    facts = await _pending_payment_facts(order_ids)
    for s_ in sessions:
        data_ = session_data.get(s_["session_id"])
        if data_ is None:
            continue
        if s_["pending_payment_order_id"]:
            s_["pending_payment_order_id"] = _compute_pending_payment_order_id(
                data_, facts
            )
        s_["order_ref"] = _compute_order_ref(data_, facts)

    # Nombres reales de campaña en UN batch para todo el listado.
    enriched = _origins_with_names({s["session_id"]: s["origin"] for s in sessions})
    for s_ in sessions:
        s_["origin"] = enriched.get(s_["session_id"])

    # Sort from most recent to oldest
    sessions.sort(key=lambda x: x["last_updated_timestamp"], reverse=True)
    return {"sessions": sessions}
    

class _empty_lines:
    """Context manager iterable vacío — mismo shape que un archivo abierto."""

    def __enter__(self):
        return iter(())

    def __exit__(self, *exc):
        return False


_QUOTE_AUTHOR_BY_UI_TYPE = {
    "user_message": "user",
    "human_message": "human",
    "agent_message": "agent",
    "ui_component_sent": "agent",
}


def _resolve_reply_quotes(
    messages: list[dict], text_index: dict | None = None
) -> None:
    """Completa in-place los ``reply_to`` que el ingest dejó solo con el id.

    Dos fuentes, en orden:

    1. **El JSONL** — el cliente citó un mensaje propio (su foto, su texto) o
       uno nuestro con ``wamid`` en el evento (template, componente UI, eco
       standby, adjunto del operador).
    2. **``metadata[outbound_text_index]``** — las burbujas de texto que
       salieron por ``send_message_to_session``. Un evento ``assistant`` es UN
       texto fragmentado en N burbujas, así que su wamid no cabe en el evento:
       el índice guarda wamid → ``{text, author}`` por burbuja (caso
       2026-09-17: citar una respuesta del bot mostraba "Mensaje no
       disponible").

    Sin match en ninguna (burbuja ya evictada del índice, chat previo al
    deploy) queda solo el id y el frontend muestra su fallback.
    """
    by_wamid = {m["wamid"]: m for m in messages if m.get("wamid")}
    index = text_index if isinstance(text_index, dict) else {}
    for msg in messages:
        reply_to = msg.get("reply_to")
        if not isinstance(reply_to, dict) or reply_to.get("author"):
            continue
        quoted = by_wamid.get(reply_to.get("id"))
        if quoted is not None:
            reply_to["author"] = _QUOTE_AUTHOR_BY_UI_TYPE.get(
                quoted.get("ui_type"), "agent"
            )
            if quoted.get("content"):
                reply_to["text"] = quoted["content"]
            if quoted.get("image_url"):
                reply_to["image_url"] = quoted["image_url"]
            continue
        bubble = index.get(reply_to.get("id"))
        if not isinstance(bubble, dict):
            continue
        author = bubble.get("author")
        reply_to["author"] = author if author in ("agent", "human") else "agent"
        if bubble.get("text"):
            reply_to["text"] = bubble["text"]


def _annotate_turns(messages: list[dict], session_id: str) -> None:
    annotate_turn_keys(messages, turn_traces.read_traces(WORKSPACE_VAULT_DIR, session_id), session_id)


@router.get("/sessions/{session_id}")
async def get_session_history(session_id: str):
    """
    Returns the raw historical chat events from exoclaw-temporal JSONL.
    """
    # ANTES de armar cualquier ruta: `..` resolvía al padre del vault (200).
    require_valid_session_id(session_id)
    session_path = WORKSPACE_VAULT_DIR / session_id
    if not session_path.exists() or not session_path.is_dir():
        raise HTTPException(status_code=404, detail="Session not found in Vault")
        
    history_file = session_path / "sessions" / f"{session_id}.jsonl"
    messages = []

    # Sin JSONL todavía (sesión recién creada por el ingest / seed sin
    # historial) igual devolvemos la forma completa: tag, origen, historial de
    # estados. Antes el early-return `{"session_id", "messages": []}` rompía el
    # schema Zod del frontend (campos requeridos ausentes).
    with open(history_file, 'r', encoding="utf-8") if history_file.exists() else _empty_lines() as f:
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
                    elif msg_obj.get("kind") == "ui_component":
                        # Marker de envío no-textual (catálogo, flow, botones…)
                        # escrito por flush_pending_ui_intents_activity — el
                        # frontend lo pinta como nota de sistema.
                        msg_obj["ui_type"] = "ui_component_sent"
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
    order_ref = None
    origin = None
    service_window_expires_at_ms = None

    # El metadata se lee ANTES de resolver las citas: el índice de burbujas
    # salientes del bot/operador (`outbound_text_index`) vive ahí.
    data: dict = {}
    metadata_file = session_path / "metadata.json"
    if metadata_file.exists():
        try:
            parsed = json.loads(metadata_file.read_text(encoding="utf-8"))
            data = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            data = {}

    _resolve_reply_quotes(messages, data.get("outbound_text_index"))
    # Cada burbuja con el turno del bot que la produjo: el panel pone un botón
    # por turno que abre su hilo (plan del laboratorio PR 17). Fuera del loop
    # de eventos (la traza de un chat largo pesa MB) y nunca a costa del chat:
    # si algo falla, el historial sale igual, sin botones.
    try:
        await asyncio.to_thread(_annotate_turns, messages, session_id)
    except Exception:  # noqa: BLE001
        logger.warning("dashboard: sin botones de turno para {}", session_id, exc_info=True)

    # Forma real del mensaje para el panel del chat: los botones vuelven a ser
    # botones, la foto su foto, el caption del cliente separado de lo que
    # describió la visión. `event` ausente = mensaje normal (se pinta como
    # siempre). La correlación del botón tocado se resuelve ACÁ y no en el
    # frontend: es una propiedad del hilo, no de una burbuja suelta.
    for msg_obj in messages:
        event = detect_chat_event(msg_obj)
        if event is not None:
            msg_obj["event"] = event
    annotate_touched_buttons(messages)

    if data:
        tag = data.get("tag", tag)
        motivo = data.get("motivo", motivo)
        active_route = data.get("active_route", active_route)
        phone_number_id = data.get("phone_number_id")
        status_history = data.get("status_history", [])
        pending_payment_order_id = (
            _pending_payment_order_id_from_metadata(data)
        )
        origin = _origins_with_names({session_id: session_origin(data)})[session_id]
        # El composer humano lo usa para ofrecer "Reactivar conversación"
        # (plantilla) cuando la ventana 24h ya cerró. None = desconocido.
        expires = data.get("service_window_expires_at_ms")
        service_window_expires_at_ms = expires if isinstance(expires, int) else None

    if data:
        # UNA lectura resuelve el botón "Confirmar pago" y el chip de orden:
        # no pueden contar cosas distintas del mismo pedido.
        order_ids = _order_ref_candidate_ids(data)
        if pending_payment_order_id:
            order_ids.add(pending_payment_order_id)
        if order_ids:
            facts = await _pending_payment_facts(order_ids)
            if pending_payment_order_id:
                pending_payment_order_id = _compute_pending_payment_order_id(
                    data, facts
                )
            order_ref = _compute_order_ref(data, facts)

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
        "order_ref": order_ref,
        "service_window_expires_at_ms": service_window_expires_at_ms,
        "status_history": status_history,
        "origin": origin,
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
