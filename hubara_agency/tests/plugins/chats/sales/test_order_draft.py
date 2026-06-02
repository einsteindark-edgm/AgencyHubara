"""Tests del breadcrumb determinista de datos del pedido (order_draft).

Cubre las TRES capas:
  * Funciones puras (`use_cases/order_draft.py`): merge / overwrite / clear,
    gating de proyeccion, formato del breadcrumb.
  * `SetOrderSlotTool`: persiste slots en el episodio activo (advisory).
  * Comportamiento episodio-scoped: el draft NO se filtra entre episodios
    (re-engagement) y deja de proyectarse cuando se registra la orden.

Foco en COMPORTAMIENTO, no schema (gotcha #1 del repo): verificamos que el dato
queda persistido + proyectable/no-proyectable segun el ciclo de vida del
episodio, no solo que la tool acepte los params.
"""
from __future__ import annotations

import json

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool
from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    close_episode,
    ensure_active_episode,
)
from src.plugins.chats.agent.sales.use_cases.order_draft import (
    build_order_draft_note,
    get_projectable_draft,
    update_order_draft,
)

_NOW = 1_700_000_000_000


def _active_meta() -> dict:
    """metadata con un episodio activo (como lo deja el ingest antes del turno)."""
    meta: dict = {}
    ensure_active_episode(meta, now_ms=_NOW)
    return meta


# ----------------------------------------------------------------------
# Funciones puras: update_order_draft
# ----------------------------------------------------------------------


def test_update_writes_slots_into_active_episode():
    meta = _active_meta()
    update_order_draft(
        meta, slots={"color": "Blanco", "aroma": "Lavanda"}, now_ms=_NOW
    )
    draft = meta["episodes"][-1]["order_draft"]
    assert draft["slots"] == {"color": "Blanco", "aroma": "Lavanda"}
    assert draft["updated_at_ms"] == _NOW


def test_update_overwrites_existing_slot():
    meta = _active_meta()
    update_order_draft(meta, slots={"color": "Blanco"}, now_ms=_NOW)
    update_order_draft(meta, slots={"color": "Azul"}, now_ms=_NOW + 1)
    assert meta["episodes"][-1]["order_draft"]["slots"]["color"] == "Azul"


def test_update_empty_string_clears_slot():
    meta = _active_meta()
    update_order_draft(meta, slots={"color": "Blanco"}, now_ms=_NOW)
    update_order_draft(meta, slots={"color": ""}, now_ms=_NOW + 1)
    assert "color" not in meta["episodes"][-1]["order_draft"]["slots"]


def test_update_creates_episode_when_none_active():
    """Defensivo: sin episodios, update crea uno (no pierde el dato)."""
    meta: dict = {}
    update_order_draft(meta, slots={"color": "Blanco"}, now_ms=_NOW)
    assert len(meta["episodes"]) == 1
    assert meta["episodes"][0]["order_draft"]["slots"]["color"] == "Blanco"


def test_update_normalizes_to_trimmed_string():
    meta = _active_meta()
    update_order_draft(
        meta, slots={"cantidad": 2, "ciudad": "  Bogota  "}, now_ms=_NOW
    )
    slots = meta["episodes"][-1]["order_draft"]["slots"]
    assert slots["cantidad"] == "2"
    assert slots["ciudad"] == "Bogota"


# ----------------------------------------------------------------------
# Funciones puras: get_projectable_draft (el gate)
# ----------------------------------------------------------------------


def test_projectable_returns_slots_for_active_nonempty_no_order():
    meta = _active_meta()
    update_order_draft(meta, slots={"color": "Blanco"}, now_ms=_NOW)
    assert get_projectable_draft(meta) == {"color": "Blanco"}


def test_projectable_none_when_no_episodes():
    assert get_projectable_draft({}) is None


def test_projectable_none_when_empty_draft():
    meta = _active_meta()
    update_order_draft(meta, slots={}, now_ms=_NOW)  # crea draft pero sin slots
    assert get_projectable_draft(meta) is None


def test_projectable_none_when_episode_has_order_id():
    """Post-register_order: la orden es la fuente de verdad, draft no se proyecta."""
    meta = _active_meta()
    update_order_draft(meta, slots={"color": "Blanco"}, now_ms=_NOW)
    meta["episodes"][-1]["order_id"] = "draft_123"
    assert get_projectable_draft(meta) is None


def test_projectable_none_when_episode_closed():
    meta = _active_meta()
    update_order_draft(meta, slots={"color": "Blanco"}, now_ms=_NOW)
    close_episode(
        meta, closing_tag="RECHAZO", closing_motivo=None, now_ms=_NOW + 1
    )
    assert get_projectable_draft(meta) is None


# ----------------------------------------------------------------------
# EL test de leak entre episodios (el paradigmatico)
# ----------------------------------------------------------------------


def test_draft_does_not_leak_across_episodes():
    """Re-engagement: episodio 1 con draft -> cierra -> episodio 2 nuevo.

    El draft del ep1 NO se proyecta en el ep2 (no leak, por construccion —
    mismo principio que el reset de `metadata.tag` en re-engagement).
    """
    meta = _active_meta()
    # Episodio 1: cliente eligio color + producto.
    update_order_draft(
        meta, slots={"color": "Blanco", "producto": "Luz Serena"}, now_ms=_NOW
    )
    assert get_projectable_draft(meta) == {
        "color": "Blanco",
        "producto": "Luz Serena",
    }

    # Cierra episodio 1.
    close_episode(
        meta, closing_tag="COMPRA_EXITOSA", closing_motivo="ok", now_ms=_NOW + 1
    )

    # Re-engagement: nuevo inbound -> nuevo episodio.
    ensure_active_episode(meta, now_ms=_NOW + 10_000)
    assert len(meta["episodes"]) == 2

    # El episodio 2 NO ve el draft del episodio 1.
    assert get_projectable_draft(meta) is None
    # El draft del ep1 quedo congelado en el episodio cerrado (auditoria).
    assert meta["episodes"][0]["order_draft"]["slots"]["color"] == "Blanco"


