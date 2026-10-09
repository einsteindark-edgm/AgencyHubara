"""El ingest pide las lecturas del cliente al PROVEEDOR inyectado (motor de
decisiones F2, enchufe 1) y escribe los mismos campos de hoy.

* Sin proveedor explícito, el del motor (con `reglas` por defecto: el turno
  es el de hoy).
* El proveedor recibe lo que escribió el cliente, el metadata ANTES de este
  mensaje y el historial que el cliente vio.
* Lo que escribe la visión (la descripción de una foto) no es texto del
  cliente: la lectura recibe solo el texto que el cliente puso en la foto.
"""
from __future__ import annotations

from tests.metadata_store_fakes import MergingMetadataStoreMixin

import time
from typing import Any

import pytest

from src.plugins.chats.agent.sales.decisions.readings import Inbound, Readings
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage

SID = "wa_573001234567"


class _History:
    def __init__(self, events: list[dict] | None = None) -> None:
        self._events = list(events or [])

    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        self._events.append({"role": "user", "content": content})

    def read_events(self, session_id: str) -> list[dict]:
        return list(self._events)


class _Loader:
    async def execute(self, *a: Any, **kw: Any) -> None:
        pass


class _Store(MergingMetadataStoreMixin):
    def __init__(self, seed: dict[str, dict] | None = None) -> None:
        self.store: dict[str, dict] = dict(seed or {})

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)


class _Provider:
    def __init__(self, readings: Readings) -> None:
        self.readings = readings
        self.seen: list[Inbound] = []

    async def read(self, inbound: Inbound) -> Readings:
        self.seen.append(inbound)
        return self.readings


def _msg(text: str, message_id: str = "wamid.X") -> WhatsAppMessage:
    return WhatsAppMessage(message_id=message_id, from_number="573001234567", phone_number_id="PID", text=text, media=None,
                           timestamp=str(int(time.time())))


def _draft_store(**extra) -> _Store:
    return _Store({SID: {"episodes": [{"episode_id": "ep_1", "started_at_ms": int(time.time() * 1000) - 5000,
                                       "closed_at_ms": None, "order_draft": {"slots": {"producto": "Cubo Love"}}}], **extra}})


@pytest.mark.asyncio
async def test_the_ingest_writes_what_the_provider_read(_isolate_vault_dir) -> None:
    store = _draft_store()
    provider = _Provider(Readings(purchase=("affirmation", "text"), deferral=None, courtesy=False, opt_out=False))
    history = _History([{"role": "assistant", "content": "¿Confirmas el pedido?"}])
    ingest = IngestInboundMessage(history_store=history, load_session=_Loader(), metadata_store=store, readings=provider)  # type: ignore[arg-type]

    await ingest.execute(_msg("Te confirmo, sí la quiero"))

    md = store.read(SID)
    assert md["last_inbound_signal"]["kind"] == "affirmation"
    assert isinstance(md["episodes"][0]["order_draft"].get("confirmed_at_ms"), int)
    [inbound] = provider.seen
    assert inbound.text == "Te confirmo, sí la quiero" and inbound.message_id == "wamid.X"
    assert inbound.events == [{"role": "assistant", "content": "¿Confirmas el pedido?"}]  # antes de este mensaje
    assert inbound.stage == "etapa_variantes"


@pytest.mark.asyncio
async def test_the_ingest_writes_the_opt_out_the_provider_read(_isolate_vault_dir) -> None:
    now_ms = int(time.time() * 1000)
    store = _draft_store(campaign_touches=[{"campaign_id": "mkt-1", "sent_at_ms": now_ms - 60_000}])
    provider = _Provider(Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=True))
    ingest = IngestInboundMessage(history_store=_History(), load_session=_Loader(), metadata_store=store, readings=provider)  # type: ignore[arg-type]

    await ingest.execute(_msg("por favor no me contacten"))

    assert store.read(SID)["marketing_opt_out"] is True


