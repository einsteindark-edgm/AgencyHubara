"""Guard del `run_id` que llega por URL al buzón de análisis de `ads`.

Hallazgo de endurecimiento (lectura de código, 2026-09-21; NO explotado, detrás
de `require_auth`). Misma clase que los de chats / orders / marketing:
`GET /runs/{run_id}`, `POST /runs/{run_id}/approve` y `GET /runs/{run_id}/events`
pasaban `run_id` SIN validar a `runs/record.py`, que arma
`<vault>/ad-analysis/<run_id>/record.json`. Con `%2E%2E` el archivo es
`<vault>/record.json` (FUERA de `ad-analysis/`) y con `%2E` es
`<vault>/ad-analysis/record.json`; `approve` además resumía en la caja de
GraphAgents el `execution_id` leído de ese archivo.

Los ids legítimos los genera `_new_run_id()`: `run-<12 hex>`, formato único
desde que nació el buzón. Contrato: id mal formado → 400 sin tocar el
filesystem; id bien formado que no existe → 404 (como siempre).

Qué llega de verdad al handler por HTTP: `%2E%2E` → `..`, `%2E` → `.`,
`x%0A` → `x\\n`; las variantes con `/` las corta el router (404) y el `..` crudo
lo normaliza el cliente. Por eso además se llama al handler directo con strings
arbitrarios. `/events` es un SSE infinito: solo se prueba por handler directo.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

import src.plugins.ads.api.analysis as api

_DECISION = {"decision": {"approved": True, "by": "ed"}}

_CANARY = {
    "run_id": "canario",
    "agent": "ads-analytics",
    "status": "awaiting_approval",
    "execution_id": "exec-CANARIO",
    "events": [],
    "result": None,
    "awaiting": None,
}


class _FakeBus:
    def __init__(self) -> None:
        self.subscribed = 0

    def subscribe(self) -> asyncio.Queue:
        self.subscribed += 1
        return asyncio.Queue()

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        pass


@pytest.fixture()
def harness(monkeypatch):
    """Mismo arnés que `test_analysis_api.py`: launch/resume capturados, sin caja."""
    resumed: list = []
    bus = _FakeBus()
    monkeypatch.setattr(api, "_spawn_launch", lambda run_id, agent, input: None)
    monkeypatch.setattr(
        api,
        "_spawn_resume",
        lambda run_id, execution_id, decision: resumed.append((run_id, execution_id)),
    )
    monkeypatch.setattr(api, "get_dashboard_event_bus", lambda: bus)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/ads/analysis")
    return TestClient(app), resumed, bus


def _plant_canaries(vault: Path) -> list[Path]:
    """Lo que `..` y `.` alcanzarían. `ad-analysis/` existe en prod desde el
    primer run — sin él, `ad-analysis/..` ni siquiera resuelve."""
    base = vault / "ad-analysis"
    base.mkdir(parents=True, exist_ok=True)
    planted = [vault / "record.json", base / "record.json"]
    for path in planted:
        path.write_text(json.dumps(_CANARY), encoding="utf-8")
    return planted


# ── El hallazgo, reproducido ─────────────────────────────────────────────────


@pytest.mark.parametrize("encoded", ["%2E%2E", "%2E"])
def test_get_run_never_reads_a_record_outside_its_run_directory(
    harness, _isolate_vault_dir: Path, encoded
):
    client, _resumed, _bus = harness
    _plant_canaries(_isolate_vault_dir)

    resp = client.get(f"/api/ads/analysis/runs/{encoded}")

    assert resp.status_code == 400
    assert "exec-CANARIO" not in resp.text


@pytest.mark.parametrize("encoded", ["%2E%2E", "%2E"])
def test_approve_never_resumes_an_execution_read_from_outside(
    harness, _isolate_vault_dir: Path, encoded
):
    client, resumed, _bus = harness
    planted = _plant_canaries(_isolate_vault_dir)
    before = [p.read_text(encoding="utf-8") for p in planted]

    resp = client.post(f"/api/ads/analysis/runs/{encoded}/approve", json=_DECISION)

    assert resp.status_code == 400
    assert resumed == []  # nada leído de afuera se resume en la caja
    assert [p.read_text(encoding="utf-8") for p in planted] == before


# ── Lo que llega por HTTP ────────────────────────────────────────────────────

_MALFORMED_OVER_HTTP = [
    "run-0123456789ab%0A",  # `\n` final: el `$` de re.match lo aceptaría
    "run-0123456789abc",  # 13 hex
    "run-0123456789AB",  # el generador solo emite minúsculas
    "run-xyz",
    "nope",
]


@pytest.mark.parametrize("encoded", _MALFORMED_OVER_HTTP)
def test_malformed_run_id_over_http_returns_400(harness, encoded):
    client, resumed, _bus = harness

    got = client.get(f"/api/ads/analysis/runs/{encoded}")
    approved = client.post(
        f"/api/ads/analysis/runs/{encoded}/approve", json=_DECISION
    )

    assert got.status_code == 400
    assert approved.status_code == 400
    assert resumed == []


# ── Los handlers, sin el router delante (defensa en profundidad) ─────────────

_INVALID = [
    "..",
    ".",
    "../x",
    "run-0123456789ab/../../x",
    "run-0123456789ab\\..\\x",
    "",
    "run-0123456789ab\n",
    "run-",
    "run-" + "a" * 300,  # segmento de path desmedido
]


@pytest.mark.parametrize("run_id", _INVALID)
def test_get_run_handler_rejects_invalid_run_id(harness, run_id):
    with pytest.raises(HTTPException) as exc:
        api.get_run(run_id)

    assert exc.value.status_code == 400


@pytest.mark.parametrize("run_id", _INVALID)
async def test_approve_handler_rejects_invalid_run_id(harness, run_id):
    _client, resumed, _bus = harness

    with pytest.raises(HTTPException) as exc:
        await api.approve(run_id, api.ApproveBody(decision={"approved": True}))

    assert exc.value.status_code == 400
    assert resumed == []


@pytest.mark.parametrize("run_id", _INVALID)
async def test_events_handler_rejects_invalid_run_id_before_subscribing(
    harness, run_id
):
    _client, _resumed, bus = harness

    with pytest.raises(HTTPException) as exc:
        await api.run_events(run_id, request=object())

    assert exc.value.status_code == 400
    assert bus.subscribed == 0  # ni una suscripción colgada por un id inválido


# ── Los ids reales siguen funcionando ────────────────────────────────────────


def test_every_generated_run_id_passes_the_guard(harness):
    """Generador y guard no pueden divergir: bien formado e inexistente = 404."""
    for _ in range(50):
        with pytest.raises(HTTPException) as exc:
            api.get_run(api._new_run_id())
        assert exc.value.status_code == 404


async def test_events_still_streams_a_well_formed_run(harness):
    _client, _resumed, bus = harness

    resp = await api.run_events(api._new_run_id(), request=object())

    assert isinstance(resp, StreamingResponse)
    assert bus.subscribed == 1
