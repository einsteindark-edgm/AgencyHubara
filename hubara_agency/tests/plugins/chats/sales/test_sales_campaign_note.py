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
    # Sin cupón por defecto: ninguna prueba sale a la red a buscar Medusa.
    import src.sdk.connectorkit as connectorkit

    monkeypatch.setattr(connectorkit, "get_promotions_port", lambda: _Promotions(None))
    # Un pedido anterior es de verdad por defecto (cuenta como compra).
    monkeypatch.setattr(connectorkit, "get_order_facts_port", lambda: _Facts())


class _Facts:
    """OrderFacts de prueba: todos los pedidos que se pregunten, de un tipo."""

    def __init__(self, *, is_test: bool = False, stage: str = "delivered", fail: bool = False) -> None:
        self._is_test, self._stage, self._fail = is_test, stage, fail

    async def get_facts(self, ids):
        if self._fail:
            raise OSError("medusa caída")
        facts = {i: SimpleNamespace(order_id=i, stage=self._stage, is_test=self._is_test) for i in ids}
        return SimpleNamespace(facts=facts, unresolved=set())


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
    if not (Path(__file__).resolve().parents[5] / "forge").is_dir():
        pytest.skip("clon de forge: las cajas (las velas de deseos) son de la tienda madre")
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


# ── la oferta con fecha límite cuando el cliente aplaza (operador, 2026-10-20) ─
#
# «Que eso lo diga en la venta cuando dicen te aviso o hay tiempo»: con Jev, la
# capacidad `compra` lee que el cliente aplaza (aquí, sin Jev, la regla de
# hoy). La oferta va una sola vez por episodio y con la fecha de fin del cupón
# en Medusa.

from datetime import datetime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

_BOGOTA = ZoneInfo("America/Bogota")
#: El último día del cupón, en 10 días (el dashboard guarda el fin al día siguiente a las 00:00).
LAST_DAY = datetime.now(_BOGOTA).date() + timedelta(days=10)


def _live_coupon(**extra):
    from src.sdk.connectorkit import PromotionDTO

    ends = datetime.combine(LAST_DAY + timedelta(days=1), datetime.min.time(), tzinfo=_BOGOTA)
    fields = dict(
        id="promo_1", code="BIENVENIDA", discount_type="percentage", value=5, currency_code=None,
        target_type="items", allocation="across", max_quantity=None, product_ids=(), variant_ids=(),
        collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=int(ends.timestamp() * 1000), budget_type=None, budget_limit=None, budget_used=None,
        description=None, first_purchase_only=True,
    )
    return PromotionDTO(**{**fields, **extra})


class _Promotions:
    def __init__(self, promotion) -> None:
        self._promotion = promotion

    async def get_by_code(self, code: str):
        return self._promotion if self._promotion is not None and code == self._promotion.code else None


def _messages(*texts: str) -> list[WhatsAppMessage]:
    return [
        WhatsAppMessage(
            message_id=f"wamid.OFFER{i}", from_number="5490000000000", phone_number_id="PID",
            text=text, media=None, timestamp=str(1714312345 + 60 * i),
        )
        for i, text in enumerate(texts)
    ]


async def _conversation(
    monkeypatch, promotion, *texts: str, before=None, readings=None, hooks=None
) -> tuple[list[list[str]], FakeMetadataStore]:
    """`before`: antes del segundo mensaje. `hooks`: {índice del mensaje: lo
    que pasa justo antes de que llegue} (la ruta, una respuesta nuestra)."""
    import src.sdk.connectorkit as connectorkit

    monkeypatch.setattr(connectorkit, "get_promotions_port", lambda: promotion if hasattr(promotion, "get_by_code") else _Promotions(promotion))
    loader, store = FakeLoadOrStart(), FakeMetadataStore()
    ingest = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
        catalog=_Catalog(),  # type: ignore[arg-type]
        **({"readings": readings} if readings is not None else {}),
    )
    notes: list[list[str]] = []
    for i, message in enumerate(_messages(*texts)):
        if i == 1 and before is not None:
            before(store)
        if hooks and i in hooks:
            hooks[i](store)
        await ingest.execute(message)
        notes.append(list(loader.calls[-1].extra_context or []))
    return notes, store


def _script(notes: list[str]) -> str:
    (note,) = [n for n in notes if n.startswith("[GUION DE LA CAMPAÑA")]
    return note


