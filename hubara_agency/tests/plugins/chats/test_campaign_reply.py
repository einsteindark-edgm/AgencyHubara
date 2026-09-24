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
