"""Tests para `present_variant_picker` (sesión 71f479f7).

Garantías:
  1. El intent emitido tiene `kind=variant_picker` con sections.
  2. Cada row title trae el emoji curado al inicio (NO el LLM lo pasa).
  3. Aromas se agrupan por categoría sensorial.
  4. Colores se agrupan por intensidad.
  5. Rechaza menos de 2 opciones.
  6. NO acepta campo `emoji` del LLM (closed-list strict).
"""
from __future__ import annotations

import json

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.ui_intents import PresentVariantPickerTool


@pytest.fixture
def ctx():
    return ToolContext(
        session_key="wa_test_picker",
        channel="whatsapp",
        chat_id="wa_test_picker",
    )


@pytest.fixture
def seeded_vault(tmp_path, ctx):
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")
    return vault


def _read_intents(vault, session_key: str) -> list[dict]:
    data = json.loads(
        (vault / session_key / "metadata.json").read_text(encoding="utf-8")
    )
    return data.get("pending_ui_intents", [])


@pytest.mark.asyncio
async def test_scent_picker_emojis_are_distinct(ctx, seeded_vault):
    tool = PresentVariantPickerTool(workspace=str(seeded_vault))
    result = json.loads(await tool.execute_with_context(
        ctx,
        variant_type="scent",
        options=[
            {"label": "Lavanda"},
            {"label": "Sándalo"},
            {"label": "Café"},
            {"label": "Verde menta"},
            {"label": "Frutos rojos"},
        ],
        intro_text="Tenemos estos aromas:",
    ))
    assert result["queued"] is True
    intents = _read_intents(seeded_vault, ctx.session_key)
    intent = intents[0]
    assert intent["kind"] == "variant_picker"
    # Extraer emojis del inicio de cada row title
    all_titles = []
    for s in intent["params"]["sections"]:
        for r in s["rows"]:
            all_titles.append(r["title"])
    # El emoji va al inicio de cada title — al menos 4 distintos en 5 opciones
    first_chars = [t.split()[0] for t in all_titles]
    assert len(set(first_chars)) >= 4, (
        f"Emojis no son suficientemente distintos: {first_chars}"
    )


@pytest.mark.asyncio
async def test_picker_groups_scents_by_category(ctx, seeded_vault):
    tool = PresentVariantPickerTool(workspace=str(seeded_vault))
    await tool.execute_with_context(
        ctx,
        variant_type="scent",
        options=[
            {"label": "Lavanda"},  # Frescos
            {"label": "Café"},  # Cálidos y dulces
            {"label": "Drakar"},  # Notas perfumadas
            {"label": "Frutos rojos"},  # Cítricos y frutales
        ],
        intro_text="Aromas disponibles:",
    )
    intents = _read_intents(seeded_vault, ctx.session_key)
    section_titles = [s["title"] for s in intents[0]["params"]["sections"]]
    assert "Frescos" in section_titles
    assert "Cálidos y dulces" in section_titles
    assert "Notas perfumadas" in section_titles


@pytest.mark.asyncio
async def test_picker_color_groups(ctx, seeded_vault):
    tool = PresentVariantPickerTool(workspace=str(seeded_vault))
    await tool.execute_with_context(
        ctx,
        variant_type="color",
        options=[
            {"label": "blanco"},
            {"label": "azul"},
            {"label": "morado"},
            {"label": "rosado"},
        ],
        intro_text="Estos son los colores:",
    )
    intents = _read_intents(seeded_vault, ctx.session_key)
    sections = intents[0]["params"]["sections"]
    titles = [s["title"] for s in sections]
    assert "Claros y suaves" in titles
    assert "Vibrantes" in titles
    assert "Profundos" in titles


@pytest.mark.asyncio
async def test_picker_rejects_single_option(ctx, seeded_vault):
    tool = PresentVariantPickerTool(workspace=str(seeded_vault))
    result = json.loads(await tool.execute_with_context(
        ctx,
        variant_type="scent",
        options=[{"label": "Lavanda"}],
        intro_text="Solo uno:",
    ))
    assert result["queued"] is False
    assert result["error"] == "not_enough_options"