# ----------------------------------------------------------------------
# build_order_draft_note
# ----------------------------------------------------------------------


def test_note_lists_known_slots_in_canonical_order():
    note = build_order_draft_note(
        {"color": "Blanco", "producto": "Luz Serena", "aroma": "Lavanda"}
    )
    # Orden canonico de KNOWN_SLOTS: producto < aroma < color.
    assert (
        note.index("Producto: Luz Serena")
        < note.index("Aroma: Lavanda")
        < note.index("Color: Blanco")
    )


def test_note_has_anti_reask_framing():
    note = build_order_draft_note({"color": "Blanco"})
    low = note.lower()
    assert "no" in low and "preguntar" in low
    assert "set_order_slot" in note


def test_note_includes_unknown_slots():
    note = build_order_draft_note({"color": "Blanco", "regalo": "si"})
    assert "regalo: si" in note


# ----------------------------------------------------------------------
# SetOrderSlotTool
# ----------------------------------------------------------------------


@pytest.fixture
def ctx():
    return ToolContext(
        session_key="wa_test_slot", channel="whatsapp", chat_id="wa_test_slot"
    )


@pytest.fixture
def vault(tmp_path, ctx):
    (tmp_path / ctx.session_key).mkdir(parents=True, exist_ok=True)
    # seed: episodio activo (como lo deja el ingest antes del turno).
    meta: dict = {}
    ensure_active_episode(meta, now_ms=_NOW)
    (tmp_path / ctx.session_key / "metadata.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )
    return tmp_path


def _read_metadata(vault, session_key: str) -> dict:
    return json.loads(
        (vault / session_key / "metadata.json").read_text(encoding="utf-8")
    )


@pytest.mark.asyncio
async def test_tool_persists_slots_into_active_episode(ctx, vault):
    tool = SetOrderSlotTool(workspace=str(vault), vault_dir=vault)
    raw = await tool.execute_with_context(
        ctx, color="Blanco", aroma="Lavanda", cantidad="2"
    )
    result = json.loads(raw)
    assert result["updated"] is True
    assert result["captured"] == {
        "color": "Blanco",
        "aroma": "Lavanda",
        "cantidad": "2",
    }
    assert result["order_draft"]["color"] == "Blanco"

    meta = _read_metadata(vault, ctx.session_key)
    slots = meta["episodes"][-1]["order_draft"]["slots"]
    assert slots == {"color": "Blanco", "aroma": "Lavanda", "cantidad": "2"}


@pytest.mark.asyncio
async def test_tool_merges_across_calls(ctx, vault):
    tool = SetOrderSlotTool(workspace=str(vault), vault_dir=vault)
    await tool.execute_with_context(ctx, color="Blanco")
    await tool.execute_with_context(ctx, ciudad="Bogota", telefono="3001234567")
    meta = _read_metadata(vault, ctx.session_key)
    slots = meta["episodes"][-1]["order_draft"]["slots"]
    assert slots == {
        "color": "Blanco",
        "ciudad": "Bogota",
        "telefono": "3001234567",
    }


@pytest.mark.asyncio
async def test_tool_empty_call_is_noop(ctx, vault):
    tool = SetOrderSlotTool(workspace=str(vault), vault_dir=vault)
    result = json.loads(await tool.execute_with_context(ctx))
    assert result["updated"] is False
    meta = _read_metadata(vault, ctx.session_key)
    assert "order_draft" not in meta["episodes"][-1]


@pytest.mark.asyncio
async def test_tool_output_is_projectable(ctx, vault):
    """End-to-end advisory: tras la tool, el draft es proyectable para el ingest."""
    tool = SetOrderSlotTool(workspace=str(vault), vault_dir=vault)
    await tool.execute_with_context(ctx, color="Blanco")
    meta = _read_metadata(vault, ctx.session_key)
    assert get_projectable_draft(meta) == {"color": "Blanco"}


@pytest.mark.asyncio
async def test_register_order_stops_draft_projection(ctx, vault):
    """Tras register_order exitoso, el episodio queda con order_id y el draft
    deja de proyectarse (la orden pasa a ser la fuente de verdad)."""
    slot_tool = SetOrderSlotTool(workspace=str(vault), vault_dir=vault)
    await slot_tool.execute_with_context(ctx, color="Blanco", producto="Luz Serena")
    assert get_projectable_draft(_read_metadata(vault, ctx.session_key)) is not None

    # Default port = StubOrderRegistration -> success con order_id "HUB-...".
    reg_tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault)
    await reg_tool.execute_with_context(
        ctx,
        items=[
            {
                "handle": "luz-serena",
                "quantity": 1,
                "unit_price_cop": 23000,
                "variant_label": "Lavanda, Blanco",
            }
        ],
        shipping={
            "city": "Bogota",
            "neighborhood": "Chapinero",
            "address": "Calle 1 #2-3",
            "phone": "3001234567",
        },
        payment_method="transfer",
        subtotal_cop=23000,
        shipping_cop=0,
        total_cop=23000,
    )

    meta = _read_metadata(vault, ctx.session_key)
    assert meta["episodes"][-1]["order_id"]  # register_order anoto el order_id
    assert get_projectable_draft(meta) is None  # draft deja de proyectarse
