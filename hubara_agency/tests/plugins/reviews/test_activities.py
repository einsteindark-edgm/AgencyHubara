"""Las actividades de reseñas: decidir, invitar y recibir la calificación.

Con dependencias falsas (Meta, Medusa, vault, cupones): el comportamiento
observable es qué se le manda al cliente, qué queda guardado y qué cupón
recibe — nunca dos invitaciones, nunca dos cupones por la misma calificación.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.platform.promotions.grants import FakeCouponGrantsStore
from src.plugins.reviews.agent.activities import (
    ReviewPlanInput,
    ReviewSendInput,
    ReviewSubmissionInput,
    plan_step,
    process_submission,
    send_request,
)
from src.plugins.reviews.agent.composition import ReviewsDeps
from src.plugins.reviews.agent.settings import ReviewSettings
from src.plugins.reviews.agent.store import ReviewsStore
from src.sdk.messagingkit import OutboundResult

S = "wa_573001234567"
ORDER = "order_01JABC"
HOUR = 3_600_000
T0 = 1_791_000_000_000  # entrega


class _Metadata:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = {S: data}

    def read(self, session_id: str) -> dict[str, Any]:
        return json.loads(json.dumps(self.data.get(session_id, {})))

    def update(self, session_id: str, fn) -> dict[str, Any] | None:
        fresh = self.read(session_id)
        result = fn(fresh)
        if result is not None:
            self.data[session_id] = result
        return result


class _Facts:
    def __init__(self, *, stage="delivered", is_test=False, unresolved=False) -> None:
        self.stage, self.is_test, self.unresolved = stage, is_test, unresolved

    def invalidate(self, order_id=None) -> None:
        pass

    async def get_facts(self, ids):
        if self.unresolved:
            return SimpleNamespace(facts={}, unresolved=frozenset(ids))
        fact = SimpleNamespace(order_id=ORDER, display_id="#31", stage=self.stage, is_test=self.is_test)
        return SimpleNamespace(facts={ORDER: fact}, unresolved=frozenset())


class _Orders:
    async def get(self, order_id):
        return SimpleNamespace(
            summary=SimpleNamespace(display_id="#31", customer="ana.gomez@example.com"),
            shipping_address=SimpleNamespace(first_name="Ana María", last_name="Gómez", city="Bogotá"),
            items_detail=[
                SimpleNamespace(title="Velón Encanto Silvestre", handle="encanto", product_id="prod_1",
                                thumbnail="https://cdn.example/encanto.jpg"),
                SimpleNamespace(title="Vela Buda", handle="buda", product_id="prod_2", thumbnail=None),
            ],
        )


@dataclass
class _Calls:
    flows: list = field(default_factory=list)
    templates: list = field(default_factory=list)
    ctas: list = field(default_factory=list)
    uploads: list = field(default_factory=list)
    escalations: list = field(default_factory=list)
    senders: list = field(default_factory=list)


def _deps(tmp_path: Path, *, metadata=None, facts=None, mode="on", flow_ok=True,
          template_error: Exception | None = None, quiet_end=None, calls: _Calls | None = None,
          coupons_ready: bool = True) -> tuple[ReviewsDeps, _Calls]:
    calls = calls or _Calls()

    async def upload_media(phone_id, content, mime):
        calls.uploads.append((phone_id, len(content), mime))
        return f"MEDIA_{len(calls.uploads)}"

    async def fetch_media(media_id):
        return (b"\xff\xd8photo-bytes", "image/jpeg")

    async def fetch_url(url):
        return None

    async def send_flow(session_id, flow, *, history_text, image_url=None, sender="aviso"):
        calls.flows.append((session_id, flow, history_text))
        calls.senders.append(sender)
        return OutboundResult(wa_message_id="wamid.F" if flow_ok else None, ok=flow_ok,
                              error=None if flow_ok else "(#131047) Re-engagement message")

    async def send_template(session_id, name, variables, **kwargs):
        if template_error is not None:
            raise template_error
        calls.templates.append((session_id, name, variables, kwargs))
        return OutboundResult(wa_message_id="wamid.T", ok=True)

    async def send_cta(session_id, cta, *, sender="aviso"):
        calls.ctas.append((session_id, cta))
        calls.senders.append(sender)
        return OutboundResult(wa_message_id="wamid.C", ok=True)

    async def escalate(session_id, summary):
        calls.escalations.append((session_id, summary))
        return True

    async def ready() -> bool:
        return coupons_ready

    env = {"REVIEW_REQUESTS_MODE": mode, "META_FLOW_ID_REVIEW": "FLOW1",
           "GOOGLE_REVIEW_URL": "https://hubara.com.co/opinar/"}
    deps = ReviewsDeps(
        settings=ReviewSettings.from_env(env),
        store=ReviewsStore(tmp_path),
        metadata=_Metadata(metadata if metadata is not None else {"phone_number_id": "PID", "profile": {"name": "Ana María Gómez"}}),
        order_facts=facts or _Facts(),
        order_query=_Orders(),
        grants=FakeCouponGrantsStore(),
        upload_media=upload_media,
        fetch_media=fetch_media,
        fetch_url=fetch_url,
        send_flow=send_flow,
        send_template=send_template,
        send_cta=send_cta,
        escalate=escalate,
        quiet_hours_end=lambda session_id, now_ms: quiet_end,
        vault_dir=tmp_path,
        coupons_ready=ready,
    )
    return deps, calls


# ---------- decidir ----------


@pytest.mark.asyncio
async def test_plan_is_skipped_while_the_feature_is_off(tmp_path):
    deps, _ = _deps(tmp_path, mode="off")
    step = await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 3 * HOUR))
    assert (step.action, step.reason) == ("skip", "disabled")


@pytest.mark.asyncio
async def test_plan_sends_the_flow_while_the_window_is_open(tmp_path):
    deps, _ = _deps(tmp_path, metadata={"service_window_expires_at_ms": T0 + 10 * HOUR})
    step = await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 3 * HOUR))
    assert step.action == "send_flow"


@pytest.mark.asyncio
async def test_plan_skips_test_orders(tmp_path):
    deps, _ = _deps(tmp_path, facts=_Facts(is_test=True))
    step = await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 21 * HOUR))
    assert (step.action, step.reason) == ("skip", "order_not_eligible")


@pytest.mark.asyncio
async def test_plan_waits_when_medusa_does_not_answer(tmp_path):
    deps, _ = _deps(tmp_path, facts=_Facts(unresolved=True))
    step = await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 21 * HOUR))
    assert (step.action, step.reason) == ("wait", "order_unknown")
    assert step.wait_ms > 0


@pytest.mark.asyncio
async def test_plan_respects_quiet_hours(tmp_path):
    deps, _ = _deps(tmp_path, quiet_end=T0 + 30 * HOUR)
    step = await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 21 * HOUR))
    assert (step.action, step.wait_ms) == ("wait", 9 * HOUR)


# ---------- invitar ----------


@pytest.mark.asyncio
async def test_send_flow_in_window_with_photo_card_and_order_token(tmp_path):
    deps, calls = _deps(tmp_path)
    out = await send_request(deps, ReviewSendInput(session_id=S, order_id=ORDER, action="send_flow", now_ms=T0 + 3 * HOUR))

    assert (out.status, out.channel) == ("sent", "flow")
    [(session, flow, history)] = calls.flows
    assert session == S
    assert flow.flow_id == "FLOW1"
    assert flow.flow_token.startswith(f"review:{ORDER}:")
    assert flow.header_media_id == "MEDIA_1"
    assert "#31" in flow.body and "5 %" in flow.body
    assert flow.flow_action_data["items_summary"] == "Velón Encanto Silvestre y 1 más"
    assert calls.uploads[0][0] == "PID" and calls.uploads[0][2] == "image/jpeg"
    request = deps.store.get_request(ORDER)
    assert (request["status"], request["channel"]) == ("sent", "flow")
    touches = deps.metadata.read(S)["review_request_touches"]
    assert touches[-1]["kind"] == "flow" and touches[-1]["order_id"] == ORDER


@pytest.mark.asyncio
async def test_an_order_is_invited_only_once(tmp_path):
    deps, calls = _deps(tmp_path)
    inp = ReviewSendInput(session_id=S, order_id=ORDER, action="send_flow", now_ms=T0 + 3 * HOUR)
    await send_request(deps, inp)
    again = await send_request(deps, inp)
    assert again.status == "already_requested"
    assert len(calls.flows) == 1


@pytest.mark.asyncio
async def test_send_template_out_of_window_says_the_discount(tmp_path):
    deps, calls = _deps(tmp_path)
    out = await send_request(deps, ReviewSendInput(session_id=S, order_id=ORDER, action="send_template", now_ms=T0 + 20 * HOUR))

    assert (out.status, out.channel) == ("sent", "template")
    [(session, name, variables, kwargs)] = calls.templates
    assert name == "order_review_marketing_v1"
    assert variables == {"order_reference": "#31"}
    assert kwargs["header_media_id"] == "MEDIA_1"
    assert kwargs["flow_token"].startswith(f"review:{ORDER}:")
    assert deps.metadata.read(S)["review_request_touches"][-1]["kind"] == "template"


@pytest.mark.asyncio
async def test_closed_window_race_falls_back_to_the_template(tmp_path):
    deps, calls = _deps(tmp_path, flow_ok=False)
    out = await send_request(deps, ReviewSendInput(session_id=S, order_id=ORDER, action="send_flow", now_ms=T0 + 3 * HOUR))
    assert (out.status, out.channel) == ("sent", "template")
    assert len(calls.templates) == 1


@pytest.mark.asyncio
async def test_a_failed_template_is_recorded_not_retried(tmp_path):
    deps, _ = _deps(tmp_path, template_error=RuntimeError("TemplateMetaError131049"))
    out = await send_request(deps, ReviewSendInput(session_id=S, order_id=ORDER, action="send_template", now_ms=T0 + 20 * HOUR))
    assert out.status == "failed"
    assert "131049" in (out.error or "")
    assert deps.store.get_request(ORDER)["status"] == "failed"


# ---------- recibir la calificación ----------


def _submission(**response) -> ReviewSubmissionInput:
    body = {"flow_token": f"review:{ORDER}:n1", "rating": "5", "comment": "Hermosa",
            "photo": [{"id": "MEDIA_P"}], "publish_consent": True, **response}
    return ReviewSubmissionInput(session_id=S, wa_message_id="wamid.R1",
                                 response_json=json.dumps(body), received_at_ms=T0 + 4 * HOUR)


@pytest.mark.asyncio
async def test_a_rating_is_stored_rewarded_and_thanked(tmp_path):
    deps, calls = _deps(tmp_path)
    outcome = await process_submission(deps, _submission())

    assert outcome == "stored"
    review = deps.store.get_review(f"rev_{ORDER}")
    assert (review.rating, review.comment, review.author_name, review.author_city) == (5, "Hermosa", "Ana G.", "Bogotá")
    assert [p["product_id"] for p in review.products] == ["prod_1", "prod_2"]
    assert len(review.photo_files) == 1
    assert (tmp_path / "_reviews" / review.photo_files[0]).read_bytes().startswith(b"\xff\xd8")
    [grant] = deps.grants.list_for(S)
    assert (grant.code, grant.percent, grant.reference) == ("GRACIAS10", 10, ORDER)
    assert review.coupon["code"] == "GRACIAS10"
    [(session, cta)] = calls.ctas
    assert cta.url == "https://hubara.com.co/opinar/"
    assert cta.button_text == "Opinar en Google"
    assert "10 %" in cta.body
    assert calls.escalations == []


@pytest.mark.asyncio
async def test_a_low_rating_also_alerts_the_team(tmp_path):
    deps, calls = _deps(tmp_path)
    await process_submission(deps, _submission(rating="2", comment="Llegó rota", photo=[]))

    [(session, summary)] = calls.escalations
    assert session == S
    assert "2★" in summary and "Llegó rota" in summary and "#31" in summary
    assert deps.grants.list_for(S)[0].code == "GRACIAS5"
    assert deps.store.get_review(f"rev_{ORDER}").team_alerted is True
    assert len(calls.ctas) == 1  # Google se ofrece igual (sin filtrar por estrellas)


@pytest.mark.asyncio
async def test_a_repeated_answer_does_not_give_a_second_coupon_or_thanks(tmp_path):
    deps, calls = _deps(tmp_path)
    await process_submission(deps, _submission())
    again = await process_submission(deps, _submission(rating="1"))

    assert again == "duplicate"
    assert len(deps.grants.list_for(S)) == 1
    assert len(calls.ctas) == 1


@pytest.mark.asyncio
async def test_a_non_review_answer_is_ignored(tmp_path):
    deps, calls = _deps(tmp_path)
    assert await process_submission(deps, _submission(rating="9")) == "ignored"
    assert calls.ctas == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {"tag": "HUMANO", "service_window_expires_at_ms": T0 + 10 * HOUR},
        {"last_inbound_at_ms": T0 + 3 * HOUR - 5 * 60_000, "service_window_expires_at_ms": T0 + 27 * HOUR},
    ],
)
async def test_plan_waits_while_the_conversation_is_busy(tmp_path, metadata):
    deps, _ = _deps(tmp_path, metadata=metadata)
    step = await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 3 * HOUR))
    assert (step.action, step.reason) == ("wait", "conversation_busy")


@pytest.mark.asyncio
async def test_plan_waits_until_the_thanks_coupons_exist_and_are_personal(tmp_path):
    """Revisión M5: sin GRACIAS5/GRACIAS10 vigentes y «solo quien lo recibió»
    no se invita (se prometería un cupón que no se aplica, o uno público)."""
    deps, _ = _deps(tmp_path, coupons_ready=False, metadata={"service_window_expires_at_ms": T0 + 10 * HOUR})
    step = await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 3 * HOUR))
    assert (step.action, step.reason) == ("wait", "coupon_not_configured")


@pytest.mark.asyncio
async def test_a_failure_before_meta_releases_the_invitation_to_retry(tmp_path):
    """Revisión L2: si falla algo ANTES de hablar con Meta (Medusa, la foto),
    no salió nada: la invitación se libera y el flujo la reintenta."""
    deps, calls = _deps(tmp_path)

    async def broken_upload(phone_id, content, mime):
        raise RuntimeError("meta media down")

    deps.upload_media = broken_upload
    out = await send_request(deps, ReviewSendInput(session_id=S, order_id=ORDER, action="send_flow", now_ms=T0 + 3 * HOUR))

    assert out.status == "retry"
    assert deps.store.get_request(ORDER) is None
    assert calls.flows == [] and calls.templates == []


@pytest.mark.asyncio
async def test_a_rejected_photo_header_resends_the_flow_without_it(tmp_path):
    """Revisión L4: si Meta no acepta la foto arriba del formulario, va sin foto."""
    deps, calls = _deps(tmp_path)
    results = [
        OutboundResult(wa_message_id=None, ok=False,
                       error='http_400: {"error":{"message":"(#131009) Parameter value is not valid","code":131009}}'),
        OutboundResult(wa_message_id="wamid.F2", ok=True),
    ]

    async def send_flow(session_id, flow, *, history_text, image_url=None, sender="aviso"):
        calls.flows.append((session_id, flow, history_text))
        return results.pop(0)

    deps.send_flow = send_flow
    out = await send_request(deps, ReviewSendInput(session_id=S, order_id=ORDER, action="send_flow", now_ms=T0 + 3 * HOUR))

    assert (out.status, out.channel) == ("sent", "flow")
    assert [f.header_media_id for _, f, _ in calls.flows] == ["MEDIA_1", None]
    assert calls.flows[1][1].header_text == "Califica tu pedido"


# ---------- premortem 2026-10-14 ----------


class _CachedFacts(_Facts):
    """Como el `OrderFactsStore` real del worker: sirve lo que tenía en caché
    (el pedido visto en «shipping» por otro despertar) hasta que se invalida."""

    def __init__(self) -> None:
        super().__init__(stage="shipping")
        self.invalidated: list = []

    def invalidate(self, order_id=None) -> None:
        self.invalidated.append(order_id)
        self.stage = "delivered"


@pytest.mark.asyncio
async def test_plan_reads_the_order_stage_fresh_not_from_a_stale_cache(tmp_path):
    """P1: el caché de pedidos del worker (TTL, sin invalidaciones del bus)
    tenía el pedido en «shipping»: el flujo se descartaba para siempre."""
    facts = _CachedFacts()
    deps, _ = _deps(tmp_path, facts=facts, metadata={"service_window_expires_at_ms": T0 + 10 * HOUR})
    step = await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 3 * HOUR))
    assert step.action == "send_flow"
    assert facts.invalidated == [ORDER]


@pytest.mark.asyncio
async def test_test_orders_are_invited_in_test_mode_for_the_test_numbers(tmp_path):
    """P8: en modo prueba el operador prueba con su número y un pedido «de
    prueba»; antes se descartaba sin decir por qué."""
    deps, _ = _deps(tmp_path, facts=_Facts(is_test=True), mode="test",
                    metadata={"service_window_expires_at_ms": T0 + 10 * HOUR})
    deps.settings = ReviewSettings.from_env({"REVIEW_REQUESTS_MODE": "test", "REVIEW_TEST_SESSIONS": S,
                                             "META_FLOW_ID_REVIEW": "FLOW1"})
    step = await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 3 * HOUR))
    assert step.action == "send_flow"


@pytest.mark.asyncio
async def test_a_second_order_waits_while_the_first_invitation_is_unanswered(tmp_path):
    """P10: dos pedidos entregados juntos no mandan dos invitaciones seguidas."""
    touches = [{"order_id": "order_OTHER", "kind": "flow", "at_ms": T0 + 2 * HOUR}]
    metadata = {"service_window_expires_at_ms": T0 + 10 * HOUR, "review_request_touches": touches}
    deps, _ = _deps(tmp_path, metadata=metadata)
    step = await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 3 * HOUR))
    assert (step.action, step.reason) == ("wait", "another_invitation_pending")


@pytest.mark.asyncio
async def test_a_second_order_is_invited_once_the_first_was_rated(tmp_path):
    from src.plugins.reviews.agent.store import ReviewRecord

    touches = [{"order_id": "order_OTHER", "kind": "flow", "at_ms": T0 + 2 * HOUR}]
    metadata = {"service_window_expires_at_ms": T0 + 10 * HOUR, "review_request_touches": touches}
    deps, _ = _deps(tmp_path, metadata=metadata)
    deps.store.save_review(ReviewRecord(
        order_id="order_OTHER", display_id="#30", session_id=S, rating=5, comment=None, publish_consent=False,
        author_name="Cliente de Hubara", author_city=None, products=(), photo_files=(), submitted_at_ms=T0,
        wa_message_id="wamid.X"))
    step = await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 3 * HOUR))
    assert step.action == "send_flow"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["timeout: ReadTimeout", "http_504: Gateway Timeout", "Server disconnected"])
async def test_an_ambiguous_flow_send_is_never_sent_twice(tmp_path, error):
    """P3: un timeout o un 5xx pudo haber llegado a Meta: no se reenvía."""
    deps, calls = _deps(tmp_path)

    async def send_flow(session_id, flow, *, history_text, image_url=None, sender="aviso"):
        calls.flows.append((session_id, flow, history_text))
        return OutboundResult(wa_message_id=None, ok=False, error=error)

    deps.send_flow = send_flow
    out = await send_request(deps, ReviewSendInput(session_id=S, order_id=ORDER, action="send_flow", now_ms=T0 + 3 * HOUR))
    assert out.status == "failed"
    assert len(calls.flows) == 1 and calls.templates == []


@pytest.mark.asyncio
async def test_a_template_meta_has_not_approved_yet_releases_the_invitation(tmp_path):
    """I3: encendido antes de que Meta apruebe la plantilla (o pausada): no
    se gasta la invitación del pedido, se reintenta después."""
    deps, calls = _deps(tmp_path)

    async def send_template(session_id, name, variables, **kwargs):
        calls.templates.append((session_id, name, variables, kwargs))
        return OutboundResult(wa_message_id=None, ok=False, error=(
            'http_404: {"error":{"message":"(#132001) Template name does not exist in the translation",'
            '"code":132001}}'))

    deps.send_template = send_template
    out = await send_request(deps, ReviewSendInput(session_id=S, order_id=ORDER, action="send_template", now_ms=T0 + 20 * HOUR))
    assert out.status == "retry"
    assert deps.store.get_request(ORDER) is None


@pytest.mark.asyncio
async def test_a_low_rating_the_team_did_not_receive_is_retried(tmp_path):
    """P2: «alguien del equipo te escribe hoy» solo vale si chats confirmó que
    la conversación quedó con el equipo."""
    deps, calls = _deps(tmp_path)

    async def not_escalated(session_id, summary):
        calls.escalations.append((session_id, summary))
        return False

    deps.escalate = not_escalated
    with pytest.raises(RuntimeError):
        await process_submission(deps, _submission(rating="2", photo=[]))
    assert deps.store.get_review(f"rev_{ORDER}").team_alerted is False

    async def escalated(session_id, summary):
        return True

    deps.escalate = escalated
    assert await process_submission(deps, _submission(rating="2", photo=[])) == "completed"
    assert deps.store.get_review(f"rev_{ORDER}").team_alerted is True
    assert len(deps.grants.list_for(S)) == 1 and len(calls.ctas) == 1


@pytest.mark.asyncio
async def test_a_photo_that_could_not_be_downloaded_is_retried_first(tmp_path):
    """P7: la foto del cliente no se pierde por un tropiezo de Meta: la
    actividad se reintenta antes de guardar la reseña."""
    deps, calls = _deps(tmp_path)

    async def no_media(media_id):
        return None

    deps.fetch_media = no_media
    with pytest.raises(RuntimeError):
        await process_submission(deps, _submission(), attempt=1)
    assert deps.store.get_review(f"rev_{ORDER}") is None and deps.grants.list_for(S) == []

    # El último intento guarda lo que hay (sin foto) y respeta lo prometido.
    assert await process_submission(deps, _submission(), attempt=4) == "stored"
    assert deps.store.get_review(f"rev_{ORDER}").photo_files == ()
    assert deps.grants.list_for(S)[0].percent == 10


@pytest.mark.asyncio
async def test_a_photo_within_the_flow_limit_is_kept(tmp_path):
    """P7/I6: el formulario acepta fotos de hasta 10 MB."""
    deps, _ = _deps(tmp_path)

    async def big(media_id):
        return (b"\xff\xd8" + b"0" * (9 * 1024 * 1024), "image/jpeg")

    deps.fetch_media = big
    await process_submission(deps, _submission())
    assert len(deps.store.get_review(f"rev_{ORDER}").photo_files) == 1


@pytest.mark.asyncio
async def test_photo_files_never_carry_the_order_id(tmp_path):
    """M1: el nombre de la foto termina en una URL pública; el id del pedido
    de Medusa abre sus datos personales."""
    deps, _ = _deps(tmp_path)
    await process_submission(deps, _submission())
    [rel] = deps.store.get_review(f"rev_{ORDER}").photo_files
    assert ORDER not in rel


@pytest.mark.asyncio
async def test_the_public_name_is_who_rated_not_the_gift_recipient(tmp_path):
    """P6: un regalo trae el nombre y la ciudad de quien lo recibió; publica
    su nombre quien calificó (su perfil de WhatsApp) y la ciudad solo si es
    la misma persona. Un correo nunca sale como nombre."""
    deps, _ = _deps(tmp_path, metadata={"phone_number_id": "PID", "profile": {"name": "Laura Ríos"}})
    await process_submission(deps, _submission())
    review = deps.store.get_review(f"rev_{ORDER}")
    assert (review.author_name, review.author_city) == ("Laura R.", None)


@pytest.mark.asyncio
async def test_without_a_usable_name_the_review_is_anonymous(tmp_path):
    deps, _ = _deps(tmp_path, metadata={"phone_number_id": "PID", "profile": {"name": "</script><b>"}})
    await process_submission(deps, _submission())
    assert deps.store.get_review(f"rev_{ORDER}").author_name == "Cliente de Hubara"


# ---------- que se vea por qué no salió (premortem P4/I5) ----------


@pytest.mark.asyncio
async def test_every_decision_is_recorded_with_its_reason(tmp_path):
    deps, _ = _deps(tmp_path, coupons_ready=False)
    await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 3 * HOUR))
    plan = deps.store.get_plan(ORDER)
    assert (plan["action"], plan["reason"], plan["session_id"], plan["at_ms"]) == (
        "wait", "coupon_not_configured", S, T0 + 3 * HOUR)

    deps, _ = _deps(tmp_path, mode="off")
    await plan_step(deps, ReviewPlanInput(session_id=S, order_id=ORDER, delivered_at_ms=T0, now_ms=T0 + 4 * HOUR))
    assert (deps.store.get_plan(ORDER)["action"], deps.store.get_plan(ORDER)["reason"]) == ("skip", "disabled")


# ---------- ¿llegó? (Meta avisa por el webhook de estados — premortem I10/C7) ----------


@pytest.mark.asyncio
async def test_the_sent_message_id_is_kept_to_follow_its_delivery(tmp_path):
    deps, _ = _deps(tmp_path)
    await send_request(deps, ReviewSendInput(session_id=S, order_id=ORDER, action="send_flow", now_ms=T0 + 3 * HOUR))
    assert deps.store.get_request(ORDER)["wa_message_id"] == "wamid.F"
    assert deps.metadata.read(S)["review_request_touches"][-1]["wa_message_id"] == "wamid.F"


def _sent_with_status(tmp_path, *, channel: str, status: str | None, error_code: str | None = None):
    touch = {"order_id": ORDER, "kind": channel, "at_ms": T0 + 3 * HOUR, "wa_message_id": "wamid.F"}
    if status:
        touch["status"] = status
    if error_code:
        touch["error_code"] = error_code
    deps, _ = _deps(tmp_path, metadata={"phone_number_id": "PID", "review_request_touches": [touch]})
    deps.store.claim_request(ORDER, session_id=S, now_ms=T0 + 3 * HOUR)
    deps.store.update_request(ORDER, status="sent", channel=channel, wa_message_id="wamid.F")
    return deps


@pytest.mark.asyncio
async def test_a_flow_meta_did_not_deliver_because_the_window_closed_is_reopened(tmp_path):
    """Meta suele aceptar el envío y avisar después (131047): la invitación se
    reabre para que salga la plantilla."""
    from src.plugins.reviews.agent.activities import ReviewDeliveryInput, check_delivery

    deps = _sent_with_status(tmp_path, channel="flow", status="failed", error_code="131047")
    assert await check_delivery(deps, ReviewDeliveryInput(session_id=S, order_id=ORDER)) == "retry"
    assert deps.store.get_request(ORDER) is None


@pytest.mark.asyncio
async def test_a_template_meta_refused_to_deliver_is_recorded_as_failed(tmp_path):
    from src.plugins.reviews.agent.activities import ReviewDeliveryInput, check_delivery

    deps = _sent_with_status(tmp_path, channel="template", status="failed", error_code="131049")
    assert await check_delivery(deps, ReviewDeliveryInput(session_id=S, order_id=ORDER)) == "failed"
    request = deps.store.get_request(ORDER)
    assert request["status"] == "failed" and "131049" in request["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "sent", "delivered", "read"])
async def test_a_delivered_or_unknown_invitation_stays_sent(tmp_path, status):
    from src.plugins.reviews.agent.activities import ReviewDeliveryInput, check_delivery

    deps = _sent_with_status(tmp_path, channel="flow", status=status)
    assert await check_delivery(deps, ReviewDeliveryInput(session_id=S, order_id=ORDER)) == "sent"
    assert deps.store.get_request(ORDER)["status"] == "sent"


@pytest.mark.asyncio
async def test_the_invitation_and_the_thanks_are_marked_as_reviews_notices(tmp_path):
    """C5: el historial los marca como aviso de reseñas (no del bot) y el
    turno siguiente los cita: el bot sabe a qué responde el cliente."""
    deps, calls = _deps(tmp_path)
    await send_request(deps, ReviewSendInput(session_id=S, order_id=ORDER, action="send_flow", now_ms=T0 + 3 * HOUR))
    await process_submission(deps, _submission())
    assert calls.senders == ["reviews", "reviews"]
