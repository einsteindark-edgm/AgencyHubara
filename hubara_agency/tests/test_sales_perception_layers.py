"""Las capas ①②③ del turno de ventas detrás del modo (plan del laboratorio §3.2,
PR 14), con el workflow REAL sobre el servidor de pruebas de Temporal.

El modo viaja en el 4.º argumento de la señal (`inbound_meta.perception_mode`,
lo pone el ingest desde el techo de Terraform): sin modo, o con `off`, el
turno es el de hoy y no se agenda ningún comando nuevo (ni siquiera el marker
de `workflow.patched`). Con modo:

  * `shadow`: la percepción corre en paralelo al LLM y la verificación después
    de enviar. Solo se registran en la traza; la respuesta no cambia.
  * `on` / `canary`: ① el plan llega al LLM como nota del turno; ② si el
    corte por tool deja un asunto sin atender, una ronda más; ③ si la
    verificación dice que falta un asunto con claridad, un complemento como
    turno de sistema (una burbuja). Si el clasificador falla, el turno sale
    como hoy (fail-open).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import ExecuteToolInput, LLMChatInput, LLMResponseData, ToolCallData
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.perception.contracts import (
    PerceiveInput,
    PerceiveOutput,
    VerifyInput,
    VerifyOutput,
)
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities

CATALOG_REPLY = "¡Claro! Te dejo nuestro catálogo 👇"
BURST = ["vi que hacen velas con otros diseños, ¿me mandas el catálogo?", "y el envío a Bogotá cuánto sale?"]
TOPICS = [{"topic": "catalogo", "msg": 1, "p": 0.96}, {"topic": "envio", "msg": 2, "p": 0.93}]


def _tool(name: str, **args) -> LLMResponseData:
    return LLMResponseData(content="", finish_reason="tool_calls", has_tool_calls=True,
                           tool_calls=[ToolCallData(id=f"id-{name}", name=name, arguments=args)])


class Classifier:
    def __init__(self, *, perceive_ok: bool = True, decision: str = "send", missing: list[str] | None = None) -> None:
        self.perceive_ok = perceive_ok
        self.decision = decision
        self.missing = missing or []
        self.perceived: list[PerceiveInput] = []
        self.verified: list[VerifyInput] = []

    def activities(self) -> list:
        @activity.defn(name="perceive_burst")
        async def perceive(inp: PerceiveInput) -> PerceiveOutput:
            self.perceived.append(inp)
            if not self.perceive_ok:
                return PerceiveOutput(ok=False, profile=inp.profile, error="timeout")
            return PerceiveOutput(ok=True, profile=inp.profile, model="typesafe/jev-1.13", topics=TOPICS,
                                  stage="descubrimiento", answers=[{"q": "topic.catalogo", "type": "noul", "p": 0.96, "picked": True}])

        @activity.defn(name="verify_coverage")
        async def verify(inp: VerifyInput) -> VerifyOutput:
            self.verified.append(inp)
            return VerifyOutput(ok=True, decision=self.decision, missing=list(self.missing), cost_usd=0.0002)

        return [perceive, verify]


class LLM:
    def __init__(self, responses: list[LLMResponseData]) -> None:
        self.responses = list(responses)
        self.inputs: list[list[dict]] = []

    def activity(self):
        @activity.defn(name="llm_chat")
        async def llm_chat(input: LLMChatInput) -> LLMResponseData:
            self.inputs.append(list(input.messages))
            if self.responses:
                return self.responses.pop(0)
            return LLMResponseData(content="ok", finish_reason="stop", has_tool_calls=False, tool_calls=[])

        return llm_chat


async def _run(tmp_path: Path, *, meta: dict | None, llm: LLM, classifier: Classifier,
               tool_results: dict | None = None) -> Tracker:
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    base = _make_fake_activities(tracker, workspace_path=str(workspace), tool_results=tool_results or {},
                                 prior_history=[{"role": "user", "content": "Hola"}, {"role": "assistant", "content": "¡Buenas!"}])
    results = tool_results or {}

    @activity.defn(name="execute_tool")
    async def execute_tool(input: ExecuteToolInput) -> str:
        # Como la tool real: `send_reply` devuelve el texto validado.
        tracker.execute_tool_calls.append(input.name)
        if input.name == "send_reply":
            return json.dumps({"reply": {"text": input.params.get("text", "")}}, ensure_ascii=False)
        return results.get(input.name, "ok")

    replaced = {"llm_chat", "execute_tool"}
    acts = [a for a in base if a.__temporal_activity_definition.name not in replaced] + [
        llm.activity(), execute_tool, *classifier.activities()
    ]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflow], activities=acts):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_layers", runtime_workspace_path=str(workspace)),
                id="session-wa_layers", task_queue=SALES_QUEUE,
            )
            for i, text in enumerate(BURST):
                args = [text, None, None] if meta is None else [text, None, None, {**meta, "ts_ms": 1_000 + i * 7_000}]
                await handle.signal(HubaraSalesSessionWorkflow.send_message, args=args)
            await handle.result()
    return tracker


def _customer_trace(tracker: Tracker) -> dict:
    return next(t for t in tracker.turn_traces if t["trigger"] == "customer")


@pytest.mark.asyncio
async def test_without_mode_the_turn_is_todays_and_no_layer_runs(tmp_path: Path) -> None:
    classifier = Classifier()
    tracker = await _run(tmp_path, meta=None, llm=LLM([_tool("send_reply", text=CATALOG_REPLY)]), classifier=classifier)

    assert classifier.perceived == [] and classifier.verified == []
    trace = _customer_trace(tracker)
    assert trace["mode"] == "off"
    assert not any(s["kind"] in ("perception", "plan", "verify") for s in trace["steps"])


@pytest.mark.asyncio
async def test_mode_off_in_the_signal_is_the_same_as_no_mode(tmp_path: Path) -> None:
    classifier = Classifier()
    tracker = await _run(tmp_path, meta={"perception_mode": "off", "perception_profile": "jev-v1"},
                         llm=LLM([_tool("send_reply", text=CATALOG_REPLY)]), classifier=classifier)

    assert classifier.perceived == [] and _customer_trace(tracker)["mode"] == "off"


@pytest.mark.asyncio
async def test_shadow_records_perception_and_verification_without_changing_the_reply(tmp_path: Path) -> None:
    classifier = Classifier(decision="complement", missing=["envio"])
    llm = LLM([_tool("send_reply", text=CATALOG_REPLY)])
    tracker = await _run(tmp_path, meta={"perception_mode": "shadow", "perception_profile": "jev-v1"}, llm=llm, classifier=classifier)

    assert [m["text"] for m in classifier.perceived[0].messages] == BURST
    assert not any("[PLAN DEL TURNO]" in json.dumps(msgs, ensure_ascii=False) for msgs in llm.inputs)
    assert tracker.send_whatsapp_calls == [("wa_layers", CATALOG_REPLY)]  # sin complemento en sombra
    trace = _customer_trace(tracker)
    assert trace["mode"] == "shadow"
    kinds = [s["kind"] for s in trace["steps"]]
    assert "perception" in kinds and "verify" in kinds
    assert kinds.index("verify") > kinds.index("outbound")  # en sombra verifica DESPUÉS de enviar
    verify = next(s for s in trace["steps"] if s["kind"] == "verify")
    assert verify["decision"] == "complement" and verify["applied"] is False
    assert verify["complement_scheduled"] is False and verify["cost_usd"] == 0.0002


@pytest.mark.asyncio
async def test_on_the_plan_reaches_the_llm_as_the_turn_note(tmp_path: Path) -> None:
    classifier = Classifier()
    llm = LLM([_tool("send_reply", text="Te dejo el catálogo y el envío a Bogotá cuesta $X")])
    tracker = await _run(tmp_path, meta={"perception_mode": "on", "perception_profile": "jev-v1"}, llm=llm, classifier=classifier)

    assert tracker.build_prompt_calls
    context = " ".join(tracker.build_prompt_calls[0].plugin_context or [])
    assert "[PLAN DEL TURNO]" in context and "catálogo (mensaje 1)" in context
    trace = _customer_trace(tracker)
    assert trace["mode"] == "on"
    plan = next(s for s in trace["steps"] if s["kind"] == "plan")
    assert [c["topic"] for c in plan["checklist"]] == ["catalogo", "envio"]
    assert classifier.verified and classifier.verified[0].reply_text.startswith("Te dejo el catálogo")


@pytest.mark.asyncio
async def test_on_a_tool_cut_that_leaves_a_topic_gets_one_more_round(tmp_path: Path) -> None:
    classifier = Classifier()
    llm = LLM([_tool("send_shipping_rates"), _tool("send_reply", text=CATALOG_REPLY)])
    tracker = await _run(tmp_path, meta={"perception_mode": "on", "perception_profile": "jev-v1"}, llm=llm, classifier=classifier,
                         tool_results={"send_shipping_rates": json.dumps({"queued": True})})

    assert len(llm.inputs) >= 2
    assert "[SISTEMA] Antes de terminar el turno" in json.dumps(llm.inputs[1], ensure_ascii=False)
    assert ("wa_layers", CATALOG_REPLY) in tracker.send_whatsapp_calls
    trace = _customer_trace(tracker)
    assert any(s["kind"] == "guard" and s["name"] == "turn_policy_extra_round" for s in trace["steps"])


@pytest.mark.asyncio
async def test_on_a_clear_gap_after_the_reply_becomes_one_complement_bubble(tmp_path: Path) -> None:
    classifier = Classifier(decision="complement", missing=["envio"])
    llm = LLM([_tool("send_reply", text=CATALOG_REPLY), _tool("send_reply", text="El envío a Bogotá cuesta $X")])
    tracker = await _run(tmp_path, meta={"perception_mode": "on", "perception_profile": "jev-v1"}, llm=llm, classifier=classifier)

    assert [text for _, text in tracker.send_whatsapp_calls][:2] == [CATALOG_REPLY, "El envío a Bogotá cuesta $X"]
    triggers = [t["trigger"] for t in tracker.turn_traces]
    assert triggers[:2] == ["customer", "complement"]
    complement_prompt = json.dumps(llm.inputs[1], ensure_ascii=False)
    assert "Complemento del turno" in complement_prompt and "envío" in complement_prompt
    assert len(classifier.verified) == 1  # el complemento no se vuelve a verificar
    # La traza del turno del cliente dice que agendó el complemento: el
    # laboratorio espera ese segundo turno sin adivinar (PR 15).
    verify = next(s for s in _customer_trace(tracker)["steps"] if s["kind"] == "verify")
    assert verify["complement_scheduled"] is True and verify["cost_usd"] == 0.0002


@pytest.mark.asyncio
async def test_a_failing_classifier_leaves_the_turn_as_today(tmp_path: Path) -> None:
    classifier = Classifier(perceive_ok=False)
    llm = LLM([_tool("send_reply", text=CATALOG_REPLY)])
    tracker = await _run(tmp_path, meta={"perception_mode": "on", "perception_profile": "jev-v1"}, llm=llm, classifier=classifier)

    assert not any("[PLAN DEL TURNO]" in " ".join(c.plugin_context or []) for c in tracker.build_prompt_calls)
    assert classifier.verified == []
    assert tracker.send_whatsapp_calls == [("wa_layers", CATALOG_REPLY)]
    perception = next(s for s in _customer_trace(tracker)["steps"] if s["kind"] == "perception")
    assert perception["fallback"] == "timeout"
