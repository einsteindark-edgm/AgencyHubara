"""El cliente responde a una campaña de marketing (conversación del
2026-09-22, sesión `session-wa_573…`, runs 31c15a38 / 01a0caee).

Llegó la campaña con el cupón AMOR26; el cliente contestó "AMOR26" y el bot
le ofreció retomar la Trilogía del Terror de un carrito web de 4 días antes:
el LLM nunca vio la plantilla (vive solo en el JSONL del dashboard) y el
episodio viejo seguía abierto (INTERESADO no cierra), así que se inyectaban
la nota de lead web y el draft de la Trilogía.

Contrato:
- la PRIMERA respuesta tras una campaña cierra el episodio abierto con
  `CAMPAIGN_REPLY` y abre uno nuevo (draft, cupón y nota web del viejo se apagan);
- ese turno lleva la nota `[RESPUESTA A CAMPAÑA…]` con lo que recibió el
  cliente (mensaje, cupón, productos) y va a Ventas aunque la ruta sea
  remarketing (el remarketing no recibe `plugin_context`).
"""
from __future__ import annotations

import time

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.campaign_reply import (
    build_campaign_reply_note,
    unanswered_campaign_touch,
)
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    CAMPAIGN_CLOSING_TAG,
)
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)

_DAY_MS = 24 * 60 * 60 * 1000
_SESSION = "wa_573001234567"


def _touch(sent_at_ms: int, **extra) -> dict:
    return {
        "campaign_id": "cmp_amor",
        "campaign_name": "Amor y amistad",
        "sent_at_ms": sent_at_ms,
        "message": "Celebra el amor con velas. Usa el código AMOR26 al pagar.",
        "coupon_code": "AMOR26",
        "product_handles": ["duo-zodiacal", "cruz-de-vida"],
        **extra,
    }


# --- unanswered_campaign_touch ----------------------------------------------


def test_touch_sent_after_last_inbound_is_unanswered():
    now = 100 * _DAY_MS
    touch = _touch(now - 60_000)
    metadata = {"last_inbound_at_ms": now - 4 * _DAY_MS, "campaign_touches": [touch]}
    assert unanswered_campaign_touch(metadata, now) == touch


def test_touch_already_answered_is_not_a_campaign_reply():
    now = 100 * _DAY_MS
    metadata = {
        "last_inbound_at_ms": now - 30_000,
        "campaign_touches": [_touch(now - 60_000)],
    }
    assert unanswered_campaign_touch(metadata, now) is None


def test_contact_without_previous_inbound_replies_to_campaign():
    """Contacto importado por CSV: nunca escribió antes."""
    now = 100 * _DAY_MS
    touch = _touch(now - 60_000)
    assert unanswered_campaign_touch({"campaign_touches": [touch]}, now) == touch


def test_touch_older_than_attribution_window_is_ignored():
    now = 100 * _DAY_MS
    metadata = {"campaign_touches": [_touch(now - 8 * _DAY_MS)]}
    assert unanswered_campaign_touch(metadata, now) is None


def test_latest_unanswered_touch_wins_and_broken_shapes_are_tolerated():
    now = 100 * _DAY_MS
    newest = _touch(now - 60_000, campaign_id="cmp_new")
    metadata = {
        "last_inbound_at_ms": now - 3 * _DAY_MS,
        "campaign_touches": [
            "roto",
            {"campaign_id": "sin_fecha"},
            newest,
            _touch(now - 2 * _DAY_MS, campaign_id="cmp_old"),
        ],
    }
    assert unanswered_campaign_touch(metadata, now) == newest


def test_test_send_touch_counts_as_campaign_reply():
    """El envío de prueba del operador debe comportarse igual que el real."""
    now = 100 * _DAY_MS
    touch = _touch(now - 60_000, test=True)
    assert unanswered_campaign_touch({"campaign_touches": [touch]}, now) == touch


# --- build_campaign_reply_note ---------------------------------------------


def test_note_tells_the_llm_what_the_customer_received():
    note = build_campaign_reply_note(_touch(1))
    assert note.startswith("[RESPUESTA A CAMPAÑA")
    assert "Amor y amistad" in note
    assert "Celebra el amor con velas" in note
    assert "AMOR26" in note
    assert "duo-zodiacal" in note and "cruz-de-vida" in note
    assert "apply_coupon" in note
    # No retoma pedidos anteriores salvo que el cliente los traiga.
    assert "NO retomes" in note


