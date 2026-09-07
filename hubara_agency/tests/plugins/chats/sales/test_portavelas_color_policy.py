"""HU portavelas — el color del portavelas es según disponibilidad, y la
política SOLO aplica cuando el pedido incluye un producto con portavela.

Comportamiento contratado (2026-08-31, corregido 2026-09-07 tras el run
943e6bff — un pedido sin portavelas recibió "se escogen los colores del
portavelas"):
  1. Si el cliente pregunta el color del portavelas, el agente responde que
     es según disponibilidad y que los colores se escogen al finalizar el
     pago del pedido (guion siempre-cargado, aplica en cualquier etapa).
  2. Al cerrar el pedido (register_order success), la tool decide de forma
     DETERMINISTA contra el catálogo si algún ítem trae portavela
     (`portavelas.included`). Solo en ese caso el `motivo` que viaja en
     `order_registered_decision` lleva la nota operativa para el humano y
     el envelope instruye al LLM a incluirla en `escalate_to_human` y a
     avisar al comprador en la despedida.
  3. Pedido SIN portavelas: el motivo NO menciona el portavelas y el envelope
     instruye explícitamente al LLM a NO mencionarlo.
  4. Sin catálogo inyectado (o handle desconocido) la tool es conservadora:
     no menciona el portavelas (nunca al comprador equivocado).

La capa determinista (motivo + flag en la decisión → guard del workflow)
cubre el caso en que el LLM no obedezca el prompt; los guiones cubren el
path feliz conversacional.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import LocalSnapshotCatalogClient
from src.plugins.chats.agent.sales.tools.order_registration import (
    RegisterOrderTool,
)

_WORKSPACE = (
    Path(__file__).resolve().parents[4]
    / "src/plugins/chats/agent/sales/workspace"
)

_CRUZ_ITEMS = [
    {
        "handle": "cruz-de-vida",
        "quantity": 1,
        "unit_price_cop": 17000,
        "variant_label": "Lavanda",
    }
]

_DUO_ITEMS = [
    {
        "handle": "duo-zodiacal",
        "quantity": 1,
        "unit_price_cop": 17000,
        "variant_label": "Leo",
    }
]

_SAMPLE_SHIPPING = {
    "city": "Bogotá",
    "neighborhood": "Chapinero",
    "address": "Calle 100 #15-20 Apto 502",
    "phone": "3001234567",
    "receiver_name": "Ana Pérez",
}


@pytest.fixture
def ctx():
    return ToolContext(
        session_key="wa_test_policy",
        channel="whatsapp",
        chat_id="wa_test_policy",
    )


@pytest.fixture
def vault(tmp_path, ctx):
    (tmp_path / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (tmp_path / ctx.session_key / "metadata.json").write_text(
        "{}", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def catalog(tmp_path) -> LocalSnapshotCatalogClient:
    """Snapshot mínimo con el shape del catálogo vivo: solo el Dúo Zodiacal
    menciona el portavela en su description."""
    snap = tmp_path / "catalog"
    snap.mkdir()
    (snap / "snapshot.json").write_text(
        json.dumps(
            [
                {
                    "id": "1",
                    "handle": "cruz-de-vida",
                    "title": "Cruz de Vida",
                    "status": "published",
                    "description": "Vela en forma de cruz con la paloma de la paz.",
                },
                {
                    "id": "2",
                    "handle": "duo-zodiacal",
                    "title": "Duo Zodiacal",
                    "status": "published",
                    "description": (
                        "Este set incluye una vela pilar diseñada para "
                        "encenderse sobre su base: un plato portavela de "
                        "concreto pulido."
                    ),
                },
            ]
        ),
        encoding="utf-8",
    )
    (snap / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "fetched_at": "2099-01-01T00:00:00+00:00",
                "product_count": 2,
            }
        )
    )
    return LocalSnapshotCatalogClient(snapshot_dir=snap)


async def _register_ok(ctx, vault, items, catalog=None) -> dict:
    tool = RegisterOrderTool(
        workspace=str(vault), vault_dir=vault, catalog=catalog
    )
    subtotal = sum(int(it["unit_price_cop"]) * int(it["quantity"]) for it in items)
    return json.loads(
        await tool.execute_with_context(
            ctx,
            items=items,
            shipping=_SAMPLE_SHIPPING,
            payment_method="transfer",
            subtotal_cop=subtotal,
            shipping_cop=0,
            total_cop=subtotal,
        )
    )


# ----------------------------------------------------------------------
# 3) Pedido SIN portavelas (run 943e6bff): nada del portavelas, en ningún lado
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_order_without_portavelas_never_mentions_it(ctx, vault, catalog):
    result = await _register_ok(ctx, vault, _CRUZ_ITEMS, catalog)
    assert result["registered"] is True
    assert result["portavelas"]["included"] is False
    assert result["order_registered"]["portavelas_included"] is False
    assert "portavela" not in result["order_registered"]["motivo"].lower()
    # El envelope instruye al LLM a NO mencionarlo (antes lo mandaba a
    # avisar al comprador incondicionalmente — el bug).
    summary = result["summary"]
    assert "NO menciones el portavelas" in summary
    assert "escogen los colores" not in summary


# ----------------------------------------------------------------------
# 2) Pedido CON portavelas: nota determinista al humano + aviso al comprador
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_order_with_portavelas_carries_note_for_human(ctx, vault, catalog):
    """El `motivo` de `order_registered_decision` es lo que la red de
    seguridad escribe en `metadata.motivo` cuando escala a humano. La nota
    del portavelas tiene que viajar ahí para que el humano la vea SIEMPRE
    que el pedido lo incluya, aunque el LLM no la ponga en su summary."""
    result = await _register_ok(ctx, vault, _DUO_ITEMS, catalog)
    assert result["registered"] is True
    assert result["portavelas"] == {
        "included": True,
        "handles": ["duo-zodiacal"],
    }
    assert result["order_registered"]["portavelas_included"] is True
    motivo = result["order_registered"]["motivo"]
    assert "color del portavelas" in motivo
    assert "disponibilidad" in motivo


@pytest.mark.asyncio
async def test_envelope_with_portavelas_instructs_note_and_buyer_notice(
    ctx, vault, catalog
):
    result = await _register_ok(ctx, vault, _DUO_ITEMS, catalog)
    summary = result["summary"]
    # Nota para el summary de escalate_to_human.
    assert "color del portavelas" in summary
    # Aviso al comprador en la despedida: los colores se escogen al
    # finalizar el pago.
    assert "finalizar el pago" in summary
    assert "escogen" in summary
    assert "NO menciones el portavelas" not in summary


@pytest.mark.asyncio
async def test_mixed_order_counts_as_portavelas(ctx, vault, catalog):
    result = await _register_ok(
        ctx, vault, _CRUZ_ITEMS + _DUO_ITEMS, catalog
    )
    assert result["registered"] is True
    assert result["portavelas"]["included"] is True
    assert result["portavelas"]["handles"] == ["duo-zodiacal"]


# ----------------------------------------------------------------------
# 4) Conservador: sin catálogo / handle desconocido → no se menciona
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_catalog_is_conservative(ctx, vault):
    result = await _register_ok(ctx, vault, _DUO_ITEMS, catalog=None)
    assert result["registered"] is True
    assert result["portavelas"]["included"] is False
    assert "portavela" not in result["order_registered"]["motivo"].lower()
    assert "NO menciones el portavelas" in result["summary"]


@pytest.mark.asyncio
async def test_unknown_handle_is_conservative(ctx, vault, catalog):
    items = [{"handle": "no-existe", "quantity": 1, "unit_price_cop": 17000}]
    result = await _register_ok(ctx, vault, items, catalog)
    assert result["registered"] is True
    assert result["portavelas"]["included"] is False


# ----------------------------------------------------------------------
# 1) Guiones: la política existe en los archivos que el LLM SÍ carga,
#    y el cierre la trata como CONDICIONAL
# ----------------------------------------------------------------------


def _read_ws(rel: str) -> str:
    return (_WORKSPACE / rel).read_text(encoding="utf-8")


def test_sales_script_answers_portavelas_color_question() -> None:
    """El guion siempre-cargado responde la pregunta en cualquier etapa."""
    script = _read_ws("skills/sales_script/SKILL.md")
    assert "portavelas" in script
    assert "según disponibilidad" in script


def test_etapa_cierre_makes_portavelas_notice_conditional() -> None:
    """El guion de cierre: despedida base SIN portavelas; la nota al humano y
    el aviso al comprador solo si el envelope trae `portavelas.included`."""
    cierre = _read_ws("skills/etapa_cierre/SKILL.md")
    assert "portavelas.included" in cierre
    # La despedida base (la que va SIEMPRE) no habla del portavelas.
    assert (
        "Listo, tu pedido quedó registrado 🤍. Gracias por elegir a Hubara."
        in cierre
    )
    # La prohibición explícita existe para el caso sin portavelas.
    assert "no lo incluye" in cierre.lower() or "no incluye" in cierre.lower()
    # La nota al humano sigue documentada para el caso con portavelas.
    assert "color del portavelas" in cierre
    assert "finalizar el pago" in cierre


def test_etapa_variantes_excludes_portavelas_from_slots() -> None:
    """El color del portavelas NO es una variante del pedido: no se fija
    con set_order_slot ni se pide con picker."""
    variantes = _read_ws("skills/etapa_variantes/SKILL.md")
    assert "portavelas" in variantes
    assert "disponibilidad" in variantes


def test_mba_closing_script_makes_portavelas_notice_conditional() -> None:
    """El guion del Meta Business Agent (texto puro, sin envelope) condiciona
    la frase al producto: solo si el pedido incluye un producto con
    portavela."""
    mba_cierre = (
        _WORKSPACE.parents[3]
        / "mba/agents/sales/skills/guion-de-cierre.md"
    ).read_text(encoding="utf-8")
    assert (
        "Listo, tu pedido quedó registrado 🤍. Gracias por elegir a Hubara."
        in mba_cierre
    )
    assert "portavela" in mba_cierre
    assert "solo si" in mba_cierre.lower()