@pytest.mark.asyncio
async def test_a_customer_who_defers_gets_the_offer_with_its_deadline_once(monkeypatch) -> None:
    notes, _ = await _conversation(
        monkeypatch, _live_coupon(), PREFILLED, "Lo pienso y te aviso", "Bueno, igual te aviso después"
    )
    day = f"{LAST_DAY.day} de "

    first, deferring, again = (_script(n) for n in notes)
    assert "BIENVENIDA" in first and "acaba de aplazar" not in first
    assert "acaba de aplazar" in deferring and f"hasta el {day}" in deferring
    # La nota del aplazamiento deja decirla en la misma frase breve.
    assert any("EL CLIENTE APLAZÓ" in n and "oferta" in n for n in notes[1])
    # Una sola vez por episodio.
    assert "acaba de aplazar" not in again


@pytest.mark.asyncio
async def test_without_a_live_coupon_nobody_is_offered_a_discount(monkeypatch) -> None:
    notes, _ = await _conversation(monkeypatch, None, PREFILLED, "Lo pienso y te aviso")

    assert "BIENVENIDA" not in _script(notes[1]) and "acaba de aplazar" not in _script(notes[1])


def _old_order(store: FakeMetadataStore) -> None:
    (sid,) = store.store
    old = {"episode_id": "ep_000", "started_at_ms": 1, "closed_at_ms": 2, "closing_tag": "COMPRA_EXITOSA",
           "order_id": "order_old"}
    store.store[sid]["episodes"].insert(0, old)


@pytest.mark.asyncio
async def test_a_customer_who_bought_before_is_not_offered_a_first_purchase_coupon(monkeypatch) -> None:
    notes, _ = await _conversation(monkeypatch, _live_coupon(), PREFILLED, "Lo pienso y te aviso", before=_old_order)

    assert "acaba de aplazar" not in _script(notes[1]) and "BIENVENIDA" not in _script(notes[1])


@pytest.mark.asyncio
async def test_a_coupon_for_everyone_is_offered_to_who_bought_before(monkeypatch) -> None:
    """El cupón propio de la campaña (VELITAS5, decisión del operador del
    2026-10-20) puede no ser de primera compra: entonces también le llega a
    quien ya compró, sin la frase «solo en su primera compra»."""
    def _bought_before(store: FakeMetadataStore) -> None:
        (sid,) = store.store
        old = {"episode_id": "ep_000", "started_at_ms": 1, "closed_at_ms": 2, "closing_tag": "COMPRA_EXITOSA",
               "order_id": "order_old"}
        store.store[sid]["episodes"].insert(0, old)

    notes, _ = await _conversation(
        monkeypatch, _live_coupon(first_purchase_only=False), PREFILLED, "Lo pienso y te aviso", before=_bought_before
    )

    now = next(x for x in _script(notes[1]).splitlines() if x.startswith("AHORA"))
    assert "BIENVENIDA" in now and f"hasta el {LAST_DAY.day} de " in now
    assert "primera compra" not in now and "list_promotions" not in now



# ── revisión del 2026-10-20 ──────────────────────────────────────────────────


def _offered(store: FakeMetadataStore) -> object:
    (sid,) = store.store
    return store.store[sid]["episodes"][-1].get(sc.OFFER_KEY)


@pytest.mark.asyncio
async def test_on_the_remarketing_route_the_offer_is_not_spent(monkeypatch) -> None:
    """La respuesta a un toque va al agente de remarketing y las notas no le
    llegan a Ventas: si se marcara la oferta como dicha, nunca se diría."""
    def _remarketing_route(store: FakeMetadataStore) -> None:
        (sid,) = store.store
        store.store[sid]["active_route"] = "remarketing"

    _, store = await _conversation(monkeypatch, _live_coupon(), PREFILLED, "Lo pienso y te aviso", before=_remarketing_route)

    assert _offered(store) is None


@pytest.mark.asyncio
async def test_with_a_coupon_already_applied_no_other_is_offered(monkeypatch) -> None:
    def _applied(store: FakeMetadataStore) -> None:
        (sid,) = store.store
        store.store[sid]["episodes"][-1]["applied_coupon"] = {
            "code": "AMOR2026", "promotion": {"discount_type": "percentage", "value": 10}, "applied_at_ms": 1,
        }

    notes, store = await _conversation(monkeypatch, _live_coupon(), PREFILLED, "Lo pienso y te aviso", before=_applied)

    assert "BIENVENIDA" not in _script(notes[1]) and _offered(store) is None


