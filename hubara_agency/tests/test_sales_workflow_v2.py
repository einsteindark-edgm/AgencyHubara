"""Workflow de ventas V2 (motor de decisiones, F4 — diseño v2 §08).

`HubaraSalesSessionWorkflowV2` es un tipo NUEVO: nace sin historia, así que no
necesita `workflow.patched` (toma cada rama del V1 en su camino más nuevo y
deja las ramas que solo existían para re-jugar historias viejas). No tiene
reglas de texto: aplica los veredictos que el motor dejó grabados (la activity
`decide_egress`). El V1 queda congelado.

Diferencias con el V1, todas a propósito (y solo estas):
  1. el panel del dashboard muestra solo lo que de verdad salió;
  2. el texto se suprime por el selector de variantes solo si el selector
     SALIÓ (un selector rechazado dejaba al cliente sin respuesta);
  3. sin la ronda extra de la capa ② por palabras (quedan la nota ① y la
     verificación ③ por la fachada);
  4. el egreso lo decide el motor (con `reglas`, idéntico al V1), antes de
     grabar el turno: el LLM recuerda lo que de verdad salió.

La paridad con el V1 (B0 = A1) la prueban las suites del V1 corridas contra
el V2 (`tests/sales_workflow_versions.py`) y la comparación de abajo.
"""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import LLMResponseData, ToolCallData
from src.platform.contracts import PaymentPendingClosureResult
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
from tests.sales_workflow_versions import PARITY_SUITES
from tests.test_sales_workflow_debounce import (
    FIRST_CONTACT_GREETING,
    SALES_QUEUE,
    Tracker,
    _final_resp,
    _make_fake_activities,
    _tool_resp,
)

V2_FILE = Path(__file__).resolve().parents[1] / "src/plugins/chats/agent/sales/workflows/sales_session_v2.py"

_RETURNING = [
    {"role": "user", "content": "Hola"},
    {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
]


def _calls(*calls: tuple[str, dict]) -> LLMResponseData:
    return LLMResponseData(
        content="",
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[ToolCallData(id=f"c{i}", name=name, arguments=args) for i, (name, args) in enumerate(calls, 1)],
    )


def _reply(text: str) -> str:
    return json.dumps({"reply": {"text": text}}, ensure_ascii=False)


async def _run(
    workflow_cls: type,
    tmp_path: Path,
    *,
    responses: list[LLMResponseData],
    tool_results: dict[str, str] | None = None,
    prior_history: list[dict] | None = _RETURNING,
    customer_text: str = "Hola, busco una vela",
    meta: dict | None = None,
    replace: list[Any] | None = None,
    extra: list[Any] | None = None,
    **fakes: Any,
) -> Tracker:
    """Un mensaje del cliente y después silencio (el idle cierra la sesión)."""
    tracker = Tracker()
    workspace = tmp_path / workflow_cls.__name__
    workspace.mkdir()
    replaced = {a.__temporal_activity_definition.name for a in (replace or [])}
    acts = [
        a
        for a in _make_fake_activities(
            tracker,
            workspace_path=str(workspace),
            llm_responses=responses,
            tool_results=tool_results or {},
            prior_history=prior_history,
            **fakes,
        )
        if a.__temporal_activity_definition.name not in replaced
    ]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[workflow_cls],
            activities=[*acts, *(replace or []), *(extra or [])],
        ):
            handle = await env.client.start_workflow(
                workflow_cls.run,
                SalesSessionInput(session_id="wa_v2", runtime_workspace_path=str(workspace)),
                id="session-wa_v2",
                task_queue=SALES_QUEUE,
            )
            args: list[Any] = [customer_text, None, None]
            if meta is not None:
                args.append(meta)
            await handle.signal(workflow_cls.send_message, args=args)
            await handle.result()
    return tracker


def _sent(tracker: Tracker) -> list[str]:
    return [m for (_s, m) in tracker.send_whatsapp_calls]


