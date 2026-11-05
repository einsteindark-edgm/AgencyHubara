"""DeepSeek: cost by API key from the platform's usage page, plus the balance."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import httpx

from src.plugins.costs.domain.money import to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import Source, Status
from src.plugins.costs.providers.deepseek import DeepSeekProvider

RANGE = DateRange(date(2026, 11, 2), date(2026, 11, 3))
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
NOV2 = 1793595600  # 2026-11-02 00:00 Bogotá
NOV3 = 1793682000
NOV4 = 1793768400


def _key(tid: str, name: str, valid: bool = True) -> dict:
    return {"tracking_id": tid, "name": name, "sensitive_id": "sk-ab***cd",
            "valid": valid, "key_type": "NORMAL"}


def _envelope(biz_data: dict | None, biz_code: int = 0, biz_msg: str = "") -> dict:
    return {"code": 0, "msg": "", "data": {"biz_code": biz_code, "biz_msg": biz_msg,
                                           "biz_data": biz_data}}


COST = _envelope({
    "start": NOV2, "end": NOV4, "bucket": 86400, "models": ["deepseek-flash"], "functions": [],
    "data": [
        {"currency": "USD", "function_series": [], "series": [
            {"api_key": _key("t-prod", "Hubara Ventas"), "model": "deepseek-flash",
             "buckets": [{"time": NOV2, "cost": "1.2500000000000000"},
                         {"time": NOV3, "cost": "0.7500000000000000"}]},
            {"api_key": _key("t-prod", "Hubara Ventas"), "model": "deepseek-pro",
             "buckets": [{"time": NOV3, "cost": "0.5000000000000000"}]},
            {"api_key": _key("t-lab", "Laboratorios"), "model": "deepseek-flash",
             "buckets": [{"time": NOV2, "cost": "3.0000000000000000"},
                         {"time": NOV3, "cost": "0"}]},
        ]},
        {"currency": "CNY", "function_series": [], "series": [
            {"api_key": _key("t-prod", "Hubara Ventas"), "model": "deepseek-flash",
             "buckets": [{"time": NOV2, "cost": "7.1000000000000000"}]},
        ]},
    ],
})
AMOUNT = _envelope({
    "start": NOV2, "end": NOV4, "bucket": 86400, "models": ["deepseek-flash"],
    "functions": [], "function_series": [],
    "series": [
        {"api_key": _key("t-prod", "Hubara Ventas"), "model": "deepseek-flash",
         "buckets": [{"time": NOV2, "usage": {"REQUEST": 120, "RESPONSE_TOKEN": 9}},
                     {"time": NOV3, "usage": {"REQUEST": 80, "RESPONSE_TOKEN": 9}}]},
        {"api_key": _key("t-lab", "Laboratorios"), "model": "deepseek-flash",
         "buckets": [{"time": NOV2, "usage": {"REQUEST": 900}}]},
    ],
})
BALANCE = {"is_available": True, "balance_infos": [
    {"currency": "USD", "total_balance": "12.34", "granted_balance": "0.00",
     "topped_up_balance": "12.34"}]}


def _handler(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith("/by_api_key/cost"):
        return httpx.Response(200, json=COST)
    if req.url.path.endswith("/by_api_key/amount"):
        return httpx.Response(200, json=AMOUNT)
    if req.url.path.endswith("/user/balance"):
        return httpx.Response(200, json=BALANCE)
    return httpx.Response(404)


def _run(provider: DeepSeekProvider, handler=_handler):
    seen: list[httpx.Request] = []

    def _h(req):
        seen.append(req)
        return handler(req)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_h)) as client:
            return await provider.fetch(client, RANGE, NOW)

    return asyncio.run(go()), seen


def _provider(**kw) -> DeepSeekProvider:
    base = dict(platform_token="session", api_key="sk-x", production_keys=["Hubara Ventas"])
    base.update(kw)
    return DeepSeekProvider(**base)


def test_cost_is_split_by_api_key_and_currency() -> None:
    report, _ = _run(_provider())

    assert report.status is Status.OK
    assert report.source is Source.METERED
    by = {(ln.label, ln.currency): ln for ln in report.lines}
    prod_usd = by[("Hubara Ventas", "USD")]
    # Both models of the same key add up, day by day.
    assert prod_usd.daily == {date(2026, 11, 2): to_micros("1.25"), date(2026, 11, 3): to_micros("1.25")}
    assert prod_usd.production is True
    assert prod_usd.quantity == 200
    assert prod_usd.unit == "solicitudes"
    assert by[("Hubara Ventas", "CNY")].amount_micros == to_micros("7.10")


def test_keys_outside_production_are_marked_apart() -> None:
    report, _ = _run(_provider())

    lab = {ln.label: ln for ln in report.lines}["Laboratorios"]
    assert lab.production is False
    assert lab.amount_micros == to_micros("3")


def test_platform_is_asked_for_bogota_midnights_with_the_session_token() -> None:
    _, seen = _run(_provider())

    cost_req = next(r for r in seen if r.url.path.endswith("/cost"))
    assert cost_req.url.host == "platform.deepseek.com"
    assert cost_req.url.params["start"] == str(NOV2)
    assert cost_req.url.params["end"] == str(NOV4)
    assert cost_req.url.params["tz"] == "-18000"
    assert cost_req.headers["authorization"] == "Bearer session"


def test_balance_is_reported_as_a_note() -> None:
    report, _ = _run(_provider())

    assert any("12.34" in n for n in report.notes)


def test_without_platform_session_it_still_shows_the_balance() -> None:
    report, seen = _run(_provider(platform_token=None))

    assert report.status is Status.NOT_CONNECTED
    assert "DEEPSEEK_PLATFORM_TOKEN" in (report.how_to_connect or "")
    assert any("12.34" in n for n in report.notes)
    assert all(r.url.host != "platform.deepseek.com" for r in seen)


def test_expired_session_is_explained() -> None:
    def handler(req):
        if req.url.host == "platform.deepseek.com":
            return httpx.Response(200, json=_envelope(None, biz_code=40003, biz_msg="UNAUTHORIZED"))
        return _handler(req)

    report, _ = _run(_provider(), handler)

    assert report.status is Status.ERROR
    assert "UNAUTHORIZED" in (report.error or "")
    assert "DEEPSEEK_PLATFORM_TOKEN" in (report.error or "")


def test_balance_failure_does_not_hide_the_cost() -> None:
    def handler(req):
        if req.url.path.endswith("/user/balance"):
            return httpx.Response(500)
        return _handler(req)

    report, _ = _run(_provider(), handler)

    assert report.status is Status.OK
    assert report.lines