class _JevReadsDeferral:
    """El proveedor de lecturas con Jev: «hay tiempo todavía» o «voy a
    esperar» no los lee la regla de hoy, Jev sí (capacidad `compra`,
    «aplaza»); «sí, quiero…» es una compra («elige»)."""

    async def read(self, inbound):
        from src.plugins.chats.agent.sales.decisions.readings import Readings

        text = (inbound.text or "").lower()
        deferring = any(p in text for p in ("hay tiempo", "voy a esperar"))
        kind = "deferral" if deferring else ("affirmation" if "sí, quiero" in text else None)
        choice = {"deferral": "aplaza", "affirmation": "elige"}.get(kind or "", "pregunta")
        compra = {"capability": "compra", "by": "jev", "provider": "jev", "value": [kind, "text"],
                  "answers": [{"q": "compra.que_hace", "choice": choice, "confidence": 0.9}]}
        return Readings(purchase=(kind, "text"), deferral=None, courtesy=False, opt_out=False, verdicts=(compra,))


@pytest.mark.asyncio
async def test_a_deferral_only_jev_reads_also_brings_the_offer(monkeypatch) -> None:
    notes, _ = await _conversation(
        monkeypatch, _live_coupon(), PREFILLED, "Gracias, hay tiempo todavía", readings=_JevReadsDeferral()
    )

    assert "acaba de aplazar" in _script(notes[1])


# ── caso del 2026-10-21 (velitas, respuesta al toque 2 de remarketing) ──────
#
# «No gracias voy a esperar» contestando al toque y, 6 s después, «Feliz día y
# bendiciones». El aplazamiento entró por la ruta de remarketing (ahí no se
# ofrece: sus notas no le llegan a Ventas) y la despedida, sin señal, lo tapaba
# para todo lo que viniera después. Nadie ofreció el 5 %.


def _route(route: str):
    def _set(store: FakeMetadataStore) -> None:
        (sid,) = store.store
        store.store[sid]["active_route"] = route

    return _set


def _we_replied(store: FakeMetadataStore) -> None:
    import time

    (sid,) = store.store
    _route("ventas")(store)
    store.store[sid]["last_outbound"] = {"sent_at_ms": int(time.time() * 1000) + 1, "kind": "text"}


@pytest.mark.asyncio
async def test_a_deferral_written_to_remarketing_is_offered_when_the_next_message_reaches_ventas(monkeypatch) -> None:
    notes, store = await _conversation(
        monkeypatch, _live_coupon(), PREFILLED, "No gracias voy a esperar", "Feliz día y bendiciones",
        readings=_JevReadsDeferral(), hooks={1: _route("remarketing"), 2: _route("ventas")},
    )

    assert "acaba de aplazar" in _script(notes[2]) and _offered(store) is not None
    # Las guardas del cierre siguen leyendo solo el último mensaje.
    from src.plugins.chats.shared.purchase_signals import is_current_inbound_deferral

    (sid,) = store.store
    assert is_current_inbound_deferral(store.store[sid]) is False


@pytest.mark.asyncio
async def test_after_our_reply_an_old_deferral_does_not_bring_the_offer(monkeypatch) -> None:
    notes, store = await _conversation(
        monkeypatch, _live_coupon(), PREFILLED, "No gracias voy a esperar", "Feliz día y bendiciones",
        readings=_JevReadsDeferral(), hooks={1: _route("remarketing"), 2: _we_replied},
    )

    assert "acaba de aplazar" not in _script(notes[2]) and _offered(store) is None


@pytest.mark.asyncio
async def test_a_yes_after_the_deferral_is_not_answered_with_the_offer(monkeypatch) -> None:
    notes, store = await _conversation(
        monkeypatch, _live_coupon(), PREFILLED, "No gracias voy a esperar", "Bueno, sí, quiero el combo",
        readings=_JevReadsDeferral(), hooks={1: _route("remarketing"), 2: _route("ventas")},
    )

    assert "acaba de aplazar" not in _script(notes[2]) and _offered(store) is None


