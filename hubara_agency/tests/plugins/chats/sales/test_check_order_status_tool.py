"""Tests de `CheckOrderStatusTool` (convivencia ETA/Sales, 2026-06-10).

El agente ETA es notificador puro: cuando el cliente pregunta "¿cuándo llega
mi pedido?", responde SALES con esta tool. Lee `metadata.eta_tracking` del
vault (estado compartido que mantiene el notificador) — shape v2 multi-pedido
con compat v1, y degrada limpio sin tracking.

Foco en COMPORTAMIENTO (gotcha #1): la tool devuelve los datos que el LLM
necesita para responder (status humano + last_update + order_id por pedido),
no solo un JSON válido.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.order_status import CheckOrderStatusTool

SID = "wa_573009998877"


def _ctx() -> ToolContext:
    return ToolContext(session_key=SID, channel="whatsapp", chat_id=SID)


def _tool(vault: Path, query_port=None) -> CheckOrderStatusTool:
    return CheckOrderStatusTool(
        workspace="/tmp/ws-unused", vault_dir=vault, query_port=query_port
    )


class _FakeQueryPort:
    """OrderQueryPort in-memory: order_id → summary attrs (None = no existe)."""

    def __init__(self, orders: dict[str, dict | None]) -> None:
        self._orders = orders
        self.gets: list[str] = []

    async def get(self, order_id: str):
        self.gets.append(order_id)
        summary = self._orders.get(order_id)
        if summary is None:
            return None
        return SimpleNamespace(summary=SimpleNamespace(**summary))


class _BrokenQueryPort:
    async def get(self, order_id: str):
        raise TimeoutError("Medusa en Railway tardó demasiado (L-2)")


def _write_meta(vault: Path, data: dict) -> None:
    d = vault / SID
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path / "vault"


async def test_no_tracking_degrades_clean(vault: Path) -> None:
    _write_meta(vault, {"active_route": "ventas"})
    out = json.loads(await _tool(vault).execute_with_context(_ctx()))
    assert out["orders"] == []
    assert "no tiene pedidos" in out["note"]


async def test_orden_registrada_sin_tracking_aparece_con_pay_status(
    vault: Path,
) -> None:
    """El caso post-compra devuelta al bot: la orden existe (episodes[].order_id
    / registered_order) pero eta_tracking aún no (la orden no fue agendada).
    Antes la tool decía "no tiene pedidos" — debe listarla, y con el port
    inyectado enriquecer con la etapa y el pay_status REALES (el guion nunca
    afirma 'pago confirmado' si pay_status != paid)."""
    _write_meta(
        vault,
        {
            "tag": "RETOMA_VENTA",
            "registered_order": {"order_id": "order_A"},
            "episodes": [
                {"id": "ep_001", "closed_at_ms": 2, "order_id": "order_A"}
            ],
        },
    )
    port = _FakeQueryPort(
        {
            "order_A": {
                "status": "preparing",
                "pay_status": "paid",
                "display_id": "#12",
                "total_cop": 45000,
            }
        }
    )
    out = json.loads(await _tool(vault, port).execute_with_context(_ctx()))
    assert port.gets == ["order_A"]
    assert len(out["orders"]) == 1
    order = out["orders"][0]
    assert order["order_id"] == "order_A"
    assert order["status"] == "en preparación"
    assert order["pay_status"] == "paid"
    assert order["display_id"] == "#12"


async def test_port_roto_degrada_a_datos_locales(vault: Path) -> None:
    """L-2: Medusa en Railway puede tardar/fallar — la tool nunca revienta;
    responde con lo local (eta_tracking) y marca el estado en vivo como no
    disponible."""
    _write_meta(
        vault,
        {
            "eta_tracking": {
                "orders": {
                    "order_A": {
                        "order_id": "order_A",
                        "current_stage": "shipping",
                        "notified_stages": ["shipping"],
                        "events": [{"at_ms": 1_752_000_000_000}],
                    }
                }
            },
            "episodes": [{"id": "ep_001", "closed_at_ms": 2, "order_id": "order_A"}],
        },
    )
    out = json.loads(
        await _tool(vault, _BrokenQueryPort()).execute_with_context(_ctx())
    )
    assert len(out["orders"]) == 1
    order = out["orders"][0]
    assert order["order_id"] == "order_A"
    assert order["status"] == "en camino"  # lo local sobrevive
    assert order.get("pay_status") is None  # sin dato en vivo, no se inventa


async def test_multi_order_v2_lists_all(vault: Path) -> None:
    _write_meta(
        vault,
        {"eta_tracking": {"orders": {
            "order_A": {
                "order_id": "order_A", "current_stage": "shipping",
                "notified_stages": ["preparing", "ready", "shipping"],
                "events": [{"stage": "shipping", "at_ms": 1_700_000_000_000}],
            },
            "order_B": {
                "order_id": "order_B", "current_stage": "preparing",
                "notified_stages": ["preparing"], "events": [],
                "started_at_ms": 1_700_000_100_000,
            },
        }}},
    )
    out = json.loads(await _tool(vault).execute_with_context(_ctx()))
    by_id = {o["order_id"]: o for o in out["orders"]}
    assert set(by_id) == {"order_A", "order_B"}
    assert by_id["order_A"]["status"] == "en camino"
    assert by_id["order_A"]["last_update"] is not None
    assert by_id["order_B"]["status"] == "en preparación"


async def test_legacy_v1_shape_supported(vault: Path) -> None:
    _write_meta(
        vault,
        {"eta_tracking": {"order_id": "order_OLD", "current_stage": "delivered",
                          "notified_stages": ["delivered"], "events": []}},
    )
    out = json.loads(await _tool(vault).execute_with_context(_ctx()))
    assert len(out["orders"]) == 1
    assert out["orders"][0]["order_id"] == "order_OLD"
    assert out["orders"][0]["status"] == "entregado"


async def test_missing_metadata_file_degrades_clean(vault: Path) -> None:
    out = json.loads(await _tool(vault).execute_with_context(_ctx()))
    assert out["orders"] == []
