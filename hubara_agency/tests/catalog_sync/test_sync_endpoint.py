"""Test del wiring HTTP `POST /api/catalog/sync` — el flag `force`.

El botón "Sincronizar" del dashboard hace un sync DELTA por defecto (barato,
solo re-pushea lo que cambió). Cuando Meta falla el fetch async de una imagen
sin que cambien los datos, el delta lo saltea como "sin cambios" y el catálogo
queda irrecuperable desde el dashboard. El checkbox "Forzar re-sync completo"
manda `{"force": true}` → el workflow corre con `force_full_refresh=True` →
todos los items se re-pushean → Meta re-encola las imágenes.

Acá validamos SOLO lo que vive en la capa API: que el `force` del body llega
como `force_full_refresh` al `CatalogSyncInput` que arranca el workflow.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.catalog.api as api_mod


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(api_mod.router)
    return TestClient(app)


@pytest.mark.parametrize(
    "body,expected_force",
    [
        ({}, False),                 # botón normal → delta
        ({"force": False}, False),   # explícito delta
        ({"force": True}, True),     # checkbox "forzar re-sync completo"
    ],
)
def test_sync_body_force_flows_to_workflow_input(body, expected_force):
    captured: dict = {}

    async def _start(name, inp, **kw):
        captured["input"] = inp
        return AsyncMock(first_execution_run_id="run_test")

    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(side_effect=_start)

    with (
        patch.object(
            api_mod, "get_temporal_client", AsyncMock(return_value=fake_client)
        ),
        patch.object(
            api_mod, "_find_running_sync", AsyncMock(return_value=None)
        ),
        patch.object(api_mod, "get_dashboard_event_bus"),
    ):
        resp = _client().post("/sync", json=body)

    assert resp.status_code == 200, resp.text
    assert captured["input"].force_full_refresh is expected_force