def test_note_for_legacy_touch_without_content_still_names_the_campaign():
    note = build_campaign_reply_note(
        {"campaign_id": "c", "campaign_name": "Día de la madre", "sent_at_ms": 1}
    )
    assert "Día de la madre" in note
    assert "None" not in note


# --- Ingest: el caso real ---------------------------------------------------


class _History:
    def append_user_event(self, *_a, **_k) -> None:
        pass


class _Loader:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(
        self,
        session_id,
        message,
        phone_number_id,
        extra_context=None,
        prefer_sales=False,
        inbound_meta=None,
    ) -> None:
        self.calls.append(
            {
                "message": message,
                "extra_context": extra_context or [],
                "prefer_sales": prefer_sales,
            }
        )


class _Store:
    def __init__(self, data: dict) -> None:
        self.data = {_SESSION: data}

    def read(self, session_id):
        return dict(self.data.get(session_id, {}))

    def write(self, session_id, data):
        self.data[session_id] = dict(data)

    def update(self, session_id, mutator):
        result = mutator(self.read(session_id))
        if result is not None:
            self.write(session_id, result)
        return result


def _message(text: str) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.CAMP",
        from_number=_SESSION.removeprefix("wa_"),
        phone_number_id="PID",
        text=text,
        media=None,
        timestamp="1714312345",
    )


def _stale_trilogia_metadata(now_ms: int, *, route: str = "ventas") -> dict:
    """El episodio del carrito web de la Trilogía, abierto como INTERESADO."""
    started = now_ms - 4 * _DAY_MS
    return {
        "active_route": route,
        "tag": "INTERESADO",
        "motivo": "Armó carrito web con la Trilogía del Terror y no cerró.",
        "last_inbound_at_ms": started + 60_000,
        "web_cart": {
            "cart_id": "cart_01",
            "status": "hydrated",
            "episode_id": "ep_004",
            "items_summary": ["1x Trilogía del Terror"],
        },
        "episodes": [
            {
                "episode_id": "ep_004",
                "started_at_ms": started,
                "closed_at_ms": None,
                "closing_tag": None,
                "order_id": None,
                "order_draft": {
                    "slots": {"producto": "Trilogía del Terror", "cantidad": "1"}
                },
                "applied_coupon": {"code": "VIEJO10"},
            }
        ],
        "campaign_touches": [_touch(now_ms - 2 * 60_000)],
    }


def _use_case(store: _Store, loader: _Loader) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_campaign_reply_closes_stale_episode_and_opens_a_new_one():
    now = int(time.time() * 1000)
    store = _Store(_stale_trilogia_metadata(now))
    loader = _Loader()

    await _use_case(store, loader).execute(_message("AMOR26"))

    episodes = store.data[_SESSION]["episodes"]
    assert len(episodes) == 2
    old, new = episodes
    assert old["closing_tag"] == CAMPAIGN_CLOSING_TAG == "CAMPAIGN_REPLY"
    assert old["closed_at_ms"] is not None
    assert "Amor y amistad" in old["closing_motivo"]
    assert new["closed_at_ms"] is None
    assert "order_draft" not in new and "applied_coupon" not in new
    assert store.data[_SESSION]["tag"] == "NO_ETIQUETADO"


@pytest.mark.asyncio
async def test_campaign_reply_turn_carries_campaign_note_not_the_old_order():
    now = int(time.time() * 1000)
    store = _Store(_stale_trilogia_metadata(now))
    loader = _Loader()

    await _use_case(store, loader).execute(_message("AMOR26"))

    assert len(loader.calls) == 1
    context = "\n".join(loader.calls[0]["extra_context"])
    assert "[RESPUESTA A CAMPAÑA" in context
    assert "AMOR26" in context
    assert "Trilogía" not in context
    assert "LEAD CALIENTE DESDE LA WEB" not in context
    assert "DATOS DEL PEDIDO YA CONFIRMADOS" not in context
    # No es un re-engagement genérico: la nota de frontera ("saluda y
    # pregunta en qué ayudar") contradiría la campaña.
    assert "episodio NUEVO" not in context
    assert loader.calls[0]["prefer_sales"] is True