def _persisted(tracker: Tracker) -> list[str]:
    return [m for (_s, m) in tracker.persist_calls]


def _customer_trace(tracker: Tracker) -> dict:
    return next(t for t in tracker.turn_traces if t["trigger"] == "customer")


# ── Contrato del tipo nuevo ──────────────────────────────────────────────────


def test_v2_is_a_new_type_with_the_v1_signals_queries_and_input() -> None:
    v1 = workflow._Definition.must_from_class(HubaraSalesSessionWorkflow)
    v2 = workflow._Definition.must_from_class(HubaraSalesSessionWorkflowV2)

    assert v2.name == "HubaraSalesSessionWorkflowV2"
    assert set(v2.signals) == set(v1.signals) == {"send_message"}
    assert set(v2.queries) == set(v1.queries) == {"get_last_response", "is_processing"}
    assert v2.arg_types == v1.arg_types == [SalesSessionInput]


def test_v2_signal_keeps_the_optional_fourth_argument_typed_any() -> None:
    """Un valor que no decodifique como el tipo anotado hace que Temporal
    DESCARTE la señal entera (y con ella el mensaje del cliente)."""
    v2 = workflow._Definition.must_from_class(HubaraSalesSessionWorkflowV2)

    assert v2.signals["send_message"].arg_types == workflow._Definition.must_from_class(
        HubaraSalesSessionWorkflow
    ).signals["send_message"].arg_types


def _v2_tree() -> ast.Module:
    return ast.parse(V2_FILE.read_text(encoding="utf-8"), filename=str(V2_FILE))


def test_v2_code_needs_no_workflow_patched() -> None:
    """Tipo nuevo, sin historia: ni `patched` ni `deprecate_patch` propios (los
    del turno compartido `run_agent_turn` siguen adentro del helper)."""
    calls = {
        node.func.attr
        for node in ast.walk(_v2_tree())
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert not calls & {"patched", "deprecate_patch"}


# Detectores de texto: la regla de hoy vive SOLO en el motor (su respaldo
# `reglas`). `is_no_message_abstention` sí se permite: es el centinela del
# protocolo NO_MESSAGE, no una lectura del texto.
_TEXT_DETECTORS = frozenset({
    "looks_like_admin_leak",
    "salvage_customer_text",
    "strip_portavelas_notice",
    "sanitize_llm_text",
    "keep_customer_safe_sentences",
    "breaks_human_persona",
    "should_send_first_contact_greeting",
    "text_greets",
    "uncovered_topics",
    "turn_policy_of",
    "TurnPolicy",
})
_TEXT_DETECTOR_MODULES = frozenset({
    "src.platform.llm_text_sanitizer",
    "src.sdk.textkit",
    "src.plugins.chats.agent.sales.first_contact_greeting",
    "src.plugins.chats.agent.sales.decisions.plan",
    "src.plugins.chats.agent.sales.decisions.egress",
})


def test_v2_imports_no_text_detector() -> None:
    imported: set[str] = set()
    modules: set[str] = set()
    used: set[str] = set()
    for node in ast.walk(_v2_tree()):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)

    assert not imported & _TEXT_DETECTORS, imported & _TEXT_DETECTORS
    assert not modules & _TEXT_DETECTOR_MODULES, modules & _TEXT_DETECTOR_MODULES
    assert not used & _TEXT_DETECTORS, used & _TEXT_DETECTORS
    assert "is_no_message_abstention" in imported  # el centinela sí


@pytest.mark.parametrize("suite", PARITY_SUITES)
def test_every_v1_suite_also_runs_against_v2_and_each_exclusion_names_a_real_test(suite: str) -> None:
    """B0 = A1: las suites del V1 corren contra el V2. Una exclusión solo puede
    nombrar un test que existe (con su motivo), nunca una suite entera."""
    module = importlib.import_module(suite)
    tests = {name for name in dir(module) if name.startswith("test_")}

    assert hasattr(module, "_sales_workflow_version"), f"{suite} no corre contra el V2"
    excluded = dict(module.V2_EXCLUDED)
    assert set(excluded) <= tests, set(excluded) - tests
    assert all(isinstance(reason, str) and reason.strip() for reason in excluded.values())
    assert len(excluded) < len(tests)