@pytest.mark.asyncio
async def test_without_a_provider_the_readings_are_todays(_isolate_vault_dir) -> None:
    store = _draft_store()
    ingest = IngestInboundMessage(history_store=_History(), load_session=_Loader(), metadata_store=store)  # type: ignore[arg-type]

    await ingest.execute(_msg("sí, dale"))

    assert store.read(SID)["last_inbound_signal"]["kind"] == "affirmation"


@pytest.mark.asyncio
async def test_the_vision_description_is_not_read_as_the_customer(_isolate_vault_dir) -> None:
    store = _draft_store()
    provider = _Provider(Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=False))
    ingest = IngestInboundMessage(history_store=_History(), load_session=_Loader(), metadata_store=store, readings=provider)  # type: ignore[arg-type]
    receipt = '[el cliente envió una foto: comprobante de pago del viernes 25 de septiembre] con el texto: "listo, ya pagué"'

    await ingest.execute(_msg(receipt, "wamid.X_vision"), persisted_image_url="media/x.jpg", customer_text="listo, ya pagué")
    await ingest.execute(_msg("[el cliente envió una foto: una vela roja]", "wamid.Y_vision"), persisted_image_url="media/y.jpg",
                         customer_text=None)

    assert [i.text for i in provider.seen] == ["listo, ya pagué", None]


# --- la cortesía en el episodio nuevo (caso de producción del 2026-09-29) ----

READY = "Hola, tu pedido #47 ya está listo. Te compartimos la foto para que lo veas. ¿Nos confirmas para coordinar la entrega?"


class _Capture:
    def __init__(self) -> None:
        self.extra_context: list[list[str]] = []

    async def execute(self, session_id: str, message: str, phone_number_id: str | None,
                      extra_context: list[str] | None = None, **kw: Any) -> None:
        self.extra_context.append(list(extra_context or []))


class _CourtesyProvider(_Provider):
    """Lee la cortesía; el acuse de la despedida no absorbe nada."""

    async def read_ack(self, inbound: Inbound) -> Any:
        from src.plugins.chats.agent.sales.decisions.capabilities import Verdict

        return Verdict(capability="acuse", value=False, by="reglas", provider="reglas", rule=False)


def _after_purchase() -> _Store:
    now = int(time.time() * 1000)
    closed = {"episode_id": "ep_001", "started_at_ms": now - 5 * 86_400_000, "closed_at_ms": now - 4 * 86_400_000,
              "closing_tag": "COMPRA_EXITOSA", "order_id": "order_TEST"}
    return _Store({SID: {"episodes": [closed], "tag": "COMPRA_EXITOSA", "active_route": "ventas"}})


@pytest.mark.asyncio
@pytest.mark.parametrize(("courtesy", "says", "never"), [
    (True, "solo agradece", "pregunta en qué puedes ayudar"),
    (False, "pregunta en qué puedes ayudar", "solo agradece"),
])
async def test_the_new_episode_note_follows_the_courtesy(_isolate_vault_dir, courtesy: bool, says: str, never: str) -> None:
    """El ETA avisó «pedido listo» y el cliente solo agradeció: el episodio
    nuevo no le pide al LLM «pregunta en qué puedes ayudar hoy» (el bot
    contestó «Cuéntame, ¿en qué te puedo ayudar hoy?»). Sin cortesía (con
    `reglas`, siempre), la nota de hoy."""
    loader = _Capture()
    provider = _CourtesyProvider(
        Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=False, courtesy_only=courtesy)
    )
    ingest = IngestInboundMessage(history_store=_History([{"role": "assistant", "content": READY}]), load_session=loader,
                                  metadata_store=_after_purchase(), readings=provider)  # type: ignore[arg-type]

    await ingest.execute(_msg("Hola cómo están? Son geniales. Muchas gracias"))

    [note] = [n for n in loader.extra_context[-1] if "episodio NUEVO" in n]
    assert says in note and never not in note


