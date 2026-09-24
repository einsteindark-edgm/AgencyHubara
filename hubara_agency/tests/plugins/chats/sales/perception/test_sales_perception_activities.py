"""Activities de las capas ① y ③ (plan del laboratorio §3.2): llaman al
clasificador por el puerto del SDK y NUNCA fallan. Un error, un timeout o un
perfil desconocido devuelven un plan vacío (①) o `send` (③): el turno sale
como hoy (fail-open)."""
from __future__ import annotations

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.perception.activities import perceive_burst_activity, verify_coverage_activity
from src.plugins.chats.agent.sales.perception.contracts import PerceiveInput, VerifyInput

SID = "wa_573001234567"
MESSAGES = [
    {"text": "¿me mandas el catálogo? mi número es 3001234567", "ts_ms": 1_000},
    {"text": "y el envío a Bogotá cuánto sale?", "ts_ms": 8_000},
]


@pytest.fixture(autouse=True)
def _fake_classifier(monkeypatch):
    from src.sdk import connectorkit

    monkeypatch.setenv("PERCEPTION_PROVIDER", "fake")
    connectorkit.get_perception_port.cache_clear()
    yield
    connectorkit.get_perception_port.cache_clear()


async def test_perceive_returns_the_plan_and_the_answers_for_the_trace() -> None:
    out = await ActivityEnvironment().run(
        perceive_burst_activity, PerceiveInput(session_id=SID, profile="jev-v1", messages=MESSAGES)
    )

    assert out.ok and out.profile == "jev-v1"
    assert [t["topic"] for t in out.topics] == ["catalogo", "envio"]
    assert any(a["q"] == "topic.catalogo" and a["picked"] for a in out.answers)
    assert not any(a["q"] == "topic.queja" and a["picked"] for a in out.answers)


async def test_perceive_fails_open(monkeypatch) -> None:
    monkeypatch.setenv("PERCEPTION_PROVIDER", "off")
    from src.sdk import connectorkit

    connectorkit.get_perception_port.cache_clear()

    out = await ActivityEnvironment().run(
        perceive_burst_activity, PerceiveInput(session_id=SID, profile="jev-v1", messages=MESSAGES)
    )

    assert not out.ok and out.topics == [] and out.error


async def test_verify_decides_on_the_reply_that_is_about_to_go_out() -> None:
    topics = [{"topic": "catalogo", "msg": 1, "p": 0.9}, {"topic": "envio", "msg": 2, "p": 0.9}]

    covered = await ActivityEnvironment().run(
        verify_coverage_activity,
        VerifyInput(session_id=SID, profile="jev-v1", messages=MESSAGES, topics=topics,
                    reply_text="Te dejo el catálogo y el envío a Bogotá cuesta $X", components=[]),
    )

    assert covered.ok and covered.decision == "send" and covered.missing == []
    assert [a["q"] for a in covered.answers] == ["cover.catalogo", "cover.envio"]


async def test_verify_fails_open(monkeypatch) -> None:
    monkeypatch.setenv("PERCEPTION_PROVIDER", "off")
    from src.sdk import connectorkit

    connectorkit.get_perception_port.cache_clear()

    out = await ActivityEnvironment().run(
        verify_coverage_activity,
        VerifyInput(session_id=SID, profile="jev-v1", messages=MESSAGES,
                    topics=[{"topic": "catalogo", "msg": 1, "p": 0.9}], reply_text="hola", components=[]),
    )

    assert out.decision == "send" and not out.ok
