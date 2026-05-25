"""Tests para `RegisterOrderTool` (sesión c4e3416f → upgrade Medusa).

La tool es el cierre formal de la venta. Hoy delega al `OrderRegistrationPort`
(DEHA hexagonal) — el adapter live es `MedusaOrderRegistration` (Medusa v2
Draft Orders) y el fallback es `StubOrderRegistration`.

Estos tests usan un **FakeOrderRegistrationPort** in-memory para validar el
contrato de la tool sin tocar la red ni mockear httpx. Los tests del adapter
Medusa están en `tests/platform/orders/test_medusa_order_registration.py`.

Garantías verificadas:
  1. Path success → persiste `metadata.registered_order` con order_id del port.
  2. Path failure → persiste a `metadata.failed_order_registrations[]` (audit)
     pero NO sobrescribe `registered_order` (preserva éxitos previos).
  3. Historial completo en `registered_orders_history`.
  4. Summary del envelope guía al LLM:
       success → `manage_conversation_tag(COMPRA_EXITOSA)`.
       failure → `escalate_to_human(ORDER_REGISTRATION_FAILED)`.
  5. Sin port inyectado → usa StubOrderRegistration por default (back-compat).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.orders.port import (
    OrderItem,
    OrderRegistrationPort,
    OrderRegistrationResult,
    OrderShipping,
)
from src.platform.orders.stub import StubOrderRegistration
from src.plugins.chats.agent.sales.tools.order_registration import (
    RegisterOrderTool,
)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


@dataclass
class FakeOrderRegistrationPort:
    """In-memory test double for OrderRegistrationPort.

    Lets us drive both success and failure branches deterministically.
    Records every call so tests can assert payload normalization.
    """
    return_success: bool = True
    fixed_order_id: str = "draft_test_001"
    error_detail: str | None = None
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
        self.calls.append({
            "session_key": session_key,
            "items": list(items),
            "shipping": shipping,
            "payment_method": payment_method,
            "subtotal_cop": subtotal_cop,
            "shipping_cop": shipping_cop,
            "total_cop": total_cop,
            "currency": currency,
        })
        if self.return_success:
            return OrderRegistrationResult(
                success=True,
                order_id=self.fixed_order_id,
                provider="medusa",
                customer_id="cus_test_001",
                raw_payload={"id": self.fixed_order_id, "status": "draft"},
                items_resolved=[
                    {
                        "title": "Test Product",
                        "variant_id": "var_test_001",
                        "quantity": items[0].quantity,
                        "unit_price": items[0].unit_price_cop,
                    }
                ],
            )
        return OrderRegistrationResult(
            success=False,
            order_id=None,
            provider="medusa",
            error_detail=self.error_detail or "test forced failure",
        )


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def ctx():
    return ToolContext(
        session_key="wa_test_register",
        channel="whatsapp",
        chat_id="wa_test_register",
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


_SAMPLE_ITEMS = [
    {
        "handle": "cruz-de-vida",
        "quantity": 1,
        "unit_price_cop": 17000,
        "variant_label": "Lavanda",
    }
]

_SAMPLE_SHIPPING = {
    "city": "Bogotá",
    "neighborhood": "Chapinero",
    "address": "Calle 100 #15-20 Apto 502",
    "phone": "3001234567",
}


# ----------------------------------------------------------------------
# Success path
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_order_persists_to_metadata(ctx, vault):
    fake = FakeOrderRegistrationPort(fixed_order_id="draft_lavanda_001")
    tool = RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, port=fake
    )
    result = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="transfer",
            subtotal_cop=17000,
            shipping_cop=0,
            total_cop=17000,
        )
    )
    assert result["registered"] is True
    assert result["order_id"] == "draft_lavanda_001"
    assert result["provider"] == "medusa"

    metadata = _read_metadata(vault, ctx.session_key)
    order = metadata["registered_order"]
    assert order["order_id"] == "draft_lavanda_001"
    assert order["provider"] == "medusa"
    assert order["success"] is True
    assert order["customer_id"] == "cus_test_001"
    assert order["items"] == _SAMPLE_ITEMS
    assert order["shipping"] == _SAMPLE_SHIPPING
    assert order["payment_method"] == "transfer"
    assert order["total_cop"] == 17000
    assert order["currency"] == "COP"
    assert isinstance(order["registered_at_ms"], int)
    # Raw payload del provider queda guardado para auditoria/debug.
    assert order["raw_provider_payload"]["status"] == "draft"


@pytest.mark.asyncio
async def test_register_order_forwards_correct_dtos_to_port(ctx, vault):
    """El port recibe DTOs frozen con los valores correctos — sin perder el
    variant_label, sin convertir a JSON innecesariamente."""
    fake = FakeOrderRegistrationPort()
    tool = RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, port=fake
    )
    await tool.execute_with_context(
        ctx,
        items=_SAMPLE_ITEMS,
        shipping=_SAMPLE_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=0,
        total_cop=17000,
    )
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["session_key"] == ctx.session_key
    assert call["payment_method"] == "transfer"
    assert call["total_cop"] == 17000
    assert call["currency"] == "COP"
    item = call["items"][0]
    assert isinstance(item, OrderItem)
    assert item.handle == "cruz-de-vida"
    assert item.quantity == 1
    assert item.unit_price_cop == 17000
    assert item.variant_label == "Lavanda"
    assert isinstance(call["shipping"], OrderShipping)
    assert call["shipping"].city == "Bogotá"
    assert call["shipping"].neighborhood == "Chapinero"


@pytest.mark.asyncio
async def test_register_order_idempotent_history(ctx, vault):
    """Si el LLM llama 2 veces (caso raro), el último gana pero el historial
    queda para auditoría."""
    fake = FakeOrderRegistrationPort(fixed_order_id="draft_v1")
    tool = RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, port=fake
    )
    r1 = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="card",
            subtotal_cop=17000,
            shipping_cop=0,
            total_cop=17000,
        )
    )
    fake.fixed_order_id = "draft_v2"  # segunda llamada devuelve otro id
    r2 = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="cash_on_delivery",
            subtotal_cop=17000,
            shipping_cop=5000,
            total_cop=22000,
        )
    )
    assert r1["order_id"] != r2["order_id"]

    metadata = _read_metadata(vault, ctx.session_key)
    # registered_order = el último éxito
    assert metadata["registered_order"]["order_id"] == "draft_v2"
    assert metadata["registered_order"]["payment_method"] == "cash_on_delivery"
    # historial preserva ambos
    assert len(metadata["registered_orders_history"]) == 2
    ids_in_history = [h["order_id"] for h in metadata["registered_orders_history"]]
    assert "draft_v1" in ids_in_history
    assert "draft_v2" in ids_in_history


@pytest.mark.asyncio
async def test_register_order_summary_directs_llm_to_next_step(ctx, vault):
    """El summary success debe decirle al LLM qué hacer después (tag
    COMPRA_EXITOSA + despedida) para que la secuencia canónica de cierre
    se respete."""
    fake = FakeOrderRegistrationPort()
    tool = RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, port=fake
    )
    result = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="transfer",
            subtotal_cop=17000,
            shipping_cop=0,
            total_cop=17000,
        )
    )
    summary = result["summary"]
    assert "COMPRA_EXITOSA" in summary
    assert "manage_conversation_tag" in summary


# ----------------------------------------------------------------------
# Failure path — Medusa unavailable
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_order_on_failure_persists_audit_log(ctx, vault):
    """Si el port reporta success=False, NO se sobrescribe registered_order
    pero se persiste un audit log en `failed_order_registrations[]`."""
    fake = FakeOrderRegistrationPort(
        return_success=False,
        error_detail="medusa_api_error: HTTP 503",
    )
    tool = RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, port=fake
    )
    raw = await tool.execute_with_context(
        ctx,
        items=_SAMPLE_ITEMS,
        shipping=_SAMPLE_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=0,
        total_cop=17000,
    )
    result = json.loads(raw)
    assert result["registered"] is False
    assert result["order_id"] is None
    assert "audit_id" in result and result["audit_id"].startswith("AUDIT-")
    assert "HTTP 503" in result["error_detail"]

    metadata = _read_metadata(vault, ctx.session_key)
    # NO se setea registered_order para no fingir éxito.
    assert "registered_order" not in metadata
    # SI hay log de auditoría con datos completos.
    assert len(metadata["failed_order_registrations"]) == 1
    failed = metadata["failed_order_registrations"][0]
    assert failed["success"] is False
    assert failed["error_detail"] == "medusa_api_error: HTTP 503"
    assert failed["items"] == _SAMPLE_ITEMS
    assert failed["shipping"] == _SAMPLE_SHIPPING


@pytest.mark.asyncio
async def test_register_order_on_failure_directs_llm_to_escalate(ctx, vault):
    """El summary failure debe instruir al LLM a escalar con la categoría
    correcta — NO debe permitir que el LLM marque COMPRA_EXITOSA."""
    fake = FakeOrderRegistrationPort(
        return_success=False,
        error_detail="medusa_api_error: HTTP 500",
    )
    tool = RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, port=fake
    )
    result = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="transfer",
            subtotal_cop=17000,
            shipping_cop=0,
            total_cop=17000,
        )
    )
    summary = result["summary"]
    assert "escalate_to_human" in summary
    assert "ORDER_REGISTRATION_FAILED" in summary
    # Anti-patrón: el LLM debe ser instruido explícitamente a NO usar
    # `COMPRA_EXITOSA` cuando falla — la venta no está formalmente cerrada.
    # No basta con "no mencionarlo": el prompt incluye "NO uses
    # manage_conversation_tag(COMPRA_EXITOSA)" para evitar que el LLM
    # invente el tag terminal por su cuenta.
    assert "NO uses" in summary
    assert "COMPRA_EXITOSA" in summary  # mencionado en contexto prohibitivo


@pytest.mark.asyncio
async def test_register_order_failure_preserves_previous_success(ctx, vault):
    """Si una venta exitosa previa quedó persistida, un fallo posterior NO
    debe sobrescribirla (idempotency defense)."""
    # Setup: ya hay un registered_order previo de una sesión exitosa.
    metadata_path = vault / ctx.session_key / "metadata.json"
    metadata_path.write_text(
        json.dumps({"registered_order": {"order_id": "draft_previous_OK"}}),
        encoding="utf-8",
    )
    fake = FakeOrderRegistrationPort(return_success=False)
    tool = RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, port=fake
    )
    await tool.execute_with_context(
        ctx,
        items=_SAMPLE_ITEMS,
        shipping=_SAMPLE_SHIPPING,
        payment_method="transfer",
        subtotal_cop=17000,
        shipping_cop=0,
        total_cop=17000,
    )
    metadata = _read_metadata(vault, ctx.session_key)
    # El éxito previo sigue ahí (defense in depth).
    assert metadata["registered_order"]["order_id"] == "draft_previous_OK"
    # Y el intento fallido quedó loggeado aparte.
    assert len(metadata["failed_order_registrations"]) == 1


# ----------------------------------------------------------------------
# Robustness
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_order_handles_corrupted_metadata_gracefully(
    ctx, vault
):
    """Si metadata.json está corrupto, NO debe perder el pedido —
    sobrescribe con el order registrado."""
    (vault / ctx.session_key / "metadata.json").write_text(
        "{ corrupt no comma }", encoding="utf-8"
    )
    fake = FakeOrderRegistrationPort(fixed_order_id="draft_resilient")
    tool = RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, port=fake
    )
    result = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="card",
            subtotal_cop=17000,
            shipping_cop=0,
            total_cop=17000,
        )
    )
    assert result["registered"] is True
    metadata = _read_metadata(vault, ctx.session_key)
    assert metadata["registered_order"]["order_id"] == "draft_resilient"


@pytest.mark.asyncio
async def test_register_order_default_port_is_stub(ctx, vault):
    """Back-compat: si nadie inyecta port, la tool usa StubOrderRegistration
    (NO rompe los tests legacy que solo pasan workspace/vault_dir)."""
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault)
    # El default debe ser StubOrderRegistration (sin necesidad de Medusa).
    assert isinstance(tool._port, StubOrderRegistration)

    result = json.loads(
        await tool.execute_with_context(
            ctx,
            items=_SAMPLE_ITEMS,
            shipping=_SAMPLE_SHIPPING,
            payment_method="transfer",
            subtotal_cop=17000,
            shipping_cop=0,
            total_cop=17000,
        )
    )
    # Stub siempre devuelve success=True con id local HUB-*.
    assert result["registered"] is True
    assert result["provider"] == "stub"
    assert result["order_id"].startswith(f"HUB-{ctx.session_key}-")


def test_register_order_port_protocol_runtime_check():
    """Sanity: FakeOrderRegistrationPort + StubOrderRegistration cumplen el
    Protocol (verificado por `runtime_checkable`)."""
    assert isinstance(FakeOrderRegistrationPort(), OrderRegistrationPort)
    assert isinstance(StubOrderRegistration(), OrderRegistrationPort)
