"""Activities del agente de reseñas: decidir, invitar y recibir la calificación.

Las funciones `plan_step`, `send_request` y `process_submission` son la lógica
con dependencias inyectadas (`ReviewsDeps`, testeable sin Temporal); las
`*_activity` son los wrappers que registra el worker con las dependencias
reales (`get_reviews_deps`).

Invariantes:
  * una invitación por pedido (se RECLAMA en el vault antes de enviar) y un
    envío fallido se registra, no se reintenta (mandar dos veces es peor que
    no mandar);
  * una calificación por pedido: la respuesta repetida no da otro cupón ni
    otro gracias;
  * el cupón y el botón a Google van con CUALQUIER número de estrellas
    (Google prohíbe premiar su reseña o pedirla solo a los contentos); con
    1–3 estrellas además la conversación pasa al equipo.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import unicodedata
from dataclasses import dataclass, replace
from typing import Any

from temporalio import activity

from src.plugins.reviews.agent.card import render_review_card
from src.plugins.reviews.agent.store import ReviewRecord, review_id_for
from src.plugins.reviews.domain.copy import (
    FLOW_CTA,
    FLOW_SCREEN,
    TEMPLATE_NAME,
    expires_label,
    flow_action_data,
    invitation_body,
    template_variables,
)
from src.plugins.reviews.domain.planning import ReviewRequestFacts, plan_review_request
from src.plugins.reviews.domain.submission import (
    author_display,
    parse_review_response,
    review_flow_token,
    reward_percent,
    thanks_text,
)
from src.sdk.connectorkit import CouponGrant
from src.sdk.messagingkit import (
    InteractiveCTAUrlOutbound,
    InteractiveFlowOutbound,
    marketing_opt_out_info,
)
from src.sdk.runtime import mba_controls_thread

_DAY_MS = 86_400_000
#: Con Medusa sin responder, el flujo vuelve a mirar en este tiempo.
_ORDER_UNKNOWN_RETRY_MS = 30 * 60_000
#: La respuesta de Meta cuando la ventana de 24 h ya cerró.
_WINDOW_CLOSED_CODE = "131047"
#: Plantilla inexistente en el idioma (aún sin aprobar), pausada o
#: deshabilitada: no salió nada, la invitación se reintenta (premortem I3).
_TEMPLATE_NOT_READY_CODES = ("132001", "132015", "132016")
#: El cliente escribió hace menos de esto: está conversando, no se interrumpe.
_ACTIVE_CONVERSATION_MS = 30 * 60_000
#: Otra invitación sin contestar más reciente que esto hace esperar a esta.
_SIBLING_INVITATION_MS = _DAY_MS
#: El formulario acepta fotos de hasta 10 MB (`max-file-size-kb: 10240`).
MAX_PHOTO_BYTES = 10 * 1024 * 1024 + 512 * 1024
#: Intentos de la actividad antes de guardar la reseña sin la foto que no bajó.
_PHOTO_ATTEMPTS = 4
_FOOTER = "Hubara · Velas artesanales de Bogotá"


@dataclass(frozen=True)
class ReviewPlanInput:
    session_id: str = ""
    order_id: str = ""
    delivered_at_ms: int = 0
    now_ms: int = 0


@dataclass(frozen=True)
class ReviewStepOut:
    action: str = "skip"
    wait_ms: int = 0
    reason: str = ""


@dataclass(frozen=True)
class ReviewSendInput:
    session_id: str = ""
    order_id: str = ""
    #: send_flow (ventana abierta) | send_template (cerrada).
    action: str = ""
    now_ms: int = 0


@dataclass(frozen=True)
class ReviewSendOut:
    #: sent | already_requested | skipped | failed | retry
    status: str = ""
    channel: str | None = None
    error: str | None = None
    #: El id de Meta del mensaje (para seguir su entrega por el webhook).
    wa_message_id: str | None = None


@dataclass(frozen=True)
class ReviewDeliveryInput:
    session_id: str = ""
    order_id: str = ""


@dataclass(frozen=True)
class ReviewSubmissionInput:
    session_id: str = ""
    wa_message_id: str = ""
    response_json: str = "{}"
    received_at_ms: int = 0


# ── decidir ─────────────────────────────────────────────────────────────────


#: Motivos que el operador tiene que ver en los logs (algo falta configurar).
_CONFIG_REASONS = ("flow_not_configured", "coupon_not_configured")


async def plan_step(deps: Any, inp: ReviewPlanInput) -> ReviewStepOut:
    """Lee los hechos, pregunta al dominio y deja la decisión registrada (el
    panel muestra por qué no salió una invitación — premortem P4)."""
    step = await _plan(deps, inp)
    deps.store.record_plan(
        inp.order_id, session_id=inp.session_id, action=step.action, reason=step.reason, at_ms=inp.now_ms
    )
    log = activity_logger()
    if step.reason in _CONFIG_REASONS:
        log.warning("review request %s waits: %s", inp.order_id, step.reason)
    else:
        log.info("review request %s: %s %s", inp.order_id, step.action, step.reason)
    return step


async def _plan(deps: Any, inp: ReviewPlanInput) -> ReviewStepOut:
    settings = deps.settings
    if not settings.allows(inp.session_id):
        reason = "disabled" if settings.mode == "off" else "not_in_test_list"
        return ReviewStepOut(action="skip", reason=reason)
    if not settings.flow_id:
        return ReviewStepOut(action="skip", reason="flow_not_configured")

    if deps.coupons_ready is not None and not await deps.coupons_ready():
        # Sin los cupones personales listos no se promete nada; se reintenta
        # (el tope de 48 h sigue mandando).
        return ReviewStepOut(action="wait", wait_ms=settings.timing.poll_ms, reason="coupon_not_configured")
    # El caché de pedidos del worker no recibe las invalidaciones del bus del
    # dashboard: la etapa se lee fresca (premortem P1).
    deps.order_facts.invalidate(inp.order_id)
    snapshot = await deps.order_facts.get_facts([inp.order_id])
    fact = snapshot.facts.get(inp.order_id)
    if fact is None and inp.order_id in snapshot.unresolved:
        return ReviewStepOut(action="wait", wait_ms=_ORDER_UNKNOWN_RETRY_MS, reason="order_unknown")
    # En modo prueba el operador prueba con pedidos «de prueba» (P8).
    order_ok = (
        fact is not None
        and fact.stage == "delivered"
        and (not fact.is_test or settings.mode == "test")
    )

    metadata = deps.metadata.read(inp.session_id) or {}
    window = metadata.get("service_window_expires_at_ms")
    step = plan_review_request(
        inp.now_ms,
        ReviewRequestFacts(
            delivered_at_ms=inp.delivered_at_ms,
            window_expires_at_ms=window if isinstance(window, int) else None,
            quiet_hours_end_ms=deps.quiet_hours_end(inp.session_id, inp.now_ms),
            opted_out=marketing_opt_out_info(metadata) is not None,
            order_ok=order_ok,
            already_requested=deps.store.get_request(inp.order_id) is not None,
            already_reviewed=deps.store.get_review(review_id_for(inp.order_id)) is not None,
            conversation_busy=_conversation_busy(metadata, inp.session_id, inp.now_ms),
            another_invitation_pending=_another_invitation_pending(deps, metadata, inp.order_id, inp.now_ms),
        ),
        settings.timing,
    )
    return ReviewStepOut(action=step.action, wait_ms=step.wait_ms, reason=step.reason)


def _conversation_busy(metadata: dict[str, Any], session_id: str, now_ms: int) -> bool:
    """Escalación abierta al equipo (tag HUMANO: p. ej. llegó roto), el cliente
    escribiendo en los últimos 30 min, o Meta Business Agent al frente del
    hilo. La post-venta normal (ruta humano con tag COMPRA_EXITOSA) NO cuenta."""
    if metadata.get("tag") == "HUMANO":
        return True
    last = metadata.get("last_inbound_at_ms")
    if isinstance(last, int) and 0 <= now_ms - last < _ACTIVE_CONVERSATION_MS:
        return True
    return mba_controls_thread(metadata, session_id)


def _another_invitation_pending(deps: Any, metadata: dict[str, Any], order_id: str, now_ms: int) -> bool:
    """¿Salió hace menos de un día la invitación de OTRO pedido de este cliente
    y no la ha contestado? (dos pedidos entregados juntos)."""
    for touch in metadata.get("review_request_touches") or []:
        if not isinstance(touch, dict) or touch.get("order_id") in (None, order_id):
            continue
        if touch.get("status") == "failed":
            continue
        at_ms = touch.get("at_ms")
        if not isinstance(at_ms, int) or not 0 <= now_ms - at_ms < _SIBLING_INVITATION_MS:
            continue
        try:
            answered = deps.store.get_review(review_id_for(str(touch["order_id"]))) is not None
        except ValueError:
            continue
        if not answered:
            return True
    return False


# ── invitar ─────────────────────────────────────────────────────────────────


def _phone_number_id(metadata: dict[str, Any]) -> str:
    return str(metadata.get("phone_number_id") or os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "")


async def _order_photo(deps: Any, session_id: str, order_id: str, metadata: dict[str, Any], detail: Any) -> bytes | None:
    """La foto de «pedido listo» que subió el equipo; si no, la del primer
    producto con foto; si nada, None (la tarjeta dibuja una vela)."""
    entry = (metadata.get("order_photos") or {}).get(order_id)
    if isinstance(entry, dict) and entry.get("filename"):
        path = deps.vault_dir / session_id / "media" / str(entry["filename"])
        try:
            return path.read_bytes()
        except OSError:
            pass
    for item in getattr(detail, "items_detail", None) or []:
        url = getattr(item, "thumbnail", None)
        if url:
            data = await deps.fetch_url(url)
            if data:
                return data
    return None


def _record_touch(
    deps: Any, session_id: str, order_id: str, kind: str, now_ms: int, wa_message_id: str | None
) -> None:
    """`review_request_touches`: la plantilla da contexto a «NO MÁS» (baja) y
    el webhook de estados anota en el toque si llegó (`status`, `error_code`)."""

    def _append(fresh: dict[str, Any]) -> dict[str, Any] | None:
        if not fresh:
            return None
        touches = list(fresh.get("review_request_touches") or [])
        touch: dict[str, Any] = {"order_id": order_id, "kind": kind, "at_ms": now_ms}
        if wa_message_id:
            touch["wa_message_id"] = wa_message_id
        touches.append(touch)
        fresh["review_request_touches"] = touches[-20:]
        return fresh

    deps.metadata.update(session_id, _append)


class _NotSent(Exception):
    """Falló algo ANTES de hablar con Meta: no salió nada al cliente."""


async def send_request(deps: Any, inp: ReviewSendInput) -> ReviewSendOut:
    """Envía la invitación del pedido (una sola vez).

    Si falla antes de llamar a Meta (Medusa, la foto, subirla), la invitación
    se LIBERA y vuelve `retry`: el flujo la reintenta (revisión L2). Si falla
    al enviar, queda `failed` y no se reintenta (dos invitaciones son peores
    que ninguna)."""
    if not deps.store.claim_request(inp.order_id, session_id=inp.session_id, now_ms=inp.now_ms):
        return ReviewSendOut(status="already_requested")
    try:
        out = await _send(deps, inp)
    except _NotSent as exc:
        deps.store.release_request(inp.order_id)
        return ReviewSendOut(status="retry", error=str(exc)[:300])
    except Exception as exc:  # noqa: BLE001 — se registra; no se reintenta
        out = ReviewSendOut(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
    deps.store.update_request(
        inp.order_id,
        status=out.status,
        channel=out.channel,
        error=out.error,
        sent_at_ms=inp.now_ms if out.status == "sent" else None,
        wa_message_id=out.wa_message_id,
    )
    if out.status == "sent" and out.channel:
        _record_touch(deps, inp.session_id, inp.order_id, out.channel, inp.now_ms, out.wa_message_id)
    return out


async def _send(deps: Any, inp: ReviewSendInput) -> ReviewSendOut:
    metadata = deps.metadata.read(inp.session_id) or {}
    try:
        detail = await deps.order_query.get(inp.order_id)
        if detail is None:
            return ReviewSendOut(status="skipped", error="order_not_found")
        reference = str(detail.summary.display_id or inp.order_id)[:20]
        titles = [str(i.title) for i in detail.items_detail or [] if getattr(i, "title", None)]
        photo = await _order_photo(deps, inp.session_id, inp.order_id, metadata, detail)
        # Pillow fuera del event loop del worker.
        card = await asyncio.to_thread(render_review_card, reference, photo)
        media_id = await deps.upload_media(_phone_number_id(metadata), card, "image/jpeg")
    except Exception as exc:  # noqa: BLE001 — nada salió: se reintenta
        raise _NotSent(f"{type(exc).__name__}: {exc}") from exc
    token = review_flow_token(inp.order_id, str(inp.now_ms))
    action_data = flow_action_data(reference, titles)

    if inp.action == "send_flow":
        flow = InteractiveFlowOutbound(
            flow_id=deps.settings.flow_id,
            flow_token=token,
            flow_cta=FLOW_CTA,
            flow_action="navigate",
            flow_action_screen=FLOW_SCREEN,
            flow_action_data=action_data,
            body=invitation_body(reference),
            footer=_FOOTER,
            header_media_id=media_id,
        )
        history_text = f"{invitation_body(reference)}\n[{FLOW_CTA}]"
        result = await deps.send_flow(inp.session_id, flow, history_text=history_text)
        if _rejected_for_sure(result) and _WINDOW_CLOSED_CODE not in str(result.error or ""):
            # Revisión L4: si Meta no acepta la foto arriba del formulario, va
            # con un título de texto (la invitación no se pierde por la foto).
            # Solo ante un rechazo seguro (4xx): un timeout o un 5xx pudo haber
            # llegado y se mandaría dos veces (premortem P3).
            flow = replace(flow, header_media_id=None, header_text="Califica tu pedido")
            result = await deps.send_flow(inp.session_id, flow, history_text=history_text)
        if result.ok:
            return ReviewSendOut(status="sent", channel="flow", wa_message_id=result.wa_message_id)
        if _WINDOW_CLOSED_CODE not in str(result.error or ""):
            return ReviewSendOut(status="failed", channel="flow", error=str(result.error)[:300])
        # La ventana cerró entre la decisión y el envío: va la plantilla.
        if marketing_opt_out_info(metadata) is not None:
            return ReviewSendOut(status="skipped", error="opted_out")

    result = await deps.send_template(
        inp.session_id,
        TEMPLATE_NAME,
        template_variables(reference),
        header_media_id=media_id,
        flow_token=token,
        flow_action_data=action_data,
    )
    if not result.ok:
        if any(code in str(result.error or "") for code in _TEMPLATE_NOT_READY_CODES):
            raise _NotSent(f"plantilla no disponible en Meta: {str(result.error)[:200]}")
        return ReviewSendOut(status="failed", channel="template", error=str(result.error)[:300])
    return ReviewSendOut(status="sent", channel="template", wa_message_id=result.wa_message_id)


def _rejected_for_sure(result: Any) -> bool:
    """Meta respondió un 4xx: el mensaje NO salió (se puede mandar otro)."""
    return not result.ok and str(result.error or "").startswith("http_4")


async def check_delivery(deps: Any, inp: ReviewDeliveryInput) -> str:
    """¿Llegó la invitación? Meta suele aceptar el envío y avisar después por
    el webhook de estados (chats lo anota en el toque). Un formulario que no
    llegó porque la ventana ya había cerrado (131047) reabre la invitación:
    el flujo vuelve a planear y sale la plantilla. Otro fallo queda `failed`.
    Devuelve `sent` | `failed` | `retry`."""
    request = deps.store.get_request(inp.order_id)
    if request is None or request.get("status") != "sent":
        return str((request or {}).get("status") or "missing")
    touch = _touch_of(deps.metadata.read(inp.session_id) or {}, inp.order_id, request.get("wa_message_id"))
    if touch is None or touch.get("status") != "failed":
        return "sent"
    code = str(touch.get("error_code") or "")
    if code == _WINDOW_CLOSED_CODE and request.get("channel") == "flow":
        deps.store.release_request(inp.order_id)
        activity_logger().info("review flow %s not delivered (131047): back to planning", inp.order_id)
        return "retry"
    deps.store.update_request(inp.order_id, status="failed", error=f"meta no lo entregó: {code or 'sin código'}")
    activity_logger().warning("review request %s not delivered by Meta: %s", inp.order_id, code)
    return "failed"


def _touch_of(metadata: dict[str, Any], order_id: str, wa_message_id: Any) -> dict[str, Any] | None:
    for touch in reversed(metadata.get("review_request_touches") or []):
        if not isinstance(touch, dict) or touch.get("order_id") != order_id:
            continue
        if wa_message_id and touch.get("wa_message_id") not in (None, wa_message_id):
            continue
        return touch
    return None


# ── recibir la calificación ─────────────────────────────────────────────────


def _photo_name(order_id: str, index: int) -> str:
    """El nombre termina en una URL pública: nunca el id del pedido de Medusa
    (con él se leen los datos del pedido — premortem M1). Determinista, para
    que un reintento escriba encima."""
    return hashlib.sha256(f"review-photo:{order_id}:{index}".encode()).hexdigest()[:24]


async def _save_photos(deps: Any, order_id: str, media_ids: tuple[str, ...]) -> tuple[str, ...]:
    saved = []
    folder = deps.store.root / "photos"
    for index, media_id in enumerate(media_ids[:3]):
        fetched = await deps.fetch_media(media_id)
        if not fetched:
            continue
        content, mime = fetched
        if len(content) > MAX_PHOTO_BYTES:
            continue
        ext = ".png" if "png" in str(mime) else ".jpg"
        name = f"{_photo_name(order_id, index)}{ext}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(content)
        saved.append(f"photos/{name}")
    return tuple(saved)


def _first_word(name: Any) -> str:
    words = str(name or "").split()
    if not words:
        return ""
    plain = unicodedata.normalize("NFKD", words[0].casefold())
    return "".join(c for c in plain if not unicodedata.combining(c))


def _author(detail: Any, metadata: dict[str, Any]) -> tuple[str, str | None]:
    """Quien calificó es quien escribe por WhatsApp: su nombre de perfil, no el
    de la dirección (en un regalo es quien lo recibió — premortem P6). La
    ciudad de envío solo si es la misma persona."""
    profile = metadata.get("profile") if isinstance(metadata.get("profile"), dict) else {}
    name = str(profile.get("name") or "")
    author = author_display(name)
    address = getattr(detail, "shipping_address", None)
    same_person = bool(_first_word(name)) and _first_word(name) == _first_word(getattr(address, "first_name", None))
    city = (getattr(address, "city", None) or None) if same_person else None
    return author, city


async def process_submission(deps: Any, inp: ReviewSubmissionInput, *, attempt: int = _PHOTO_ATTEMPTS) -> str:
    """Guarda la calificación, concede el cupón, agradece y (1–3★) avisa.

    `attempt` es el intento de la actividad: si la foto no bajó, los primeros
    intentos fallan para reintentar (premortem P7); el último guarda lo que hay."""
    try:
        response = json.loads(inp.response_json or "{}")
    except ValueError:
        return _unprocessed(inp, "ignored")
    submission = parse_review_response(response if isinstance(response, dict) else {})
    if submission is None:
        return _unprocessed(inp, "ignored")
    request = deps.store.get_request(submission.order_id)
    if request is not None and request.get("session_id") not in (None, inp.session_id):
        return _unprocessed(inp, "session_mismatch")

    review_id = review_id_for(submission.order_id)
    existing = deps.store.get_review(review_id)
    if existing is not None and existing.coupon and existing.team_alerted >= (existing.rating <= 3):
        return "duplicate"
    if existing is None:
        detail = await deps.order_query.get(submission.order_id)
        if detail is None:
            return _unprocessed(inp, "order_not_found")
        photo_files = await _save_photos(deps, submission.order_id, submission.photo_media_ids)
        wanted = min(len(submission.photo_media_ids), 3)
        if len(photo_files) < wanted and attempt < _PHOTO_ATTEMPTS:
            raise RuntimeError(f"la foto de la reseña de {submission.order_id} no bajó; se reintenta")
        author, city = _author(detail, deps.metadata.read(inp.session_id) or {})
        record, _ = deps.store.save_review(
            ReviewRecord(
                order_id=submission.order_id,
                display_id=str(detail.summary.display_id or ""),
                session_id=inp.session_id,
                rating=submission.rating,
                comment=submission.comment,
                publish_consent=submission.publish_consent,
                author_name=author,
                author_city=city,
                products=tuple(
                    {"product_id": i.product_id, "title": i.title, "handle": i.handle}
                    for i in detail.items_detail or []
                    if getattr(i, "product_id", None)
                ),
                photo_files=photo_files,
                submitted_at_ms=inp.received_at_ms,
                wa_message_id=inp.wa_message_id,
            )
        )
    else:
        record = existing

    if not record.coupon:
        percent = reward_percent(submission)
        expires_at_ms = inp.received_at_ms + deps.settings.coupon_days * _DAY_MS
        grant = deps.grants.grant(
            CouponGrant(
                code=deps.settings.coupon_code_for(percent),
                session_id=inp.session_id,
                percent=percent,
                granted_at_ms=inp.received_at_ms,
                expires_at_ms=expires_at_ms,
                source="review",
                reference=submission.order_id,
            )
        )
        coupon = {"code": grant.code, "percent": grant.percent, "expires_at_ms": grant.expires_at_ms}
        record = deps.store.update_review(review_id, coupon=coupon) or replace(record, coupon=coupon)
        cta = InteractiveCTAUrlOutbound(
            body=thanks_text(
                rating=record.rating,
                percent=grant.percent,
                expires_label=expires_label(grant.expires_at_ms),
            ),
            button_text="Opinar en Google",
            url=deps.settings.google_url,
        )
        try:
            result = await deps.send_cta(inp.session_id, cta)
            if not result.ok:
                activity_logger().warning("review thanks not delivered %s: %s", inp.session_id, result.error)
        except Exception as exc:  # noqa: BLE001 — el cupón ya quedó; el gracias es best-effort
            activity_logger().warning("review thanks failed %s: %s", inp.session_id, exc)

    if record.rating <= 3 and not record.team_alerted:
        comment = f": «{record.comment}»" if record.comment else ""
        summary = (
            f"Calificó {record.rating}★ el pedido {record.display_id}{comment}. "
            "Le prometimos que alguien del equipo le escribe hoy."
        )
        if not await deps.escalate(inp.session_id, summary):
            # Lo prometido («alguien del equipo te escribe hoy») solo vale si
            # chats confirmó el paso al equipo (premortem P2): se reintenta.
            raise RuntimeError(f"chats no confirmó el paso al equipo de {inp.session_id}")
        deps.store.update_review(review_id, team_alerted=True)
    return "stored" if existing is None else "completed"


def _unprocessed(inp: ReviewSubmissionInput, outcome: str) -> str:
    activity_logger().warning("review submission %s not processed: %s", inp.wa_message_id, outcome)
    return outcome


def activity_logger() -> Any:
    try:
        return activity.logger
    except Exception:  # noqa: BLE001 — fuera de una activity (tests)
        import logging

        return logging.getLogger(__name__)


# ── wrappers Temporal ───────────────────────────────────────────────────────


@activity.defn(name="reviews_plan_step")
async def plan_step_activity(inp: ReviewPlanInput) -> ReviewStepOut:
    from src.plugins.reviews.agent.composition import get_reviews_deps

    return await plan_step(get_reviews_deps(), inp)


@activity.defn(name="reviews_send_request")
async def send_request_activity(inp: ReviewSendInput) -> ReviewSendOut:
    from src.plugins.reviews.agent.composition import get_reviews_deps

    return await send_request(get_reviews_deps(), inp)


@activity.defn(name="reviews_check_delivery")
async def check_delivery_activity(inp: ReviewDeliveryInput) -> str:
    from src.plugins.reviews.agent.composition import get_reviews_deps

    return await check_delivery(get_reviews_deps(), inp)


@activity.defn(name="reviews_process_submission")
async def process_submission_activity(inp: ReviewSubmissionInput) -> str:
    from src.plugins.reviews.agent.composition import get_reviews_deps

    return await process_submission(get_reviews_deps(), inp, attempt=activity.info().attempt)