@pytest.mark.parametrize("courtesy", [False, True])
def test_a_request_about_the_closed_order_is_escalated_not_promised(courtesy: bool) -> None:
    """Laboratorio caso-cortesia-1001 (2026-09-30), caso de control: la nota
    decía que el pedido lo gestiona un humano por separado y «NO lo retomes»;
    los dos bots le prometieron al cliente «un colega coordina la entrega» sin
    escalar. La nota ahora dice qué hacer cuando el cliente pide algo de ese
    pedido."""
    from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import build_episode_boundary_note

    note = build_episode_boundary_note(
        {"closing_tag": "COMPRA_EXITOSA", "order_id": "order_TEST"}, courtesy=courtesy
    )

    assert "escalate_to_human" in note
    assert "entrega" in note


class _BrokenProvider:
    """El motor no puede leer (p. ej. el paquete de la tienda no compila)."""

    async def read(self, inbound: Inbound) -> Readings:
        from src.sdk.decisionkit import BundleError, Diagnostic

        raise BundleError([Diagnostic("DB005", "capabilities/compra.yaml: decide[0].when", "roto")])


@pytest.mark.asyncio
async def test_if_the_engine_cannot_read_the_message_is_kept_with_the_rules_of_the_code(_isolate_vault_dir) -> None:
    """Premortem 2026-10-02: un paquete que no compila hacía fallar el ingest
    ANTES de guardar el mensaje (Meta ya tenía su 200: no reintenta). El
    mensaje nunca se pierde: lecturas con las reglas del código (las del
    proveedor `reglas`), el mensaje en el historial y el turno sigue."""
    import structlog

    from src.plugins.chats.agent.sales.decisions import bundled_ingest
    from src.plugins.chats.agent.sales.decisions.readings import Inbound as _In

    store = _draft_store()
    history = _History([{"role": "assistant", "content": "¿Confirmas el pedido?"}])
    ingest = IngestInboundMessage(history_store=history, load_session=_Loader(), metadata_store=store,
                                  readings=_BrokenProvider())  # type: ignore[arg-type]

    with structlog.testing.capture_logs() as logs:
        await ingest.execute(_msg("Te confirmo, sí la quiero"))

    assert {"role": "user", "content": "Te confirmo, sí la quiero"} in history.read_events(SID)
    kind, _source = bundled_ingest.purchase_signal(_In(session_id=SID, text="Te confirmo, sí la quiero", now_ms=0))
    assert store.read(SID).get("last_inbound_signal", {}).get("kind") == kind
    assert any(e["event"] == "ingest.readings_failed" and e["log_level"] == "error" for e in logs), logs


# ── El cliente escribe justo después de comprar (caso 2026-10-09, pedido #64) ──
#
# El humano vendió, «Confirmar pago» devolvió la conversación al bot y el
# cliente escribió 6 minutos después. La nota del episodio nuevo le pidió al
# LLM «saluda con calidez y pregunta en qué puedes ayudar hoy» y dijo que el
# pago estaba en verificación (ya estaba confirmado): el bot le dio la
# bienvenida como a un cliente nuevo. Con el pedido en curso, la nota lleva sus
# datos reales (OrderFacts, nunca la copia del vault) y la etapa es post-venta.


def _just_bought(minutes_ago: int = 6) -> _Store:
    now = int(time.time() * 1000)
    closed = {"episode_id": "ep_001", "started_at_ms": now - 3 * 3_600_000, "closed_at_ms": now - minutes_ago * 60_000,
              "closing_tag": "COMPRA_EXITOSA", "order_id": "order_64"}
    return _Store({SID: {"episodes": [closed], "tag": "COMPRA_EXITOSA", "active_route": "ventas"}})


def _facts(stage: str = "preparing", pay_status: str = "paid"):
    from src.sdk.connectorkit import OrderFacts

    return OrderFacts(order_id="order_64", display_id="#64", total_cop=52_900, currency_code="cop",
                      pay_status=pay_status, stage=stage, customer="Cliente", is_draft=False)


