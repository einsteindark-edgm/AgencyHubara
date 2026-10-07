"""Avisos push a la App Operador (`chats/api/mobile_push.py` + rutas de `mobile.py`).

El backend despierta los teléfonos cuando aparece un incendio GRAVE que no
había avisado (push urgente) o cambian las ventas calientes del widget (push
normal, espaciado). El push no lleva datos de clientes: solo «ponte al día».
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from src.platform.auth import ACTOR_STATE_KEY
from src.plugins.chats.api import mobile
from src.plugins.chats.api.mobile import MobileDeps
from src.plugins.chats.api.mobile_devices import register_token, registered_tokens, unregister_token
from src.plugins.chats.api.mobile_push import HOT_MIN_GAP_MS, PushDispatcher
from src.sdk.connectorkit import FakePushAdapter, FirebaseClientOptions, InMemoryOrderFacts, NullPushAdapter
from src.sdk.dashboardkit import DashboardEvent
from tests.plugins.chats.test_mobile_api import _MIN, NOW, _Catalog, _Harness, _iso

_OPTIONS = FirebaseClientOptions(
    project_id="proyecto-prueba", application_id="1:000:android:abc", api_key="llave-prueba", gcm_sender_id="000"
)


@pytest.fixture
def h(tmp_path: Path) -> _Harness:
    vault = tmp_path / "vault"
    vault.mkdir()
    deps = MobileDeps(
        vault_dir=vault, catalog=_Catalog(), order_facts=InMemoryOrderFacts(), now_ms=lambda: NOW,
        payment_instructions_text=lambda: None,
    )
    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/chats")
    app.dependency_overrides[mobile.get_mobile_deps] = lambda: deps
    return _Harness(TestClient(app), vault, deps)


@pytest.fixture
def devices(h: _Harness) -> TestClient:
    """Con el actor verificado donde lo deja `require_auth` (de un header de prueba)."""

    def _auth(request: Request) -> None:
        setattr(request.state, ACTOR_STATE_KEY, request.headers.get("x-test-actor", "ana@equipo.test"))

    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/chats", dependencies=[Depends(_auth)])
    app.dependency_overrides[mobile.get_mobile_deps] = lambda: h.deps
    return TestClient(app)


def _grave(fire_id: str) -> dict[str, Any]:
    return {"fire_id": fire_id, "severity": "grave", "kind": "wants_human"}


def _hot(session: str, stage: str = "etapa_cierre", **extra: Any) -> dict[str, Any]:
    return {"session_id": session, "name": "Laura", "stage": stage, "product": "Dúo Zodiacal", "cart_value_cop": 89_900,
            "risk": False, "updated_ms": NOW, **extra}


class _World:
    """Lo que el despachador mira: incendios, ventas calientes y el reloj."""

    def __init__(self) -> None:
        self.fires: list[dict[str, Any]] = []
        self.hot: list[dict[str, Any]] = []
        self.now = NOW
        self.reads = 0

    async def read_fires(self) -> list[dict[str, Any]]:
        self.reads += 1
        return self.fires

    async def read_hot(self) -> list[dict[str, Any]]:
        return self.hot


def _dispatcher(h: _Harness, port: Any, world: _World) -> PushDispatcher:
    return PushDispatcher(port=port, vault_dir=h.vault, fires=world.read_fires, hot=world.read_hot,
                          now_ms=lambda: world.now)


def _reasons(port: FakePushAdapter) -> list[tuple[str, str, bool]]:
    return [(token, m.data["reason"], m.urgent) for token, m in port.sent]


# ── El despachador ───────────────────────────────────────────────────────────


async def test_a_new_grave_fire_wakes_every_registered_phone_with_an_urgent_push(h: _Harness) -> None:
    register_token(h.vault, "ana@equipo.test", "tok-ana", app_version="1.0.0", now_ms=NOW)
    register_token(h.vault, "beto@equipo.test", "tok-beto", app_version="1.0.0", now_ms=NOW)
    port, world = FakePushAdapter(), _World()
    world.fires = [_grave("chat:wa_test_sofia"), {"fire_id": "order:o1", "severity": "hoy"}]

    await _dispatcher(h, port, world).tick()

    assert sorted(_reasons(port)) == [("tok-ana", "fire", True), ("tok-beto", "fire", True)]
    token, message = port.sent[0]
    # Solo qué hacer: ni nombres, ni mensajes, ni teléfonos (el push pasa por Google).
    assert dict(message.data) == {"type": "sync", "reason": "fire"}
    assert message.collapse_key == "fires"


async def test_the_same_grave_fire_is_pushed_once_and_again_only_if_it_comes_back(h: _Harness) -> None:
    register_token(h.vault, "ana@equipo.test", "tok-ana", app_version="1.0.0", now_ms=NOW)
    port, world = FakePushAdapter(), _World()
    dispatcher = _dispatcher(h, port, world)
    world.fires = [_grave("chat:wa_test_sofia")]

    await dispatcher.tick()
    await dispatcher.tick()
    assert len(port.sent) == 1

    world.fires = []
    await dispatcher.tick()
    world.fires = [_grave("chat:wa_test_sofia")]
    await dispatcher.tick()
    assert len(port.sent) == 2


async def test_fires_that_can_wait_wake_nobody(h: _Harness) -> None:
    register_token(h.vault, "ana@equipo.test", "tok-ana", app_version="1.0.0", now_ms=NOW)
    port, world = FakePushAdapter(), _World()
    world.fires = [{"fire_id": "chat:wa_test_x", "severity": "hoy"}, {"fire_id": "chat:wa_test_y", "severity": "espera"}]

    await _dispatcher(h, port, world).tick()

    assert port.sent == []


async def test_a_change_in_hot_sales_refreshes_the_widget_with_a_normal_push_at_most_every_two_minutes(
    h: _Harness,
) -> None:
    register_token(h.vault, "ana@equipo.test", "tok-ana", app_version="1.0.0", now_ms=NOW)
    port, world = FakePushAdapter(), _World()
    dispatcher = _dispatcher(h, port, world)
    world.hot = [_hot("wa_test_laura")]

    await dispatcher.tick()
    assert _reasons(port) == [("tok-ana", "hot", False)]
    assert port.sent[0][1].collapse_key == "hot"

    # Solo pasó el tiempo («hace 5 min»): el widget no cambia de verdad.
    world.hot = [_hot("wa_test_laura", updated_ms=NOW + 5 * _MIN)]
    await dispatcher.tick()
    assert len(port.sent) == 1

    # Cambió (otra venta), pero hace menos de dos minutos del último: espera…
    world.now += _MIN
    world.hot = [_hot("wa_test_laura"), _hot("wa_test_camilo")]
    await dispatcher.tick()
    assert len(port.sent) == 1
    assert dispatcher.seconds_until_due() == HOT_MIN_GAP_MS / 1000 - 60

    # …y sale apenas se cumple el plazo, aunque no haya otro cambio.
    world.now += HOT_MIN_GAP_MS
    await dispatcher.tick()
    assert _reasons(port)[-1] == ("tok-ana", "hot", False)


async def test_without_registered_phones_it_does_not_even_read_the_fires(h: _Harness) -> None:
    port, world = FakePushAdapter(), _World()
    world.fires = [_grave("chat:wa_test_sofia")]

    await _dispatcher(h, port, world).tick()

    assert world.reads == 0
    assert port.sent == []


async def test_a_phone_that_signs_up_later_is_woken_for_the_graves_already_open(h: _Harness) -> None:
    port, world = FakePushAdapter(), _World()
    dispatcher = _dispatcher(h, port, world)
    world.fires = [_grave("chat:wa_test_sofia")]
    register_token(h.vault, "ana@equipo.test", "tok-ana", app_version="1.0.0", now_ms=NOW)
    await dispatcher.tick()

    # Ana cerró sesión: nadie a quien avisar. Cuando un teléfono vuelve a registrarse, lo abierto le llega.
    unregister_token(h.vault, "ana@equipo.test", "tok-ana")
    await dispatcher.tick()
    register_token(h.vault, "beto@equipo.test", "tok-beto", app_version="1.0.0", now_ms=NOW)
    await dispatcher.tick()

    assert _reasons(port) == [("tok-ana", "fire", True), ("tok-beto", "fire", True)]


async def test_a_token_that_google_no_longer_knows_is_forgotten(h: _Harness) -> None:
    register_token(h.vault, "ana@equipo.test", "tok-viejo", app_version="1.0.0", now_ms=NOW)
    register_token(h.vault, "ana@equipo.test", "tok-nuevo", app_version="1.0.0", now_ms=NOW)
    port, world = FakePushAdapter(dead_tokens={"tok-viejo"}), _World()
    world.fires = [_grave("chat:wa_test_sofia")]

    await _dispatcher(h, port, world).tick()

    assert registered_tokens(h.vault) == ["tok-nuevo"]


def test_only_chat_and_order_changes_wake_the_dispatcher(h: _Harness) -> None:
    dispatcher = _dispatcher(h, FakePushAdapter(), _World())
    dispatcher.on_event(DashboardEvent(domain="eta", type="changed"))
    assert not dispatcher.woken
    dispatcher.on_event(DashboardEvent(domain="chats", type="session_updated", id="wa_test_sofia"))
    assert dispatcher.woken


def test_it_looks_again_every_minute_because_a_chat_turns_grave_just_by_waiting(h: _Harness) -> None:
    assert _dispatcher(h, FakePushAdapter(), _World()).seconds_until_due() == 60


async def test_the_real_fires_of_the_vault_reach_the_push(h: _Harness) -> None:
    # Sofía pidió un humano y lleva rato escribiendo: es un incendio grave de verdad (las reglas de la bandeja).
    h.seed("wa_test_sofia", {
        "active_route": "humano", "escalation_reason": "EXPLICIT_REQUEST", "profile": {"name": "Sofía Pérez"},
        "last_inbound_at_ms": NOW - 5 * _MIN,
    }, events=[{"role": "user", "content": "¿hola?", "timestamp": _iso(NOW - m * _MIN)} for m in (12, 8, 5)])
    register_token(h.vault, "ana@equipo.test", "tok-ana", app_version="1.0.0", now_ms=NOW)
    port = FakePushAdapter()

    await mobile.push_dispatcher(h.deps, port).tick()

    assert _reasons(port) == [("tok-ana", "fire", True)]


# ── Lo que pide la app ───────────────────────────────────────────────────────


def test_the_phone_gets_the_firebase_options_only_when_the_server_can_push(h: _Harness) -> None:
    h.deps.push = FakePushAdapter(options=_OPTIONS)
    assert h.client.get("/api/chats/mobile/push").json() == {"enabled": True, "firebase": {
        "project_id": "proyecto-prueba", "application_id": "1:000:android:abc", "api_key": "llave-prueba",
        "gcm_sender_id": "000",
    }}

    h.deps.push = NullPushAdapter()
    assert h.client.get("/api/chats/mobile/push").json() == {"enabled": False}
    h.deps.push = None
    assert h.client.get("/api/chats/mobile/push").json() == {"enabled": False}


def test_probar_avisos_sends_an_urgent_test_push_to_the_phones_of_who_asks(
    h: _Harness, devices: TestClient
) -> None:
    port = FakePushAdapter()
    h.deps.push = port
    for actor, token in (("ana@equipo.test", "tok-ana"), ("beto@equipo.test", "tok-beto")):
        register_token(h.vault, actor, token, app_version="1.0.0", now_ms=NOW)

    r = devices.post("/api/chats/mobile/devices/test")

    assert r.status_code == 200, r.text
    assert r.json() == {"sent": 1}
    [(token, message)] = port.sent
    assert (token, dict(message.data), message.urgent) == ("tok-ana", {"type": "test"}, True)


def test_probar_avisos_says_what_is_missing(h: _Harness, devices: TestClient) -> None:
    h.deps.push = NullPushAdapter()
    r = devices.post("/api/chats/mobile/devices/test")
    assert r.status_code == 503
    assert "Firebase" in r.json()["detail"]

    h.deps.push = FakePushAdapter()
    r = devices.post("/api/chats/mobile/devices/test")
    assert r.status_code == 409
    assert "registrad" in r.json()["detail"]

    register_token(h.vault, "ana@equipo.test", "tok-ana", app_version="1.0.0", now_ms=NOW)
    h.deps.push = FakePushAdapter(fail=True)
    r = devices.post("/api/chats/mobile/devices/test")
    assert r.status_code == 502
    assert json.loads(r.text)["detail"]