def test_the_guard_catches_a_detector_import() -> None:
    """Control negativo del guard de arriba: una importación de un detector,
    aunque venga por el módulo del V1, se ve."""
    tree = ast.parse("from src.plugins.chats.agent.sales.workflows.sales_session import looks_like_admin_leak\n")
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}

    assert names & _TEXT_DETECTORS


async def test_a_v2_session_replays_its_own_history(tmp_path: Path) -> None:
    """R-DET: una sesión del V2 (ráfaga, egreso, capas ①③, cierre por
    inactividad) re-juega su propia historia sin divergir."""
    from temporalio.worker import Replayer

    from tests.test_sales_perception_layers import CATALOG_REPLY, LLM, Classifier, _tool

    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    classifier = Classifier(decision="complement", missing=["envio"])
    llm = LLM([_tool("send_reply", text=CATALOG_REPLY), _tool("send_reply", text="El envío a Bogotá cuesta $X")])
    acts = [
        a
        for a in _make_fake_activities(
            tracker, workspace_path=str(workspace), prior_history=_RETURNING,
            tool_results={"send_reply": _reply(CATALOG_REPLY)},
        )
        if a.__temporal_activity_definition.name != "llm_chat"
    ]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2],
            activities=[*acts, llm.activity(), *classifier.activities()],
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id="wa_v2", runtime_workspace_path=str(workspace)),
                id="session-wa_v2-replay",
                task_queue=SALES_QUEUE,
            )
            meta = {"perception_mode": "on", "perception_profile": "jev-v1"}
            await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=["¿me mandas el catálogo?", None, None, {**meta, "ts_ms": 1}])
            await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=["y el envío?", None, None, {**meta, "ts_ms": 2}])
            await handle.result()
            history = await handle.fetch_history()

    assert [t["trigger"] for t in tracker.turn_traces][:2] == ["customer", "complement"]
    await Replayer(workflows=[HubaraSalesSessionWorkflowV2]).replay_workflow(history)

    # Control negativo: la historia sí ejercita el egreso (sin su activity, el
    # re-juego choca con lo grabado).
    from temporalio.worker import UnsandboxedWorkflowRunner

    async def no_activity(self, session_id: str, final_text: str, context: dict) -> dict:
        return {"text": final_text, "llm_text": final_text, "final_text": final_text}

    original = HubaraSalesSessionWorkflowV2._decide_egress
    HubaraSalesSessionWorkflowV2._decide_egress = no_activity  # type: ignore[method-assign]
    try:
        with pytest.raises(workflow.NondeterminismError):
            await Replayer(
                workflows=[HubaraSalesSessionWorkflowV2], workflow_runner=UnsandboxedWorkflowRunner()
            ).replay_workflow(history)
    finally:
        HubaraSalesSessionWorkflowV2._decide_egress = original  # type: ignore[method-assign]


# ── 4 · El egreso lo decide el motor, antes de grabar el turno ───────────────


def _scripted_egress(verdict: EgressOutput, seen: list[EgressInput]):
    @activity.defn(name="decide_egress")
    async def decide_egress(inp: EgressInput) -> EgressOutput:
        seen.append(inp)
        return verdict

    return decide_egress