# ── caso del 2026-10-21, sin anuncio: «Hola», la foto de la caja y «Luego te aviso» ──
#
# El cliente no llegó por un anuncio: escribió «Hola», mandó la foto de la caja
# cuadrada (el sistema la reconoció) y dijo «Luego te aviso». La conversación
# no quedó en la campaña (solo la reconocían el anuncio y el pedido) y, además,
# el número tenía 7 pedidos de prueba que el filtro de la oferta contaba como
# compras. Ventas se despidió sin el 5 %.


def _photo_of(handle: str, title: str) -> str:
    from src.plugins.chats.agent.sales.use_cases.photo_product import HOW_IMAGE, PhotoProduct, photo_reentry_text

    product = PhotoProduct(handle=handle, title=title, how=HOW_IMAGE)
    return photo_reentry_text("velas rectangulares apiladas, cada una con una palabra grabada", product) + (
        ' con el texto: "Uy esta está buena"'
    )


def _has_script(notes: list[str]) -> bool:
    return any(n.startswith("[GUION DE LA CAMPAÑA") for n in notes)


@pytest.mark.asyncio
async def test_a_customer_who_names_a_campaign_product_gets_its_script(monkeypatch) -> None:
    notes, store = await _conversation(monkeypatch, _live_coupon(), "Hola", "¿Cuánto valen las velas de deseos cuadradas?")

    assert not _has_script(notes[0]) and _has_script(notes[1]) and "BIENVENIDA" in _script(notes[1])
    (sid,) = store.store
    assert store.store[sid]["episodes"][-1][sc.EPISODE_KEY] == "velitas-7-dic"


@pytest.mark.asyncio
async def test_the_photo_of_a_campaign_box_and_then_a_deferral_bring_the_offer(monkeypatch) -> None:
    notes, store = await _conversation(
        monkeypatch, _live_coupon(), "Hola", _photo_of("velas-de-deseos-cuadradas", "Caja de Velas de deseos cuadradas"),
        "Luego te aviso",
    )

    assert _has_script(notes[1]) and "acaba de aplazar" in _script(notes[2]) and _offered(store) is not None


def _opened_the_card(handle: str):
    """El cliente abrió la ficha del producto en el catálogo de WhatsApp (o en la web)."""
    def _seen(store: FakeMetadataStore) -> None:
        (sid,) = store.store
        episode_id = store.store[sid]["episodes"][-1]["episode_id"]
        store.store[sid]["web_product_ref"] = {
            "sku": f"HUB-{handle}", "origin": "catalog", "status": "resolved", "handle": handle,
            "title": handle.replace("-", " ").capitalize(), "episode_id": episode_id,
        }

    return _seen


@pytest.mark.asyncio
async def test_a_customer_who_opened_the_card_of_a_campaign_box_gets_its_script(monkeypatch) -> None:
    notes, _ = await _conversation(
        monkeypatch, _live_coupon(), "Hola", "¿Qué precio tiene?", before=_opened_the_card("velas-de-deseos-onduladas")
    )

    assert _has_script(notes[1])


@pytest.mark.asyncio
async def test_the_card_of_another_episode_does_not_count(monkeypatch) -> None:
    def _old_card(store: FakeMetadataStore) -> None:
        _opened_the_card("velas-de-deseos-onduladas")(store)
        (sid,) = store.store
        store.store[sid]["web_product_ref"]["episode_id"] = "ep_viejo"

    notes, _ = await _conversation(monkeypatch, _live_coupon(), "Hola", "¿Qué precio tiene?", before=_old_card)

    assert not _has_script(notes[1])


@pytest.mark.asyncio
async def test_a_conversation_of_another_campaign_that_ends_up_on_the_boxes_gets_their_hook(monkeypatch) -> None:
    """Operador, 2026-10-21: «aplica para campañas que sean de otros productos
    pero la conversación termina hablando de productos que tienen el gancho o
    el descuento: ahí debería reconocerse y hacerle el gancho sobre ese
    producto». El producto del que habla el cliente ahora manda."""
    from dataclasses import replace

    (velitas,) = sc.active_campaigns()
    halloween = replace(velitas, id="halloween", nombre="Halloween", productos=("trilogia-del-terror",),
                        anuncios=("noche de brujas",), cupon=None)
    monkeypatch.setattr(sc, "active_campaigns", lambda: (halloween, velitas))

    notes, store = await _conversation(
        monkeypatch, _live_coupon(), "Hola, ¿cuánto vale la Trilogía del Terror?",
        "¿Y las velas de deseos cuadradas?", "Luego te aviso",
    )

    assert "«Halloween»" in _script(notes[0])
    assert "«Velitas del 7 de diciembre»" in _script(notes[1])
    assert "acaba de aplazar" in _script(notes[2]) and _offered(store) is not None
    (sid,) = store.store
    assert store.store[sid]["episodes"][-1][sc.EPISODE_KEY] == "velitas-7-dic"


