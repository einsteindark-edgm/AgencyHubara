"""D1.6 — cliente HTTP de Thread Control (Meta Business Agent Cloud API).

Contrato leído del OpenAPI publicado por Meta (2026-09-04, v1.0.0):
`POST https://api.facebook.com/business/whatsapp/phone_numbers/{phone_number_id}/thread_control`
body `{messaging_product: "whatsapp", action: release|take|pass, to, metadata?}`,
header `X-API-Version: 1.0.0`, respuesta `{messaging_product: "whatsapp"}`.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from src.plugins.mba.adapters.meta_api import MBA_API_BASE_URL, MbaApiError
from src.plugins.mba.adapters.thread_control import (
    THREAD_CONTROL_API_VERSION,
    FakeThreadControl,
    MetaThreadControl,
    ThreadControlError,
    ThreadControlPort,
    ThreadControlResult,
    thread_control_url,
)

PHONE = "PHONE_777"
CUSTOMER = "573001234567"
URL = f"https://api.facebook.com/business/whatsapp/phone_numbers/{PHONE}/thread_control"


class _Sleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _client(token: str = "tok", sleeper: _Sleeper | None = None) -> MetaThreadControl:
    return MetaThreadControl(token=lambda: token, sleep=sleeper or _Sleeper())


def test_url_and_version_follow_metas_openapi() -> None:
    assert MBA_API_BASE_URL == "https://api.facebook.com"
    assert thread_control_url(MBA_API_BASE_URL, PHONE) == URL
    assert (
        THREAD_CONTROL_API_VERSION == "1.0.0"
    )  # el OpenAPI de thread_control solo enumera 1.0.0
    assert isinstance(MetaThreadControl(token=lambda: "x"), ThreadControlPort)
    assert isinstance(FakeThreadControl(), ThreadControlPort)


@respx.mock
async def test_release_posts_the_documented_body_and_headers() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"messaging_product": "whatsapp"})
    )
    res = await _client().release(
        phone_number_id=PHONE, to=CUSTOMER, metadata="hubara:handoff_resolved"
    )
    assert res == ThreadControlResult(action="release", to=CUSTOMER)
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["X-API-Version"] == THREAD_CONTROL_API_VERSION
    assert req.headers["Content-Type"].startswith("application/json")
    assert json.loads(req.content) == {
        "messaging_product": "whatsapp",
        "action": "release",
        "to": CUSTOMER,
        "metadata": "hubara:handoff_resolved",
    }


@respx.mock
async def test_take_omits_metadata_when_absent() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"messaging_product": "whatsapp"})
    )
    res = await _client().take(phone_number_id=PHONE, to=CUSTOMER)
    assert res.action == "take"
    assert json.loads(route.calls.last.request.content) == {
        "messaging_product": "whatsapp",
        "action": "take",
        "to": CUSTOMER,
    }


@respx.mock
async def test_without_a_token_nothing_is_called() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"messaging_product": "whatsapp"})
    )
    for token in ("", "PLACEHOLDER_set_out_of_band"):
        with pytest.raises(ThreadControlError) as exc:
            await _client(token=token).release(phone_number_id=PHONE, to=CUSTOMER)
        assert exc.value.kind == "not_configured" and isinstance(exc.value, MbaApiError)
    assert route.call_count == 0


@respx.mock
async def test_a_4xx_is_a_rejection_with_metas_message_and_is_not_retried() -> None:
    body = {
        "error": {
            "message": "(#100) You must hold thread control to release it",
            "code": 100,
            "fbtrace_id": "X",
        }
    }
    route = respx.post(URL).mock(return_value=httpx.Response(400, json=body))
    with pytest.raises(ThreadControlError) as exc:
        await _client().release(phone_number_id=PHONE, to=CUSTOMER)
    assert exc.value.kind == "rejected" and exc.value.status == 400
    assert "hold thread control" in exc.value.detail
    assert route.call_count == 1


@respx.mock
async def test_5xx_is_retried_with_backoff_then_reported_as_unavailable() -> None:
    sleeper = _Sleeper()
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(500, json={"error": {"message": "boom"}}),
            httpx.Response(502, text="<html>bad gateway</html>"),
            httpx.Response(200, json={"messaging_product": "whatsapp"}),
        ]
    )
    res = await _client(sleeper=sleeper).release(phone_number_id=PHONE, to=CUSTOMER)
    assert res.action == "release" and route.call_count == 3
    assert sleeper.delays == [0.5, 1.0]

    sleeper = _Sleeper()
    route = respx.post(URL).mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    with pytest.raises(ThreadControlError) as exc:
        await _client(sleeper=sleeper).release(phone_number_id=PHONE, to=CUSTOMER)
    assert (
        exc.value.kind == "unavailable"
        and exc.value.status == 503
        and exc.value.attempts == 3
    )
    assert sleeper.delays == [0.5, 1.0]


@respx.mock
async def test_a_timeout_is_ambiguous_and_is_not_retried_because_release_is_not_idempotent() -> (
    None
):
    """L-1 (CAPI): Meta pudo haber procesado el release; un segundo POST daría
    4xx y taparía el éxito del primero."""
    sleeper = _Sleeper()
    route = respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ThreadControlError) as exc:
        await _client(sleeper=sleeper).release(phone_number_id=PHONE, to=CUSTOMER)
    assert (
        exc.value.kind == "ambiguous"
        and exc.value.status is None
        and exc.value.attempts == 1
    )
    assert "ReadTimeout" in exc.value.detail
    assert route.call_count == 1 and sleeper.delays == []


@respx.mock
async def test_metadata_is_truncated_to_metas_limit_and_the_token_never_leaks_into_errors() -> (
    None
):
    route = respx.post(URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "nope"}})
    )
    with pytest.raises(ThreadControlError) as exc:
        await _client(token="SECRET-TOKEN").release(
            phone_number_id=PHONE, to=CUSTOMER, metadata="x" * 2500
        )
    assert len(json.loads(route.calls.last.request.content)["metadata"]) == 2000
    assert "SECRET-TOKEN" not in str(exc.value) and "SECRET-TOKEN" not in repr(
        exc.value.__dict__
    )


def test_only_the_shared_base_module_writes_the_mba_api_host() -> None:
    """Ratchet: el host de la Cloud API de MBA vive en un solo lugar (como el
    del Graph API en platform/meta/graph.py)."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "src"
    # domain/config.py define el host (el preview de D0 arma los requests
    # literales con él); meta_api lo re-exporta para los adapters (D1.6 / D2.1).
    allowed = {"plugins/mba/domain/config.py"}
    offenders = sorted(
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if "api.facebook.com" in p.read_text(encoding="utf-8")
        and p.relative_to(src).as_posix() not in allowed
    )
    assert offenders == [], offenders


@respx.mock
async def test_429_is_retried_like_a_5xx() -> None:
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, json={"error": {"message": "rate"}}),
            httpx.Response(200, json={"messaging_product": "whatsapp"}),
        ]
    )
    await _client().release(phone_number_id=PHONE, to=CUSTOMER)
    assert route.call_count == 2


async def test_the_fake_records_calls_and_can_fail_on_demand() -> None:
    fake = FakeThreadControl()
    await fake.release(phone_number_id=PHONE, to=CUSTOMER, metadata="m")
    assert fake.calls == [("release", PHONE, CUSTOMER, "m")]
    fake.fail_with = ThreadControlError(
        "unavailable", status=500, detail="x", attempts=3
    )
    with pytest.raises(ThreadControlError):
        await fake.take(phone_number_id=PHONE, to=CUSTOMER)
