"""Guard del id de sesión que llega por URL a los endpoints de orders que tocan el vault.

Hallazgo de endurecimiento (lectura de código, 2026-09-18; NO explotado, todo
detrás de ``require_auth``). Misma clase que el de chats
(``tests/plugins/chats/test_dashboard_session_id_guard.py``):

* ``POST /vault-orders/{session_key}/{audit_id}/retry|resolve`` pasaban
  ``session_key`` SIN validar a ``reconcile_one`` / ``mark_resolved_manually``
  (platform), que arman ``<vault>/<session_key>/metadata.json``. Con ``%2E%2E``
  el archivo es el del PADRE del vault: ``retry`` re-registraba en Medusa un
  pedido leído de ahí y ambos lo REESCRIBÍAN.
* ``GET /orders/by-session/{session_id}`` aceptaba ``.`` (el vault mismo) y
  directorios que no son sesión (``_analytics``).

``audit_id`` NO es vector: solo se compara contra ``order_id`` dentro del JSON,
nunca arma una ruta.

Qué llega de verdad al handler por HTTP: ``%2E%2E`` → ``..``, ``%2E`` → ``.``,
``wa_1%0A`` → ``wa_1\\n``; las variantes con ``/`` las corta el router (404) y
el ``..`` crudo lo normaliza el cliente. Por eso además se llama al handler
directo con strings arbitrarios (defensa en profundidad).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import src.plugins.orders.api as orders_api
from src.platform.orders.port import OrderRegistrationResult


@dataclass
class FakeRegistrationPort:
    calls: list = field(default_factory=list)

    async def register_order(self, **kwargs) -> OrderRegistrationResult:
        self.calls.append(kwargs)
        return OrderRegistrationResult(
            success=True, order_id="draft_NEW", provider="medusa"
        )


class FakeQueryPort:
    async def get(self, order_id: str):
        return None


def _pending_order(order_id: str = "AUDIT-1") -> dict:
    return {
        "order_id": order_id, "provider": "medusa", "success": False,
        "items": [{"handle": "vela", "quantity": 1, "unit_price_cop": 10000}],
        "shipping": {"city": "Bogotá", "neighborhood": "Centro",
                     "address": "Calle 1", "phone": "+15550001111"},
        "payment_method": "transfer", "subtotal_cop": 10000, "shipping_cop": 0,
        "total_cop": 10000, "currency": "COP",
        "registered_at_ms": 1779000000000, "status": "pending",
    }


def _write_metadata(directory: Path, data: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "metadata.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Vault como SUBDIRECTORIO de tmp_path: `..` tiene un padre controlado."""
    vault = tmp_path / "vault"
    vault.mkdir()
    port = FakeRegistrationPort()
    monkeypatch.setattr(orders_api, "WORKSPACE_VAULT_DIR", vault)
    monkeypatch.setattr(orders_api, "get_order_registration_port", lambda: port)
    monkeypatch.setattr(orders_api, "get_order_query_port", lambda: FakeQueryPort())
    app = FastAPI()
    app.include_router(orders_api.router, prefix="/api/orders")
    return TestClient(app), vault, port


# ── El hallazgo, reproducido ─────────────────────────────────────────────────


def test_retry_with_dotdot_never_touches_the_vault_parent(harness):
    client, vault, port = harness
    outside = _write_metadata(
        vault.parent, {"failed_order_registrations": [_pending_order()]}
    )
    before = outside.read_text(encoding="utf-8")

    resp = client.post("/api/orders/vault-orders/%2E%2E/AUDIT-1/retry")

    assert resp.status_code == 400
    assert port.calls == []  # nada de afuera del vault se registra en Medusa
    assert outside.read_text(encoding="utf-8") == before


def test_resolve_with_dotdot_never_rewrites_the_vault_parent(harness):
    client, vault, _port = harness
    outside = _write_metadata(
        vault.parent, {"failed_order_registrations": [_pending_order()]}
    )
    before = outside.read_text(encoding="utf-8")

    resp = client.post(
        "/api/orders/vault-orders/%2E%2E/AUDIT-1/resolve", json={"note": "x"}
    )

    assert resp.status_code == 400
    assert outside.read_text(encoding="utf-8") == before


# ── Lo que llega por HTTP ────────────────────────────────────────────────────

_REACHABLE = [
    "%2E%2E",  # `..`  → padre del vault
    "%2E",  # `.`   → el vault mismo
    "wa_1%0A",  # `wa_1\n`
    "_analytics",  # dir real del vault que no es sesión
]


@pytest.mark.parametrize("encoded", _REACHABLE)
@pytest.mark.parametrize("action", ["retry", "resolve"])
def test_vault_orders_reject_invalid_session_key(harness, action, encoded):
    client, _vault, port = harness

    resp = client.post(f"/api/orders/vault-orders/{encoded}/AUDIT-1/{action}")

    assert resp.status_code == 400
    assert port.calls == []


