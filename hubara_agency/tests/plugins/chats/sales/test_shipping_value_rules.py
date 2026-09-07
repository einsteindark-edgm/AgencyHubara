"""Reglas para el VALOR DEL ENVÍO (requisito del operador 2026-09-07).

1. Si el cliente pregunta cuánto vale el envío, la respuesta es UN mensaje
   estándar (tarifas mínimas Bogotá / nacional + "el valor definitivo se
   confirma al despachar"). Lo manda el sistema de forma determinista vía la
   tool `send_shipping_rates` → intent `shipping_rates` → texto FIJO.
2. El "Resumen de tu pedido" (`present_order_confirmation`) con CONTRA
   ENTREGA no da el valor del envío ni un total que lo incluya: muestra el
   subtotal de productos, "Envío: Por confirmar*", dirección, medio de pago
   y la nota de que el valor final se recalcula con la transportadora antes
   de despachar (aclaración del operador: la nota es SOLO contra entrega).
3. Con pago anticipado o link de pago el envío se cobra por adelantado: el
   resumen muestra el envío aclarando que es TARIFA MÍNIMA, más el total —
   coherente con el mensaje de datos de pago (#236).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.platform.whatsapp import dtos as wa_dtos
from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    _build_history_event,
    _dispatch_intent,
)
from src.plugins.chats.agent.sales.config.shipping import (
    ORDER_SUMMARY_SHIPPING_NOTE,
    SHIPPING_RATES_MESSAGE,
)


def _wa_client() -> SimpleNamespace:
    return SimpleNamespace(
        send_text=AsyncMock(return_value=SimpleNamespace(ok=True)),
        send_interactive_buttons=AsyncMock(
            return_value=SimpleNamespace(ok=True)
        ),
    )


async def _dispatch(wa_client, kind: str, params: dict):
    return await _dispatch_intent(
        wa_client=wa_client,
        wa_dtos=wa_dtos,
        kind=kind,
        params=params,
        fallback={},
        phone_number_id="phone-1",
        to_number="573000000000",
        last_inbound_message_id=None,
    )


# ---------------------------------------------------------------------------
# Regla 2 — resumen del pedido sin valor de envío
# ---------------------------------------------------------------------------

_ORDER_PARAMS = {
    "reference_id": "HUB-hubara-s1-1",
    "items": [
        {"title": "Velón Amor Eterno", "quantity": 1, "unit_price_cop": 38500},
        {"title": "Encanto Silvestre", "quantity": 1, "unit_price_cop": 38000},
        {"title": "Sagrado Rostro", "quantity": 1, "unit_price_cop": 36000},
    ],
    "subtotal_cop": 112500,
    "shipping_cop": 16940,
    "tax_cop": 0,
    "total_cop": 129440,
    "currency": "COP",
    "shipping_address_summary": "Calle 59b sur 38, Poblado, Medellín",
    "payment_method": "cash_on_delivery",
}


async def _order_summary_body() -> str:
    wa_client = _wa_client()
    await _dispatch(wa_client, "order_confirmation", dict(_ORDER_PARAMS))
    wa_client.send_interactive_buttons.assert_awaited_once()
    outbound = wa_client.send_interactive_buttons.await_args.args[2]
    return outbound.body


@pytest.mark.asyncio
async def test_order_summary_matches_operator_format():
    body = await _order_summary_body()
    expected = "\n".join([
        "*Resumen de tu pedido*",
        "• 1× Velón Amor Eterno — $38.500",
        "• 1× Encanto Silvestre — $38.000",
        "• 1× Sagrado Rostro — $36.000",
        "",
        "Subtotal productos: $112.500 COP",
        "",
        "Envío: Por confirmar*",
        "",
        "📍 Dirección: Calle 59b sur 38, Poblado, Medellín",
        "",
        "💳 Medio de pago: Contra entrega",
        "",
        ORDER_SUMMARY_SHIPPING_NOTE,
    ])
    assert body == expected


@pytest.mark.asyncio
async def test_order_summary_never_states_shipping_value_nor_total():
    """Contra entrega: el valor del envío (aunque el LLM lo pasó como 16.940)
    y el total que lo incluye NO se le dan al cliente: se recalcula con la
    transportadora al despachar."""
    body = await _order_summary_body()
    assert "16.940" not in body
    assert "129.440" not in body
    assert "Total" not in body
    assert "transportadora" in body


@pytest.mark.asyncio
async def test_order_summary_shipping_pending_even_when_llm_passes_zero():
    """`shipping_cop=0` (el LLM asumió envío gratis) no cambia la regla: el
    envío siempre queda por confirmar — no existe envío sin costo."""
    wa_client = _wa_client()
    await _dispatch(
        wa_client,
        "order_confirmation",
        {**_ORDER_PARAMS, "shipping_cop": 0, "total_cop": 112500},
    )
    body = wa_client.send_interactive_buttons.await_args.args[2].body
    assert "Envío: Por confirmar*" in body
    assert "sin costo" not in body.lower()


# --- Pago anticipado / link de pago: el envío se cobra por adelantado -------

_PREPAID_EXPECTED_TAIL = [
    "",
    "Subtotal productos: $112.500 COP",
    "Envío (tarifa mínima): $16.940 COP",
    "Total: $129.440 COP",
    "",
    "📍 Dirección: Calle 59b sur 38, Poblado, Medellín",
    "",
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "label"),
    [("transfer", "Pago anticipado (Nequi)"), ("payment_link", "Link de pago")],
)
async def test_order_summary_prepaid_shows_minimum_shipping_and_total(
    method: str, label: str
):
    """Pago anticipado / link: el cliente paga el envío ahora, así que el
    resumen muestra el envío ACLARANDO que es tarifa mínima, y el total. Sin
    "Por confirmar" ni la nota 📌 (esas son solo contra entrega)."""
    wa_client = _wa_client()
    await _dispatch(
        wa_client, "order_confirmation", {**_ORDER_PARAMS, "payment_method": method}
    )
    body = wa_client.send_interactive_buttons.await_args.args[2].body
    expected = "\n".join([
        "*Resumen de tu pedido*",
        "• 1× Velón Amor Eterno — $38.500",
        "• 1× Encanto Silvestre — $38.000",
        "• 1× Sagrado Rostro — $36.000",
        *_PREPAID_EXPECTED_TAIL,
        f"💳 Medio de pago: {label}",
    ])
    assert body == expected
    assert "Por confirmar" not in body
    assert ORDER_SUMMARY_SHIPPING_NOTE not in body


@pytest.mark.asyncio
async def test_order_summary_prepaid_zero_shipping_says_no_cost():
    """Mismo criterio que payment_instructions (#236): envío 0 = "sin costo",
    nunca se inventa un reparto."""
    wa_client = _wa_client()
    await _dispatch(
        wa_client,
        "order_confirmation",
        {**_ORDER_PARAMS, "payment_method": "transfer", "shipping_cop": 0,
         "total_cop": 112500},
    )
    body = wa_client.send_interactive_buttons.await_args.args[2].body
    assert "Envío: sin costo" in body
    assert "Total: $112.500 COP" in body


@pytest.mark.asyncio
async def test_order_summary_fits_whatsapp_button_body_limit():
    """La nota del envío suma texto: el body del interactive.button tiene un
    tope de 1024 chars (Meta rechaza el mensaje si lo pasa)."""
    body = await _order_summary_body()
    assert len(body) <= 1024


# ---------------------------------------------------------------------------
# Regla 1 — mensaje estándar de tarifas de envío
# ---------------------------------------------------------------------------


def test_shipping_rates_message_is_the_operator_standard_text():
    assert SHIPPING_RATES_MESSAGE == (
        "Nuestras tarifas mínimas de envío son 🚚:\n"
        "• Bogotá y municipios cercanos: $7.900\n"
        "• Nivel Nacional: $16.940\n"
        "El valor definitivo se confirma al despachar según el tamaño y "
        "peso de tu paquete📏📦📦"
    )


@pytest.mark.asyncio
async def test_shipping_rates_intent_sends_exact_standard_text():
    wa_client = _wa_client()
    result = await _dispatch(wa_client, "shipping_rates", {})
    assert result is not None and result.ok
    wa_client.send_text.assert_awaited_once_with(
        "phone-1", "573000000000", SHIPPING_RATES_MESSAGE
    )


@pytest.mark.asyncio
async def test_shipping_rates_ignores_llm_params():
    """El texto es FIJO: cualquier param que el LLM cuele no lo altera."""
    wa_client = _wa_client()
    await _dispatch(
        wa_client, "shipping_rates", {"text": "El envío vale $5.000"}
    )
    sent = wa_client.send_text.await_args.args[2]
    assert sent == SHIPPING_RATES_MESSAGE


def test_shipping_rates_history_event_is_the_real_text():
    """El operador ve en el dashboard exactamente lo que recibió el cliente
    (mismo criterio que payment_instructions)."""
    ev = _build_history_event("shipping_rates", {})
    assert ev == {"role": "assistant", "content": SHIPPING_RATES_MESSAGE}


# ---------------------------------------------------------------------------
# Tools — `send_shipping_rates` encola el intent y corta el turno; el resumen
# del pedido no le devuelve al LLM un total que incluya el envío
# ---------------------------------------------------------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

from exoclaw.agent.tools import ToolContext  # noqa: E402

from src.platform.catalog import ProductNotFoundError  # noqa: E402
from src.platform.catalog.dtos import (  # noqa: E402
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.workflow_helpers import _ends_turn  # noqa: E402
from src.plugins.chats.agent.sales.tools.ui_intents import (  # noqa: E402
    PresentOrderConfirmationTool,
)


def _ctx() -> ToolContext:
    return ToolContext(session_key="s_ship", channel="whatsapp", chat_id="c")


def _read_intents(tmp_path: Path, session_key: str) -> list[dict]:
    data = json.loads(
        (tmp_path / "isolated_vault" / session_key / "metadata.json").read_text(
            encoding="utf-8"
        )
    )
    return data["pending_ui_intents"]


@pytest.mark.asyncio
async def test_send_shipping_rates_tool_queues_fixed_intent(tmp_path: Path):
    from src.plugins.chats.agent.sales.tools.ui_intents import (
        SendShippingRatesTool,
    )

    tool = SendShippingRatesTool(workspace=str(tmp_path))
    assert tool.name == "send_shipping_rates"
    result = json.loads(await tool.execute_with_context(_ctx()))
    assert result["queued"] is True
    # El envelope le dice al LLM que el mensaje YA salió y que no repita ni
    # reformule las tarifas en su propio texto.
    assert "no" in result["summary"].lower()
    assert "tarifa" in result["summary"].lower()

    (intent,) = _read_intents(tmp_path, "s_ship")
    assert intent["kind"] == "shipping_rates"
    assert intent["analytics"]["component_id"] == "shipping_rates"


def test_send_shipping_rates_ends_the_turn():
    """El mensaje estándar ES la respuesta: el LLM no agrega otra burbuja
    reformulando las tarifas (mismo corte L-11 que send_quick_replies)."""
    assert _ends_turn(["send_shipping_rates"], version=2) is True


class _OneProductCatalog:
    def __init__(self) -> None:
        self._p = CatalogProductDTO(
            id="prod_x",
            handle="velon-amor-eterno",
            title="Velón Amor Eterno",
            status="published",
            variants=[
                CatalogVariantDTO(
                    id="v_x",
                    title="Unico",
                    prices=[CatalogPriceDTO(amount="38500", currency_code="cop")],
                )
            ],
        )

    async def get_by_handle(self, handle: str):
        if handle != self._p.handle:
            raise ProductNotFoundError(handle)
        return self._p


@pytest.mark.asyncio
async def test_order_confirmation_envelope_prepaid_hands_llm_the_total(
    tmp_path: Path,
):
    """Pago anticipado: el cliente sí ve el total (envío tarifa mínima
    incluido), así que el envelope se lo da al LLM y no habla de "por
    confirmar"."""
    tool = PresentOrderConfirmationTool(
        workspace=str(tmp_path), catalog=_OneProductCatalog()
    )
    result = json.loads(
        await tool.execute_with_context(
            _ctx(),
            items=[{"handle": "velon-amor-eterno", "quantity": 1, "unit_price_cop": 38500}],
            shipping_cop=16940,
            shipping_address_summary="Calle 59b sur 38, Poblado, Medellín",
            payment_method="transfer",
        )
    )
    assert result["queued"] is True
    assert "55.440" in result["summary"]
    assert "tarifa mínima" in result["summary"].lower()
    assert "por confirmar" not in result["summary"].lower()


@pytest.mark.asyncio
async def test_order_confirmation_envelope_cod_does_not_hand_llm_a_total(
    tmp_path: Path,
):
    """Contra entrega: el summary que lee el LLM no trae "total $X" (lo
    repetiría al cliente con el envío adentro); dice que el envío queda por
    confirmar."""
    tool = PresentOrderConfirmationTool(
        workspace=str(tmp_path), catalog=_OneProductCatalog()
    )
    result = json.loads(
        await tool.execute_with_context(
            _ctx(),
            items=[{"handle": "velon-amor-eterno", "quantity": 1, "unit_price_cop": 38500}],
            shipping_cop=16940,
            shipping_address_summary="Calle 59b sur 38, Poblado, Medellín",
            payment_method="cash_on_delivery",
        )
    )
    assert result["queued"] is True
    assert "55.440" not in result["summary"]
    assert "55440" not in result["summary"]
    assert "por confirmar" in result["summary"].lower()
    # El intent sigue llevando los montos para analytics/consistencia.
    (intent,) = _read_intents(tmp_path, "s_ship")
    assert intent["params"]["shipping_cop"] == 16940
    assert intent["params"]["total_cop"] == 55440
