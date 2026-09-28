"""Marco de capacidades del motor (diseño v2 §07, fase F2).

Cada pieza de código quemado pasa a ser una CAPACIDAD: una o dos preguntas
cerradas a Jev, una política que convierte la respuesta en decisión y la regla
de hoy como respaldo. El consumidor (tool, activity, ingest) le pide la
decisión al motor y no sabe quién contestó. Tres proveedores:

* `reglas` — decide la regla; Jev no se consulta (así nace todo: el turno es
  el de hoy).
* `sombra` — decide la regla; Jev contesta al lado y cada desacuerdo va a la
  cola que califica Claude Code.
* `jev` — decide Jev; si falla, tarda, duda o viene otra versión que la
  calibrada, decide la regla. Los pisos (p. ej. la baja explícita) nunca se
  quitan.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import capabilities as caps
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.sdk.connectorkit import PerceptionResult, TypedAnswer, TypedQuestion


@dataclass(frozen=True)
class Ask:
    text: str


class Unsubscribe:
    """Capacidad de juguete: ¿el cliente pide no recibir más mensajes?"""

    name = "juguete"
    timeout_s = 0.5

    def rule(self, inp: Ask) -> bool:
        return "no más" in inp.text

    def ask(self, inp: Ask):
        return f"Mensaje del cliente: {inp.text}", [
            TypedQuestion(id="baja", kind="noul", text="¿Pide dejar de recibir mensajes?", criteria={"true": "sí", "false": "no"})
        ]

    def decide(self, inp: Ask, result, rule: bool, thresholds) -> bool | None:
        p = next((a.p for a in result.answers if a.id == "baja"), None)
        if p is None:
            return None
        if p >= 0.85:
            return True
        if p <= 0.15:
            return False
        return None

    def floor(self, inp: Ask, rule: bool, jev: bool) -> bool:
        return rule or jev  # la frase explícita nunca se quita

    def same(self, a: bool, b: bool) -> bool:
        return a == b


class _Port:
    def __init__(self, p: float | None, *, model: str = "typesafe/jev-1.13-20260917", error: str | None = None,
                 delay_s: float = 0.0) -> None:
        answers = {} if p is None else {"baja": TypedAnswer(id="baja", kind="noul", p=p)}
        self.fake = FakePerceptionAdapter(answers, error=error)
        self.model, self.delay_s, self.calls = model, delay_s, 0

    async def ask(self, state, questions, *, timeout_s, redact=()):
        self.calls += 1
        if self.delay_s:
            try:
                await asyncio.wait_for(asyncio.sleep(self.delay_s), timeout=timeout_s)
            except TimeoutError:
                return PerceptionResult(ok=False, error="timeout", provider="fake", model=self.model)
        result = await self.fake.ask(state, questions, timeout_s=timeout_s, redact=redact)
        return dataclasses.replace(result, model=self.model if result.ok else result.model)


@pytest.fixture
def port(monkeypatch):
    from src.sdk import connectorkit

    holder: dict[str, _Port] = {"port": _Port(0.95)}

    def _get(_oracle: str) -> _Port:
        return holder["port"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


async def test_reglas_is_todays_rule_and_never_asks_jev(port) -> None:
    verdict = await caps.decide(Unsubscribe(), Ask("gracias"), provider="reglas", profile_id="jev-v1")

    assert (verdict.value, verdict.by, verdict.provider) == (False, "reglas", "reglas")
    assert port["port"].calls == 0


async def test_sombra_keeps_the_rule_and_queues_the_disagreement(port, tmp_path: Path) -> None:
    log = DisagreementLog(tmp_path)

    verdict = await caps.decide(
        Unsubscribe(), Ask("no me escriban más"), provider="sombra", profile_id="jev-v1",
        disagreements=log, session_id="wa_573001234567",
    )

    assert (verdict.value, verdict.by, verdict.rule, verdict.jev, verdict.agree) == (False, "reglas", False, True, False)
    [item] = log.pending()
    assert item["capability"] == "juguete" and (item["rule"], item["jev"]) == (False, True)
    assert "no me escriban más" in item["state"] and item["session_id"] == "wa_573001234567"


async def test_sombra_with_agreement_queues_nothing(port, tmp_path: Path) -> None:
    log = DisagreementLog(tmp_path)

    await caps.decide(Unsubscribe(), Ask("no más mensajes"), provider="sombra", profile_id="jev-v1", disagreements=log)

    assert log.pending() == []


async def test_jev_decides_with_the_floor_on_top(port) -> None:
    port["port"] = _Port(0.05)

    explicit = await caps.decide(Unsubscribe(), Ask("no más"), provider="jev", profile_id="jev-v1")
    doubtful = await caps.decide(Unsubscribe(), Ask("quiero saber no más el precio"), provider="jev", profile_id="jev-v1")

    assert (explicit.value, explicit.by) == (True, "piso")  # la frase explícita nunca se quita
    assert (doubtful.value, doubtful.by) == (True, "piso")
    port["port"] = _Port(0.95)
    new = await caps.decide(Unsubscribe(), Ask("no me escriban"), provider="jev", profile_id="jev-v1")
    assert (new.value, new.by) == (True, "jev")


@pytest.mark.parametrize(
    ("oracle", "reason"),
    [(_Port(None, error="http_503"), "http_503"), (_Port(0.5), "duda"), (_Port(0.95, delay_s=5), "timeout")],
)
async def test_jev_falls_back_to_the_rule_when_it_fails_doubts_or_is_late(port, oracle, reason: str) -> None:
    port["port"] = oracle

    verdict = await caps.decide(Unsubscribe(), Ask("no me escriban"), provider="jev", profile_id="jev-v1")

    assert (verdict.value, verdict.by, verdict.reason) == (False, "respaldo", reason)


async def test_another_jev_version_than_the_calibrated_one_does_not_act(port, monkeypatch) -> None:
    from src.plugins.chats.agent.sales.decisions import profiles

    v1 = profiles.get_engine_profile("jev-v1")
    table = dict(profiles.load_engine_profiles()) | {"jev-v1": dataclasses.replace(v1, calibrated_model="typesafe/jev-1.12-x")}
    monkeypatch.setattr(profiles, "load_engine_profiles", lambda: table)

    verdict = await caps.decide(Unsubscribe(), Ask("no me escriban"), provider="jev", profile_id="jev-v1")

    assert (verdict.value, verdict.by, verdict.reason) == (False, "respaldo", "model_changed")
    assert verdict.jev is True  # lo que dijo Jev queda para medir


async def test_the_verdict_travels_as_json(port) -> None:
    verdict = await caps.decide(Unsubscribe(), Ask("no me escriban"), provider="jev", profile_id="jev-v1")

    trace = verdict.to_trace()
    assert json.loads(json.dumps(trace)) == trace
    assert trace["capability"] == "juguete" and trace["by"] == "jev" and trace["model"] == "typesafe/jev-1.13-20260917"


async def test_an_unknown_profile_is_the_rule(port) -> None:
    verdict = await caps.decide(Unsubscribe(), Ask("no me escriban"), provider="jev", profile_id="no-existe")

    assert (verdict.value, verdict.by, verdict.reason) == (False, "respaldo", "unknown_profile")


def test_claude_code_labels_a_disagreement_and_the_log_counts_who_won(tmp_path: Path) -> None:
    log = DisagreementLog(tmp_path)
    first = log.record(capability="juguete", state="a", rule=False, jev=True, model="m", answers=[], session_id="wa_1")
    second = log.record(capability="juguete", state="b", rule=True, jev=False, model="m", answers=[], session_id="wa_2")

    log.label(first, True, note="pide la baja con otras palabras")
    log.label(second, True)

    assert log.pending() == []
    assert log.score("juguete") == {"labeled": 2, "jev": 1, "rule": 1, "neither": 0}
    with pytest.raises(KeyError):
        log.label("no-existe", True)