async def _new_episode_note(store: _Store, order_facts: Any) -> tuple[str, dict]:
    loader = _Capture()
    ingest = IngestInboundMessage(history_store=_History(), load_session=loader, metadata_store=store,
                                  order_facts=order_facts)  # type: ignore[arg-type]

    await ingest.execute(_msg("…"))

    [note] = [n for n in loader.extra_context[-1] if "episodio NUEVO" in n]
    return note, store.read(SID)


def _reader(*facts: Any):
    async def read(order_id: str):
        return next((f for f in facts if f.order_id == order_id), None)

    return read


@pytest.mark.asyncio
async def test_right_after_buying_the_note_carries_the_order_and_does_not_welcome(_isolate_vault_dir) -> None:
    from src.plugins.chats.agent.sales.use_cases.funnel_stage import STAGE_POSTCIERRE, resolve_funnel_stage

    note, metadata = await _new_episode_note(_just_bought(), _reader(_facts()))

    assert "#64" in note and "en preparación" in note
    assert "pago ya está confirmado" in note
    assert "pregunta en qué puedes ayudar" not in note and "verificación de pago" not in note
    assert "no le des la bienvenida" in note
    assert metadata["episodes"][-1]["after_order"] == {"order_id": "order_64", "display_id": "#64"}
    assert resolve_funnel_stage(metadata) == STAGE_POSTCIERRE


@pytest.mark.asyncio
async def test_with_the_payment_still_unconfirmed_the_note_does_not_say_it_is_paid(_isolate_vault_dir) -> None:
    note, _ = await _new_episode_note(_just_bought(), _reader(_facts(stage="new", pay_status="pending")))

    assert "#64" in note and "pago ya está confirmado" not in note
    assert "aún no está confirmado" in note


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["delivered", "cancelled"])
async def test_an_order_already_delivered_or_cancelled_opens_a_new_conversation_as_today(
    _isolate_vault_dir, stage: str,
) -> None:
    note, metadata = await _new_episode_note(_just_bought(), _reader(_facts(stage=stage)))

    assert "pregunta en qué puedes ayudar" in note
    assert "after_order" not in metadata["episodes"][-1]


@pytest.mark.asyncio
async def test_without_the_order_data_the_note_is_todays(_isolate_vault_dir) -> None:
    """Medusa caído o lento: nada se inventa; la nota y la etapa de siempre."""
    async def broken(order_id: str):
        raise TimeoutError("medusa")

    note, metadata = await _new_episode_note(_just_bought(), broken)

    assert "pregunta en qué puedes ayudar" in note
    assert "after_order" not in metadata["episodes"][-1]


@pytest.mark.asyncio
async def test_a_purchase_from_months_ago_is_not_post_sale(_isolate_vault_dir) -> None:
    note, metadata = await _new_episode_note(_just_bought(minutes_ago=60 * 24 * 45), _reader(_facts()))

    assert "pregunta en qué puedes ayudar" in note
    assert "after_order" not in metadata["episodes"][-1]


@pytest.mark.asyncio
async def test_a_thank_you_right_after_buying_gets_a_short_warm_reply(_isolate_vault_dir) -> None:
    loader = _Capture()
    provider = _CourtesyProvider(
        Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=False, courtesy_only=True)
    )
    ingest = IngestInboundMessage(history_store=_History(), load_session=loader, metadata_store=_just_bought(),
                                  readings=provider, order_facts=_reader(_facts()))  # type: ignore[arg-type]

    await ingest.execute(_msg("Muchas gracias!"))

    [note] = [n for n in loader.extra_context[-1] if "episodio NUEVO" in n]
    assert "#64" in note and "solo agradece" in note and "pago ya está confirmado" in note


@pytest.mark.asyncio
async def test_the_production_reader_reads_order_facts(monkeypatch) -> None:
    """El lector que arma la composición: OrderFacts por id, con su tope."""
    from src.plugins.chats.agent.sales import composition
    from src.sdk.connectorkit import InMemoryOrderFacts

    monkeypatch.setattr(composition, "_order_facts_port", lambda: InMemoryOrderFacts([_facts()]))
    read = composition.build_order_facts_reader()

    assert (await read("order_64")).display_id == "#64"
    assert await read("order_otro") is None
