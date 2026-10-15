"""Lo que el LLM manda mal al llamar una tool no deja al cliente sin respuesta.

Incidente 2026-10-15: con Gemini de respaldo, el LLM llamó una tool con un
argumento inventado, la tool lanzó `TypeError`, la activity `execute_tool`
falló sus 2 intentos con los mismos argumentos y el turno murió callado.

Se prueba el turno entero (`run_agent_turn`) con la activity `execute_tool` de
producción: el dispatch real, el corte de turno real. Solo el LLM es falso.

  * Falta un argumento obligatorio o un tipo no sirve: la tool no corre, el
    modelo lee el error y responde en la ronda siguiente. Ojo:
    `present_products` corta el turno (espera al cliente); un error en texto
    plano el corte lo toma por «salió», y el bot se callaba.
  * La tool lanza `TypeError` (error de programación, determinista): no se
    reintenta con los mismos argumentos y el turno sigue; el modelo lee que la
    tool falló y responde sin ella.
  * La tool falla en cada intento con otra excepción (un servicio caído): se
    reintenta (puede ser pasajero) y, agotados los intentos, el modelo lee que
    falló y responde sin ella. Antes el `ActivityError` tumbaba el workflow de
    ventas con el mensaje del cliente.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolBase, ToolContext
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError, WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from exoclaw_temporal.config import (
    BuildPromptInput,
    LLMChatInput,
    LLMConfig,
    LLMResponseData,
    RecordTurnInput,
    SessionInput,
    ToolCallData,
    WorkspaceConfig,
)
from src.platform import tool_extensions
from src.platform.temporal.activities import execute_tool
from src.platform.workflow_helpers import TOOL_FAILURE_PATCH, PendingMessage, run_agent_turn

QUEUE = "test-tool-dispatch-bad-args"
SESSION = "wa_bad_args"
ANSWER = "Tenemos velones rojos y verdes, ¿cuál te muestro?"


class _PresentProducts(ToolBase):
    """Como la de ventas: `intro_text` obligatorio, corta el turno."""

    name = "present_products"
    description = "Envía productos al cliente."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "intro_text": {"type": "string"},
            "category": {"type": "string"},
            "handles": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["intro_text"],
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute_with_context(
        self, ctx: ToolContext, intro_text: str = "", category: str | None = None, handles: list[str] | None = None
    ) -> str:
        self.calls.append({"intro_text": intro_text, "category": category, "handles": handles})
        return '{"queued": true, "count": 2}'


class _Buggy(ToolBase):
    """Una tool con un error de programación: revienta igual en cada intento."""

    name = "search_products"
    description = "Busca productos."
    parameters: dict[str, Any] = {"type": "object", "properties": {"query": {"type": "string"}}}

    def __init__(self) -> None:
        self.attempts = 0

    async def execute_with_context(self, ctx: ToolContext, query: str = "") -> str:
        self.attempts += 1
        return "%d productos" % query  # TypeError: %d format: a real number is required


class _Broken(ToolBase):
    """Una tool cuyo servicio está caído: falla en cada intento."""

    name = "search_products"
    description = "Busca productos."
    parameters: dict[str, Any] = {"type": "object", "properties": {"query": {"type": "string"}}}

    def __init__(self) -> None:
        self.attempts = 0

    async def execute_with_context(self, ctx: ToolContext, query: str = "") -> str:
        self.attempts += 1
        raise RuntimeError("el catálogo no responde")


class _State:
    def __init__(self) -> None:
        self.replies: list[Any] = []
        self.calls: list[list[dict]] = []


STATE = _State()


@activity.defn(name="build_prompt")
async def _build_prompt(inp: BuildPromptInput) -> list[dict]:
    return [{"role": "system", "content": "s"}, {"role": "user", "content": inp.message}]


@activity.defn(name="llm_chat")
async def _llm_chat(inp: LLMChatInput) -> LLMResponseData:
    STATE.calls.append(list(inp.messages))
    n = len(STATE.calls)
    reply = STATE.replies[min(n - 1, len(STATE.replies) - 1)]
    if isinstance(reply, list):
        calls = [ToolCallData(id=f"c{n}_{i}", name=name, arguments=args) for i, (name, args) in enumerate(reply)]
        return LLMResponseData(content="", finish_reason="tool_calls", has_tool_calls=True, tool_calls=calls)
    return LLMResponseData(content=reply, finish_reason="stop", has_tool_calls=False, tool_calls=[])


@activity.defn(name="record_turn")
async def _record_turn(inp: RecordTurnInput) -> None:
    return None


@workflow.defn(name="ToolDispatchBadArgsProbeWorkflow")
class _ProbeWorkflow:
    @workflow.run
    async def run(self, workspace: str) -> dict:
        session = SessionInput(
            session_id=SESSION, channel="whatsapp", chat_id=SESSION,
            llm=LLMConfig(model="fake", max_iterations=10),
            workspace=WorkspaceConfig(path=workspace), tool_definitions_json="[]",
        )
        result = await run_agent_turn(session, PendingMessage(message="¿Qué velones tienen?"), episode_id="ep_001")
        return {"final": result.final_content}


@pytest.fixture
def tool(monkeypatch: pytest.MonkeyPatch) -> _PresentProducts:
    instance = _PresentProducts()
    monkeypatch.setattr(tool_extensions, "_EXTENSIONS", {"test.present_products": lambda ws: instance})
    return instance


@pytest.fixture
def buggy(monkeypatch: pytest.MonkeyPatch) -> _Buggy:
    instance = _Buggy()
    monkeypatch.setattr(tool_extensions, "_EXTENSIONS", {"test.search_products": lambda ws: instance})
    return instance


@pytest.fixture
def broken(monkeypatch: pytest.MonkeyPatch) -> _Broken:
    instance = _Broken()
    monkeypatch.setattr(tool_extensions, "_EXTENSIONS", {"test.search_products": lambda ws: instance})
    return instance


async def _turn(tmp_path: Path, replies: list[Any]) -> dict[str, Any]:
    STATE.__init__()
    STATE.replies = replies
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=QUEUE, workflows=[_ProbeWorkflow],
            activities=[_build_prompt, _llm_chat, execute_tool, _record_turn],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(
                _ProbeWorkflow.run, str(tmp_path / "ws"), id="tool-dispatch-bad-args", task_queue=QUEUE
            )


async def _history(tmp_path: Path, replies: list[Any]) -> WorkflowHistory:
    """La historia del turno, haya terminado bien o caído."""
    STATE.__init__()
    STATE.replies = replies
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=QUEUE, workflows=[_ProbeWorkflow],
            activities=[_build_prompt, _llm_chat, execute_tool, _record_turn],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _ProbeWorkflow.run, str(tmp_path / "ws"), id="tool-dispatch-replay", task_queue=QUEUE
            )
            with contextlib.suppress(WorkflowFailureError):
                await handle.result()
            return await handle.fetch_history()


async def _replay(history: WorkflowHistory) -> None:
    await Replayer(workflows=[_ProbeWorkflow], workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(history)


def _tool_results() -> list[str]:
    return [m["content"] for m in STATE.calls[-1] if m.get("role") == "tool"]


@pytest.mark.parametrize(
    ("args", "culprit"),
    [
        pytest.param({"category": "velones"}, "intro_text", id="falta-obligatorio"),
        pytest.param({"intro_text": "Mira:", "handles": "velon-rojo"}, "handles", id="tipo-que-no-sirve"),
    ],
)
async def test_bad_args_let_the_model_answer_in_the_next_round(
    tmp_path: Path, tool: _PresentProducts, args: dict[str, Any], culprit: str
) -> None:
    out = await _turn(tmp_path, [[("present_products", args)], ANSWER])

    assert tool.calls == []
    assert len(STATE.calls) == 2, "el turno se cortó: el modelo nunca leyó el error"
    [result] = _tool_results()
    assert culprit in result
    assert out["final"] == ANSWER


async def test_a_type_error_inside_the_tool_is_not_retried_and_the_turn_goes_on(
    tmp_path: Path, buggy: _Buggy
) -> None:
    out = await _turn(tmp_path, [[("search_products", {"query": "velones"})], ANSWER])

    assert buggy.attempts == 1, "un TypeError se reintentó con los mismos argumentos"
    [result] = _tool_results()
    assert "search_products" in result
    assert out["final"] == ANSWER


async def test_a_tool_that_fails_every_attempt_lets_the_model_answer_without_it(
    tmp_path: Path, broken: _Broken
) -> None:
    out = await _turn(tmp_path, [[("search_products", {"query": "velones"})], ANSWER])

    assert broken.attempts == 2, "la falla puede ser pasajera: se reintenta como siempre"
    [result] = _tool_results()
    assert json.loads(result) | {"message": ""} == {"queued": False, "error": "tool_failed", "message": ""}
    assert out["final"] == ANSWER


async def test_the_turn_that_survived_a_failed_tool_replays(tmp_path: Path, broken: _Broken) -> None:
    history = await _history(tmp_path, [[("search_products", {"query": "velones"})], ANSWER])

    await _replay(history)


async def test_a_history_from_before_the_patch_replays_its_failure(
    tmp_path: Path, broken: _Broken, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una sesión que estaba corriendo en el deploy: su historia ya tiene la
    falla (el workflow cayó) y el código nuevo la re-juega igual."""
    real = workflow.patched
    monkeypatch.setattr(workflow, "patched", lambda patch_id: patch_id != TOOL_FAILURE_PATCH and real(patch_id))
    before = await _history(tmp_path, [[("search_products", {"query": "velones"})], ANSWER])
    assert before.events[-1].HasField("workflow_execution_failed_event_attributes")
    monkeypatch.setattr(workflow, "patched", real)

    await _replay(before)

    # Control negativo: sin la puerta, el código nuevo seguiría el turno donde
    # la historia cayó.
    monkeypatch.setattr(workflow, "patched", lambda patch_id: patch_id == TOOL_FAILURE_PATCH or real(patch_id))
    with pytest.raises(workflow.NondeterminismError):
        await _replay(before)
