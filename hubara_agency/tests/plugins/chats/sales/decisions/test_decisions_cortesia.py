"""«¿El cliente solo agradece o saluda?» (capacidad `cortesia`).

Caso de producción del 2026-09-29: el ETA avisó «tu pedido ya está listo… ¿Nos
confirmas para coordinar la entrega?», el cliente contestó «Hola cómo están?
Son geniales. Muchas gracias» y el bot, con la nota de episodio nuevo («saluda
con calidez y pregunta en qué puedes ayudar hoy»), respondió «…Cuéntame, ¿en
qué te puedo ayudar hoy?». Lo mismo pasa al volver de remarketing.

La regla de hoy no distingue (siempre «no»: todo sigue como hoy). Jev lee el
mensaje con lo que el cliente vio antes: un «sí» a «¿Nos confirmas…?»
responde una pregunta, no es cortesía. Con el valor, el ingest guarda la
marca del mensaje (`last_inbound_courtesy`) para quien arma el turno.
"""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas import Cortesia
from src.plugins.chats.agent.sales.decisions.readings import EngineReadings, Inbound, Readings, apply_readings
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

NOW = 1_790_000_000_000
TZ = ZoneInfo("America/Bogota")
SID = "wa_573001234567"
READY = "Hola, tu pedido #47 ya está listo. Te compartimos la foto para que lo veas. ¿Nos confirmas para coordinar la entrega?"
SAW_READY = [{"role": "assistant", "content": READY, "timestamp": "2026-09-29T21:35:34+00:00"}]
THANKS = "Hola cómo están? Son geniales. Muchas gracias"


def _inbound(text: str | None, *, events: list | None = None, **kw) -> Inbound:
    return Inbound(session_id=SID, text=text, now_ms=NOW, message_id="w1", events=events or [], tz=TZ, **kw)


def _result(p: float | None) -> PerceptionResult:
    answers = () if p is None else (TypedAnswer(id="cortesia.solo", kind="noul", p=p),)
    return PerceptionResult(ok=True, answers=answers, provider="fake", model="typesafe/jev-1.13-20260917")


def test_today_nothing_changes() -> None:
    assert Cortesia().rule(_inbound("Muchas gracias 🙏", events=SAW_READY)) is False


def test_jev_reads_the_message_with_what_the_customer_saw() -> None:
    asked = Cortesia().ask(_inbound(THANKS, events=SAW_READY))

    assert asked is not None
    state, [question] = asked
    assert READY in state and state.rstrip().endswith(f"[1] {THANKS}")
    assert question.id == "cortesia.solo" and question.kind == "noul"
    assert "agradece" in question.text and "pregunta" in question.text
    # Con Jev real (2026-09-30), sin el ejemplo «Ya lo recibí. Muchas gracias»
    # y «Quedaron hermosas…» daban 0,75 y 0,79; con él, 0,95 y 0,94 (y los que
    # preguntan o confirman siguen en 0,02 a 0,05).
    assert "ya recibió el pedido" in question.text


def test_the_question_reads_real_courtesy_like_the_customer_writes_it() -> None:
    """Simulación de la conversación real ···4148 (2026-10-01): con la
    pregunta anterior, Jev real daba 0,76 a «Hola cómo están? S even geniales.
    Muchas gracias» (el aviso terminaba en «¿Nos confirmas…?» y la pregunta
    excluía «responder una pregunta de la tienda»), 0,13 a «Hola sí.. están
    geniales» tras «¿te gustó?» y 0,20 a «te enviaremos fotos». La nueva
    redacción: 18 de 18 (mensajes reales y emulados) con el umbral en 0,80;
    los que confirman, eligen o preguntan siguen en 0,02 a 0,11."""
    _state, [question] = Cortesia().ask(_inbound(THANKS, events=SAW_READY))

    assert "«¿cómo están?» no cuenta como pregunta" in question.text
    assert "responder que le gustó sí es cortesía" in question.text
    assert "promete mandar fotos" in question.text
    assert "un dato o una decisión que la tienda necesite" in question.text


