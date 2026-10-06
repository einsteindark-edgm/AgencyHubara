"""Cada pregunta a Jev suma su costo a la conversación (episodio en el vault).

Ventas pregunta a Jev en tres lugares: las capacidades del motor (`decide`:
ingest, tools, remarketing, egreso), la lectura del turno (① `perceive_burst`)
y la revisión antes de enviar (③ `verify_coverage`). En los tres, lo que cobró
OpenRouter se suma a `episodes[].jev_usage` — también en sombra, que pregunta
igual aunque decida la regla. Con la regla sola no se pregunta ni se cobra.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.decisions import activities as decision_activities
from src.plugins.chats.agent.sales.decisions import engine
from src.plugins.chats.agent.sales.decisions.capabilities import decide
from src.plugins.chats.agent.sales.decisions.contracts import PerceiveInput, TurnDecisions, VerifyInput, VerifyOutput
from src.sdk import connectorkit
from src.sdk.connectorkit import FakePerceptionAdapter, TypedQuestion

SID = "wa_573001234567"


def _seed(vault: Path) -> Path:
    path = vault / SID / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"episodes": [{"episode_id": "ep_001", "closed_at_ms": None}]}), encoding="utf-8")
    return path


def _usage(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8"))["episodes"][0].get("jev_usage")


@dataclass
class _Saludo:
    """Una capacidad mínima: una pregunta sí/no."""

    name: str = "saludo"
    thresholds = {"yes": 0.8}

    def rule(self, inp):
        return False

    def ask(self, inp):
        return "estado", (TypedQuestion(id="saludo.es", kind="noul", text="¿Saluda?"),)

    def decide(self, inp, result, rule, thresholds):
        return result.answers[0].p >= 0.8

    def floor(self, inp, rule, jev):
        return jev

    def same(self, a, b):
        return a == b


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["jev", "sombra"])
async def test_a_capability_question_costs_the_conversation(_isolate_vault_dir: Path, monkeypatch, provider: str) -> None:
    path = _seed(_isolate_vault_dir)
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: FakePerceptionAdapter(cost_usd=0.00002))

    await decide(_Saludo(), {}, provider=provider, profile_id="jev-v3", session_id=SID)

    assert _usage(path) == {"calls": 1, "cost_usd_micros": 20}


@pytest.mark.asyncio
async def test_with_the_rule_alone_nothing_is_asked_nor_charged(_isolate_vault_dir: Path, monkeypatch) -> None:
    path = _seed(_isolate_vault_dir)
    fake = FakePerceptionAdapter(cost_usd=0.00002)
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)

    await decide(_Saludo(), {}, provider="reglas", profile_id="jev-v3", session_id=SID)

    assert fake.calls == [] and _usage(path) is None


@pytest.mark.asyncio
async def test_reading_the_turn_charges_the_profile_and_its_shadow(_isolate_vault_dir: Path, monkeypatch) -> None:
    path = _seed(_isolate_vault_dir)

    async def perceive(inp, *, redact=(), context=None):
        return TurnDecisions(ok=True, profile=inp.profile, cost_usd=0.00003, shadow={"profile": "jev-v4", "cost_usd": 0.00002})

    monkeypatch.setattr(engine, "perceive", perceive)
    monkeypatch.setattr(engine, "needs_context", lambda _profile: False)

    await ActivityEnvironment().run(decision_activities.perceive_burst_activity, PerceiveInput(session_id=SID, profile="jev-v3"))

    assert _usage(path) == {"calls": 2, "cost_usd_micros": 50}


@pytest.mark.asyncio
async def test_reviewing_the_reply_charges_the_conversation(_isolate_vault_dir: Path, monkeypatch) -> None:
    path = _seed(_isolate_vault_dir)

    async def verify(inp, *, redact=()):
        return VerifyOutput(ok=True, decision="send", cost_usd=0.00004)

    monkeypatch.setattr(engine, "verify", verify)

    await ActivityEnvironment().run(decision_activities.verify_coverage_activity, VerifyInput(session_id=SID, profile="jev-v3"))

    assert _usage(path) == {"calls": 1, "cost_usd_micros": 40}
