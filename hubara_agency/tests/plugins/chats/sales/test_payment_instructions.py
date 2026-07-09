"""Datos bancarios DETERMINISTAS para pago por transferencia.

Caso real (sesión wa_573125671604, pedido order_01KX29MMREV14JART3EYR1AAXZ):
tras registrar el pedido con método transferencia, el LLM ALUCINÓ datos
bancarios ("Cuenta: 1234-567890", "NIT: 901.XXX.XXX" — placeholders
inventados, no existen en ningún archivo del sistema) y los mandó con
markdown crudo (`**Banco**`). Contrato nuevo:

  1. `register_order` con `payment_method="transfer"` y `registered=true`
     ENCOLA un intent `payment_instructions` (params solo order_id/total —
     jamás datos bancarios, que NO pasan por el LLM).
  2. El flush renderiza una plantilla FIJA desde env
     (`PAYMENT_TRANSFER_*`) con formato WhatsApp (`*negrita*`, no `**`).
  3. Sin config → NO se envía nada (el sistema jamás inventa datos de
     cuenta) y el intent queda en failures (visible al operador).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.orders.port import (
    OrderItem,
    OrderRegistrationResult,
    OrderShipping,
)
from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    _dispatch_intent,
)
from src.plugins.chats.agent.sales.tools.order_registration import (
    RegisterOrderTool,
)
from src.platform.whatsapp import dtos as wa_dtos


@dataclass
class FakePort:
    return_success: bool = True
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def register_order(
        self,
        *,
        session_key: str,
        items: list[OrderItem],
        shipping: OrderShipping,
        payment_method: str,
        subtotal_cop: int,
        shipping_cop: int,
        total_cop: int,
        currency: str = "COP",
    ) -> OrderRegistrationResult:
        self.calls.append({"payment_method": payment_method})
        if self.return_success:
            return OrderRegistrationResult(
                success=True,
                order_id="order_test_001",
                provider="medusa",
                customer_id="cus_test",
                raw_payload={},
            )
        return OrderRegistrationResult(
            success=False, order_id=None, provider="medusa",
            error_detail="forced",
        )


@pytest.fixture
def ctx():
    return ToolContext(
        session_key="wa_test_payment",
        channel="whatsapp",
        chat_id="wa_test_payment",
    )


@pytest.fixture
def vault(tmp_path, ctx):
    (tmp_path / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (tmp_path / ctx.session_key / "metadata.json").write_text(
        "{}", encoding="utf-8"
    )
    return tmp_path


def _read_metadata(vault, session_key: str) -> dict:
    return json.loads(
        (vault / session_key / "metadata.json").read_text(encoding="utf-8")
    )


_ITEMS = [{"handle": "duo-zodiacal", "quantity": 1, "unit_price_cop": 35000}]
_SHIPPING = {
    "city": "Bogotá",
    "neighborhood": "Candelaria",
    "address": "Calle Falsa 123",
    "phone": "3001234567",
}


async def _register(ctx, vault, *, payment_method: str, port=None) -> dict:
    tool = RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, port=port or FakePort()
    )
    return json.loads(await tool.execute_with_context(
        ctx,
        items=_ITEMS,
        shipping=_SHIPPING,
        payment_method=payment_method,
        subtotal_cop=35000,
        shipping_cop=12000,
        total_cop=47000,
    ))


# ----------------------------------------------------------------------
# 1. La tool encola el intent — solo en transfer + success
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_transfer_success_queues_payment_instructions(ctx, vault):
    envelope = await _register(ctx, vault, payment_method="transfer")
    assert envelope["registered"] is True

    data = _read_metadata(vault, ctx.session_key)
    (intent,) = data["pending_ui_intents"]
    assert intent["kind"] == "payment_instructions"
    assert intent["params"]["order_id"] == "order_test_001"
    assert intent["params"]["total_cop"] == 47000
    # Los datos bancarios NUNCA viajan en el intent (no pasan por el LLM)
    serialized = json.dumps(intent["params"])
    assert "cuenta" not in serialized.lower()
    assert "banco" not in serialized.lower()


@pytest.mark.asyncio
async def test_register_cash_on_delivery_queues_nothing(ctx, vault):
    await _register(ctx, vault, payment_method="cash_on_delivery")
    data = _read_metadata(vault, ctx.session_key)
    assert data.get("pending_ui_intents", []) == []


@pytest.mark.asyncio
async def test_register_transfer_failure_queues_nothing(ctx, vault):
    envelope = await _register(
        ctx, vault, payment_method="transfer", port=FakePort(return_success=False)
    )
    assert envelope["registered"] is False
    data = _read_metadata(vault, ctx.session_key)
    assert data.get("pending_ui_intents", []) == []


# ----------------------------------------------------------------------
# 2. El dispatch renderiza plantilla fija desde env — formato WhatsApp
# ----------------------------------------------------------------------


_ENV = {
    "PAYMENT_TRANSFER_BANK": "Bancolombia",
    "PAYMENT_TRANSFER_ACCOUNT_TYPE": "Cuenta de ahorros",
    "PAYMENT_TRANSFER_ACCOUNT_NUMBER": "123-456789-01",
    "PAYMENT_TRANSFER_HOLDER": "Hubara SAS",
    "PAYMENT_TRANSFER_HOLDER_ID": "NIT 901.234.567-8",
}


def _set_env(monkeypatch, env: dict) -> None:
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


@pytest.mark.asyncio
async def test_dispatch_renders_exact_bank_details_from_env(monkeypatch):
    _set_env(monkeypatch, _ENV)
    wa_client = SimpleNamespace(
        send_text=AsyncMock(
            return_value=SimpleNamespace(ok=True, wa_message_id="wamid.pay.1")
        )
    )
    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="payment_instructions",
        params={"order_id": "order_test_001", "total_cop": 47000, "currency": "COP"},
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )
    assert result is not None and result.ok is True
    wa_client.send_text.assert_awaited_once()
    text = wa_client.send_text.await_args.args[2]
    # Todos los datos vienen VERBATIM de la config
    assert "Bancolombia" in text
    assert "Cuenta de ahorros" in text
    assert "123-456789-01" in text
    assert "Hubara SAS" in text
    assert "NIT 901.234.567-8" in text
    assert "$47.000" in text
    # Formato WhatsApp: bold con UN asterisco, jamás markdown doble
    assert "**" not in text
    assert "*Banco*" in text


@pytest.mark.asyncio
async def test_dispatch_without_config_sends_nothing(monkeypatch):
    """Sin PAYMENT_TRANSFER_* configurado el sistema NO manda nada — jamás
    inventa datos de cuenta (ni siquiera parciales)."""
    _set_env(monkeypatch, {})
    wa_client = SimpleNamespace(send_text=AsyncMock())
    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="payment_instructions",
        params={"order_id": "order_test_001", "total_cop": 47000, "currency": "COP"},
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )
    assert result is None
    wa_client.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_with_partial_config_sends_nothing(monkeypatch):
    """Config a medias (falta el número de cuenta) → tampoco se envía."""
    partial = dict(_ENV)
    del partial["PAYMENT_TRANSFER_ACCOUNT_NUMBER"]
    _set_env(monkeypatch, partial)
    wa_client = SimpleNamespace(send_text=AsyncMock())
    result = await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind="payment_instructions",
        params={"order_id": "order_test_001", "total_cop": 47000, "currency": "COP"},
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )
    assert result is None
    wa_client.send_text.assert_not_awaited()
