"""Tests para `cap_list_rows_total` — anti-bug 10d8bd21.

Bug: Meta cap el TOTAL de rows del `interactive.list` en 10, no 10 por
section. Mandar 11 → error 131009 "Total row count exceed max allowed
count: 10". El cliente nunca ve el componente.

Esta función reparte hasta 10 rows entre las sections preservando el
orden y maximizando representación de cada section.
"""
from __future__ import annotations

from src.platform.whatsapp.limits import (
    MAX_LIST_ROWS_TOTAL,
    cap_list_rows_total,
)


def _total_rows(sections):
    return sum(len(s.get("rows") or []) for s in sections)


def _row_ids(sections):
    return [r["id"] for s in sections for r in s.get("rows") or []]


# =============================================================================
# Caso exacto del bug 10d8bd21: 11 aromas en 4 sections
# =============================================================================


def test_exact_bug_payload_caps_at_10():
    """Bug original: 4 sections sumando 11 rows → 11 rows enviados → 400.

    Esperado: cap total <= 10, preservando representación de cada
    section."""
    sections = [
        {"title": "Frescos", "rows": [
            {"id": "scent.lavanda", "title": "💜 Lavanda"},
            {"id": "scent.limoncillo", "title": "🍋 Limoncillo"},
            {"id": "scent.verde_menta", "title": "🌿 Verde menta"},
            {"id": "scent.coco_cremoso", "title": "🥥 Coco cremoso"},
        ]},
        {"title": "Cítricos y frutales", "rows": [
            {"id": "scent.frutos_rojos", "title": "🍓 Frutos rojos"},
            {"id": "scent.ylan_ylang", "title": "🌸 Ylan ylang"},
        ]},
        {"title": "Cálidos y dulces", "rows": [
            {"id": "scent.cafe", "title": "☕ Café"},
            {"id": "scent.sandalo", "title": "🪵 Sándalo"},
        ]},
        {"title": "Notas perfumadas", "rows": [
            {"id": "scent.caballero", "title": "🌙 Caballero de la noche"},
            {"id": "scent.drakar", "title": "🖤 Drakar"},
            {"id": "scent.chanel", "title": "✨ Chanel"},
        ]},
    ]
    capped = cap_list_rows_total(sections, total_cap=10)
    assert _total_rows(capped) == 10, (
        f"Cap roto: total {_total_rows(capped)} != 10. Capped={capped}"
    )
    # Las 4 sections deben sobrevivir (representación de cada categoría)
    assert len(capped) == 4
    # El orden de sections se preserva
    titles = [s["title"] for s in capped]
    assert titles == ["Frescos", "Cítricos y frutales", "Cálidos y dulces", "Notas perfumadas"]


def test_under_cap_passes_through():
    """Si total <= 10, no se toca nada (no overhead)."""
    sections = [
        {"title": "Frescos", "rows": [{"id": "a", "title": "A"}]},
        {"title": "Dulces", "rows": [{"id": "b", "title": "B"}]},
    ]
    capped = cap_list_rows_total(sections, total_cap=10)
    assert capped == [
        {"title": "Frescos", "rows": [{"id": "a", "title": "A"}]},
        {"title": "Dulces", "rows": [{"id": "b", "title": "B"}]},
    ]


def test_empty_sections_dropped():
    """Sections con `rows: []` deben filtrarse — Meta las rechaza."""
    sections = [
        {"title": "Frescos", "rows": [{"id": "a", "title": "A"}]},
        {"title": "Vacíos", "rows": []},
    ]
    capped = cap_list_rows_total(sections, total_cap=10)
    titles = [s["title"] for s in capped]
    assert "Vacíos" not in titles


def test_no_sections_returns_empty():
    assert cap_list_rows_total([], 10) == []


def test_uses_default_cap_constant():
    """Sin pasar `total_cap`, usa `MAX_LIST_ROWS_TOTAL` (=10)."""
    sections = [
        {"title": "S", "rows": [{"id": f"r{i}", "title": f"R{i}"} for i in range(15)]},
    ]
    capped = cap_list_rows_total(sections)
    assert _total_rows(capped) == MAX_LIST_ROWS_TOTAL


def test_round_robin_distributes_fairly():
    """Si una section tiene muchísimo y otra poco, el cap reparte de
    forma que las pocas no quedan sin representación."""
    sections = [
        {"title": "Mucho", "rows": [{"id": f"a{i}", "title": f"A{i}"} for i in range(20)]},
        {"title": "Poco", "rows": [{"id": "b", "title": "B"}]},
    ]
    capped = cap_list_rows_total(sections, total_cap=10)
    titles = [s["title"] for s in capped]
    assert "Poco" in titles  # la sección "Poco" sobrevive
    # Total cap respetado
    assert _total_rows(capped) == 10


def test_section_with_less_than_quota_redistributes():
    """Si una section tiene MENOS rows que su cuota, el sobrante va a las
    otras (no se desperdicia)."""
    sections = [
        {"title": "A", "rows": [{"id": "a1", "title": "A1"}]},  # 1 row
        {"title": "B", "rows": [{"id": f"b{i}", "title": f"B{i}"} for i in range(20)]},  # 20 rows
    ]
    capped = cap_list_rows_total(sections, total_cap=10)
    # A sigue con 1 row; B debe tener 9 (no 5)
    a_rows = next(s for s in capped if s["title"] == "A")["rows"]
    b_rows = next(s for s in capped if s["title"] == "B")["rows"]
    assert len(a_rows) == 1
    assert len(b_rows) == 9
    assert _total_rows(capped) == 10


def test_total_rows_never_exceeds_cap():
    """Property: para cualquier input no-vacío, total_rows(output) <= cap."""
    import random

    random.seed(42)
    for _ in range(20):
        n_sections = random.randint(1, 8)
        sections = [
            {
                "title": f"S{i}",
                "rows": [
                    {"id": f"s{i}_r{j}", "title": f"row{j}"}
                    for j in range(random.randint(0, 15))
                ],
            }
            for i in range(n_sections)
        ]
        capped = cap_list_rows_total(sections, total_cap=10)
        assert _total_rows(capped) <= 10
