"""Gemini: Google Cloud's billing export in BigQuery, net of credits."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.plugins.costs.domain.money import to_micros
from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import Source, Status
from src.plugins.costs.providers.gemini import GeminiProvider

RANGE = DateRange(date(2026, 11, 2), date(2026, 11, 3))
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "hubara-billing",
    "client_email": "costs-reader@hubara-billing.iam.gserviceaccount.com",
    "private_key": _KEY.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode(),
    "token_uri": "https://oauth2.googleapis.com/token",
}
TABLES = {"tables": [
    {"tableReference": {"projectId": "hubara-billing", "datasetId": "billing_export",
                        "tableId": "cloud_pricing_export"}},
    {"tableReference": {"projectId": "hubara-billing", "datasetId": "billing_export",
                        "tableId": "gcp_billing_export_v1_000000_AAAAAA_BBBBBB"}},
]}


def _row(day: str, service: str, currency: str, cost: str, credits: str) -> dict:
    return {"f": [{"v": day}, {"v": service}, {"v": currency}, {"v": cost}, {"v": credits}]}


QUERY = {"jobComplete": True, "totalRows": "3", "rows": [
    _row("2026-11-02", "Gemini API", "COP", "1500.5", "-500.5"),
    _row("2026-11-03", "Gemini API", "COP", "700", "0"),
    _row("2026-11-03", "BigQuery", "COP", "0.01", "0"),
]}


def _handler(seen: list[httpx.Request], query=QUERY):
    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "ya29.t", "expires_in": 3600})
        if req.url.path.endswith("/tables"):
            return httpx.Response(200, json=TABLES)
        if req.url.path.endswith("/queries"):
            return httpx.Response(200, json=query)
        return httpx.Response(404)

    return h


def _run(provider: GeminiProvider, query=QUERY):
    seen: list[httpx.Request] = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(seen, query))) as c:
            return await provider.fetch(c, RANGE, NOW)

    return asyncio.run(go()), seen


def _provider(**kw) -> GeminiProvider:
    base = dict(service_account=SERVICE_ACCOUNT, dataset="billing_export", project=None)
    base.update(kw)
    return GeminiProvider(**base)


def test_net_cost_per_service_and_day_in_the_billing_currency() -> None:
    report, _ = _run(_provider())

    assert report.status is Status.OK
    assert report.source is Source.BILLED
    gemini = {ln.label: ln for ln in report.lines}["Gemini API"]
    assert gemini.currency == "COP"
    assert gemini.daily == {date(2026, 11, 2): to_micros("1000"), date(2026, 11, 3): to_micros("700")}


def test_service_account_signs_a_readonly_bigquery_assertion() -> None:
    _, seen = _run(_provider())

    token_req = seen[0]
    form = parse_qs(token_req.content.decode())
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    claims = jwt.decode(
        form["assertion"][0], _KEY.public_key(), algorithms=["RS256"],
        audience="https://oauth2.googleapis.com/token",
    )
    assert claims["iss"] == SERVICE_ACCOUNT["client_email"]
    assert claims["scope"] == "https://www.googleapis.com/auth/bigquery.readonly"


def test_query_targets_the_export_table_with_the_bogota_instants() -> None:
    _, seen = _run(_provider())

    query = next(r for r in seen if r.url.path.endswith("/queries"))
    assert query.url.path == "/bigquery/v2/projects/hubara-billing/queries"
    assert query.headers["authorization"] == "Bearer ya29.t"
    body = json.loads(query.content)
    assert "gcp_billing_export_v1_000000_AAAAAA_BBBBBB" in body["query"]
    params = {p["name"]: p["parameterValue"]["value"] for p in body["queryParameters"]}
    assert params == {"start": "2026-11-02T05:00:00Z", "end": "2026-11-04T05:00:00Z"}
    assert body["useLegacySql"] is False


def test_without_service_account_it_explains_the_export() -> None:
    report, seen = _run(_provider(service_account=None))

    assert report.status is Status.NOT_CONNECTED
    assert "BigQuery" in (report.how_to_connect or "")
    assert seen == []


def test_export_without_table_yet_is_not_a_zero() -> None:
    seen: list[httpx.Request] = []

    def h(req):
        seen.append(req)
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(200, json={"tables": []})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            return await _provider().fetch(c, RANGE, NOW)

    report = asyncio.run(go())
    assert report.status is Status.NOT_CONNECTED
    assert report.production_usd_micros is None


def test_slow_query_is_an_error_not_an_empty_month() -> None:
    report, _ = _run(_provider(), query={"jobComplete": False})

    assert report.status is Status.ERROR
    assert "BigQuery" in (report.error or "")