async def test_v2_sends_what_the_engine_decided_not_what_the_llm_wrote(tmp_path: Path) -> None:
    seen: list[EgressInput] = []
    verdict = EgressOutput(text="Texto que decidió el motor", llm_text="Texto del LLM", final_text="Texto que decidió el motor")

    tracker = await _run(
        HubaraSalesSessionWorkflowV2,
        tmp_path,
        responses=[_final_resp("Texto del LLM")],
        replace=[_scripted_egress(verdict, seen)],
    )

    assert _sent(tracker) == ["Texto que decidió el motor"]
    customer_input = seen[0]
    assert (customer_input.session_id, customer_input.final_text, customer_input.admin_turn) == ("wa_v2", "Texto del LLM", False)
    assert customer_input.first_contact is False
    trace = _customer_trace(tracker)
    assert trace["llm_text"] == "Texto que decidió el motor"
    assert trace["egress"]["verdicts"] == []


async def test_v2_blocks_when_the_engine_blocks(tmp_path: Path) -> None:
    verdict = EgressOutput(
        text="", blocked=True, llm_text="Etiqueta registrada.", final_text="Etiqueta registrada.",
        guards=[{"name": "admin_text_guard", "before": "Etiqueta registrada.", "after": ""}],
    )

    tracker = await _run(
        HubaraSalesSessionWorkflowV2, tmp_path, responses=[_final_resp("Etiqueta registrada.")],
        replace=[_scripted_egress(verdict, [])],
    )

    assert _sent(tracker) == [] and _persisted(tracker) == []
    trace = _customer_trace(tracker)
    assert trace["suppressed_reason"] == "admin_text_guard"
    assert "admin_text_guard" in trace["guards"]


async def test_v2_sends_the_greeting_when_the_engine_says_so(tmp_path: Path) -> None:
    verdict = EgressOutput(text="", greeting_needed=True)

    tracker = await _run(
        HubaraSalesSessionWorkflowV2,
        tmp_path,
        responses=[_tool_resp("present_products")],
        tool_results={"present_products": json.dumps({"queued": True})},
        prior_history=None,
        replace=[_scripted_egress(verdict, [])],
    )

    assert _sent(tracker) == [FIRST_CONTACT_GREETING]
    assert tracker.timeline.index(f"send:{FIRST_CONTACT_GREETING}") < tracker.timeline.index("flush")


_PORTAVELAS_FAREWELL = (
    "Listo, tu pedido quedó registrado 🤍 Al finalizar el pago del pedido se escogen los colores del "
    "portavelas, según disponibilidad. Gracias por elegir a Hubara."
)


def _register_payload() -> str:
    return json.dumps(
        {
            "registered": True,
            "order_id": "order_456",
            "order_registered": {
                "session_id": "wa_v2", "order_id": "order_456", "payment_method": "transfer",
                "total_cop": 17000, "currency": "COP", "motivo": "pedido", "portavelas_included": False,
            },
        },
        ensure_ascii=False,
    )


_REGISTER = [_calls(("register_order", {"confirmado": True}))]
_B0_CORPUS: dict[str, dict[str, Any]] = {
    "texto normal": {"responses": [_final_resp("¿Qué aroma te gustaría? 🤍")]},
    "razonamiento + respuesta": {
        "responses": [_final_resp("El cliente dice que le gusta. Le respondo.\n\n¡Qué bueno! Cuesta $45.000 🤍")]
    },
    "reporte interno": {"responses": [_final_resp("La conversación quedó etiquetada como `INTERESADO`.")]},
    "acuse de relevo": {"responses": [_final_resp("Listo, la conversación quedó en manos del equipo humano.")]},
    "código de cupón": {"responses": [_final_resp("Usa el código VELAS_10 al pagar 🤍")]},
    "centinela": {"responses": [_final_resp("NO_MESSAGE")]},
    "despedida con portavelas": {
        "responses": [*_REGISTER, _final_resp(_PORTAVELAS_FAREWELL)],
        "tool_results": {"register_order": "__register__"},
        "payment_closure_result": PaymentPendingClosureResult(acted=False, escalated=True),
    },
    "solo portavelas": {
        "responses": [*_REGISTER, _final_resp("Los colores del portavelas se escogen al pagar.")],
        "tool_results": {"register_order": "__register__"},
        "payment_closure_result": PaymentPendingClosureResult(acted=False, escalated=True),
    },
    "primer contacto por el catálogo": {
        "responses": [_tool_resp("present_products")],
        "tool_results": {"present_products": json.dumps({"queued": True})},
        "prior_history": None,
    },
    "primer contacto que ya saluda": {
        "responses": [_calls(("send_reply", {"text": "¡Hola! Bienvenido a *Hubara* 🤍"}), ("present_products", {}))],
        "tool_results": {
            "send_reply": _reply("¡Hola! Bienvenido a *Hubara* 🤍"),
            "present_products": json.dumps({"queued": True}),
        },
        "prior_history": None,
    },
}


