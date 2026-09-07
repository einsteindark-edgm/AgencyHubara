"""Detección determinista de "este producto trae portavela".

Run 943e6bff (2026-09-07): el cliente cerró un pedido SIN portavelas y
recibió "Al finalizar el pago del pedido se escogen los colores del
portavelas, según disponibilidad". La política del portavelas solo aplica a
los productos que lo incluyen (hoy en el catálogo vivo: el Dúo Zodiacal, cuya
description dice "un plato portavela de concreto pulido"). La fuente de
verdad es el CATÁLOGO, no el LLM: un flag explícito en `metadata.portavelas`
gana; si no está, la mención en título/description decide.
"""
from __future__ import annotations

from src.platform.catalog import CatalogProductDTO
from src.platform.catalog.portavelas import (
    PORTAVELAS_METADATA_KEY,
    order_includes_portavelas,
    product_includes_portavelas,
)


def _product(
    handle: str,
    *,
    title: str = "Vela",
    description: str | None = None,
    metadata: dict[str, str] | None = None,
) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}",
        handle=handle,
        title=title,
        status="published",
        description=description,
        metadata=metadata,
    )


def test_duo_zodiacal_description_mentions_portavela() -> None:
    p = _product(
        "duo-zodiacal",
        title="Duo Zodiacal",
        description=(
            "Diseñada para encenderse directamente sobre su base: un plato "
            "portavela de concreto pulido con acabado marmoleado."
        ),
    )
    assert product_includes_portavelas(p) is True


def test_plain_candle_without_mention_has_no_portavelas() -> None:
    p = _product(
        "cruz-vida",
        title="Cruz de Vida",
        description="Una representación majestuosa de la resurrección.",
    )
    assert product_includes_portavelas(p) is False


def test_product_without_description_has_no_portavelas() -> None:
    assert product_includes_portavelas(_product("velon-gorrion")) is False


def test_metadata_flag_true_wins_over_silent_description() -> None:
    p = _product(
        "set-nuevo",
        description="Vela pilar artesanal.",
        metadata={PORTAVELAS_METADATA_KEY: "true"},
    )
    assert product_includes_portavelas(p) is True


def test_metadata_flag_false_wins_over_description_mention() -> None:
    """El operador puede apagar la política aunque la description mencione
    el portavela (ej. texto de marketing sobre un accesorio que ya no va)."""
    p = _product(
        "duo-viejo",
        description="Antes venía con plato portavela.",
        metadata={PORTAVELAS_METADATA_KEY: "false"},
    )
    assert product_includes_portavelas(p) is False


def test_order_includes_portavelas_if_any_item_does() -> None:
    cruz = _product("cruz-vida", description="Vela en forma de cruz.")
    duo = _product("duo-zodiacal", description="plato portavela de concreto")
    assert order_includes_portavelas([cruz]) is False
    assert order_includes_portavelas([cruz, duo]) is True
    assert order_includes_portavelas([]) is False
