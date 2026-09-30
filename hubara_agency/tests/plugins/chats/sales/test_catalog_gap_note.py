"""Ventas dice que NO existe lo que el cliente pide o muestra fuera del catálogo.

Incidente 2026-09-23 (lead CTWA «Velas Artesanales»): el cliente mandó una
foto — «[el cliente envió una foto: vela azul en forma de cubo con texto
'Love', vela rosa en forma de cilindro, vela roja en forma de cubo de
corazones, vela rosa con diseño de dragón]» — y escribió «Estás y en vaso
también». Ningún producto viene en vaso y no vendemos velas de dragón (Cubo
Love tiene una sola variante, «Unico»). Ventas contestó solo con otra tarjeta
de catálogo y nunca lo aclaró; el cliente siguió creyendo que existían y el
remarketing terminó afirmando «el Cubo Love también viene en vaso».

Mecánica: el ingest compara, sin LLM, lo que el cliente pidió o mostró contra
el catálogo (`unavailable_terms`, el mismo detector de la guarda del
remarketing, en `chats/shared`) y el turno lleva una nota que NOMBRA lo que no
existe — el A/B del remarketing mostró que la regla sola no alcanza, nombrarlo
sí. El guion (SOUL.md) dice qué hacer con eso: decirlo y ofrecer la
alternativa real más cercana, nunca solo reenviar el catálogo.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.catalog_gap import build_catalog_gap_note
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.sdk.catalogkit import CatalogProductDTO, CatalogVariantDTO
from tests.test_ingest_inbound_message import (
    FakeHistoryStore,
    FakeLoadOrStart,
    FakeMetadataStore,
)

_TAGS = ["Aroma: Café", "Aroma: Lavanda", "Color: Azul", "Color: Rosado", "Color: Rojo"]


def _product(title: str, handle: str, description: str = "") -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}",
        handle=handle,
        title=title,
        status="published",
        description=description,
        variants=[CatalogVariantDTO(id=f"var_{handle}", title="Unico")],
        tags=list(_TAGS),
        options={"Unico": ["Unico"]},
    )


CATALOG = [
    _product(
        "Cubo Love",
        "cubo-love",
        "La clásica vela cúbica con la palabra \"LOVE\" grabada en bajorrelieve.",
    ),
    _product("Cilindro Love", "cilindro-love", "Vela cilíndrica con la palabra LOVE."),
    _product("Cubo de corazón", "cubo-de-corazon", "Vela de corazones entrelazados."),
    _product("Velón Koala", "velon-koala", "Velón con forma de koala."),
]

PHOTO = (
    "[el cliente envió una foto: vela azul en forma de cubo con texto 'Love', "
    "vela rosa en forma de cilindro, vela roja en forma de cubo de corazones, "
    "vela rosa con diseño de dragón]"
)
ASKS_VASO = "Estás y en vaso también"


# --- La nota (pura) ---------------------------------------------------------


def test_photo_of_a_dragon_candle_names_dragon_as_not_in_catalog() -> None:
    note = build_catalog_gap_note(PHOTO, CATALOG)

    assert note is not None
    assert "«dragón»" in note
    assert "no aparece por nombre en el catálogo" in note
    # lo que sí vendemos (cubo, cilindro, corazones) no se marca
    for real in ("cubo", "cilindro", "corazones", "love"):
        assert f"«{real}»" not in note.lower()


def test_asking_for_a_glass_version_names_vaso_and_asks_for_the_real_alternative() -> None:
    note = build_catalog_gap_note(ASKS_VASO, CATALOG)

    assert note is not None
    assert "«vaso»" in note
    assert "alternativa" in note
    assert "solo el catálogo" in note  # no basta con reenviar el catálogo


SACRIFICIO = _product(
    "Sacrificio de Amor",
    "sacrificio-de-amor",
    "Enmarcada en una estructura geométrica pura en forma de cruz, esta vela en relieve simboliza "
    "la entrega. El contraste entre el marco sólido y el detallado rostro interior crea un juego "
    "visual único.",
)
PHOTO_OF_OUR_CATALOG = (
    "[el cliente envió una foto: vela gris con detalles dorados en forma de cruz y rostro de Jesús]"
    ' con el texto: "tienes esta?"'
)


def test_the_note_never_denies_before_searching_the_catalog() -> None:
    """Caso 6543 del laboratorio (caso-fotos-0929-r4, turno 2): la foto era una
    captura de nuestro catálogo («Sacrificio de Amor»); la visión la describió
    como «vela gris… rostro de Jesús» y «jesús» no aparece escrito en el
    catálogo. La nota decía «NO existe… díselo claro (eso no lo manejamos)» y el
    bot lo negó sin buscar. Lo que no aparece por nombre puede estar descrito
    con otras palabras: primero se busca, y solo se niega si no hay nada parecido."""
    note = build_catalog_gap_note(PHOTO_OF_OUR_CATALOG, [*CATALOG, SACRIFICIO])

    assert note is not None and "«jesús»" in note
    assert "NO existe" not in note
    assert "Antes de responder, búscalo con search_products" in note
    assert "no le digas que no lo manejamos" in note
    assert note.index("search_products") < note.index("(eso no lo manejamos)"), "negar es el último recurso"


def test_question_about_a_real_product_has_no_note() -> None:
    assert build_catalog_gap_note("¿El cubo de corazón viene en azul?", CATALOG) is None


def test_everyday_phrases_are_not_products() -> None:
    """«en cuanto», «de acuerdo», «con nequi»: el turno no se llena de avisos
    de «eso no existe» por palabras que no son productos."""
    text = "De acuerdo, en cuanto pueda te pago con nequi o con tarjeta"

    assert build_catalog_gap_note(text, CATALOG) is None


def test_without_catalog_there_is_no_note() -> None:
    """Sin catálogo no se puede saber qué no existe: mejor callar que acusar."""
    assert build_catalog_gap_note(ASKS_VASO, []) is None


# --- El ingest la pone en el turno de Ventas --------------------------------


class _Catalog:
    def __init__(self, products: list[CatalogProductDTO] | None = None, *, fail: bool = False):
        self._products = products or []
        self._fail = fail

    async def search(self, q: str, *, limit: int = 10, category: str | None = None):
        if self._fail:
            raise OSError("snapshot no disponible")
        return SimpleNamespace(results=list(self._products)[:limit])


def _text(text: str) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.GAP",
        from_number="5490000000000",
        phone_number_id="PID",
        text=text,
        media=None,
        timestamp="1714312345",
    )


def _ingest(loader: FakeLoadOrStart, catalog: _Catalog | None) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
        catalog=catalog,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_sales_turn_carries_the_gap_note_when_the_customer_asks_for_a_vaso() -> None:
    loader = FakeLoadOrStart()

    await _ingest(loader, _Catalog(CATALOG)).execute(_text(ASKS_VASO))

    assert len(loader.calls) == 1
    notes = loader.calls[0].extra_context or []
    assert any("«vaso»" in n and "no aparece por nombre en el catálogo" in n for n in notes), notes


@pytest.mark.asyncio
async def test_sales_turn_after_the_photo_carries_the_gap_note_for_dragon() -> None:
    """La descripción de la foto reentra por el ingest como texto sintético."""
    loader = FakeLoadOrStart()

    await _ingest(loader, _Catalog(CATALOG)).execute(_text(PHOTO))

    notes = loader.calls[0].extra_context or []
    assert any("«dragón»" in n for n in notes), notes


@pytest.mark.asyncio
async def test_catalog_down_never_blocks_the_turn() -> None:
    loader = FakeLoadOrStart()

    await _ingest(loader, _Catalog(fail=True)).execute(_text(ASKS_VASO))

    assert len(loader.calls) == 1
    assert loader.calls[0].extra_context is None


# --- El guion dice qué hacer con eso ----------------------------------------

SOUL = Path("src/plugins/chats/agent/sales/workspace/SOUL.md")


def test_soul_says_to_search_first_then_state_the_gap_and_offer_the_closest_real_alternative() -> None:
    soul = SOUL.read_text(encoding="utf-8")
    rule = next(
        (line for line in soul.splitlines() if "no aparece por nombre en el catálogo" in line.lower()),
        None,
    )

    assert rule is not None, "SOUL.md no tiene la regla de lo que no aparece por nombre en el catálogo"
    assert "búscalo con search_products" in rule, rule
    assert rule.index("search_products") < rule.index("manejamos"), "negar es el último recurso"
    assert re.search(r"no (lo|la|los|las) manejamos", rule), rule
    assert "alternativa real más cercana" in rule, rule
    assert "solo el catálogo" in rule, rule
