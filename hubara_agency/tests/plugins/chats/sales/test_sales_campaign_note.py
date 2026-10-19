"""El turno de Ventas lleva el guion de la campaña (operador, 2026-10-19).

Fin de semana 17-18 oct: el cliente de las velitas preguntaba el precio apenas
veía el catálogo, una caja no alcanzaba el mínimo de contra entrega y nada lo
movía a comprar ya. Ahora el ingest reconoce que la conversación es de la
campaña (el anuncio, el mensaje prellenado o el producto del pedido), marca el
episodio y le pasa al turno el guion con los precios del catálogo. Las demás
conversaciones no reciben nada nuevo.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from src.plugins.chats.shared import sales_campaigns as sc
from src.sdk.catalogkit import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
from tests.test_ingest_inbound_message import FakeHistoryStore, FakeLoadOrStart, FakeMetadataStore

PREFILLED = "¡Hola! Quiero más información sobre las velitas del 7 de diciembre."


def _product(handle: str, title: str, price: int) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published",
        variants=[CatalogVariantDTO(
            id=f"var_{handle}", title="Multicolor",
            prices=[CatalogPriceDTO(amount=str(price), currency_code="cop")],
        )],
        options={"Color": ["Rojo y verde", "Multicolor"]},
    )


CATALOG = [
    _product("velas-de-deseos-onduladas", "Caja de Velas de deseos onduladas", 22_500),
    _product("velas-de-deseos-cuadradas", "Caja de Velas de deseos cuadradas", 25_000),
    _product("trilogia-del-terror", "Trilogía del Terror", 45_000),
]


class _Catalog:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def search(self, q: str, *, limit: int = 10, category: str | None = None):
        if self._fail:
            raise OSError("snapshot no disponible")
        return SimpleNamespace(results=list(CATALOG)[:limit])


@pytest.fixture(autouse=True)
def _campaign_on(monkeypatch):
    """La campaña del repo, vigente el día de la prueba (sus fechas son de 2026)."""
    (campaign,) = sc.load_campaigns(Path(__file__).resolve().parents[3] / "fixtures" / "sales_campaigns")
    always = sc.Campaign(**{**vars(campaign), "desde": date(2000, 1, 1), "hasta": date(2999, 1, 1)})
    monkeypatch.setattr(sc, "active_campaigns", lambda: (always,))
    # El paquete vivo: el que sabe que las velas de deseos se cobran por caja.
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")


def _text(text: str) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.CAMP", from_number="5490000000000", phone_number_id="PID",
        text=text, media=None, timestamp="1714312345",
    )


async def _turn(text: str, *, catalog: _Catalog | None = None) -> tuple[list[str], dict]:
    loader, store = FakeLoadOrStart(), FakeMetadataStore()
    ingest = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
        catalog=catalog or _Catalog(),  # type: ignore[arg-type]
    )
    await ingest.execute(_text(text))
    (session_id,) = store.store
    return list(loader.calls[0].extra_context or []), store.store[session_id]


@pytest.mark.asyncio
async def test_a_customer_of_the_campaign_gets_its_script_with_the_catalog_prices() -> None:
    notes, metadata = await _turn(PREFILLED)

    (note,) = [n for n in notes if "GUION DE LA CAMPAÑA" in n]
    assert "$22.500 la caja de 10" in note and "$25.000 la caja de 12" in note
    assert "2 × Caja de Velas de deseos onduladas: $45.000 (paga contra entrega)" in note
    assert "Trilogía" not in note
    assert metadata["episodes"][-1][sc.EPISODE_KEY] == "velitas-7-dic"


@pytest.mark.asyncio
async def test_another_customer_gets_no_script() -> None:
    notes, metadata = await _turn("Hola, ¿qué precio tiene la Trilogía del Terror?")

    assert not any("GUION DE LA CAMPAÑA" in n for n in notes)
    assert sc.EPISODE_KEY not in metadata["episodes"][-1]


@pytest.mark.asyncio
async def test_without_catalog_the_script_still_goes_without_prices() -> None:
    notes, _ = await _turn(PREFILLED, catalog=_Catalog(fail=True))

    (note,) = [n for n in notes if "GUION DE LA CAMPAÑA" in n]
    assert "search_products" in note and "$" not in note


@pytest.mark.asyncio
async def test_a_broken_campaign_never_breaks_the_turn(monkeypatch) -> None:
    """Revisión del 2026-10-19: una campaña que falla al evaluarse no puede
    tumbar el ingest de todos los clientes."""
    def _boom(*args, **kwargs):
        raise TypeError("can't compare datetime.datetime to datetime.date")

    monkeypatch.setattr(sc, "campaign_for", _boom)

    notes, _ = await _turn(PREFILLED)

    assert not any("GUION DE LA CAMPAÑA" in n for n in notes)


@pytest.mark.asyncio
async def test_the_words_of_the_ad_only_count_in_the_first_message() -> None:
    """Revisión del 2026-10-19: «noche de velitas» es de temporada; quien pide
    otra cosa para esa noche en la mitad de la charla no es de la campaña."""
    loader, store = FakeLoadOrStart(), FakeMetadataStore()
    ingest = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
        catalog=_Catalog(),  # type: ignore[arg-type]
    )
    await ingest.execute(_text("Hola, ¿qué precio tiene la Trilogía del Terror?"))
    second = WhatsAppMessage(
        message_id="wamid.CAMP2", from_number="5490000000000", phone_number_id="PID",
        text="¿Y qué tienen para la noche de velitas?", media=None, timestamp="1714312400",
    )
    await ingest.execute(second)

    assert not any("GUION DE LA CAMPAÑA" in n for n in loader.calls[-1].extra_context or [])
