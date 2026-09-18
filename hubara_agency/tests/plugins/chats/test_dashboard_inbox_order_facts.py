"""El inbox resuelve el estado de los pedidos en UNA lectura de OrderFacts.

Wiring de los dos endpoints (lista y detalle) con la capa canónica: el
`metadata.json` aporta el candidato y el estado real lo pone Orders. Con N
chats esperando verificación de pago se hace UNA sola consulta, no N.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.chats.api.dashboard as dashboard
from src.platform.orders.facts import OrderFacts, InMemoryOrderFacts


def _fact(order_id: str, *, pay: str) -> OrderFacts:
    return OrderFacts(
        order_id=order_id, display_id="#1", total_cop=50000, currency_code="cop",
        pay_status=pay, stage="preparing", customer="Ana", is_draft=False, created_at_ms=1,
    )


class CountingFacts(InMemoryOrderFacts):
    def __init__(self, facts):
        super().__init__(facts)
        self.calls: list[set[str]] = []

    async def get_facts(self, order_ids):
        ids = set(order_ids)
        self.calls.append(ids)
        return await super().get_facts(ids)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    facts = CountingFacts([_fact("order_31", pay="paid"), _fact("order_32", pay="pending")])
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/api/dashboard")
    monkeypatch.setattr(dashboard, "_resolve_ad_names", lambda ids: {})
    monkeypatch.setattr(
        "src.sdk.connectorkit.get_order_facts_port", lambda: facts, raising=False
    )
    with patch("src.plugins.chats.api.dashboard.WORKSPACE_VAULT_DIR", tmp_path):
        yield TestClient(app), tmp_path, facts


def _seed(vault: Path, sid: str, order_id: str, **over) -> None:
    data = {
        "tag": "HUMANO",
        "active_route": "humano",
        "escalation_reason": "PAYMENT_VERIFICATION_PENDING",
        "registered_order": {"success": True, "order_id": order_id},
        "episodes": [{"episode_id": "ep_1", "order_id": order_id}],
    }
    data.update(over)
    d = vault / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def test_inbox_list_uses_one_read_and_the_real_payment_state(client) -> None:
    c, vault, facts = client
    _seed(vault, "wa_31", "order_31")  # pagado en Medusa → sin botón
    _seed(vault, "wa_32", "order_32")  # sin pagar → con botón
    _seed(vault, "wa_40", "order_40", escalation_reason="OTRA")  # sin botón

    by_id = {s["session_id"]: s for s in c.get("/api/dashboard/sessions").json()["sessions"]}

    assert by_id["wa_31"]["pending_payment_order_id"] is None
    assert by_id["wa_32"]["pending_payment_order_id"] == "order_32"
    assert by_id["wa_40"]["pending_payment_order_id"] is None
    # UNA sola lectura para toda la bandeja. order_40 no es candidato al botón
    # pero sí al chip de orden de la fila, que se resuelve en el mismo batch.
    assert facts.calls == [{"order_31", "order_32", "order_40"}]


def test_session_detail_uses_the_real_payment_state(client) -> None:
    c, vault, facts = client
    _seed(vault, "wa_31", "order_31")
    assert c.get("/api/dashboard/sessions/wa_31").json()["pending_payment_order_id"] is None
    assert facts.calls == [{"order_31"}]


def test_inbox_survives_medusa_down(client, monkeypatch) -> None:
    """Sin Orders, el inbox no rompe: vale la regla vieja por etiquetas."""
    c, vault, _facts = client

    def _boom():
        raise RuntimeError("medusa caído")

    monkeypatch.setattr("src.sdk.connectorkit.get_order_facts_port", _boom, raising=False)
    _seed(vault, "wa_32", "order_32")
    by_id = {s["session_id"]: s for s in c.get("/api/dashboard/sessions").json()["sessions"]}
    assert by_id["wa_32"]["pending_payment_order_id"] == "order_32"
