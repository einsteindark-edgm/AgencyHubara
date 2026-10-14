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

import json
import os
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

_DAY_MS = 86_400_000
#: Con Medusa sin responder, el flujo vuelve a mirar en este tiempo.
_ORDER_UNKNOWN_RETRY_MS = 30 * 60_000
#: La respuesta de Meta cuando la ventana de 24 h ya cerró.
_WINDOW_CLOSED_CODE = "131047"
_MAX_PHOTO_BYTES = 8 * 1024 * 1024
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
    #: sent | already_requested | skipped | failed
    status: str = ""
    channel: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ReviewSubmissionInput:
    session_id: str = ""
    wa_message_id: str = ""
    response_json: str = "{}"
    received_at_ms: int = 0


# ── decidir ─────────────────────────────────────────────────────────────────


async def plan_step(deps: Any, inp: ReviewPlanInput) -> ReviewStepOut:
    """Lee los hechos del pedido y la conversación y pregunta al dominio."""
    settings = deps.settings
    if not settings.allows(inp.session_id):
        reason = "disabled" if settings.mode == "off" else "not_in_test_list"
        return ReviewStepOut(action="skip", reason=reason)
    if not settings.flow_id:
        return ReviewStepOut(action="skip", reason="flow_not_configured")

    snapshot = await deps.order_facts.get_facts([inp.order_id])
    fact = snapshot.facts.get(inp.order_id)
    if fact is None and inp.order_id in snapshot.unresolved:
        return ReviewStepOut(action="wait", wait_ms=_ORDER_UNKNOWN_RETRY_MS, reason="order_unknown")
    order_ok = fact is not None and fact.stage == "delivered" and not fact.is_test

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
        ),
        settings.timing,
    )
    return ReviewStepOut(action=step.action, wait_ms=step.wait_ms, reason=step.reason)


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


def _record_touch(deps: Any, session_id: str, order_id: str, kind: str, now_ms: int) -> None:
    """`review_request_touches`: la plantilla da contexto a «NO MÁS» (baja)."""

    def _append(fresh: dict[str, Any]) -> dict[str, Any] | None:
        if not fresh:
            return None
        touches = list(fresh.get("review_request_touches") or [])
        touches.append({"order_id": order_id, "kind": kind, "at_ms": now_ms})
        fresh["review_request_touches"] = touches[-20:]
        return fresh

    deps.metadata.update(session_id, _append)


async def send_request(deps: Any, inp: ReviewSendInput) -> ReviewSendOut:
    """Envía la invitación del pedido (una sola vez)."""
    if not deps.store.claim_request(inp.order_id, session_id=inp.session_id, now_ms=inp.now_ms):
        return ReviewSendOut(status="already_requested")
    try:
        out = await _send(deps, inp)
    except Exception as exc:  # noqa: BLE001 — se registra; no se reintenta
        out = ReviewSendOut(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
    deps.store.update_request(
        inp.order_id,
        status=out.status,
        channel=out.channel,
        error=out.error,
        sent_at_ms=inp.now_ms if out.status == "sent" else None,
    )
    if out.status == "sent" and out.channel:
        _record_touch(deps, inp.session_id, inp.order_id, out.channel, inp.now_ms)
    return out


async def _send(deps: Any, inp: ReviewSendInput) -> ReviewSendOut:
    metadata = deps.metadata.read(inp.session_id) or {}
    detail = await deps.order_query.get(inp.order_id)
    if detail is None:
        return ReviewSendOut(status="skipped", error="order_not_found")
    reference = str(detail.summary.display_id or inp.order_id)[:20]
    titles = [str(i.title) for i in detail.items_detail or [] if getattr(i, "title", None)]
    card = render_review_card(reference, await _order_photo(deps, inp.session_id, inp.order_id, metadata, detail))
    media_id = await deps.upload_media(_phone_number_id(metadata), card, "image/jpeg")
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
        result = await deps.send_flow(
            inp.session_id, flow, history_text=f"{invitation_body(reference)}\n[{FLOW_CTA}]"
        )
        if result.ok:
            return ReviewSendOut(status="sent", channel="flow")
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
        return ReviewSendOut(status="failed", channel="template", error=str(result.error)[:300])
    return ReviewSendOut(status="sent", channel="template")


# ── recibir la calificación ─────────────────────────────────────────────────


async def _save_photos(deps: Any, order_id: str, media_ids: tuple[str, ...]) -> tuple[str, ...]:
    saved = []
    folder = deps.store.root / "photos"
    for index, media_id in enumerate(media_ids[:3]):
        fetched = await deps.fetch_media(media_id)
        if not fetched:
            continue
        content, mime = fetched
        if len(content) > _MAX_PHOTO_BYTES:
            continue
        ext = ".png" if "png" in str(mime) else ".jpg"
        name = f"{order_id}_{index}{ext}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(content)
        saved.append(f"photos/{name}")
    return tuple(saved)


def _author(detail: Any) -> tuple[str, str | None]:
    address = getattr(detail, "shipping_address", None)
    full = " ".join(
        p for p in (getattr(address, "first_name", None), getattr(address, "last_name", None)) if p
    ) or getattr(detail.summary, "customer", "")
    return author_display(full), getattr(address, "city", None) or None


async def process_submission(deps: Any, inp: ReviewSubmissionInput) -> str:
    """Guarda la calificación, concede el cupón, agradece y (1–3★) avisa."""
    try:
        response = json.loads(inp.response_json or "{}")
    except ValueError:
        return "ignored"
    submission = parse_review_response(response if isinstance(response, dict) else {})
    if submission is None:
        return "ignored"
    request = deps.store.get_request(submission.order_id)
    if request is not None and request.get("session_id") not in (None, inp.session_id):
        return "session_mismatch"

    review_id = review_id_for(submission.order_id)
    existing = deps.store.get_review(review_id)
    if existing is not None and existing.coupon and existing.team_alerted >= (existing.rating <= 3):
        return "duplicate"
    if existing is None:
        detail = await deps.order_query.get(submission.order_id)
        if detail is None:
            return "order_not_found"
        author, city = _author(detail)
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
                photo_files=await _save_photos(deps, submission.order_id, submission.photo_media_ids),
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
        await deps.escalate(inp.session_id, summary)
        deps.store.update_review(review_id, team_alerted=True)
    return "stored" if existing is None else "completed"


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


@activity.defn(name="reviews_process_submission")
async def process_submission_activity(inp: ReviewSubmissionInput) -> str:
    from src.plugins.reviews.agent.composition import get_reviews_deps

    return await process_submission(get_reviews_deps(), inp)