def test_a_text_the_customer_did_not_write_is_not_read() -> None:
    assert Cortesia().ask(_inbound("[el cliente envió una foto: una vela]", events=SAW_READY, synthetic=True)) is None


@pytest.mark.parametrize(("p", "expected"), [(0.93, True), (0.82, True), (0.6, False), (0.05, False), (None, None)])
def test_only_a_confident_yes_is_courtesy(p: float | None, expected: bool | None) -> None:
    """Con duda se atiende como siempre."""
    inp = _inbound(THANKS, events=SAW_READY)
    assert Cortesia().decide(inp, _result(p), False, {}) is expected


def test_the_dashboard_control_knows_it() -> None:
    assert "cortesia" in bots.CAPABILITIES


@pytest.fixture
def oracle(monkeypatch):
    from src.sdk import connectorkit

    holder = {"fake": FakePerceptionAdapter({"cortesia.solo": TypedAnswer(id="cortesia.solo", kind="noul", p=0.95)})}
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: holder["fake"])
    return holder


async def test_with_rules_jev_is_not_asked(tmp_path: Path, oracle) -> None:
    readings = await EngineReadings(tmp_path).read(_inbound(THANKS, events=SAW_READY))

    assert readings.courtesy_only is False
    assert [v["by"] for v in readings.verdicts if v["capability"] == "cortesia"] == ["reglas"]
    assert oracle["fake"].calls == []


async def test_the_new_bot_reads_the_courtesy(tmp_path: Path, oracle, monkeypatch) -> None:
    monkeypatch.setenv("DECISIONS_BOT", "B")

    readings = await EngineReadings(tmp_path).read(_inbound(THANKS, events=SAW_READY))

    assert readings.courtesy_only is True
    [verdict] = [v for v in readings.verdicts if v["capability"] == "cortesia"]
    assert (verdict["by"], verdict["value"]) == ("jev", True)


def _readings(*, courtesy_only: bool) -> Readings:
    return Readings(purchase=(None, "text"), deferral=None, courtesy=False, opt_out=False, courtesy_only=courtesy_only)


def test_the_courtesy_mark_belongs_to_its_message() -> None:
    metadata: dict = {}

    apply_readings(metadata, _readings(courtesy_only=True), text=THANKS, now_ms=NOW, message_id="w1", tz=TZ,
                   opt_out_campaign_id=None)
    marked = dict(metadata)
    apply_readings(metadata, _readings(courtesy_only=False), text="¿Y cuándo llega?", now_ms=NOW + 1, message_id="w2",
                   tz=TZ, opt_out_campaign_id=None)

    assert marked.get("last_inbound_courtesy") == {"message_id": "w1", "at_ms": NOW}
    assert "last_inbound_courtesy" not in metadata


# --- el contexto del turno del bot nuevo lee la marca --------------------------


def _vault_session(vault: Path, metadata: dict) -> None:
    import json

    session = vault / SID
    (session / "sessions").mkdir(parents=True, exist_ok=True)
    (session / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (session / "sessions" / f"{SID}.jsonl").write_text(
        json.dumps({"role": "assistant", "content": READY}) + "\n", encoding="utf-8"
    )


@pytest.mark.parametrize(("mark", "expected"), [("wamid.T", True), ("wamid.OLD", False)])
def test_the_turn_context_knows_the_customer_only_thanked(_isolate_vault_dir: Path, mark: str, expected: bool) -> None:
    from src.plugins.chats.agent.sales.decisions.activities import _turn_context

    _vault_session(_isolate_vault_dir, {
        "last_inbound_message_id": "wamid.T",
        "last_inbound_courtesy": {"message_id": mark, "at_ms": NOW},
        "episodes": [{"episode_id": "ep_002", "started_at_ms": NOW, "closed_at_ms": None}],
    })

    context = _turn_context(SID, [{"text": THANKS, "wamid": "wamid.T", "ts_ms": NOW}])

    assert context is not None and context.courtesy is expected
