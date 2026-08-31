"""Tests para el closed-list de emojis de variantes.

Bug origen (sesión 71f479f7): el LLM listaba 11 aromas todos con 🌿. El map
debe asignar emoji distinto por aroma, ser case+tilde insensitive, y caer
a fallback genérico para variantes no mapeadas.
"""
from __future__ import annotations

from src.platform.whatsapp.variant_emoji import (
    GENERIC_COLOR_EMOJI,
    GENERIC_SCENT_EMOJI,
    color_emoji,
    group_colors,
    group_scents,
    normalize_variant_key,
    scent_emoji,
)


def test_scent_emoji_known_returns_distinct():
    """Los aromas conocidos deben tener emojis DISTINTOS — no todos iguales."""
    known = ["Lavanda", "Sándalo", "Café", "Limoncillo", "Coco cremoso", "Verde menta"]
    emojis = [scent_emoji(s) for s in known]
    assert len(set(emojis)) == len(known), f"Emojis repetidos: {emojis}"


def test_scent_emoji_normalizes_accents():
    """Sándalo vs Sandalo deben mapear al mismo emoji."""
    assert scent_emoji("Sándalo") == scent_emoji("Sandalo")
    assert scent_emoji("Café") == scent_emoji("cafe")


def test_scent_emoji_unknown_falls_back():
    assert scent_emoji("NoExisteEsteAroma") == GENERIC_SCENT_EMOJI


def test_color_emoji_known():
    assert color_emoji("Rosado") == "🌸"
    assert color_emoji("Azul") == "🔵"
    assert color_emoji("Verde") == "🟢"


def test_color_emoji_unknown_falls_back():
    assert color_emoji("NoExisteEsteColor") == GENERIC_COLOR_EMOJI


def test_normalize_variant_key_strips_accents_and_lowers():
    assert normalize_variant_key("Sándalo") == "sandalo"
    assert normalize_variant_key("  Verde Menta  ") == "verde menta"
    assert normalize_variant_key("") == ""


def test_group_scents_preserves_category_order():
    """`Frescos` debe ir primero, `Notas perfumadas` al final."""
    scents = ["Drakar", "Lavanda", "Café"]
    grouped = group_scents(scents)
    titles = [t for t, _ in grouped]
    # Frescos contiene Lavanda → debería venir primero
    assert titles.index("Frescos") < titles.index("Notas perfumadas")
    assert titles.index("Cálidos y dulces") < titles.index("Notas perfumadas")


def test_group_scents_limoncillo_es_citrico():
    """Limoncillo es un cítrico (lemongrass) — debe listarse bajo
    'Cítricos y frutales', no bajo 'Frescos' (reporte del operador 2026-08-31)."""
    grouped = dict(group_scents(["Limoncillo", "Lavanda", "Frutos rojos"]))
    assert "Limoncillo" in grouped.get("Cítricos y frutales", [])
    assert "Limoncillo" not in grouped.get("Frescos", [])


def test_group_scents_unknown_go_to_otros():
    grouped = group_scents(["Lavanda", "AromaInventado"])
    sec_dict = dict(grouped)
    assert "Otros" in sec_dict
    assert "AromaInventado" in sec_dict["Otros"]


def test_group_colors_groups_correctly():
    grouped = group_colors(["blanco", "azul", "morado"])
    titles_to_labels = dict(grouped)
    assert "blanco" in titles_to_labels.get("Claros y suaves", [])
    assert "azul" in titles_to_labels.get("Vibrantes", [])
    assert "morado" in titles_to_labels.get("Profundos", [])
