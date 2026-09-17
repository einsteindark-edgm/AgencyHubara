"""El banner del referral CTWA (anuncio) que ve el LLM NO trae precios.

Incidente run ebbc203d (2026-09-16): el anuncio decía "💲 $45.000", el
catálogo tenía el set a $49.500, y el LLM citó el anuncio ("tiene un valor de
*$45.000 COP*") ignorando el envelope del catálogo que había leído en el
mismo turno. El anuncio es contexto de ORIGEN, no fuente de precio: los
montos del headline/body se enmascaran antes de inyectarlos al prompt.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.price_quotes import PRICE_MASK
from src.plugins.chats.agent.sales.translate import _prepend_referral_banner_if_needed

_AD = {
    "source_type": "ad",
    "headline": "Velas aromáticas desde $45.000",
    "body": "¿En qué rincón de tu casa pondrías estas velas? 🏠\nSet Trilogía del Terror x 3 velas 🎃\n💲 $45.000\n🕯️Aromatizadas\n🚚 Envíos",
}


def test_ad_prices_are_masked_in_the_banner() -> None:
    tags: list[str] = []
    text = _prepend_referral_banner_if_needed("Hola, quiero información", _AD, False, tags)
    banner = text[: -len("\nHola, quiero información")]  # el body del anuncio trae saltos de línea
    assert "45.000" not in banner and "45000" not in banner
    assert PRICE_MASK in banner
    assert "Trilogía del Terror" in banner
    assert "referral:banner_injected" in tags
    assert text.endswith("Hola, quiero información")


def test_ad_without_prices_is_untouched() -> None:
    ad = {"source_type": "ad", "headline": "Velas aromáticas", "body": "Hechas a mano 🕯️"}
    text = _prepend_referral_banner_if_needed("Hola", ad, False, [])
    assert "titulado 'Velas aromáticas'" in text and "(Hechas a mano 🕯️)" in text
    assert PRICE_MASK not in text
