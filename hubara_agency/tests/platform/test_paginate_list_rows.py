"""Tests para `paginate_list_rows` — bug 10d8bd21 fix v2 (no destructivo).

En vez de recortar las opciones al cap de 10, paginamos en N mensajes
consecutivos. Garantías:

  1. Total de rows preservado (suma de rows en todas las pages == input).
  2. Ninguna page excede `page_cap`.
  3. Si una section ENTERA cabe en una página, no se parte (UX).
  4. Si una section sola excede el cap, se parte en chunks.
  5. Orden de sections + rows preservado.
"""
from __future__ import annotations

import random

from src.platform.whatsapp.limits import (
    MAX_LIST_ROWS_TOTAL,
    paginate_list_rows,
)


def _total_rows(pages):
    return sum(len(s["rows"]) for p in pages for s in p)


def _page_rows(page):
    return sum(len(s["rows"]) for s in page)


# =============================================================================
# Caso exacto del bug 10d8bd21: 11 aromas en 4 sections
# =============================================================================


def test_11_aromas_split_into_2_pages_without_breaking_sections():
    sections = [
        {"title": "Frescos", "rows": [
            {"id": f"f{i}", "title": f"F{i}"} for i in range(4)
        ]},
        {"title": "Cítricos y frutales", "rows": [
            {"id": f"c{i}", "title": f"C{i}"} for i in range(2)
        ]},
        {"title": "Cálidos y dulces", "rows": [
            {"id": f"d{i}", "title": f"D{i}"} for i in range(2)
        ]},
        {"title": "Notas perfumadas", "rows": [
            {"id": f"n{i}", "title": f"N{i}"} for i in range(3)
        ]},
    ]
    pages = paginate_list_rows(sections, page_cap=10)
    assert len(pages) == 2

    # Page 1: Frescos(4) + Cítricos(2) + Cálidos(2) = 8 rows
    assert _page_rows(pages[0]) == 8
    page1_titles = [s["title"] for s in pages[0]]
    assert page1_titles == ["Frescos", "Cítricos y frutales", "Cálidos y dulces"]

    # Page 2: Notas (3) sola — la sección NO se partió en 2 mensajes
    assert _page_rows(pages[1]) == 3
    assert pages[1][0]["title"] == "Notas perfumadas"

    # Total preservado
    assert _total_rows(pages) == 11


def test_under_cap_returns_single_page():
    sections = [
        {"title": "S1", "rows": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]},
    ]
    pages = paginate_list_rows(sections, page_cap=10)
    assert len(pages) == 1
    assert _total_rows(pages) == 2


def test_empty_input_returns_empty():
    assert paginate_list_rows([], page_cap=10) == []


def test_section_with_no_rows_skipped():
    sections = [
        {"title": "Vacío", "rows": []},
        {"title": "Lleno", "rows": [{"id": "a", "title": "A"}]},
    ]
    pages = paginate_list_rows(sections, page_cap=10)
    titles = [s["title"] for p in pages for s in p]
    assert "Vacío" not in titles
    assert "Lleno" in titles


def test_section_larger_than_cap_gets_split():
    """Una section de 15 rows con cap 10 → primera page tiene 10, segunda
    tiene 5 (ambas con el mismo title, partida)."""
    sections = [
        {"title": "Mega", "rows": [{"id": f"r{i}", "title": f"R{i}"} for i in range(15)]},
    ]
    pages = paginate_list_rows(sections, page_cap=10)
    assert len(pages) == 2
    assert _page_rows(pages[0]) == 10
    assert _page_rows(pages[1]) == 5
    # Ambas paginas contienen la misma section title
    assert pages[0][0]["title"] == "Mega"
    assert pages[1][0]["title"] == "Mega"


def test_section_fills_remainder_of_current_page_when_split_unavoidable():
    """Si la página actual tiene 7 y viene una section de 15, llena los
    3 huecos restantes y rolea el resto a páginas nuevas."""
    sections = [
        {"title": "Pre", "rows": [{"id": f"p{i}", "title": f"P{i}"} for i in range(7)]},
        {"title": "Mega", "rows": [{"id": f"r{i}", "title": f"R{i}"} for i in range(15)]},
    ]
    pages = paginate_list_rows(sections, page_cap=10)
    # Página 1: Pre(7) + Mega(3) = 10
    assert _page_rows(pages[0]) == 10
    # Página 2: Mega(10)
    assert _page_rows(pages[1]) == 10
    # Página 3: Mega(2)
    assert _page_rows(pages[2]) == 2
    # Total: 7+15 = 22 preservados
    assert _total_rows(pages) == 22


def test_section_fits_alone_starts_new_page_when_current_not_empty():
    """Si la page actual tiene 7 y viene una section de 5 (no cabe en
    10-7=3), abre nueva page para la section de 5 — NO la partís."""
    sections = [
        {"title": "Pre", "rows": [{"id": f"p{i}", "title": f"P{i}"} for i in range(7)]},
        {"title": "Cinco", "rows": [{"id": f"c{i}", "title": f"C{i}"} for i in range(5)]},
    ]
    pages = paginate_list_rows(sections, page_cap=10)
    assert len(pages) == 2
    assert _page_rows(pages[0]) == 7
    assert _page_rows(pages[1]) == 5
    # "Cinco" no se partió
    assert pages[1][0]["title"] == "Cinco"
    assert len(pages[1][0]["rows"]) == 5


def test_preserves_row_order():
    rows = [{"id": f"r{i}", "title": f"R{i}"} for i in range(15)]
    sections = [{"title": "S", "rows": rows}]
    pages = paginate_list_rows(sections, page_cap=10)
    flat = [r for p in pages for s in p for r in s["rows"]]
    assert flat == rows  # mismo orden, sin perder ni reordenar


def test_uses_default_constant():
    """Llamada sin cap usa MAX_LIST_ROWS_TOTAL."""
    sections = [{"title": "S", "rows": [{"id": f"r{i}", "title": "x"} for i in range(15)]}]
    pages = paginate_list_rows(sections)
    # Default cap = 10 → 2 pages (10 + 5)
    assert all(_page_rows(p) <= MAX_LIST_ROWS_TOTAL for p in pages)


# =============================================================================
# Property tests — invariantes que SIEMPRE deben mantenerse
# =============================================================================


def test_property_no_page_exceeds_cap():
    """Para cualquier input, ninguna página excede el cap."""
    random.seed(42)
    for _ in range(50):
        n_sections = random.randint(0, 10)
        sections = [
            {
                "title": f"S{i}",
                "rows": [
                    {"id": f"s{i}_r{j}", "title": f"R{j}"}
                    for j in range(random.randint(0, 20))
                ],
            }
            for i in range(n_sections)
        ]
        cap = random.choice([5, 10, 15])
        pages = paginate_list_rows(sections, page_cap=cap)
        for p in pages:
            assert _page_rows(p) <= cap, (
                f"Page con {_page_rows(p)} rows excede cap {cap}"
            )


def test_property_total_rows_preserved():
    """Total de rows en la salida == total de rows en la entrada
    (excluyendo sections vacías)."""
    random.seed(123)
    for _ in range(30):
        sections = [
            {
                "title": f"S{i}",
                "rows": [
                    {"id": f"s{i}_r{j}", "title": "x"}
                    for j in range(random.randint(0, 15))
                ],
            }
            for i in range(random.randint(0, 6))
        ]
        input_total = sum(len(s["rows"]) for s in sections)
        pages = paginate_list_rows(sections, page_cap=10)
        assert _total_rows(pages) == input_total
