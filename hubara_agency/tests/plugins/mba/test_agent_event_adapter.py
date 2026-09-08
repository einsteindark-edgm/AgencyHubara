"""D1.9 — cliente HTTP de Agent Event (Meta Business Agent Cloud API).

Contrato leído del OpenAPI publicado por Meta (2026-09-08, v2.0.0):
`POST https://api.facebook.com/{entity_id}/agent_event`, header
`X-API-Version: 2.0.0`, body `{to: "+E.164", event: {type, description,
payload: "<JSON string>"}}`, respuesta `{status: "accepted", agent_event_id}`.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from src.plugins.mba.adapters.agent_event import (
    AGENT_EVENT_API_VERSION,
    AgentEventError,
    AgentEventPort,
    AgentEventResult,
    FakeAgentEvent,
    MetaAgentEvent,
    agent_event_url,
)
from src.plugins.mba.adapters.meta_api import MBA_API_BASE_URL, MbaApiError

ENTITY = "PHONE_777"
TO = "+573001234567"
URL = f"https://api.facebook.com/{ENTITY}/agent_event"


class _Sleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _client(token: str = "tok", sleeper: _Sleeper | None = None) -> MetaAgentEvent:
    return MetaAgentEvent(token=lambda: token, sleep=sleeper or _Sleeper())


def test_url_and_version_follow_metas_openapi() -> None:
    assert agent_event_url(MBA_API_BASE_URL, ENTITY) == URL
    assert AGENT_EVENT_API_VERSION == "2.0.0"  # agent_event usa 2.0.0 (thread_control es el único en 1.0.0)
    assert isinstance(MetaAgentEvent(token=lambda: "x"), AgentEventPort)
    assert isinstance(FakeAgentEvent(), AgentEventPort)


@respx.mock
async def test_emit_posts_the_documented_body_with_the_payload_as_a_json_string() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"status": "accepted", "agent_event_id": "AE_1"}))
    res = await _client().emit(
        entity_id=ENTITY, to=TO, event_type="order_shipped", description="Cuéntale al cliente que su pedido va en camino.",
        payload={"order_id": "order_1", "stage": "shipping"},
    )
    assert res == AgentEventResult(agent_event_id="AE_1", status="accepted")
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["X-API-Version"] == AGENT_EVENT_API_VERSION
    body = json.loads(req.content)
    assert body["to"] == TO
    assert body["event"]["type"] == "order_shipped"
    assert body["event"]["description"] == "Cuéntale al cliente que su pedido va en camino."
    assert json.loads(body["event"]["payload"]) == {"order_id": "order_1", "stage": "shipping"}
    assert set(body) == {"to", "event"} and set(body["event"]) == {"type", "description", "payload"}


@respx.mock
async def test_emit_without_payload_omits_the_key_and_tolerates_a_bodyless_ack() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"status": "accepted"}))
    res = await _client().emit(entity_id=ENTITY, to=TO, event_type="payment_received", description="d")
    assert res == AgentEventResult(agent_event_id=None, status="accepted")
    assert set(json.loads(route.calls.last.request.content)["event"]) == {"type", "description"}


@respx.mock
async def test_without_a_token_nothing_is_called() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"status": "accepted"}))
    for token in ("", "PLACEHOLDER_set_out_of_band"):
        with pytest.raises(AgentEventError) as exc:
            await _client(token=token).emit(entity_id=ENTITY, to=TO, event_type="order_shipped", description="d")
        assert exc.value.kind == "not_configured" and isinstance(exc.value, MbaApiError)
    assert route.call_count == 0


@respx.mock
async def test_a_4xx_is_a_rejection_with_metas_message_and_is_not_retried() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(400, json={"error": {"message": "Unknown entity"}}))
    with pytest.raises(AgentEventError) as exc:
        await _client().emit(entity_id=ENTITY, to=TO, event_type="order_shipped", description="d")
    assert (exc.value.kind, exc.value.status, exc.value.detail, exc.value.attempts) == ("rejected", 400, "Unknown entity", 1)
    assert route.call_count == 1


@respx.mock
async def test_a_5xx_is_retried_with_backoff_then_reported_as_unavailable() -> None:
    sleeper = _Sleeper()
    route = respx.post(URL).mock(return_value=httpx.Response(503, text="down"))
    with pytest.raises(AgentEventError) as exc:
        await _client(sleeper=sleeper).emit(entity_id=ENTITY, to=TO, event_type="order_shipped", description="d")
    assert exc.value.kind == "unavailable" and route.call_count == 3 and sleeper.delays == [0.5, 1.0]


@respx.mock
async def test_a_timeout_is_ambiguous_and_is_not_retried_because_the_event_is_not_idempotent() -> None:
    route = respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AgentEventError) as exc:
        await _client().emit(entity_id=ENTITY, to=TO, event_type="order_shipped", description="d")
    assert exc.value.kind == "ambiguous" and route.call_count == 1


async def test_the_fake_records_calls_and_can_fail_on_demand() -> None:
    fake = FakeAgentEvent()
    res = await fake.emit(entity_id=ENTITY, to=TO, event_type="order_shipped", description="d", payload={"a": 1})
    assert res.status == "accepted" and res.agent_event_id == "fake-1"
    assert fake.calls == [(ENTITY, TO, "order_shipped", "d", {"a": 1})]
    fake.fail_with = AgentEventError("rejected", status=400, detail="nope")
    with pytest.raises(AgentEventError):
        await fake.emit(entity_id=ENTITY, to=TO, event_type="order_shipped", description="d")
