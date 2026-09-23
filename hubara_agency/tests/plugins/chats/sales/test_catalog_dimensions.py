"""Las tools de catálogo le muestran al agente las medidas reales (2026-09-22).

Caso: un lead preguntó "¿Qué medidas tienen las velas?"; el bot consultó los
4 productos y respondió "El catálogo no trae las medidas exactas". Al día
siguiente una asesora mandó a mano calabaza 9×6, momia 5×6, fantasma 8×9 —
las mismas que Medusa ya tenía. Las tools exponen `medidas` como UNA línea
corta (pocos tokens, nada que interpretar) y solo si hay dato: sin medidas no
aparece la clave y el guion manda a confirmarlas con el equipo.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.dtos import (
    CatalogDimensionsDTO,
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    SearchResult,
)
from src.plugins.chats.agent.sales.tools.catalog import (
    GetProductByHandleTool,
    SearchProductsTool,
)

_CALABAZA = CatalogProductDTO(
    id="prod_calabaza",
    handle="calabaza",
    title="Calabaza",
    status="published",
    variants=[
        CatalogVariantDTO(
            id="v1",
            title="Unico",
            prices=[CatalogPriceDTO(amount="16000", currency_code="cop")],
        )
    ],
    dimensions=CatalogDimensionsDTO(height_cm=9.0, width_cm=6.0),
)


class _FakeCatalog:
    def __init__(self, product: CatalogProductDTO) -> None:
        self._product = product

    async def search(self, q: str, limit: int = 10):
        return SearchResult(
            query=q,
            count=1,
            truncated=False,
            stale=False,
            manifest=CatalogManifestDTO(
                version="v1", fetched_at="2026-09-23T00:00:00Z", product_count=1
            ),
            results=[self._product],
        )

    async def get_by_handle(self, handle: str):
        return self._product


def _ctx() -> ToolContext:
    return ToolContext(session_key="s", channel="whatsapp", chat_id="c")


async def _detail(product: CatalogProductDTO, tmp_path: Path) -> dict:
    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_FakeCatalog(product))
    payload = json.loads(await tool.execute_with_context(_ctx(), handle=product.handle))
    return payload["product"]


@pytest.mark.asyncio
async def test_product_detail_shows_dimensions_in_one_line(tmp_path: Path):
    product = await _detail(_CALABAZA, tmp_path)

    assert product.get("medidas") == "alto 9 cm, ancho 6 cm"


@pytest.mark.asyncio
async def test_product_without_dimensions_has_no_medidas_key(tmp_path: Path):
    product = await _detail(replace(_CALABAZA, dimensions=None), tmp_path)

    assert "medidas" not in product


@pytest.mark.asyncio
async def test_medidas_uses_decimal_comma_and_includes_weight(tmp_path: Path):
    dims = CatalogDimensionsDTO(height_cm=9.5, width_cm=6.0, weight_g=120.0)

    product = await _detail(replace(_CALABAZA, dimensions=dims), tmp_path)

    assert product["medidas"] == "alto 9,5 cm, ancho 6 cm, peso 120 g"


@pytest.mark.asyncio
async def test_search_summary_shows_dimensions(tmp_path: Path):
    """"¿Qué medidas tienen?" sobre una lista se responde con la búsqueda,
    sin un get_product_by_handle por producto (el run hizo 4)."""
    tool = SearchProductsTool(workspace=tmp_path, catalog=_FakeCatalog(_CALABAZA))

    payload = json.loads(await tool.execute_with_context(_ctx(), q="halloween"))

    assert payload["results"][0].get("medidas") == "alto 9 cm, ancho 6 cm"


def test_discovery_script_answers_with_medidas_and_never_denies_them():
    """El guion de descubrimiento (donde llegó la pregunta) dicta usar
    `medidas` tal cual y, si faltan, NO negarlas: pedirle al equipo que las
    confirme (CATALOG_GAP) — lo que terminó haciendo la asesora a mano."""
    script = (
        Path(__file__).resolve().parents[4]
        / "src/plugins/chats/agent/sales/workspace/skills/etapa_descubrimiento/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "`medidas`" in script
    assert "aproximadas" in script
    assert 'escalate_to_human("CATALOG_GAP"' in script
