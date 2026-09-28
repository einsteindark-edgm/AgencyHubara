"""Núcleo del motor (diseño v2 §02-§03): contexto, sombra doble y calibración
atada a la versión de Jev.

* Con un cuestionario que usa contexto (`rafaga-v2`), el motor le pasa a Jev
  lo que el cliente vio y los hechos del pedido.
* Sombra doble: mientras un perfil actúa, otro corre en sombra en la MISMA
  activity (dos llamadas en paralelo); lo de la sombra solo va a la traza y
  tiene su propio tiempo máximo para no demorar el turno.
* Calibración: si Jev responde con otra versión que la calibrada, lo que actúa
  baja a sombra (sin nota ni reglas) y la traza lo dice.
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import engine, profiles
from src.plugins.chats.agent.sales.decisions.context import TurnContext, Window
from src.plugins.chats.agent.sales.decisions.contracts import PerceiveInput
from src.sdk.connectorkit import PerceptionResult

MESSAGES = [{"text": "Si", "ts_ms": 1_000, "wamid": "w1"}]
CONTEXT = TurnContext(window=Window(lines=("[asesor] ¿Te lo enviamos a la misma dirección?",)), facts=("Etapa: datos de envío",))


class _Port:
    """Oráculo falso: responde lo que se le fije y registra lo que recibió."""

    def __init__(self, *, model: str = "typesafe/jev-1.13-20260917", delay_s: float = 0.0, answers=None) -> None:
        self.model = model
        self.delay_s = delay_s
        self.fake = FakePerceptionAdapter(answers or {})
        self.states: list[str] = []
        self.questions: list[tuple[str, ...]] = []

    async def ask(self, state, questions, *, timeout_s, redact=()):
        self.states.append(state)
        self.questions.append(tuple(q.id for q in questions))
        if self.delay_s:
            try:
                await asyncio.wait_for(asyncio.sleep(self.delay_s), timeout=timeout_s)
            except TimeoutError:
                return PerceptionResult(ok=False, error="timeout", provider="fake", model=self.model)
        result = await self.fake.ask(state, questions, timeout_s=timeout_s, redact=redact)
        return dataclasses.replace(result, model=self.model, latency_ms=int(self.delay_s * 1000))


@pytest.fixture
def ports(monkeypatch):
    """Un oráculo por perfil del oráculo; el motor los pide por id."""
    from src.sdk import connectorkit

    table: dict[str, _Port] = {}

    def _get(oracle: str) -> _Port:
        return table.setdefault(oracle, _Port())

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return table


def _with_profiles(monkeypatch, **extra) -> None:
    base = profiles.load_engine_profiles()
    table = dict(base) | extra
    monkeypatch.setattr(profiles, "load_engine_profiles", lambda: table)


async def test_with_a_context_questionnaire_jev_sees_what_the_customer_saw(ports, monkeypatch) -> None:
    out = await engine.perceive(PerceiveInput(session_id="wa_x", profile="jev-v2", messages=MESSAGES), context=CONTEXT)

    [state] = ports["jev-1.13"].states
    assert "¿Te lo enviamos a la misma dirección?" in state and "ESTE TURNO" in state
    assert "thread.bot_asked" in ports["jev-1.13"].questions[0]
    assert out.versions["questions"] == "rafaga-v2" and out.versions["policy"] == "turno-v2"


async def test_v1_ignores_the_context(ports) -> None:
    await engine.perceive(PerceiveInput(session_id="wa_x", profile="jev-v1", messages=MESSAGES), context=CONTEXT)

    [state] = ports["jev-1.13"].states
    assert "CONTEXTO" not in state and state.startswith("Mensajes del cliente en este turno:")


async def test_the_shadow_profile_runs_alongside_and_only_goes_to_the_trace(ports, monkeypatch) -> None:
    v1 = profiles.get_engine_profile("jev-v1")
    _with_profiles(monkeypatch, **{"jev-v1": dataclasses.replace(v1, shadow="jev-v2")})

    out = await engine.perceive(PerceiveInput(session_id="wa_x", profile="jev-v1", messages=MESSAGES), context=CONTEXT)

    assert out.versions["questions"] == "rafaga-v1"
    assert out.shadow["profile"] == "jev-v2" and out.shadow["ok"] is True
    assert "reading" in out.shadow and "topics" in out.shadow
    assert len(ports["jev-1.13"].states) == 2


async def test_a_slow_shadow_does_not_hold_the_turn(ports, monkeypatch) -> None:
    v1 = profiles.get_engine_profile("jev-v1")
    slow = dataclasses.replace(profiles.get_engine_profile("jev-v2"), oracle="jev-slow")
    _with_profiles(monkeypatch, **{"jev-v1": dataclasses.replace(v1, shadow="jev-slow-v2"), "jev-slow-v2": slow})
    ports["jev-slow"] = _Port(delay_s=5.0)

    started = asyncio.get_running_loop().time()
    out = await engine.perceive(PerceiveInput(session_id="wa_x", profile="jev-v1", messages=MESSAGES), context=CONTEXT)

    assert asyncio.get_running_loop().time() - started < engine.SHADOW_TIMEOUT_S + 1.0
    assert out.ok and out.shadow["ok"] is False and out.shadow["error"] == "timeout"


async def test_another_jev_version_than_the_calibrated_one_turns_acting_into_shadow(ports, monkeypatch) -> None:
    v1 = profiles.get_engine_profile("jev-v1")
    _with_profiles(monkeypatch, **{"jev-v1": dataclasses.replace(v1, calibrated_model="typesafe/jev-1.12-20260801")})
    many = [{"text": "¿me mandas el catálogo?", "ts_ms": 1}, {"text": "y el envío?", "ts_ms": 2}]

    out = await engine.perceive(PerceiveInput(session_id="wa_x", profile="jev-v1", messages=many))

    assert out.topics  # lo que Jev leyó queda en la traza
    assert out.note is None and out.coverage == {}
    assert out.acting == {
        "allowed": False, "reason": "model_changed",
        "calibrated": "typesafe/jev-1.12-20260801", "served": "typesafe/jev-1.13-20260917",
    }


async def test_the_calibrated_version_acts(ports, monkeypatch) -> None:
    v1 = profiles.get_engine_profile("jev-v1")
    _with_profiles(monkeypatch, **{"jev-v1": dataclasses.replace(v1, calibrated_model="typesafe/jev-1.13-20260917")})
    many = [{"text": "¿me mandas el catálogo?", "ts_ms": 1}, {"text": "y el envío?", "ts_ms": 2}]

    out = await engine.perceive(PerceiveInput(session_id="wa_x", profile="jev-v1", messages=many))

    assert out.note is not None and out.acting == {"allowed": True}


def test_the_v2_profile_uses_the_context_questionnaire_and_policy() -> None:
    v2 = profiles.get_engine_profile("jev-v2")

    assert (v2.oracle, v2.questions, v2.policy) == ("jev-1.13", "rafaga-v2", "turno-v2")
    assert v2.thresholds["purchase_confirm"] == 0.85 and v2.thresholds["purchase_retract"] == 0.20
    assert engine.needs_context("jev-v2") and not engine.needs_context("jev-v1")


def test_the_answer_ids_stay_the_same_for_the_bench() -> None:
    """El banco de referencia califica por id de pregunta: `rafaga-v2` no
    renombra los asuntos de v1."""
    from src.plugins.chats.agent.sales.decisions.questionnaire import load_questionnaire

    assert load_questionnaire("rafaga-v2").topic_ids == load_questionnaire("rafaga-v1").topic_ids