@pytest.mark.asyncio
async def test_campaign_reply_after_closed_episode_uses_campaign_note():
    now = int(time.time() * 1000)
    data = _stale_trilogia_metadata(now)
    data["episodes"][0].update(
        closed_at_ms=now - 3 * _DAY_MS, closing_tag="COMPRA_EXITOSA"
    )
    store = _Store(data)
    loader = _Loader()

    await _use_case(store, loader).execute(_message("me interesa"))

    episodes = store.data[_SESSION]["episodes"]
    assert [e["closing_tag"] for e in episodes] == ["COMPRA_EXITOSA", None]
    context = "\n".join(loader.calls[0]["extra_context"])
    assert "[RESPUESTA A CAMPAÑA" in context
    assert "episodio NUEVO" not in context


@pytest.mark.asyncio
async def test_second_message_after_campaign_is_a_normal_turn():
    now = int(time.time() * 1000)
    store = _Store(_stale_trilogia_metadata(now))
    loader = _Loader()
    use_case = _use_case(store, loader)

    await use_case.execute(_message("AMOR26"))
    await use_case.execute(_message("quiero el duo"))

    assert len(store.data[_SESSION]["episodes"]) == 2
    second = loader.calls[1]
    assert not any("[RESPUESTA A CAMPAÑA" in n for n in second["extra_context"])
    assert second["prefer_sales"] is False


@pytest.mark.asyncio
async def test_opt_out_reply_to_campaign_gets_no_campaign_note():
    now = int(time.time() * 1000)
    store = _Store(_stale_trilogia_metadata(now))
    loader = _Loader()

    await _use_case(store, loader).execute(_message("No más"))

    assert store.data[_SESSION].get("marketing_opt_out") is True
    context = "\n".join(loader.calls[0]["extra_context"])
    assert "[RESPUESTA A CAMPAÑA" not in context


@pytest.mark.asyncio
async def test_human_route_is_left_alone_on_campaign_reply():
    now = int(time.time() * 1000)
    data = _stale_trilogia_metadata(now, route="humano")
    store = _Store(data)
    loader = _Loader()

    await _use_case(store, loader).execute(_message("AMOR26"))

    episodes = store.data[_SESSION]["episodes"]
    assert len(episodes) == 1 and episodes[0]["closed_at_ms"] is None
    assert loader.calls[0]["prefer_sales"] is False


@pytest.mark.asyncio
async def test_campaign_reply_turn_quotes_the_campaign_in_the_customer_message():
    """Run edbb0d8b: la nota llegó en el system prompt (a 43k caracteres)
    pero el historial reciente era la Trilogía y el LLM la ignoró. El turno
    del cliente cita la campaña: el LLM la lee justo antes de responder y
    queda en su historial para los turnos siguientes."""
    now = int(time.time() * 1000)
    store = _Store(_stale_trilogia_metadata(now))
    loader = _Loader()

    await _use_case(store, loader).execute(_message("Me gusta"))

    message = loader.calls[0]["message"]
    assert message.endswith("Me gusta")
    assert "Amor y amistad" in message
    assert "Celebra el amor con velas" in message


@pytest.mark.asyncio
async def test_normal_turn_message_is_not_decorated():
    now = int(time.time() * 1000)
    store = _Store(_stale_trilogia_metadata(now))
    loader = _Loader()
    use_case = _use_case(store, loader)

    await use_case.execute(_message("AMOR26"))
    await use_case.execute(_message("quiero el cubo"))

    assert loader.calls[1]["message"] == "quiero el cubo"


@pytest.mark.asyncio
async def test_campaign_episode_remembers_the_campaign_and_asks_for_a_clean_llm_history():
    """Runs edbb0d8b / 8e73b7dc: el historial del LLM es por sesión — ventas y
    remarketing siguieron viendo la Trilogía. El episodio que abre la campaña
    guarda la campaña (para el remarketing) y pide cortar el historial del LLM
    de cada agente; lo anterior viaja como resumen de una línea."""
    now = int(time.time() * 1000)
    store = _Store(_stale_trilogia_metadata(now))
    loader = _Loader()

    await _use_case(store, loader).execute(_message("Me gusta"))

    new = store.data[_SESSION]["episodes"][-1]
    assert new["opened_by_campaign"]["campaign_name"] == "Amor y amistad"
    assert new["opened_by_campaign"]["coupon_code"] == "AMOR26"
    # Sin `summary` (run 28a8e407): exoclaw lo pegaba a cada mensaje.
    assert new["llm_history_reset"] == {"applied": []}


