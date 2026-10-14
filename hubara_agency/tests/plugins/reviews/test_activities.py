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

    async def get_facts(self, ids):
        if self.unresolved:
            return SimpleNamespace(facts={}, unresolved=frozenset(ids))
        fact = SimpleNamespace(order_id=ORDER, display_id="#31", stage=self.stage, is_test=self.is_test)
        return SimpleNamespace(facts={ORDER: fact}, unresolved=frozenset())


class _Orders:
    async def get(self, order_id):
        return SimpleNamespace(
            summary=SimpleNamespace(display_id="#31", customer="Ana María Gómez"),
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


def _deps(tmp_path: Path, *, metadata=None, facts=None, mode="on", flow_ok=True,
          template_error: Exception | None = None, quiet_end=None, calls: _Calls | None = None) -> tuple[ReviewsDeps, _Calls]:
    calls = calls or _Calls()

    async def upload_media(phone_id, content, mime):
        calls.uploads.append((phone_id, len(content), mime))
        return f"MEDIA_{len(calls.uploads)}"

    async def fetch_media(media_id):
        return (b"\xff\xd8photo-bytes", "image/jpeg")

    async def fetch_url(url):
        return None

    async def send_flow(session_id, flow, *, history_text, image_url=None):
        calls.flows.append((session_id, flow, history_text))
        return OutboundResult(wa_message_id="wamid.F" if flow_ok else None, ok=flow_ok,
                              error=None if flow_ok else "(#131047) Re-engagement message")

    async def send_template(session_id, name, variables, **kwargs):
        if template_error is not None:
            raise template_error
        calls.templates.append((session_id, name, variables, kwargs))
        return OutboundResult(wa_message_id="wamid.T", ok=True)

    async def send_cta(session_id, cta):
        calls.ctas.append((session_id, cta))
        return OutboundResult(wa_message_id="wamid.C", ok=True)

    async def escalate(session_id, summary):
        calls.escalations.append((session_id, summary))

    env = {"REVIEW_REQUESTS_MODE": mode, "META_FLOW_ID_REVIEW": "FLOW1",
           "GOOGLE_REVIEW_URL": "https://hubara.com.co/opinar/"}
    deps = ReviewsDeps(
        settings=ReviewSettings.from_env(env),
        store=ReviewsStore(tmp_path),
        metadata=_Metadata(metadata if metadata is not None else {"phone_number_id": "PID"}),
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