@pytest.mark.asyncio
async def test_another_product_does_not_make_it_a_campaign_conversation(monkeypatch) -> None:
    notes, store = await _conversation(monkeypatch, _live_coupon(), "Hola", "¿Cuánto vale la Trilogía del Terror?")

    assert not _has_script(notes[1])


@pytest.mark.asyncio
async def test_old_test_orders_do_not_block_a_first_purchase_offer(monkeypatch) -> None:
    """La regla del cupón (`list_promotions`, `bought_before`): un pedido de
    prueba o cancelado no es una compra."""
    import src.sdk.connectorkit as connectorkit

    monkeypatch.setattr(connectorkit, "get_order_facts_port", lambda: _Facts(is_test=True))

    notes, _ = await _conversation(monkeypatch, _live_coupon(), PREFILLED, "Lo pienso y te aviso", before=_old_order)

    assert "acaba de aplazar" in _script(notes[1])


@pytest.mark.asyncio
async def test_if_medusa_cannot_tell_whether_an_old_order_counts_there_is_no_offer(monkeypatch) -> None:
    import src.sdk.connectorkit as connectorkit

    monkeypatch.setattr(connectorkit, "get_order_facts_port", lambda: _Facts(fail=True))

    notes, _ = await _conversation(monkeypatch, _live_coupon(), PREFILLED, "Lo pienso y te aviso", before=_old_order)

    assert "acaba de aplazar" not in _script(notes[1])


class _SlowPromotions:
    async def get_by_code(self, code: str):
        import asyncio

        await asyncio.sleep(30)


@pytest.mark.asyncio
async def test_a_slow_medusa_does_not_hold_the_turn(monkeypatch) -> None:
    import time

    started = time.monotonic()
    notes, _ = await _conversation(monkeypatch, _SlowPromotions(), PREFILLED, "Lo pienso y te aviso")

    assert time.monotonic() - started < 15
    assert "acaba de aplazar" not in _script(notes[1])


@pytest.mark.asyncio
async def test_the_offer_and_the_interest_profile_also_come_out_of_the_durable_steps(monkeypatch) -> None:
    """Entrada durable (fase 2): `InboundMessageWorkflow` corre el ingest en
    pasos, cada uno en su activity, con su resultado serializado en el
    historial de Temporal. El perfil «interesado sin compra» sale del paso del
    estado (lo lee Jev); la oferta del cupón al aplazar sale de la entrega,
    que ya ve la señal de ESTE mensaje (la escribe el historial, en orden)."""
    from dataclasses import asdict

    from temporalio.converter import DataConverter

    import src.sdk.connectorkit as connectorkit
    from src.plugins.chats.agent.sales.contracts import IngestRequest, IngestState, IngestTurn
    from src.plugins.chats.shared import customer_profiles as cp

    async def across(value, type_):
        [decoded] = await DataConverter.default.decode(await DataConverter.default.encode([value]), [type_])
        return decoded

    monkeypatch.setattr(connectorkit, "get_promotions_port", lambda: _Promotions(_live_coupon()))
    loader, store = FakeLoadOrStart(), FakeMetadataStore()
    ingest = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
        catalog=_Catalog(),  # type: ignore[arg-type]
        readings=_JevReadsDeferral(),
    )
    for message in _messages(PREFILLED, "Gracias, hay tiempo todavía"):
        request = await across(IngestRequest(message=asdict(message)), IngestRequest)
        state = await across(await ingest.read_state(request), IngestState)
        turn = await across(await ingest.prepare_turn(request, state), IngestTurn)
        await ingest.write_history(request, state, turn)
        await ingest.deliver(request, state, turn)

    (session_id,) = store.store
    assert "acaba de aplazar" in _script(list(loader.calls[-1].extra_context or []))
    assert store.store[session_id][cp.PROFILE_KEY][cp.INTERESADO_SIN_COMPRA]["source"] == "jev"