@pytest.mark.asyncio
async def test_campaign_reply_turn_opens_with_facts_of_the_previous_episode():
    """Run 28a8e407: lo anterior viaja UNA vez, en el primer mensaje, armado
    con hechos del episodio que se cerró (su borrador), no con el `motivo`
    que escribió el LLM — ese puede estar mal."""
    now = int(time.time() * 1000)
    data = _stale_trilogia_metadata(now)
    data["motivo"] = "Cliente interesado en el Duo Zodiacal."
    store = _Store(data)
    loader = _Loader()

    await _use_case(store, loader).execute(_message("Me gusta"))

    lines = loader.calls[0]["message"].split("\n")
    assert lines[0].startswith("[Conversación anterior con este cliente")
    assert "Trilogía del Terror" in lines[0]
    assert "sin compra" in lines[0]
    assert "Duo Zodiacal" not in loader.calls[0]["message"]
    assert lines[1].startswith("[El cliente responde a la campaña")
    assert lines[-1] == "Me gusta"


# --- Cupón de la campaña: se aplica solo -------------------------------------
#
# Conversación de prueba del 2026-09-24 (campaña con AMOR2026 y cupo por
# unidad): el cliente contestó «Me gusta», la nota decía «si lo menciona,
# valídalo con apply_coupon», el bot nunca lo llamó y todo quedó a precio
# lleno, con todos los aromas y colores del catálogo.

_UNITS = (
    {"handle": "cubo-love", "title": "Cubo Love", "color": "Amarillo", "aroma": "Café",
     "units_left": 1, "price_cop": 21000, "discounted_price_cop": 18900},
    {"handle": "cubo-love", "title": "Cubo Love", "color": "Lila", "aroma": "Lavanda",
     "units_left": 1, "price_cop": 21000, "discounted_price_cop": 18900},
)


def _amor_promo():
    from src.platform.promotions.port import PromotionDTO

    return PromotionDTO(
        id="promo_amor26", code="AMOR26", discount_type="percentage", value=10,
        currency_code=None, target_type="items", allocation="across", max_quantity=None,
        product_ids=("prod_cubo",), variant_ids=(), collection_ids=(),
        min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=None, budget_type=None, budget_limit=None, budget_used=None,
        description="Amor y amistad",
    )


def _applied_with_units():
    from src.plugins.chats.agent.sales.use_cases.coupon_application import (
        CouponApplication,
    )
    from src.plugins.chats.agent.sales.use_cases.coupon_quota import as_eligible

    return CouponApplication(
        "AMOR26", None, _amor_promo(), eligible=tuple(as_eligible(_UNITS)), units=_UNITS
    )


class _Applier:
    def __init__(self, result=None, *, error: Exception | None = None, delay: float = 0) -> None:
        self.result = result
        self.error = error
        self.delay = delay
        self.codes: list[str] = []

    async def __call__(self, code: str, now_ms: int):
        import asyncio

        self.codes.append(code)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


def _use_case_with_coupon(store: _Store, loader: _Loader, applier: _Applier) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
        campaign_coupon=applier,
    )


@pytest.mark.asyncio
async def test_campaign_reply_applies_the_campaign_coupon_without_asking_for_it():
    now = int(time.time() * 1000)
    store = _Store(_stale_trilogia_metadata(now))
    loader = _Loader()
    applier = _Applier(_applied_with_units())

    await _use_case_with_coupon(store, loader, applier).execute(_message("Me gusta"))

    assert applier.codes == ["AMOR26"]
    new = store.data[_SESSION]["episodes"][-1]
    assert new["applied_coupon"]["code"] == "AMOR26"
    assert [u["color"] for u in new["applied_coupon"]["units"]] == ["Amarillo", "Lila"]
    context = "\n".join(loader.calls[0]["extra_context"])
    assert "[CUPÓN APLICADO: AMOR26" in context
    assert "ya quedó aplicado" in context
    assert "no le pidas el código" in context


