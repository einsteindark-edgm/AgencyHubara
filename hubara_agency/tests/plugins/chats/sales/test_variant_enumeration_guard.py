"""Guarda de enumeración de variantes (run 9bd495be, 2026-09-14): el LLM
listó los 11 aromas como texto plano ("Tenemos 11 aromas disponibles:
Caballero de la noche, Limoncillo, …") en vez de `present_variant_picker`.
Contrato: si el texto final enumera 4+ aromas o colores del catálogo y el turno
no emitió picker, el sistema encola el picker (formato curado con emojis) y
suprime el texto plano."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.plugins.chats.agent.sales.variant_enumeration import (
    find_enumerated_variants,
    intro_before,
)

AROMAS = [
    "Caballero de la noche", "Limoncillo", "Lavanda", "Café", "Sándalo", "Ylang Ylang",
    "Coco cremoso", "Frutos rojos", "Verde menta", "Drakar", "Chanel",
]
COLORS = ["gris", "Amarillo", "verde", "Naranja", "Morado", "Azul", "Negro", "Blanco", "Lila", "Rosado", "Café"]

ENUMERATION = (
    "Tenemos 11 aromas disponibles: Caballero de la noche, Limoncillo, Lavanda, Café, "
    "Sándalo, Ylang Ylang, Coco cremoso, Frutos rojos, Verde menta, Drakar y Chanel.\n\n"
    "¿Alguno te llama la atención o prefieres que te cuente cómo huelen?"
)


def test_detects_eleven_aromas_in_order() -> None:
    hit = find_enumerated_variants(ENUMERATION, aromas=AROMAS, colors=COLORS)
    assert hit is not None
    variant_type, labels = hit
    assert variant_type == "scent"
    assert labels == AROMAS


def test_three_labels_are_not_an_enumeration() -> None:
    text = "El café es de los que más rotan; también gustan mucho la lavanda y el sándalo. ¿Cuál te llama?"
    assert find_enumerated_variants(text, aromas=AROMAS, colors=COLORS) is None


def test_detects_colors_accent_and_case_insensitive() -> None:
    text = "Lo tenemos en azul, morado, rosado, blanco y cafe. ¿Cuál prefieres?"
    hit = find_enumerated_variants(text, aromas=AROMAS, colors=COLORS)
    assert hit is not None
    assert hit[0] == "color"
    assert [label.casefold() for label in hit[1]] == ["azul", "morado", "rosado", "blanco", "café"]


def test_color_cafe_does_not_count_as_aroma_when_colors_dominate() -> None:
    text = "Colores: azul, morado, rosado, blanco, negro."
    hit = find_enumerated_variants(text, aromas=AROMAS, colors=COLORS)
    assert hit is not None and hit[0] == "color"


def test_intro_is_the_text_before_the_first_label() -> None:
    assert intro_before(ENUMERATION, AROMAS) == "Tenemos 11 aromas disponibles"
    assert intro_before("Lavanda, Café, Sándalo y Drakar", AROMAS) == ""


# ---------------------------------------------------------------- activity


class _Product:
    def __init__(self, handle: str, tags: list[str]) -> None:
        self.handle = handle
        self.tags = tags


class _Catalog:
    async def search(self, q: str = "", limit: int = 30):
        tags = [f"Aroma: {a}" for a in AROMAS] + [f"Color: {c}" for c in COLORS]
        return SimpleNamespace(results=[_Product("cubo-love", tags), _Product("velon-koala", tags[:6])])


@pytest.fixture
def guard_env(monkeypatch: pytest.MonkeyPatch, _isolate_vault_dir: Path) -> Path:
    from src.plugins.chats.agent.sales.activities import variant_enumeration_guard as mod

    monkeypatch.setattr(mod, "get_catalog_client", lambda: _Catalog())
    sid = "wa_test_enum"
    (_isolate_vault_dir / sid).mkdir(parents=True, exist_ok=True)
    (_isolate_vault_dir / sid / "metadata.json").write_text("{}", encoding="utf-8")
    return _isolate_vault_dir / sid / "metadata.json"


def _intents(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("pending_ui_intents", [])


@pytest.mark.asyncio
async def test_activity_queues_picker_for_enumerated_aromas(guard_env: Path) -> None:
    from src.plugins.chats.agent.sales.activities.variant_enumeration_guard import (
        apply_variant_enumeration_guard_activity,
    )

    assert await apply_variant_enumeration_guard_activity("wa_test_enum", ENUMERATION) is True
    intents = _intents(guard_env)
    assert [i["kind"] for i in intents] == ["variant_picker"]
    params = intents[0]["params"]
    assert params["variant_type"] == "scent"
    assert params["handle"] is None
    rows = [r for s in params["sections"] for r in s["rows"]]
    assert len(rows) == 11
    assert params["intro_text"].startswith("Tenemos 11 aromas disponibles")
    assert intents[0]["analytics"]["component_kind"] == "text.variant_picker"


@pytest.mark.asyncio
async def test_activity_is_noop_without_enumeration(guard_env: Path) -> None:
    from src.plugins.chats.agent.sales.activities.variant_enumeration_guard import (
        apply_variant_enumeration_guard_activity,
    )

    assert await apply_variant_enumeration_guard_activity("wa_test_enum", "Buena elección, el café es de los que más rotan.") is False
    assert _intents(guard_env) == []


@pytest.mark.asyncio
async def test_activity_degrades_when_catalog_is_down(guard_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.plugins.chats.agent.sales.activities import variant_enumeration_guard as mod

    class _Broken:
        async def search(self, q: str = "", limit: int = 30):
            raise RuntimeError("snapshot down")

    monkeypatch.setattr(mod, "get_catalog_client", lambda: _Broken())
    assert await mod.apply_variant_enumeration_guard_activity("wa_test_enum", ENUMERATION) is False
    assert _intents(guard_env) == []
