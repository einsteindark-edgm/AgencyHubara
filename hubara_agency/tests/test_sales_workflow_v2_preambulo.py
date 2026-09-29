"""Workflow V2: la muletilla del modelo la decide el egreso (capacidad `preambulo`).

El turno compartido (`run_agent_turn`) sanea el texto final con la regla de
hoy; con el gancho de egreso del V2 le pasa además lo que el LLM escribió
ANTES del saneador y el motor decide ahí la muletilla de presentación («Aquí
tienes:»). El V2 no la lee: aplica lo que el egreso dejó grabado.

* Con `reglas` (el bot B0) el resultado es el del V1 (A1): lo que recibe el
  cliente, lo que muestra el panel y la traza.
* Con Jev (el bot B), sale el texto que decidió el motor, el LLM lo recuerda
  así y la traza muestra ese saneado.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
from tests.test_sales_workflow_debounce import _final_resp
from tests.test_sales_workflow_v2 import _comparable, _customer_trace, _run, _scripted_egress, _sent

PREAMBLE = "Aquí tienes:\n¡Hola! ¿Qué aroma te gusta?"
HELLO = "¡Hola! ¿Qué aroma te gusta?"
UNKNOWN = "Claro, aquí va el mensaje para el cliente:\n\n¡Hola! La Cubo Love cuesta $45.000 🤍"
ANSWER = "¡Hola! La Cubo Love cuesta $45.000 🤍"

_CASES = {
    "muletilla conocida": PREAMBLE,
    "muletilla y comillas": 'Here\'s my attempt:\n\n"¡Hola de nuevo! Quedó pendiente tu pedido 🤍"',
    "muletilla que la regla no conoce": UNKNOWN,
    "muletilla y razonamiento": "Okay,\n\nEl cliente pregunta el precio. Le respondo.\n\n¡La Cubo Love cuesta $45.000! 🤍",
}


@pytest.mark.parametrize("case", list(_CASES))
async def test_b0_equals_a1_when_the_llm_opens_with_a_preamble(case: str, tmp_path: Path) -> None:
    responses = [_final_resp(_CASES[case])]
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    a1 = await _run(HubaraSalesSessionWorkflow, tmp_path / "a", responses=responses)
    b0 = await _run(HubaraSalesSessionWorkflowV2, tmp_path / "b", responses=list(responses))

    assert _comparable(b0) == _comparable(a1)


async def test_v2_hands_the_egress_what_the_llm_wrote_before_the_sanitizer(tmp_path: Path) -> None:
    seen: list[EgressInput] = []

    await _run(
        HubaraSalesSessionWorkflowV2,
        tmp_path,
        responses=[_final_resp(PREAMBLE)],
        replace=[_scripted_egress(EgressOutput(text=HELLO, llm_text=HELLO, final_text=HELLO), seen)],
    )

    assert (seen[0].final_text, seen[0].raw_text) == (HELLO, PREAMBLE)


class _Jev:
    """Jev falso: la primera oración es muletilla, la segunda no; todo texto
    es un mensaje para el cliente."""

    name = "fake"
    model = "fake"

    async def ask(self, state, questions, *, timeout_s, redact=()):
        from src.sdk.connectorkit import PerceptionResult, TypedAnswer

        answers = []
        for q in questions:
            if q.id.startswith("preambulo."):
                answers.append(TypedAnswer(id=q.id, kind="noul", p=0.95 if q.id == "preambulo.1" else 0.03))
            elif q.kind == "choice":
                pick = "mensaje_al_cliente"
                answers.append(TypedAnswer(id=q.id, kind="choice", choice=pick, probs=((pick, 0.97),), confidence=0.97))
            else:
                answers.append(TypedAnswer(id=q.id, kind="noul", p=0.02))
        return PerceptionResult(ok=True, answers=tuple(answers), provider="fake", model="fake")


async def test_with_jev_v2_sends_and_remembers_the_text_without_the_preamble(tmp_path: Path, monkeypatch) -> None:
    from src.sdk import connectorkit

    monkeypatch.setenv("DECISIONS_BOT", "B")
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: _Jev())

    tracker = await _run(HubaraSalesSessionWorkflowV2, tmp_path, responses=[_final_resp(UNKNOWN)])

    assert _sent(tracker) == [ANSWER]
    remembered = [
        m["content"] for m in tracker.record_turn_new_messages[0]
        if m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls")
    ]
    assert remembered == [ANSWER]
    trace = _customer_trace(tracker)
    assert trace["llm_text"] == ANSWER
    [step] = [s for s in trace["steps"] if s.get("kind") == "guard" and s.get("name") == "sanitizer"]
    assert (step["before"], step["after"]) == (UNKNOWN, ANSWER)
    [verdict] = [v for v in trace["egress"]["verdicts"] if v["capability"] == "preambulo"]
    assert verdict["by"] == "jev"