@pytest.mark.asyncio
async def test_campaign_coupon_that_no_longer_applies_is_explained_not_promised():
    from src.plugins.chats.agent.sales.use_cases.coupon_application import (
        CouponApplication,
    )

    now = int(time.time() * 1000)
    store = _Store(_stale_trilogia_metadata(now))
    loader = _Loader()
    applier = _Applier(CouponApplication("AMOR26", "expired"))

    await _use_case_with_coupon(store, loader, applier).execute(_message("Me gusta"))

    new = store.data[_SESSION]["episodes"][-1]
    assert "applied_coupon" not in new
    context = "\n".join(loader.calls[0]["extra_context"])
    assert "no se pudo aplicar" in context and "ya venció" in context
    assert "No prometas descuento" in context
    assert "[CUPÓN APLICADO" not in context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "applier",
    [
        _Applier(_applied_with_units(), delay=1),
        _Applier(error=RuntimeError("medusa caído")),
    ],
    ids=["medusa-lento", "medusa-caido"],
)
async def test_campaign_coupon_falls_back_to_apply_coupon_when_it_cannot_be_checked(
    applier, monkeypatch
):
    import src.plugins.chats.agent.sales.use_cases.ingest_inbound_message as ingest

    monkeypatch.setattr(ingest, "_CAMPAIGN_COUPON_TIMEOUT_S", 0.05)
    now = int(time.time() * 1000)
    store = _Store(_stale_trilogia_metadata(now))
    loader = _Loader()

    await _use_case_with_coupon(store, loader, applier).execute(_message("Me gusta"))

    assert "applied_coupon" not in store.data[_SESSION]["episodes"][-1]
    context = "\n".join(loader.calls[0]["extra_context"])
    assert "apply_coupon(code='AMOR26')" in context
    assert "ANTES de ofrecer" in context


@pytest.mark.asyncio
async def test_opt_out_and_second_message_do_not_apply_the_campaign_coupon():
    now = int(time.time() * 1000)
    loader = _Loader()
    opted = _Applier(_applied_with_units())
    await _use_case_with_coupon(_Store(_stale_trilogia_metadata(now)), loader, opted).execute(
        _message("No más")
    )
    assert opted.codes == []

    once = _Applier(_applied_with_units())
    use_case = _use_case_with_coupon(_Store(_stale_trilogia_metadata(now)), _Loader(), once)
    await use_case.execute(_message("Me gusta"))
    await use_case.execute(_message("el cubo love"))
    assert once.codes == ["AMOR26"]


@pytest.mark.asyncio
async def test_webhook_ingest_validates_campaign_coupons_with_the_sdk_ports(monkeypatch):
    """Gotcha #1 (tests verdes, feature muerta): el webhook real tiene que
    llegar a Medusa y al cupo por los puertos del SDK."""
    import src.plugins.chats.agent.sales.composition as comp
    import src.sdk.connectorkit as ck
    from src.platform.promotions.port import FakePromotionsPort
    from src.platform.promotions.quota_store import FakePromoQuotaStore

    monkeypatch.setattr(ck, "get_promotions_port", lambda: FakePromotionsPort([_amor_promo()]))
    monkeypatch.setattr(ck, "get_promo_quota_store", lambda: FakePromoQuotaStore())
    monkeypatch.setattr(ck, "get_coupon_sales_reader", lambda: None)
    monkeypatch.setattr(ck, "get_catalog_client", lambda: None)
    monkeypatch.setattr(ck, "get_web_cart_reader", lambda: None)
    monkeypatch.setattr(comp, "_INGEST_USE_CASE", None)

    use_case = comp.build_ingest_use_case()
    application = await use_case._check_campaign_coupon("amor26", int(time.time() * 1000))

    assert application is not None
    assert (application.applied, application.code) == (True, "AMOR26")


