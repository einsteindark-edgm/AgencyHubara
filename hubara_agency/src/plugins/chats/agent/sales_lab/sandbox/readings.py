"""El ingest de la ráfaga dentro del sandbox (motor de decisiones F2 y F3).

El sandbox arranca el workflow directo (no pasa por el webhook), así que el
paso del ingest no corría: el aplazamiento nunca se registraba, el check
CON-02 no se juzgaba en los brazos simulados y la confirmación de compra
llegaba con estado inconsistente. Acá cada mensaje de la ráfaga pasa, en
orden y con el bot del brazo (A1/B0 → reglas, B → Jev), por lo MISMO que en
producción (`ingest_inbound_message.py`):

1. la hora del último mensaje del cliente, y las lecturas (`EngineReadings`)
   con lo que el cliente vio ANTES de él (lo que ESCRIBIÓ: el texto de la
   foto, sin el banner del anuncio; el botón y el carrito aparte), y su
   escritura (`apply_readings`) en el metadata del sandbox;
2. el mensaje queda en el historial del dashboard (el evento que escribió el
   ingest de producción, `materialize.burst_records`): las capacidades que
   leen el historial durante el turno (`datos`, `item_del_pedido`, el
   contexto de Jev con la cita del cliente) ven la ráfaga como en producción;
3. las lecturas sueltas del motor: si el mensaje habla del cupón aplicado
   (`read_coupon_talk`) y lo que pide que no existe en el catálogo
   (`catalog_gap_note_for`, la misma función del ingest);
4. el `plugin_context` de SU señal: la hora de Bogotá y las notas que arma el
   ingest desde el metadata de ese momento (`turn_context`), con la foto del
   bot que citó el cliente (`build_photo_citation_note`). La coalescencia del
   workflow las junta, como en producción.

La nota del episodio nuevo (`boundary_from`: el episodio anterior, cerrado)
va en el primer mensaje, con la cortesía que leyó el motor (2026-09-30: sin
ella el laboratorio no reproducía el «¿en qué te puedo ayudar hoy?» de
producción). No se reconstruyen la nota de la respuesta a una campaña ni la
relectura del cupo del cupón en Medusa (el sandbox no tiene Medusa: queda lo
último que se supo, como cuando Medusa no responde a tiempo).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_lab.cases import without_referral_banner
from src.plugins.chats.agent.sales.decisions.retiro import en_retiro

#: La ruta de la persona del equipo (`active_route`): con ella el ingest no
#: arma la nota de fuera de catálogo.
_HUMAN_ROUTE = "humano"
#: Un mensaje que ES la foto (la reentrada de la visión: foto, foto que no se
#: pudo ver o comprobante): no lleva la nota de las fotos ya reconocidas.
_PHOTO_PREFIXES = (
    "[el cliente envió una foto: ",
    "[el cliente envió una imagen que no pude ver bien]",
    "[el cliente envió un comprobante de pago: ",
)


@dataclass(frozen=True)
class IngestedMessage:
    """Lo que el ingest dejó de UN mensaje de la ráfaga."""

    # La traza de sus lecturas (compra, retoma, baja).
    readings: list[dict[str, Any]]
    # El `plugin_context` de su señal: la hora de Bogotá y las notas del ingest.
    context: list[str]
    # La foto leída de nuevo con la visión de hoy (`sandbox/photos.py`).
    photo: dict[str, Any] | None = None
    # El texto que recibe el turno.
    text: str = ""


def _history_path(vault_dir: Path, session_id: str) -> Path:
    return Path(vault_dir) / session_id / "sessions" / f"{session_id}.jsonl"


def append_history_event(vault_dir: Path, session_id: str, event: dict[str, Any]) -> None:
    """Una línea más del historial del dashboard, con el formato del store
    de producción (`FilesystemMessageHistoryStore`)."""
    path = _history_path(vault_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _event_ms(event: dict[str, Any]) -> int | None:
    from datetime import datetime, timezone

    value = event.get("timestamp")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        return int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp() * 1000)
    return None


def _history(vault_dir: Path, session_id: str) -> list[dict[str, Any]]:
    path = _history_path(vault_dir, session_id)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            events.append(row)
    return events


#: Tipos de mensaje con texto del cliente (`parsed.text`): el texto y el botón
#: de una plantilla. Los botones y listas de nuestros mensajes, un carrito,
#: una ubicación o un adjunto no traen texto: el código lee su `interactive`
#: / `order`.
_TYPED_KINDS = frozenset({"text", "button"})


def customer_text(message: dict[str, Any]) -> str | None:
    """Lo que leen las lecturas del ingest: lo que ESCRIBIÓ el cliente, sin lo
    que agregó el ingest (campaña citada, episodio anterior, banner del
    anuncio). En una foto, solo el texto que puso en ella (`caption`): la
    descripción la escribió la visión."""
    if "caption" in message:
        caption = message.get("caption")
        return caption if isinstance(caption, str) and caption.strip() else None
    if str(message.get("kind") or "text") not in _TYPED_KINDS:
        return None
    return without_referral_banner(str(message.get("raw_text") or message.get("text") or "")) or None


def message_text(message: dict[str, Any]) -> str | None:
    """El texto del mensaje que el ingest le pasa a `apply_readings`
    (`parsed.text`): el de `customer_text`, salvo en una foto, donde es el
    texto que reentró con la descripción."""
    if "caption" in message:
        return str(message.get("raw_text") or message.get("text") or "") or None
    return customer_text(message)


def _payload(message: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = message.get(key)
    return value if isinstance(value, dict) else None


def _write_metadata(vault_dir: Path, session_id: str, metadata: dict[str, Any]) -> None:
    path = Path(vault_dir) / session_id / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")


def turn_context(
    metadata: dict[str, Any],
    *,
    at_ms: int,
    coupon_in_play: bool = True,
    photo_note: str | None = None,
    gap_note: str | None = None,
    product_note: str | None = None,
    facts_note: str | None = None,
    boundary_note: str | None = None,
) -> list[str]:
    """El `plugin_context` de la señal de un mensaje, como lo arma el ingest
    desde el metadata de ese momento: la hora de Bogotá y las notas, en el
    orden de producción. `coupon_in_play`: si el mensaje habla del cupón
    aplicado; `photo_note`: la foto del bot que citó; `gap_note`: la nota de
    lo que no existe en el catálogo; `product_note`: la foto del cliente es un
    producto nuestro (`photo_product.build_photo_product_note`); `facts_note`:
    las fotos del episodio ya reconocidas (`photo_product.build_photo_facts_note`,
    en los mensajes que no son una foto); `boundary_note`: la nota del episodio
    nuevo (`build_episode_boundary_note`)."""
    from src.plugins.chats.agent.sales.context import build_bogota_context_string
    from src.plugins.chats.agent.sales.use_cases.coupons import build_coupon_note
    from src.plugins.chats.agent.sales.use_cases.order_draft import build_order_draft_note, get_projectable_draft
    from src.plugins.chats.agent.sales.use_cases.web_cart import build_web_cart_note
    from src.plugins.chats.agent.sales.use_cases.web_product_ref import build_web_product_note
    from src.plugins.chats.shared.purchase_signals import build_deferral_note
    from src.sdk.messagingkit import fresh_resume_label

    draft = get_projectable_draft(metadata)
    notes = [
        build_deferral_note(metadata, resume_label=fresh_resume_label(metadata)),
        build_web_cart_note(metadata),
        build_web_product_note(metadata),
        build_order_draft_note(draft) if draft else None,
        build_coupon_note(metadata, in_play=coupon_in_play),
        facts_note,
        product_note,
        photo_note,
        gap_note,
    ]
    bogota = build_bogota_context_string(now=datetime.fromtimestamp(at_ms / 1000, tz=timezone.utc))
    # La nota del episodio nuevo va primero, como en el ingest.
    return [bogota, *(n for n in [boundary_note, *notes] if n)]


async def ingest_burst(
    metadata: dict[str, Any],
    messages: Sequence[dict[str, Any]],
    *,
    session_id: str,
    vault_dir: Path,
    at_ms: int,
    records: Sequence[dict[str, Any]] | None = None,
    catalog: Any = None,
    on_message: Callable[[int], None] | None = None,
    between: Sequence[dict[str, Any]] = (),
    photos: Any = None,
    boundary_from: dict[str, Any] | None = None,
) -> list[IngestedMessage]:
    """Pasa cada mensaje por el ingest (ver el módulo): escribe el metadata
    (mutación y archivo) y el historial del sandbox. `records`: el evento del
    dashboard de cada mensaje (`materialize.burst_records`); sin él, uno con
    la misma forma. `catalog`: el del sandbox (sin catálogo, no hay nota de
    fuera de catálogo, como en producción). `on_message(k)`: avisa antes de
    cada mensaje (el caso anota de cuál salen las decisiones). `between`: los
    eventos del dashboard que pasaron entre el primer mensaje de la ráfaga y
    el inicio del turno sin ser de ella (`dashboard_between` del caso: la
    respuesta del turno anterior en una ráfaga partida, un mensaje del
    equipo); cada uno entra en su lugar por la hora, así cada mensaje se lee
    con lo que había cuando llegó y el turno arranca con el historial de
    producción. `photos` (`sandbox/photos.LabPhotoStep`): la foto del banco
    se vuelve a leer con la visión de hoy; su segmento cambia en el mensaje
    (lo que recibe el turno) y en su evento del dashboard, y la señal lleva la
    nota si la foto es un producto nuestro."""
    from src.plugins.chats.agent.sales.decisions.readings import (
        EngineReadings,
        Inbound,
        apply_readings,
        read_coupon_talk,
    )
    from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode
    from src.plugins.chats.agent.sales.use_cases.episode_memory import (
        quote_template_in_turn,
        unseen_template_text,
        with_previous_episode,
    )
    from src.plugins.chats.agent.sales.use_cases.funnel_stage import resolve_funnel_stage
    from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
        build_episode_boundary_note,
        build_photo_citation_note,
        catalog_gap_note_for,
    )
    from src.plugins.chats.agent.sales.use_cases.photo_product import build_photo_facts_note
    from src.plugins.chats.agent.sales_lab.sandbox.materialize import burst_record
    from src.plugins.chats.agent.sales_lab.sandbox.photos import stored_photo
    from src.sdk.messagingkit import compute_service_window_expiry, opt_out_campaign_id, resolve_local_timezone

    tz = resolve_local_timezone(session_id)
    provider = EngineReadings(Path(vault_dir))
    out: list[IngestedMessage] = []
    pending = sorted((dict(e) for e in between), key=lambda e: _event_ms(e) or 0)
    for k, message in enumerate(messages, 1):
        if on_message is not None:
            on_message(k)
        # La foto, leída con la visión de hoy (como la reentrada del ingest).
        reread = await photos.reread(message) if photos is not None else None
        if reread is not None and isinstance(message, dict):
            for key in ("text", "raw_text"):
                if isinstance(message.get(key), str):
                    message[key] = reread.apply(message[key])
            # Como el ingest de hoy antes de la reentrada: la foto queda en el
            # metadata con su episodio y, si se reconoció, el producto.
            active = get_active_episode(metadata)
            recent = list(metadata.get("recent_image_descriptions") or [])
            recent.append(stored_photo(
                {"media_id": Path(str(message.get("image"))).stem, "kind": "foto_producto"}, reread,
                episode_id=(active or {}).get("episode_id"),
            ))
            metadata["recent_image_descriptions"] = recent[-20:]
        is_photo = reread is not None or without_referral_banner(str(message.get("text") or "")).startswith(
            _PHOTO_PREFIXES
        )
        ts = message.get("ts_ms")
        now_ms = int(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else int(at_ms)
        while pending and (_event_ms(pending[0]) or 0) < now_ms:
            append_history_event(vault_dir, session_id, pending.pop(0))
        # Lo que el cliente vio ANTES de este mensaje (el ingest lo lee del
        # historial antes de guardar el mensaje).
        events = _history(vault_dir, session_id)
        # Como el ingest: la plantilla a la que responde (un aviso del ETA, un
        # gancho), si es lo último que recibió antes de este mensaje.
        template = unseen_template_text(events)
        wamid = str(message.get("wamid") or f"lab.{k}")
        # Como el ingest, antes de las lecturas: el último mensaje del cliente
        # y la ventana de servicio que reabre (la nota del aplazamiento
        # confirma la fecha solo si la dio ESTE mensaje: `fresh_resume_label`).
        metadata["last_inbound_at_ms"] = now_ms
        metadata["service_window_expires_at_ms"] = compute_service_window_expiry(now_ms)
        readings = await provider.read(
            Inbound(
                session_id=session_id, text=customer_text(message), now_ms=now_ms, message_id=wamid,
                interactive=_payload(message, "interactive"), order=_payload(message, "order"),
                metadata=metadata, events=events, stage=resolve_funnel_stage(metadata), tz=tz,
            )
        )
        apply_readings(
            metadata, readings, text=message_text(message), now_ms=now_ms, message_id=wamid, tz=tz,
            opt_out_campaign_id=opt_out_campaign_id(metadata, now_ms),
        )
        record = records[k - 1] if records is not None and k <= len(records) else burst_record(message, at_ms=at_ms)
        if reread is not None:
            record = {**record, "content": reread.apply(str(record.get("content") or ""))}
        append_history_event(vault_dir, session_id, record)
        # Como el ingest: la señal vale para el ÚLTIMO mensaje del cliente.
        metadata["last_inbound_message_id"] = wamid
        _write_metadata(vault_dir, session_id, metadata)
        # Las lecturas sueltas, con el texto efectivo (una foto ya leída) y lo
        # que el cliente vio antes de este mensaje.
        effective = str(message.get("raw_text") or message.get("text") or "")
        coupon = await read_coupon_talk(
            Path(vault_dir), session_id=session_id, metadata=metadata, text=effective, events=events
        )
        gap = (
            await catalog_gap_note_for(catalog, vault_dir=Path(vault_dir), session_id=session_id, text=effective)
            if metadata.get("active_route") != _HUMAN_ROUTE
            else None
        )
        # La foto del bot que citó el cliente (la cita va en su evento del
        # dashboard: `reply_to`, como el `context` del webhook).
        quoted = record.get("reply_to") if isinstance(record.get("reply_to"), dict) else None
        photo = build_photo_citation_note({"id": quoted.get("id")} if quoted else None, metadata)
        # El mensaje que abrió el episodio lleva la nota del episodio nuevo,
        # con la cortesía que acaba de leer (como el ingest).
        boundary = (
            build_episode_boundary_note(boundary_from, courtesy=readings.courtesy_only)
            if boundary_from is not None and k == 1
            else None
        )
        # El texto del turno, como el ingest: la plantilla citada adelante y,
        # en el mensaje que abrió el episodio, la conversación anterior.
        text = str(message.get("text") or "")
        if template is not None:
            text = quote_template_in_turn(template, text)
        if boundary_from is not None and k == 1:
            text = with_previous_episode(boundary_from, text)
        out.append(
            IngestedMessage(
                text=text,
                readings=list(readings.verdicts),
                context=turn_context(
                    metadata, at_ms=at_ms, coupon_in_play=bool(coupon.value), photo_note=photo, gap_note=gap,
                    product_note=reread.note if reread is not None else None,
                    facts_note=None if is_photo else build_photo_facts_note(metadata),
                    boundary_note=boundary,
                ),
                photo=(
                    {"image": message.get("image"), "description": reread.description, "product": reread.product,
                     "trace": reread.trace}
                    if reread is not None
                    else None
                ),
            )
        )
    for event in pending:
        append_history_event(vault_dir, session_id, event)
    return out


@en_retiro("funcion:apply_burst_readings")
async def apply_burst_readings(
    metadata: dict[str, Any],
    messages: Sequence[dict[str, Any]],
    *,
    session_id: str,
    vault_dir: Path,
    at_ms: int,
    records: Sequence[dict[str, Any]] | None = None,
) -> list[list[dict[str, Any]]]:
    """`ingest_burst` sin catálogo; devuelve solo la traza de las lecturas de
    cada mensaje."""
    ingested = await ingest_burst(
        metadata, messages, session_id=session_id, vault_dir=vault_dir, at_ms=at_ms, records=records
    )
    return [m.readings for m in ingested]
