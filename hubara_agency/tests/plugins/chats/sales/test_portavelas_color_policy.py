"""HU portavelas — el color del portavelas es según disponibilidad.

Comportamiento contratado (2026-08-31):
  1. Si el cliente pregunta el color del portavelas, el agente responde que
     es según disponibilidad y que los colores se escogen al finalizar el
     pago del pedido (guion siempre-cargado, aplica en cualquier etapa).
  2. Al cerrar el pedido (register_order success), el humano que toma la
     verificación del pago SIEMPRE recibe la nota operativa "definir el
     color del portavelas con el cliente" — de forma DETERMINISTA vía el
     `motivo` que viaja en `order_registered_decision` (lo consume la red
     de seguridad `ensure_payment_pending_closure` y termina en
     `metadata.motivo` / `status_history`, que es lo que ve el dashboard).
  3. El envelope de la tool instruye al LLM a incluir la misma nota en su
     `escalate_to_human(summary=...)` y a avisarle al comprador en la
     despedida que al finalizar el pago se escogen los colores.

La capa determinista (motivo) cubre el caso en que el LLM no obedezca el
prompt; los guiones cubren el path feliz conversacional.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.order_registration import (
    RegisterOrderTool,
)

_WORKSPACE = (
    Path(__file__).resolve().parents[4]
    / "src/plugins/chats/agent/sales/workspace"
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


@pytest.fixture
def ctx():
    return ToolContext(
        session_key="wa_test_portavelas",
        channel="whatsapp",
        chat_id="wa_test_portavelas",
    )


@pytest.fixture
def vault(tmp_path, ctx):
    (tmp_path / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (tmp_path / ctx.session_key / "metadata.json").write_text(
        "{}", encoding="utf-8"
    )
    return tmp_path


async def _register_ok(ctx, vault) -> dict:
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault)
    return json.loads(
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


# ----------------------------------------------------------------------
# 2) Nota determinista al humano (viaja en el motivo de la escalación)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_order_motivo_carries_portavelas_note_for_human(ctx, vault):
    """El `motivo` de `order_registered_decision` es lo que la red de
    seguridad escribe en `metadata.motivo` cuando escala a humano. La nota
    del portavelas tiene que viajar ahí para que el humano la vea SIEMPRE,
    aunque el LLM no la incluya en su propio summary."""
    result = await _register_ok(ctx, vault)
    assert result["registered"] is True
    motivo = result["order_registered"]["motivo"]
    assert "color del portavelas" in motivo
    assert "disponibilidad" in motivo


# ----------------------------------------------------------------------
# 3) El envelope instruye al LLM: nota en la escalación + aviso al comprador
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_envelope_summary_instructs_note_and_buyer_notice(ctx, vault):
    result = await _register_ok(ctx, vault)
    summary = result["summary"]
    # Nota para el summary de escalate_to_human.
    assert "color del portavelas" in summary
    # Aviso al comprador en la despedida: los colores se escogen al
    # finalizar el pago.
    assert "finalizar el pago" in summary
    assert "escogen" in summary


# ----------------------------------------------------------------------
# 1) Guiones: la política existe en los archivos que el LLM SÍ carga
# ----------------------------------------------------------------------


def _read_ws(rel: str) -> str:
    return (_WORKSPACE / rel).read_text(encoding="utf-8")


def test_sales_script_answers_portavelas_color_question() -> None:
    """El guion siempre-cargado responde la pregunta en cualquier etapa."""
    script = _read_ws("skills/sales_script/SKILL.md")
    assert "portavelas" in script
    assert "según disponibilidad" in script


def test_etapa_cierre_includes_note_and_buyer_notice() -> None:
    """El guion de cierre pide la nota en el summary de escalación y el
    aviso al comprador en la despedida."""
    cierre = _read_ws("skills/etapa_cierre/SKILL.md")
    assert "color del portavelas" in cierre
    assert "finalizar el pago" in cierre


def test_etapa_variantes_excludes_portavelas_from_slots() -> None:
    """El color del portavelas NO es una variante del pedido: no se fija
    con set_order_slot ni se pide con picker."""
    variantes = _read_ws("skills/etapa_variantes/SKILL.md")
    assert "portavelas" in variantes
    assert "disponibilidad" in variantes
