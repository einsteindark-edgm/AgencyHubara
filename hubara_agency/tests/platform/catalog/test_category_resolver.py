"""Resolver determinístico de categorías (typo-tolerante, sin LLM).

El cliente escribe "velas religosas" / "religiosas" / "difusor" y el sistema
DEBE resolver a la categoría real del catálogo — o decir honestamente que no
existe, con la lista cerrada de las que sí. Nada de esto puede depender del
LLM: es un choke point determinista (mismo patrón que el variant picker).
"""
from __future__ import annotations

from src.platform.catalog.categories import (
    CatalogCategoryDTO,
    resolve_category,
)

CATS = [
    CatalogCategoryDTO(
        slug="velas-aromaticas", label="Velas Aromáticas", product_count=4
    ),
    CatalogCategoryDTO(
        slug="velas-religiosas", label="Velas Religiosas", product_count=3
    ),
    CatalogCategoryDTO(slug="difusores", label="Difusores", product_count=2),
]


def test_resolve_exact_label_ignoring_case_and_accents():
    res = resolve_category("velas religiosas", CATS)
    assert res.matched is not None
    assert res.matched.slug == "velas-religiosas"
    assert res.confidence == "exact"


def test_resolve_partial_name_matches_the_only_category_with_that_word():
    res = resolve_category("religiosas", CATS)
    assert res.matched is not None
    assert res.matched.slug == "velas-religiosas"


def test_resolve_tolerates_singular_plural():
    res = resolve_category("difusor", CATS)
    assert res.matched is not None
    assert res.matched.slug == "difusores"


def test_resolve_tolerates_typo():
    res = resolve_category("velas religosas", CATS)
    assert res.matched is not None
    assert res.matched.slug == "velas-religiosas"
    assert res.confidence == "fuzzy"


def test_resolve_typo_in_single_word_query():
    res = resolve_category("aromaticaz", CATS)
    assert res.matched is not None
    assert res.matched.slug == "velas-aromaticas"


def test_ambiguous_query_returns_candidates_not_a_wrong_match():
    res = resolve_category("velas", CATS)
    assert res.matched is None
    assert res.confidence == "ambiguous"
    assert [c.slug for c in res.candidates] == [
        "velas-aromaticas",
        "velas-religiosas",
    ]


def test_unknown_query_matches_nothing():
    res = resolve_category("zapatos", CATS)
    assert res.matched is None
    assert res.confidence == "none"
    assert res.candidates == []


def test_resolve_ignores_filler_words_around_the_category():
    for query in ("las religiosas", "quiero ver las religiosas", "de las religiosas porfa"):
        res = resolve_category(query, CATS)
        assert res.matched is not None, query
        assert res.matched.slug == "velas-religiosas", query


def test_filler_words_do_not_drown_a_typo_match():
    res = resolve_category("me muestras las aromaticaz porfa", CATS)
    assert res.matched is not None
    assert res.matched.slug == "velas-aromaticas"


def test_nonsense_still_matches_nothing():
    for query in ("quiero unos zapatos porfa", "tienen bicicletas"):
        res = resolve_category(query, CATS)
        assert res.matched is None, query
        assert res.candidates == [], query
