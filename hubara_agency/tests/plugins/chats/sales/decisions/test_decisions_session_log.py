"""Las decisiones de Jev quedan con su conversación (Calidad LLM, 2026-10-02).

Calidad LLM muestra en producción lo mismo que el laboratorio: en cada turno,
qué decidió cada capacidad y si lo decidió Jev o la regla. Cada decisión en
la que Jev participa (sombra o jev) queda en `<vault>/<sesión>/evals/
decisions.jsonl` con su etapa (`ingest`, con el mensaje que la disparó, o
`turno`), compacta y con el texto personal tapado. Con la regla de hoy no se
escribe nada: el bot actual no deja rastro nuevo.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.sales.decisions import capabilities as caps
from src.plugins.chats.agent.sales.decisions.decision_log import SessionDecisionLog, decisions_path, read_decisions
from src.plugins.chats.agent.sales.decisions.readings import EngineReadings, Inbound
from src.sdk import connectorkit
from src.sdk.connectorkit import TypedAnswer
from tests.plugins.chats.sales.decisions.test_decisions_capabilities import Ask, Unsubscribe, _Port

SID = "wa_573001234567"


@pytest.fixture
def port(monkeypatch):
    holder = {"port": _Port(0.95)}

    def _get(_oracle: str):
        return holder["port"]

    _get.cache_clear = lambda: None
    monkeypatch.setattr(connectorkit, "get_perception_port", _get)
    return holder


async def test_a_jev_decision_of_the_turn_stays_with_its_conversation(port, tmp_path: Path) -> None:
    await caps.decide(Unsubscribe(), Ask("no me escriban"), provider="jev", profile_id="jev-v1",
                      session_id=SID, decisions=SessionDecisionLog(tmp_path))

    [row] = read_decisions(tmp_path, SID)
    assert decisions_path(tmp_path, SID).parent == tmp_path / SID / "evals"
    assert (row["stage"], row["capability"], row["by"], row["provider"], row["value"]) == ("turno", "juguete", "jev", "jev", True)
    assert row["answers"] == [{"q": "baja", "p": 0.95}] and isinstance(row["at_ms"], int)


async def test_with_todays_rule_nothing_is_written(port, tmp_path: Path) -> None:
    await caps.decide(Unsubscribe(), Ask("no más"), provider="reglas", profile_id="jev-v1",
                      session_id=SID, decisions=SessionDecisionLog(tmp_path))

    assert read_decisions(tmp_path, SID) == [] and not decisions_path(tmp_path, SID).exists()


async def test_a_log_that_fails_never_breaks_the_decision(port, tmp_path: Path) -> None:
    class Broken:
        def record(self, *_a, **_kw):
            raise OSError("disco lleno")

    verdict = await caps.decide(Unsubscribe(), Ask("no me escriban"), provider="jev", profile_id="jev-v1",
                                session_id=SID, decisions=Broken())

    assert (verdict.value, verdict.by) == (True, "jev")


async def test_the_ingest_leaves_each_decision_with_the_message_that_triggered_it(tmp_path: Path, monkeypatch) -> None:
    fake = FakePerceptionAdapter({"cortesia.solo": TypedAnswer(id="cortesia.solo", kind="noul", p=0.9)})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: fake)
    monkeypatch.setenv("DECISIONS_BOT", "B")

    await EngineReadings(tmp_path).read(Inbound(session_id=SID, text="gracias!", now_ms=1_000, message_id="wamid.1"))

    rows = read_decisions(tmp_path, SID)
    assert {r["capability"] for r in rows} >= {"compra", "retoma", "cortesia"}
    assert {(r["stage"], r["message_id"]) for r in rows} == {("ingest", "wamid.1")}
    assert all(r.get("bundle") == "ventas@1" for r in rows)


def test_the_text_of_a_decision_goes_without_the_customers_data(tmp_path: Path) -> None:
    verdict = caps.Verdict(capability="rescate", value="Listo Pedrito, te escribo al 3001234567", by="jev",
                           provider="jev", rule="", jev="Listo Pedrito, te escribo al 3001234567")

    SessionDecisionLog(tmp_path).record(SID, verdict, redact=("Pedrito",), at_ms=5)

    [row] = read_decisions(tmp_path, SID)
    assert "Pedrito" not in str(row) and "3001234567" not in str(row) and row["at_ms"] == 5


def test_a_broken_line_does_not_hide_the_others(tmp_path: Path) -> None:
    SessionDecisionLog(tmp_path).record(SID, caps.Verdict(capability="baja", value=True, by="jev", provider="jev", rule=False),
                                        at_ms=1)
    with decisions_path(tmp_path, SID).open("ab") as f:
        f.write(b'no-json\n{"capability": "\xc3"}\n')

    assert [r["capability"] for r in read_decisions(tmp_path, SID)] == ["baja"]


async def test_when_jev_had_nothing_to_ask_nothing_is_written(port, tmp_path: Path) -> None:
    """Sin pregunta para Jev decide la regla y no hay nada que mostrar: un
    turno común no deja rastro en el vault (ni crea la carpeta de la sesión)."""
    class NothingToAsk(Unsubscribe):
        def ask(self, inp):
            return None

    verdict = await caps.decide(NothingToAsk(), Ask("hola"), provider="jev", profile_id="jev-v1",
                                session_id=SID, decisions=SessionDecisionLog(tmp_path))

    assert verdict.reason == "no_question" and not (tmp_path / SID).exists()
