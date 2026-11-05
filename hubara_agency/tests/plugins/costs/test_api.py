"""HTTP surface of the Costs tab: one endpoint per provider card."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.costs import api as costs_api
from src.plugins.costs.composition import get_costs_service
from src.plugins.costs.domain.fx import FxTable
from src.plugins.costs.domain.report import CostLine, ProviderReport, Source
from src.plugins.costs.service import CostsService

NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)  # 10:00 in Bogotá


class _Fx:
    async def load(self, client, rng):
        return FxTable(cop_per_usd={rng.start: Decimal("4000")}, note="TRM de prueba")


class _Provider:
    id = "deepseek"
    label = "DeepSeek"
    connected = True

    def __init__(self) -> None:
        self.ranges = []

    async def fetch(self, client, rng, now):
        self.ranges.append(rng)
        line = CostLine(key="k", label="Hubara Ventas", currency="USD",
                        daily={rng.start: 7_888_983})
        return ProviderReport.ok(self.id, rng, Source.METERED, [line])


@pytest.fixture
def provider() -> _Provider:
    return _Provider()


@pytest.fixture
def client(provider) -> TestClient:
    service = CostsService(
        [provider], _Fx(), timeout_s=5, live_ttl_s=60, closed_ttl_s=60, now=lambda: NOW
    )
    app = FastAPI()
    app.include_router(costs_api.router, prefix="/api/costs")
    app.dependency_overrides[get_costs_service] = lambda: service
    return TestClient(app)


def test_providers_endpoint_lists_cards_and_the_default_two_weeks(client) -> None:
    body = client.get("/api/costs/providers").json()

    assert body["providers"] == [{"id": "deepseek", "label": "DeepSeek", "connected": True}]
    assert body["today"] == "2026-11-04"
    assert body["default_start"] == "2026-10-22"
    assert body["default_end"] == "2026-11-04"
    assert body["max_days"] == 92


def test_provider_report_for_a_range(client, provider) -> None:
    resp = client.get("/api/costs/providers/deepseek?start=2026-10-22&end=2026-11-04")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["production_usd_micros"] == 7_888_983
    assert body["lines"][0]["label"] == "Hubara Ventas"
    assert body["cached"] is False
    assert provider.ranges[0].start == date(2026, 10, 22)


def test_without_dates_it_uses_the_last_two_weeks(client, provider) -> None:
    client.get("/api/costs/providers/deepseek")

    assert (provider.ranges[0].start, provider.ranges[0].end) == (date(2026, 10, 22), date(2026, 11, 4))


def test_invalid_range_answers_422_in_spanish(client) -> None:
    resp = client.get("/api/costs/providers/deepseek?start=2026-11-04&end=2026-10-01")

    assert resp.status_code == 422
    assert "inicio" in resp.json()["detail"]


def test_unknown_provider_is_404(client) -> None:
    assert client.get("/api/costs/providers/nope").status_code == 404


def test_router_is_not_public() -> None:
    assert getattr(costs_api, "PUBLIC_ROUTER", False) is False
