"""Acuse tras la despedida (capacidad `acuse`; run 4cb3a34f, #379).

Ventas se despidió y cerró el episodio; si el cliente solo acusa recibo
(«☺️👍», «gracias», «igualmente»), el mensaje queda en el chat y no despierta
al agente. Lo estructural lo sigue decidiendo el código (episodio cerrado,
una reacción o un sticker, una plantilla posterior); lo que dice el TEXTO lo
decide el motor, con el mismo proveedor de lecturas del ingest que compra,
retoma y baja:

* reglas — `is_closing_ack` (pocas palabras de cortesía, emojis, sin «?»).
* Jev — «¿es solo un acuse o una cortesía a la despedida, sin preguntar,
  pedir ni contar nada nuevo?», con lo que el cliente vio antes a la vista.
  Sesgo a la seguridad: absorbe solo con p ≥ 0,90; si duda, despierta al bot
  (una pregunta real nunca se traga). Piso: un mensaje con signo de
  pregunta nunca se absorbe.
"""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.readings import EngineReadings, Inbound
from src.sdk.connectorkit import TypedAnswer

NOW = 1_790_000_000_000
TZ = ZoneInfo("America/Bogota")
SID = "wa_573001234567"
FAREWELL = (
    "Con gusto, muchas gracias a ti por escribirnos 🤍. Si más adelante quieres consentir a alguien con una "
    "de nuestras velas, aquí estamos para ayudarte. ¡Éxitos para ti también!"
)
FAREWELL_WITH_QUESTION = "¡Gracias por tu compra, Laura! 🤍 ¿Quieres que te avise cuando llegue la colección nueva?"
# Ventas cerró el episodio (RECHAZO) con la despedida; el borrador guarda
# datos personales de ESTE cliente.
CLOSED = {
    "episodes": [
        {
            "episode_id": "ep_002",
            "closed_at_ms": NOW - 37_000,
            "closing_tag": "RECHAZO",
            "order_draft": {"slots": {"nombre_recibe": "Laura Gómez", "direccion": "Cra 7 # 12-30"}},
        }
    ]
}
SAW = [
    {"role": "user", "content": "Quería moldes y ya conseguí, pero gracias"},
    {"role": "assistant", "content": FAREWELL},
]
LONG_COURTESY = "Muchas gracias, que Dios le bendiga mucho y feliz día 🙏"


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