@pytest.mark.asyncio
async def test_webhook_shares_one_sales_read_between_campaign_replies(monkeypatch):
    """Campaña masiva: cada primera respuesta valida el cupón; las vendidas
    de Medusa se leen una vez y se comparten unos segundos."""
    import src.plugins.chats.agent.sales.composition as comp
    import src.sdk.connectorkit as ck
    from src.platform.promotions.port import FakePromotionsPort
    from src.platform.promotions.quota_store import FakePromoQuotaStore
    from src.platform.promotions.quotas import PromoUnitQuota

    class _Reader:
        calls = 0

        async def sold_units(self, *, since, exclude=None):
            _Reader.calls += 1
            return {}

    quotas = FakePromoQuotaStore()
    quotas.replace(
        "promo_amor26", "AMOR26",
        [PromoUnitQuota("q1", "promo_amor26", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                        "Rosado", "Café", 5, "2026-09-24T19:00:00Z", "ana")],
        show_units_left=True, actor="ana", now_iso="2026-09-24T19:00:00Z",
    )
    monkeypatch.setattr(ck, "get_promotions_port", lambda: FakePromotionsPort([_amor_promo()]))
    monkeypatch.setattr(ck, "get_promo_quota_store", lambda: quotas)
    monkeypatch.setattr(ck, "get_coupon_sales_reader", lambda: _Reader())
    monkeypatch.setattr(ck, "get_catalog_client", lambda: None)
    monkeypatch.setattr(ck, "get_web_cart_reader", lambda: None)
    monkeypatch.setattr(comp, "_INGEST_USE_CASE", None)

    use_case = comp.build_ingest_use_case()
    now = int(time.time() * 1000)
    await use_case._check_campaign_coupon("AMOR26", now)
    await use_case._check_campaign_coupon("AMOR26", now)

    assert _Reader.calls == 1


# --- El cupo del cupón aplicado se relee con cada mensaje ---------------------
#
# Prueba en vivo 2026-09-24 (15:59 Bogotá): el cliente pidió 2 Cilindro Love
# Azul · Lavanda (cupo 1) y a "¿Si tienes 2 de esa?" el bot dijo "Sí, claro":
# no fue a mirar cuántas quedaban. El cupo es la existencia de la promoción;
# con 1 unidad por combinación, lo que se guardó al aplicar el cupón queda
# viejo apenas otro cliente compra. Cada mensaje relee cuánto queda.


def _cilindro(color: str, aroma: str, left: int) -> dict:
    return {"handle": "cilindro-love", "title": "Cilindro Love", "color": color, "aroma": aroma,
            "units_left": left, "price_cop": 23500, "discounted_price_cop": 21150}


def _episode_with_quota_coupon(now_ms: int, *, route: str = "ventas") -> dict:
    from dataclasses import asdict

    item = {"producto": "Cilindro Love", "color": "Azul", "aroma": "Lavanda", "cantidad": "2"}
    return {
        "active_route": route,
        "tag": "NO_ETIQUETADO",
        "last_inbound_at_ms": now_ms - 60_000,
        "episodes": [
            {
                "episode_id": "ep_009",
                "started_at_ms": now_ms - 10 * 60_000,
                "closed_at_ms": None,
                "closing_tag": None,
                "order_id": None,
                "applied_coupon": {
                    "code": "AMOR26",
                    "promotion": asdict(_amor_promo()),
                    "applied_at_ms": now_ms - 5 * 60_000,
                    "eligible_products": [],
                    "quota": True,
                    "units": [_cilindro("Azul", "Lavanda", 1), _cilindro("Rosado", "Caballero de la noche", 1)],
                    "show_units_left": True,
                },
                "order_draft": {"slots": dict(item), "items": [dict(item)]},
            }
        ],
    }


class _UnitsNow:
    def __init__(self, offer=None, *, error: Exception | None = None, delay: float = 0) -> None:
        self.offer = offer
        self.error = error
        self.delay = delay
        self.calls = 0

    async def __call__(self, promotion: dict):
        import asyncio

        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.offer


def _use_case_with_units(store: _Store, loader: _Loader, units_now: _UnitsNow, applier=None) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
        campaign_coupon=applier,
        coupon_units_now=units_now,
    )


@pytest.mark.asyncio
async def test_each_message_rereads_how_many_are_left_before_the_bot_answers():
    from src.plugins.chats.agent.sales.use_cases.coupon_quota import QuotaOffer

    now = int(time.time() * 1000)
    store = _Store(_episode_with_quota_coupon(now))
    loader = _Loader()
    # Otro cliente se llevó la última Azul · Lavanda mientras tanto.
    live = QuotaOffer(True, None, (_cilindro("Rosado", "Caballero de la noche", 1),), True,
                      (_cilindro("Azul", "Lavanda", 0),))
    units_now = _UnitsNow(live)

    await _use_case_with_units(store, loader, units_now).execute(_message("Si tienes 2 de esa ?"))

    assert units_now.calls == 1
    applied = store.data[_SESSION]["episodes"][-1]["applied_coupon"]
    assert [u["color"] for u in applied["units"]] == ["Rosado"]
    assert [u["color"] for u in applied["sold_out"]] == ["Azul"]
    context = "\n".join(loader.calls[0]["extra_context"])
    assert "de Cilindro Love Azul · Lavanda ya no quedan unidades con el descuento" in context


