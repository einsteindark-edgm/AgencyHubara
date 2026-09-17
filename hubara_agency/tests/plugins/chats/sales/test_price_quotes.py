"""Módulo puro `price_quotes`: montos COP en texto, enmascarado de precios del
anuncio y detección de montos sin respaldo en el catálogo (run ebbc203d)."""
from __future__ import annotations

from src.plugins.chats.agent.sales.price_quotes import (
    PRICE_MASK,
    extract_cop_amounts,
    find_unexplained_amounts,
    mask_prices,
)


def test_extracts_es_co_formats() -> None:
    text = "Vale *$45.000 COP*, o $ 49.500, o 16.940 pesos, o 17500 COP; no 3001234567 ni $12."
    assert extract_cop_amounts(text) == [45000, 49500, 16940, 17500]


def test_mask_prices_replaces_amounts_keeping_the_rest() -> None:
    body = "Set Trilogía del Terror x 3 velas 🎃\n💲 $45.000\n🕯️Aromatizadas\n🚚 Envíos"
    masked = mask_prices(body)
    assert "45.000" not in masked
    assert PRICE_MASK in masked
    assert "Trilogía del Terror" in masked and "Envíos" in masked


def test_mask_prices_without_amounts_is_identity() -> None:
    assert mask_prices("Velas aromáticas 🕯️") == "Velas aromáticas 🕯️"


def test_product_price_sentence_with_non_catalog_amount_is_unexplained() -> None:
    text = (
        "El set de la Trilogía del Terror tiene un valor de *$45.000 COP*.\n\n"
        "Sobre el pago: sí manejamos contra entrega, y aplica justo desde $45.000 en productos."
    )
    hits = find_unexplained_amounts(text, catalog_prices={49500, 16000}, policy_amounts={45000, 7900, 16940})
    assert [h.amount for h in hits] == [45000]
    assert "tiene un valor de" in hits[0].sentence


def test_policy_amounts_need_policy_context() -> None:
    ok = "El contra entrega aplica desde $45.000 en productos; vas en $49.500."
    assert find_unexplained_amounts(ok, catalog_prices={49500}, policy_amounts={45000}) == []


def test_catalog_multiples_and_totals_are_explained() -> None:
    text = "Serían 2 sets: $99.000 en productos. Con el envío mínimo ($16.940) quedaría en $115.940."
    assert find_unexplained_amounts(text, catalog_prices={49500}, policy_amounts={16940}) == []


def test_difference_to_threshold_is_explained_in_policy_context() -> None:
    text = "Vas en $29.000 en productos; te faltan $16.000 para el contra entrega."
    assert find_unexplained_amounts(text, catalog_prices={29000}, policy_amounts={45000}) == []


def test_unexplained_amount_without_context_is_flagged() -> None:
    hits = find_unexplained_amounts("Te queda en $40.000 🤍", catalog_prices={49500}, policy_amounts={45000})
    assert [h.amount for h in hits] == [40000]