@pytest.mark.parametrize("encoded", _REACHABLE)
def test_by_session_rejects_invalid_session_id(harness, encoded):
    client, vault, _port = harness
    (vault / "_analytics").mkdir()

    resp = client.get(f"/api/orders/orders/by-session/{encoded}")

    assert resp.status_code == 400


# ── Los handlers, sin el router delante (defensa en profundidad) ─────────────

_INVALID = [
    "..",
    "../x",
    "wa_1/../../etc",
    "",
    "wa_1\n",
    ".",
    "_analytics",
    "wa_",  # prefijo sin cuerpo
    "wa_a.b",  # el charset no puede ni expresar `..`
    "wa_" + "1" * 300,  # segmento de path desmedido
]


async def _call(handler: str, session_id: str):
    if handler == "retry":
        return await orders_api.retry_vault_order(
            session_key=session_id, audit_id="AUDIT-1"
        )
    if handler == "resolve":
        return await orders_api.resolve_vault_order(
            session_key=session_id,
            audit_id="AUDIT-1",
            note=None,
            resolved_order_id=None,
        )
    return await orders_api.list_orders_by_session(session_id=session_id)


@pytest.mark.parametrize("session_id", _INVALID)
@pytest.mark.parametrize("handler", ["retry", "resolve", "by-session"])
async def test_handlers_reject_invalid_session_id_with_400(
    harness, handler, session_id
):
    with pytest.raises(HTTPException) as exc:
        await _call(handler, session_id)

    assert exc.value.status_code == 400


# ── Las formas reales siguen funcionando ─────────────────────────────────────


@pytest.mark.parametrize(
    "session_id",
    [
        "wa_15550001111",  # prod: `wa_` + el `from` de Meta (solo dígitos)
        "wa_+15550001111",  # seeds del vault local: prefijo E.164 con `+`
        "wa_test_enum",  # sesiones de test con guion bajo
    ],
)
def test_valid_session_ids_still_work(harness, session_id):
    client, vault, port = harness
    _write_metadata(
        vault / session_id, {"failed_order_registrations": [_pending_order()]}
    )

    by_session = client.get(f"/api/orders/orders/by-session/{session_id}")
    retry = client.post(f"/api/orders/vault-orders/{session_id}/AUDIT-1/retry")

    assert by_session.status_code == 200
    assert retry.status_code == 200
    assert retry.json()["outcome"] == "resolved"
    assert len(port.calls) == 1


# ── El session_key que viene de Medusa también es dato de AFUERA ─────────────


class FakeMedusa:
    def __init__(self, session_key: str) -> None:
        self._session_key = session_key

    async def get_order(self, order_id: str, *, fields: str | None = None) -> dict:
        return {"id": order_id, "metadata": {"session_key": self._session_key}}


async def test_session_key_from_medusa_cannot_point_outside_the_vault(
    harness, monkeypatch
):
    """`order.metadata.session_key` se edita en Medusa Admin: con una sesión
    real delante, `/../../x` pasaba el `startswith("wa_")` y resolvía fuera."""
    _client, vault, _port = harness
    _write_metadata(vault / "wa_15550001111", {})
    _write_metadata(vault.parent / "x", {"customer_name": "canario"})
    monkeypatch.setattr(
        orders_api, "get_medusa_client", lambda: FakeMedusa("wa_15550001111/../../x")
    )

    resolved = await orders_api._resolve_session_for_order(
        backend_order_id="order_1", shipping_phone=None, vault_dir=vault
    )

    assert resolved is None


async def test_session_key_from_medusa_still_resolves_a_real_session(
    harness, monkeypatch
):
    _client, vault, _port = harness
    _write_metadata(vault / "wa_+15550001111", {})
    monkeypatch.setattr(
        orders_api, "get_medusa_client", lambda: FakeMedusa("wa_+15550001111")
    )

    resolved = await orders_api._resolve_session_for_order(
        backend_order_id="order_1", shipping_phone=None, vault_dir=vault
    )

    assert resolved == "wa_+15550001111"


# ── La foto del pedido (#311) también recibe el id de sesión por URL ─────────


@pytest.mark.parametrize("encoded", ["%2E", "wa_1%0A"])
def test_order_photo_file_is_never_served_from_outside_a_session(harness, encoded):
    """`GET /order-photos/{session_id}/{backend_id}` valida con `is_safe_segment`:
    antes de este PR aceptaba `.` (la RAÍZ del vault) y el salto de línea final,
    así que servía la foto que dijera un `metadata.json` que no es de ninguna
    sesión."""
    client, vault, _port = harness
    session_dir = vault / ("." if encoded == "%2E" else "wa_1\n")
    _write_metadata(
        session_dir, {"order_photos": {"order_1": {"filename": "x.jpg", "mime": "image/jpeg"}}}
    )
    (session_dir / "media").mkdir()
    (session_dir / "media" / "x.jpg").write_bytes(b"\xff\xd8\xff" + b"CANARIO")

    resp = client.get(f"/api/orders/order-photos/{encoded}/order_1")

    assert resp.status_code == 404
    assert b"CANARIO" not in resp.content