@pytest.mark.asyncio
async def test_rereading_the_quota_says_how_many_carry_the_discount():
    from src.plugins.chats.agent.sales.use_cases.coupon_quota import QuotaOffer

    now = int(time.time() * 1000)
    store = _Store(_episode_with_quota_coupon(now))
    loader = _Loader()
    live = QuotaOffer(True, None, (_cilindro("Azul", "Lavanda", 1), _cilindro("Rosado", "Caballero de la noche", 1)), True)

    await _use_case_with_units(store, loader, _UnitsNow(live)).execute(_message("Si tienes 2 de esa ?"))

    context = "\n".join(loader.calls[0]["extra_context"])
    assert "queda 1 con el descuento" in context
    assert "1 a $21.150 y 1 a precio normal ($23.500)" in context


@pytest.mark.asyncio
async def test_every_unit_sold_is_said_as_such():
    from src.plugins.chats.agent.sales.use_cases.coupon_quota import QuotaOffer

    now = int(time.time() * 1000)
    store = _Store(_episode_with_quota_coupon(now))
    loader = _Loader()

    await _use_case_with_units(store, loader, _UnitsNow(QuotaOffer(True, "quota_exhausted"))).execute(
        _message("hola")
    )

    applied = store.data[_SESSION]["episodes"][-1]["applied_coupon"]
    assert applied["exhausted"] is True and applied["units"] == []
    assert "ya no quedan unidades con descuento" in "\n".join(loader.calls[0]["extra_context"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "units_now",
    [
        _UnitsNow(error=RuntimeError("medusa caído")),
        _UnitsNow(None, delay=1),
    ],
    ids=["medusa-caido", "medusa-lento"],
)
async def test_when_the_quota_cannot_be_reread_the_last_known_units_stay(units_now, monkeypatch):
    import src.plugins.chats.agent.sales.use_cases.ingest_inbound_message as ingest

    monkeypatch.setattr(ingest, "_CAMPAIGN_COUPON_TIMEOUT_S", 0.05)
    now = int(time.time() * 1000)
    store = _Store(_episode_with_quota_coupon(now))
    loader = _Loader()

    await _use_case_with_units(store, loader, units_now).execute(_message("Si tienes 2 de esa ?"))

    applied = store.data[_SESSION]["episodes"][-1]["applied_coupon"]
    assert [u["color"] for u in applied["units"]] == ["Azul", "Rosado"]
    assert "queda 1 con el descuento" in "\n".join(loader.calls[0]["extra_context"])


@pytest.mark.asyncio
async def test_no_reread_for_a_human_conversation_or_a_coupon_just_applied():
    now = int(time.time() * 1000)
    human = _UnitsNow()
    await _use_case_with_units(
        _Store(_episode_with_quota_coupon(now, route="humano")), _Loader(), human
    ).execute(_message("hola"))
    assert human.calls == 0

    # Respuesta a la campaña: el cupón se acaba de validar con las vendidas.
    just_applied = _UnitsNow()
    await _use_case_with_units(
        _Store(_stale_trilogia_metadata(now)), _Loader(), just_applied, applier=_Applier(_applied_with_units())
    ).execute(_message("Me gusta"))
    assert just_applied.calls == 0


# --- …pero solo mientras se habla del cupón ------------------------------------
#
# Pedido del operador (2026-09-24): el cupo se consulta SOLO si el mensaje toca
# el cupón. Si el cliente cambia de tema, el turno es del catálogo normal (sin
# límite de colores, aromas ni unidades); si vuelve al cupón, vuelve toda su
# lógica con el cupo recién leído.


def _both_left():
    from src.plugins.chats.agent.sales.use_cases.coupon_quota import QuotaOffer

    return QuotaOffer(
        True, None, (_cilindro("Azul", "Lavanda", 1), _cilindro("Rosado", "Caballero de la noche", 1)), True
    )


@pytest.mark.asyncio
async def test_the_quota_is_read_only_while_the_conversation_is_about_the_coupon():
    now = int(time.time() * 1000)
    metadata = _episode_with_quota_coupon(now)
    metadata["episodes"][-1].pop("order_draft")
    store = _Store(metadata)
    loader = _Loader()
    units_now = _UnitsNow(_both_left())
    use_case = _use_case_with_units(store, loader, units_now)

    # Otro tema: catálogo normal, sin leer el cupo ni ofrecer sus combinaciones.
    await use_case.execute(_message("¿Tienen portavelas?"))
    assert units_now.calls == 0
    off_topic = "\n".join(loader.calls[-1]["extra_context"])
    assert "AMOR26" in off_topic and "sin límite" in off_topic
    assert "Azul · Lavanda" not in off_topic

    # Vuelve al cupón: lo relee y el bot tiene las combinaciones y cuántas quedan.
    await use_case.execute(_message("¿Y el cupón que me llegó?"))
    assert units_now.calls == 1
    assert "Azul · Lavanda (queda 1)" in "\n".join(loader.calls[-1]["extra_context"])

    await use_case.execute(_message("¿Hacen envíos a Cali?"))
    assert units_now.calls == 1

    # Nombra el producto del cupón: otra vez.
    await use_case.execute(_message("Listo, quiero el cilindro azul con lavanda"))
    assert units_now.calls == 2


@pytest.mark.asyncio
async def test_no_quota_read_once_the_order_moved_on_to_another_product():
    now = int(time.time() * 1000)
    metadata = _episode_with_quota_coupon(now)
    draft = metadata["episodes"][-1]["order_draft"]
    draft["items"].append({"producto": "Portavelas Luna", "cantidad": "3"})
    draft["current_item"] = "portavelas luna"
    store = _Store(metadata)
    loader = _Loader()
    units_now = _UnitsNow(_both_left())

    await _use_case_with_units(store, loader, units_now).execute(_message("¿En qué colores viene?"))

    assert units_now.calls == 0
    assert "Azul · Lavanda (queda" not in "\n".join(loader.calls[-1]["extra_context"])


@pytest.mark.asyncio
async def test_webhook_rereads_the_quota_through_the_same_shared_sales_read(monkeypatch):
    """El webhook real relee el cupo con los puertos del SDK, compartiendo la
    lectura de vendidas con la validación del cupón de la campaña."""
    from dataclasses import asdict

    import src.plugins.chats.agent.sales.composition as comp
    import src.sdk.connectorkit as ck
    from src.platform.promotions.port import FakePromotionsPort
    from src.platform.promotions.quota_store import FakePromoQuotaStore
    from src.platform.promotions.quotas import PromoUnitQuota

    class _Reader:
        calls = 0

        async def sold_units(self, *, since, exclude=None):
            _Reader.calls += 1
            return {}

    quotas = FakePromoQuotaStore()
    quotas.replace(
        "promo_amor26", "AMOR26",
        [PromoUnitQuota("q1", "promo_amor26", "AMOR26", "prod_cubo", "cubo-love", "Cubo Love",
                        "Rosado", "Café", 5, "2026-09-24T19:00:00Z", "ana")],
        show_units_left=True, actor="ana", now_iso="2026-09-24T19:00:00Z",
    )
    monkeypatch.setattr(ck, "get_promotions_port", lambda: FakePromotionsPort([_amor_promo()]))
    monkeypatch.setattr(ck, "get_promo_quota_store", lambda: quotas)
    monkeypatch.setattr(ck, "get_coupon_sales_reader", lambda: _Reader())
    monkeypatch.setattr(ck, "get_catalog_client", lambda: None)
    monkeypatch.setattr(ck, "get_web_cart_reader", lambda: None)
    monkeypatch.setattr(comp, "_INGEST_USE_CASE", None)

    use_case = comp.build_ingest_use_case()
    await use_case._check_campaign_coupon("AMOR26", int(time.time() * 1000))
    offer = await use_case._coupon_units_now(asdict(_amor_promo()))

    assert offer.has_quota
    assert _Reader.calls == 1