def _comparable(tracker: Tracker) -> dict[str, Any]:
    trace = _customer_trace(tracker)
    return {
        "sent": _sent(tracker),
        "persisted": _persisted(tracker),
        "guards": trace["guards"],
        "suppressed": trace["suppressed_reason"],
        "llm_text": trace["llm_text"],
        "sent_texts": trace["sent_texts"],
        "steps": [(s["kind"], s.get("name"), s.get("reason")) for s in trace["steps"]],
    }


@pytest.mark.parametrize("case", list(_B0_CORPUS))
async def test_b0_equals_a1_on_what_the_customer_gets_and_the_trace(case: str, tmp_path: Path) -> None:
    """Con `reglas` (el bot B0) el V2 hace lo mismo que el V1 (A1): lo que el
    cliente recibe, lo que muestra el panel y la traza que califica el juez."""
    spec = dict(_B0_CORPUS[case])
    if spec.get("tool_results", {}).get("register_order") == "__register__":
        spec["tool_results"] = {**spec["tool_results"], "register_order": _register_payload()}
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    a1 = await _run(HubaraSalesSessionWorkflow, tmp_path / "a", **spec)
    b0 = await _run(HubaraSalesSessionWorkflowV2, tmp_path / "b", **spec)

    assert _comparable(b0) == _comparable(a1)


async def test_the_llm_remembers_the_farewell_as_it_went_out(tmp_path: Path) -> None:
    """V1 grababa la despedida con la oración del portavelas que después no
    salía; el V2 decide el egreso ANTES de grabar."""
    responses = [_calls(("register_order", {"confirmado": True})), _final_resp(_PORTAVELAS_FAREWELL)]
    kwargs = dict(
        responses=responses,
        tool_results={"register_order": _register_payload()},
        payment_closure_result=PaymentPendingClosureResult(acted=False, escalated=True),
        customer_text="Sí, confirmo",
    )

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    v1 = await _run(HubaraSalesSessionWorkflow, tmp_path / "a", **kwargs)
    v2 = await _run(HubaraSalesSessionWorkflowV2, tmp_path / "b", **kwargs)

    expected = "Listo, tu pedido quedó registrado 🤍 Gracias por elegir a Hubara."
    assert _sent(v1) == _sent(v2) == [expected]

    def remembered(tracker: Tracker) -> list[str]:
        return [
            m["content"] for m in tracker.record_turn_new_messages[0]
            if m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls")
        ]

    assert remembered(v1) == [_PORTAVELAS_FAREWELL]  # V1: lo que no salió
    assert remembered(v2) == [expected]


# ── 1 · El dashboard muestra solo lo que salió ───────────────────────────────


async def test_the_dashboard_shows_only_what_went_out_when_the_picker_is_the_message(tmp_path: Path) -> None:
    text = "Estos son los aromas de la Cubo Love 🤍"
    kwargs = dict(
        responses=[_calls(("send_reply", {"text": text}), ("present_variant_picker", {"handle": "cubo-love"}))],
        tool_results={"send_reply": _reply(text), "present_variant_picker": json.dumps({"queued": True})},
    )

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    v1 = await _run(HubaraSalesSessionWorkflow, tmp_path / "a", **kwargs)
    v2 = await _run(HubaraSalesSessionWorkflowV2, tmp_path / "b", **kwargs)

    assert _sent(v1) == _sent(v2) == []  # el selector ES el mensaje
    assert _persisted(v1) == [text]  # V1: el panel mostraba un texto que no salió
    assert _persisted(v2) == []