@pytest.mark.asyncio
async def test_picker_emoji_ignored_if_llm_passes_it(ctx, seeded_vault):
    """Closed-list strict: si el LLM intenta pasar su propio emoji vía label
    o un campo extra, debe ser ignorado y reemplazado por el curado."""
    tool = PresentVariantPickerTool(workspace=str(seeded_vault))
    await tool.execute_with_context(
        ctx,
        variant_type="scent",
        options=[
            {"label": "Lavanda", "emoji": "💩"},  # LLM intenta inyectar
            {"label": "Café", "emoji": "💩"},
        ],
        intro_text="Aromas:",
    )
    intents = _read_intents(seeded_vault, ctx.session_key)
    titles = [r["title"] for s in intents[0]["params"]["sections"] for r in s["rows"]]
    # Ninguno debe traer el emoji del LLM
    assert not any("💩" in t for t in titles)
    # Y los emojis curados deben estar
    assert any("💜" in t for t in titles)  # Lavanda
    assert any("☕" in t for t in titles)  # Café


@pytest.mark.asyncio
async def test_picker_paginates_when_over_meta_10_row_limit(ctx, seeded_vault):
    """Anti-bug 10d8bd21 v2 (pagination): 11 aromas → 2 intents
    consecutivos, cada uno ≤10 rows, todas las opciones preservadas."""
    tool = PresentVariantPickerTool(workspace=str(seeded_vault))
    # 11 aromas — el caso exacto del bug
    result = json.loads(await tool.execute_with_context(
        ctx,
        variant_type="scent",
        options=[
            {"label": "Lavanda"},
            {"label": "Sándalo"},
            {"label": "Café"},
            {"label": "Caballero de la noche"},
            {"label": "Limoncillo"},
            {"label": "Ylan ylang"},
            {"label": "Coco cremoso"},
            {"label": "Frutos rojos"},
            {"label": "Verde menta"},
            {"label": "Drakar"},
            {"label": "Chanel"},
        ],
        intro_text="Tenemos estos aromas:",
    ))
    assert result["queued"] is True
    # Todas las 11 opciones preservadas — distribuidas en pages
    assert result["count"] == 11
    assert result["pages"] >= 2  # mínimo 2 mensajes

    intents = _read_intents(seeded_vault, ctx.session_key)
    # Un intent por page
    assert len(intents) == result["pages"]

    # Cada intent NUNCA excede el cap de Meta (10 rows)
    for intent in intents:
        total_rows = sum(len(s["rows"]) for s in intent["params"]["sections"])
        assert total_rows <= 10, (
            f"Intent con {total_rows} rows rompería Meta"
        )

    # Suma de todas las pages = 11 (todas las opciones preservadas)
    total_across_pages = sum(
        len(s["rows"]) for intent in intents for s in intent["params"]["sections"]
    )
    assert total_across_pages == 11

    # Page 1 usa el intro_text del LLM; page 2+ usa body de continuación
    assert intents[0]["params"]["intro_text"] == "Tenemos estos aromas:"
    assert intents[1]["params"]["intro_text"] == "Más aromas disponibles:"
    # Metadata de paginación
    assert intents[0]["params"]["page"] == 1
    assert intents[0]["params"]["total_pages"] == result["pages"]
    assert intents[1]["params"]["page"] == 2


@pytest.mark.asyncio
async def test_picker_no_pagination_when_under_cap(ctx, seeded_vault):
    """Si caben en una sola página (≤10), un solo intent — no overhead."""
    tool = PresentVariantPickerTool(workspace=str(seeded_vault))
    result = json.loads(await tool.execute_with_context(
        ctx,
        variant_type="scent",
        options=[
            {"label": "Lavanda"},
            {"label": "Café"},
            {"label": "Verde menta"},
            {"label": "Drakar"},
        ],
        intro_text="Aromas:",
    ))
    assert result["queued"] is True
    assert result["pages"] == 1
    intents = _read_intents(seeded_vault, ctx.session_key)
    assert len(intents) == 1


@pytest.mark.asyncio
async def test_picker_unknown_scent_gets_fallback(ctx, seeded_vault):
    """Aroma no mapeado en el registry sale con `🕯️`, no es bloqueado."""
    tool = PresentVariantPickerTool(workspace=str(seeded_vault))
    result = json.loads(await tool.execute_with_context(
        ctx,
        variant_type="scent",
        options=[
            {"label": "AromaInventado"},
            {"label": "Lavanda"},
        ],
        intro_text="Aromas:",
    ))
    assert result["queued"] is True
    intents = _read_intents(seeded_vault, ctx.session_key)
    titles = [r["title"] for s in intents[0]["params"]["sections"] for r in s["rows"]]
    # AromaInventado va con fallback
    inventado_title = next(t for t in titles if "AromaInventado" in t)
    assert "🕯️" in inventado_title
