"""El avance de una campaña llega al dashboard por el stream SSE.

El worker de campañas es otro proceso que la API: escribe el avance en
`_campaigns/<id>.json` y no puede publicar en el bus del dashboard. La API
muestrea esos archivos y publica `marketing / campaign_updated` con el id de
la campaña que cambió; el panel invalida la lista al recibirlo.
"""
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from temporalio.testing import ActivityEnvironment

import src.plugins.marketing.api as api_mod
import src.plugins.marketing.campaign_events as events_mod
from src.plugins.marketing.agent.campaigns.activities import (
    record_campaign_progress_activity,
)
from src.plugins.marketing.campaign_events import CampaignEventsSampler
from src.plugins.marketing.campaign_store import CampaignStore
from src.plugins.marketing.domain.campaign_send import CampaignSendProgress
from src.plugins.marketing.domain.campaigns import new_campaign
from src.sdk.dashboardkit import DashboardEvent, DashboardEventBus


def _bus_with_log() -> tuple[DashboardEventBus, list[DashboardEvent]]:
    bus = DashboardEventBus()
    seen: list[DashboardEvent] = []
    bus.add_listener(seen.append)
    return bus, seen


def _published(seen: list[DashboardEvent]) -> list[tuple[str, str, str | None]]:
    return [(e.domain, e.type, e.id) for e in seen]


def _seed(vault: Path, campaign_id: str) -> None:
    CampaignStore(vault).save(new_campaign(campaign_id=campaign_id, name=campaign_id, now_ms=1))


def test_la_primera_muestra_no_publica_nada(_isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, "mkt-a")
    bus, seen = _bus_with_log()

    CampaignEventsSampler(lambda: CampaignStore(_isolate_vault_dir), bus).tick()

    assert seen == []


async def test_el_avance_que_escribe_el_worker_publica_esa_campana(
    _isolate_vault_dir: Path,
) -> None:
    _seed(_isolate_vault_dir, "mkt-a")
    _seed(_isolate_vault_dir, "mkt-b")
    bus, seen = _bus_with_log()
    sampler = CampaignEventsSampler(lambda: CampaignStore(_isolate_vault_dir), bus)
    sampler.tick()

    await ActivityEnvironment().run(
        record_campaign_progress_activity,
        "mkt-a",
        CampaignSendProgress(total=500, processed=100, sent=100),
        None,
    )
    sampler.tick()

    assert _published(seen) == [("marketing", "campaign_updated", "mkt-a")]


def test_sin_cambios_no_vuelve_a_publicar(_isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, "mkt-a")
    bus, seen = _bus_with_log()
    sampler = CampaignEventsSampler(lambda: CampaignStore(_isolate_vault_dir), bus)
    sampler.tick()
    _seed(_isolate_vault_dir, "mkt-b")
    sampler.tick()

    sampler.tick()

    assert _published(seen) == [("marketing", "campaign_updated", "mkt-b")]


def test_una_campana_borrada_tambien_publica(_isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, "mkt-a")
    bus, seen = _bus_with_log()
    sampler = CampaignEventsSampler(lambda: CampaignStore(_isolate_vault_dir), bus)
    sampler.tick()

    CampaignStore(_isolate_vault_dir).delete("mkt-a")
    sampler.tick()

    assert _published(seen) == [("marketing", "campaign_updated", "mkt-a")]


def test_una_muestra_que_falla_no_frena_las_siguientes(_isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, "mkt-a")
    bus, seen = _bus_with_log()
    calls = {"n": 0}

    def flaky_store() -> CampaignStore:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("vault a medio montar")
        return CampaignStore(_isolate_vault_dir)

    sampler = CampaignEventsSampler(flaky_store, bus)
    sampler.tick()
    _seed(_isolate_vault_dir, "mkt-b")

    sampler.tick()  # falla: se loguea y no publica
    sampler.tick()

    assert _published(seen) == [("marketing", "campaign_updated", "mkt-b")]


def test_con_la_api_arriba_el_cambio_llega_al_bus_del_dashboard(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus, seen = _bus_with_log()
    monkeypatch.setattr(events_mod, "get_dashboard_event_bus", lambda: bus)
    monkeypatch.setattr(events_mod, "SAMPLE_INTERVAL_S", 0.02)
    app = FastAPI()
    app.include_router(api_mod.router, prefix="/api/marketing")

    with TestClient(app):
        time.sleep(0.1)  # la primera muestra es la línea base
        _seed(_isolate_vault_dir, "mkt-a")
        deadline = time.monotonic() + 3
        while not seen and time.monotonic() < deadline:
            time.sleep(0.02)

    assert ("marketing", "campaign_updated", "mkt-a") in _published(seen)