async def test_the_dashboard_does_not_show_an_enumeration_the_picker_replaced(tmp_path: Path) -> None:
    enumeration = "Tenemos lavanda, café, sándalo y coco. ¿Cuál te gusta?"
    kwargs = dict(responses=[_final_resp(enumeration)], variant_guard_result=True)

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    v1 = await _run(HubaraSalesSessionWorkflow, tmp_path / "a", **kwargs)
    v2 = await _run(HubaraSalesSessionWorkflowV2, tmp_path / "b", **kwargs)

    assert _sent(v1) == _sent(v2) == []
    assert _persisted(v1) == [enumeration]
    assert _persisted(v2) == []


# ── 2 · Un selector rechazado no deja al cliente sin respuesta ──────────────


async def test_a_rejected_picker_does_not_silence_the_answer(tmp_path: Path) -> None:
    answer = "Tenemos lavanda y café 🤍 ¿Cuál te gusta más?"
    rejected = json.dumps({"queued": False, "error": "invalid_options", "message": "No se envió nada."})
    kwargs = dict(
        responses=[_tool_resp("present_variant_picker"), _final_resp(answer)],
        tool_results={"present_variant_picker": rejected},
    )

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    v1 = await _run(HubaraSalesSessionWorkflow, tmp_path / "a", **kwargs)
    v2 = await _run(HubaraSalesSessionWorkflowV2, tmp_path / "b", **kwargs)

    assert _sent(v1) == []  # V1: suprimía por el NOMBRE de la tool
    assert _sent(v2) == [answer]
    assert _persisted(v2) == [answer]


# ── 3 · Sin la ronda extra de la capa ② (quedan ① y ③) ──────────────────────


async def test_v2_keeps_the_note_and_the_verification_but_never_the_extra_round(tmp_path: Path) -> None:
    from tests.test_sales_perception_layers import CATALOG_REPLY, LLM, Classifier, _tool

    async def turn(workflow_cls: type, where: Path) -> tuple[Tracker, Classifier]:
        where.mkdir()
        classifier = Classifier()
        llm = LLM([_tool("send_shipping_rates"), _tool("send_reply", text=CATALOG_REPLY)])
        tracker = await _run(
            workflow_cls,
            where,
            responses=[],
            tool_results={"send_shipping_rates": json.dumps({"queued": True}), "send_reply": _reply(CATALOG_REPLY)},
            customer_text="vi que hacen velas con otros diseños, ¿me mandas el catálogo? y el envío a Bogotá cuánto sale?",
            meta={"perception_mode": "on", "perception_profile": "jev-v1", "ts_ms": 1_000},
            replace=[llm.activity()],
            extra=classifier.activities(),
        )
        return tracker, classifier

    v1, _ = await turn(HubaraSalesSessionWorkflow, tmp_path / "a")
    v2, classifier = await turn(HubaraSalesSessionWorkflowV2, tmp_path / "b")

    def llm_rounds(tracker: Tracker) -> int:
        return [s["kind"] for s in _customer_trace(tracker)["steps"]].count("llm")

    assert (llm_rounds(v1), llm_rounds(v2)) == (2, 1), "V1 pedía otra ronda por palabras; V2 corta en la tool"
    assert any(s.get("name") == "turn_policy_extra_round" for s in _customer_trace(v1)["steps"])
    assert not any(s.get("name") == "turn_policy_extra_round" for s in _customer_trace(v2)["steps"])
    # La nota ① y la verificación ③ siguen en el V2.
    assert "[PLAN DEL TURNO]" in " ".join(v2.build_prompt_calls[0].plugin_context or [])
    assert classifier.verified and _customer_trace(v2)["mode"] == "on"