@pytest.fixture
def oracle(monkeypatch):
    from src.sdk import connectorkit

    holder = {"fake": FakePerceptionAdapter({})}

    def _get(_oracle: str):
        return holder["fake"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


@pytest.fixture
def jev(monkeypatch):
    """El bot con Jev en cada capacidad (brazo B del laboratorio)."""
    monkeypatch.setenv("DECISIONS_BOT", "B")


def _inbound(text: str | None, *, events: list | None = None, **kw) -> Inbound:
    return Inbound(
        session_id=SID, text=text, now_ms=NOW, message_id="w1", metadata=kw.pop("metadata", CLOSED),
        events=SAW if events is None else events, tz=TZ, **kw,
    )


def _asked(fake: FakePerceptionAdapter) -> list[str]:
    return [qid for _state, ids in fake.calls for qid in ids]


@pytest.mark.parametrize(
    ("text", "ack"),
    [("☺️👍", True), ("Muchas gracias 🙏", True), ("Gracias. Oye, ¿y tienen velas de coco?", False), (LONG_COURTESY, False)],
)
async def test_with_rules_the_ack_is_todays_rule(tmp_path: Path, oracle, text: str, ack: bool) -> None:
    verdict = await EngineReadings(tmp_path).read_ack(_inbound(text))

    assert (verdict.capability, verdict.value, verdict.by) == ("acuse", ack, "reglas")
    assert oracle["fake"].calls == []


async def test_jev_absorbs_a_long_courtesy_the_rule_misses(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({"acuse.solo_cortesia": _noul("acuse.solo_cortesia", 0.96)})

    verdict = await EngineReadings(tmp_path).read_ack(_inbound(LONG_COURTESY))

    assert (verdict.value, verdict.by, verdict.rule) == (True, "jev", False)
    [(state, ids)] = oracle["fake"].calls
    assert ids == ("acuse.solo_cortesia",)
    assert FAREWELL in state and LONG_COURTESY in state  # la despedida a la vista
    from src.sdk.connectorkit import DecisionMetrics

    assert [r["capability"] for r in DecisionMetrics(tmp_path).rows("acuse", since_ms=0)] == ["acuse"]


async def test_an_ok_that_answers_the_farewell_question_wakes_the_bot(tmp_path: Path, oracle, jev) -> None:
    """La regla absorbe «Ok»; pero la despedida le preguntó algo y el «Ok» lo
    contesta: el bot tiene que despertar."""
    oracle["fake"] = FakePerceptionAdapter({"acuse.solo_cortesia": _noul("acuse.solo_cortesia", 0.04)})
    events = [{"role": "assistant", "content": FAREWELL_WITH_QUESTION}]

    verdict = await EngineReadings(tmp_path).read_ack(_inbound("Ok", events=events))

    assert (verdict.value, verdict.by, verdict.rule) == (False, "jev", True)


async def test_when_jev_doubts_the_bot_wakes(tmp_path: Path, oracle, jev) -> None:
    """Sesgo a la seguridad: si Jev duda, el mensaje despierta al bot aunque
    la regla lo absorbiera (y el desacuerdo va a la cola)."""
    from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog

    oracle["fake"] = FakePerceptionAdapter({"acuse.solo_cortesia": _noul("acuse.solo_cortesia", 0.6)})

    verdict = await EngineReadings(tmp_path).read_ack(_inbound("Gracias"))

    assert (verdict.value, verdict.by) == (False, "jev")
    [item] = DisagreementLog(tmp_path).pending()
    assert (item["capability"], item["rule"], item["jev"]) == ("acuse", True, False)


async def test_a_question_mark_is_never_absorbed(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({"acuse.solo_cortesia": _noul("acuse.solo_cortesia", 0.97)})

    verdict = await EngineReadings(tmp_path).read_ack(_inbound("gracias!! y el domicilio?"))

    assert (verdict.value, verdict.by, verdict.jev) == (False, "piso", True)


async def test_without_what_the_customer_saw_jev_is_not_asked(tmp_path: Path, oracle, jev) -> None:
    verdict = await EngineReadings(tmp_path).read_ack(_inbound(LONG_COURTESY, events=[]))

    assert (verdict.value, verdict.reason) == (False, "no_question")
    assert oracle["fake"].calls == []


async def test_what_the_vision_wrote_is_not_read_by_jev(tmp_path: Path, oracle, jev) -> None:
    description = "[el cliente envió una foto: un sticker de un corazón]"

    verdict = await EngineReadings(tmp_path).read_ack(_inbound(description, synthetic=True))

    assert verdict.value is False and oracle["fake"].calls == []


async def test_shadow_keeps_the_rule_and_queues_the_disagreement(tmp_path: Path, oracle, monkeypatch) -> None:
    from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog

    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    bots.write_capability_modes(tmp_path, {"acuse": "shadow"})
    oracle["fake"] = FakePerceptionAdapter({"acuse.solo_cortesia": _noul("acuse.solo_cortesia", 0.95)})

    verdict = await EngineReadings(tmp_path).read_ack(_inbound(LONG_COURTESY))

    assert (verdict.value, verdict.by, verdict.provider) == (False, "reglas", "sombra")
    [item] = DisagreementLog(tmp_path).pending()
    assert (item["capability"], item["rule"], item["jev"], item["session_id"]) == ("acuse", False, True, SID)


async def test_jev_failing_falls_back_to_the_rule(tmp_path: Path, oracle, jev) -> None:
    oracle["fake"] = FakePerceptionAdapter({}, error="http_503")

    verdict = await EngineReadings(tmp_path).read_ack(_inbound("☺️👍"))

    assert (verdict.value, verdict.by) == (True, "respaldo")


async def test_the_customers_data_is_hidden_before_asking_jev(tmp_path: Path, monkeypatch, jev) -> None:
    """El episodio ya cerró, pero la despedida y el hilo pueden nombrar al
    cliente: lo que sale hacia Jev tapa los datos del borrador del episodio
    que se despidió."""
    from src.sdk import connectorkit

    seen: list[tuple[str, ...]] = []

    class _Spy(FakePerceptionAdapter):
        async def ask(self, state, questions, *, timeout_s, redact=()):
            seen.append(tuple(redact))
            return await super().ask(state, questions, timeout_s=timeout_s, redact=redact)

    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: _Spy({}))

    await EngineReadings(tmp_path).read_ack(_inbound("Gracias Laura 🙏", events=[{"role": "assistant", "content": FAREWELL_WITH_QUESTION}]))

    assert seen and {"Laura Gómez", "Laura", "Gómez", "Cra 7 # 12-30"} <= set(seen[0])
