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
     grabar el turno: el LLM recuerda lo que de verdad salió (también por
     `send_reply`);
  5. el envío respeta lo que decidió el motor: no lo vuelve a juzgar con el
     detector de hoy (V1, remarketing y ETA sí).

La paridad con el V1 (B0 = A1) la prueban las suites del V1 corridas contra
el V2 (`tests/sales_workflow_versions.py`) y la comparación de abajo.
"""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import LLMResponseData, ToolCallData
from src.platform.contracts import PaymentPendingClosureResult
from src.platform.whatsapp.activities import send_whatsapp_message_activity
from src.platform.whatsapp.dtos import OutboundResult
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput
from src.sdk.connectorkit import FakePerceptionAdapter, TypedAnswer
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
    # `photo_reading` (2026-09-30): el ingest avisa que está leyendo una foto.
    assert set(v2.signals) == set(v1.signals) == {"send_message", "photo_reading"}
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
    # Lo que el LLM escribió (egreso, turno, saludo, capa ②).
    "looks_like_admin_leak",
    "salvage_customer_text",
    "strip_portavelas_notice",
    "sanitize_llm_text",
    "keep_customer_safe_sentences",
    "customer_sentences",
    "breaks_human_persona",
    "should_send_first_contact_greeting",
    "text_greets",
    "uncovered_topics",
    "turn_policy_of",
    "TurnPolicy",
    "invented_product_claim",
    "find_unexplained_amounts",
    # Lo que escribió el cliente. Varios llegan por fachadas que V2 ya
    # importa (`src.sdk.messagingkit` reexporta la baja y la retoma).
    "is_closing_ack",
    "detect_deferral",
    "detect_purchase_affirmation",
    "is_opt_out_text",
    "parse_reengagement_deferral",
    "coupon_in_play",
    "unavailable_terms",
})
_TEXT_DETECTOR_MODULES = frozenset({
    "src.platform.llm_text_sanitizer",
    "src.sdk.textkit",
    "src.plugins.chats.agent.sales.first_contact_greeting",
    "src.plugins.chats.agent.sales.decisions.plan",
    "src.plugins.chats.agent.sales.decisions.egress",
    "src.plugins.chats.agent.sales.price_quotes",
    "src.plugins.chats.agent.sales.use_cases.closing_ack",
    "src.plugins.chats.agent.sales.use_cases.coupons",
    "src.plugins.chats.shared.product_truth",
    "src.plugins.chats.shared.purchase_signals",
    "src.platform.whatsapp.marketing_opt_out",
    "src.platform.whatsapp.reengagement_deferral",
})
_V2_PACKAGE = "src.plugins.chats.agent.sales.workflows"
_V1_MODULE = f"{_V2_PACKAGE}.sales_session"
_FACADE = V2_FILE.parents[1] / "decisions" / "facade.py"
_FACADE_MODULE = "src.plugins.chats.agent.sales.decisions.facade"


def _absolute(node: ast.ImportFrom, package: str) -> str:
    """El módulo absoluto de un `from … import …` (resuelve los relativos)."""
    if not node.level:
        return node.module or ""
    parts = package.split(".")
    base = parts[: len(parts) - node.level + 1]
    return ".".join([*base, node.module] if node.module else base)


def _import_sources(tree: ast.Module, package: str) -> dict[str, tuple[str, str]]:
    """Cada nombre que ata un import (también adentro del `with` de Temporal o
    de una función) → (módulo absoluto, nombre original; "" si es un módulo)."""
    sources: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _absolute(node, package)
            sources.update({alias.asname or alias.name: (module, alias.name) for alias in node.names})
        elif isinstance(node, ast.Import):
            sources.update({alias.asname or alias.name: (alias.name, "") for alias in node.names})
    return sources


def _is_detector(source: tuple[str, str], *, by_module: bool = True) -> bool:
    module, name = source
    modules = {module, f"{module}.{name}"} if by_module else set()
    return name in _TEXT_DETECTORS or bool(modules & _TEXT_DETECTOR_MODULES)


def _definitions(tree: ast.Module) -> dict[str, ast.AST]:
    defs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = node
        elif isinstance(node, ast.Assign):
            defs.update({t.id: node for t in node.targets if isinstance(t, ast.Name)})
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defs[node.target.id] = node
    return defs


def _detectors_reached(
    source: str,
    package: str,
    entries: set[str],
    *,
    by_module: bool = True,
    allowed: frozenset[str] = frozenset(),
) -> set[str]:
    """Los detectores de texto que alcanzan `entries` (definiciones de nivel de
    módulo de `source`), siguiendo lo que cada una usa del mismo módulo."""
    tree = ast.parse(source)
    defs, imports = _definitions(tree), _import_sources(tree, package)
    detectors = _TEXT_DETECTORS - allowed
    reached: set[str] = set()
    seen: set[str] = set()
    pending = list(entries)
    while pending:
        name = pending.pop()
        if name in seen or name in allowed:
            continue
        seen.add(name)
        if name in detectors or (name in imports and _is_detector(imports[name], by_module=by_module)):
            reached.add(name)
        if name not in defs:
            continue
        for node in ast.walk(defs[name]):
            if isinstance(node, ast.Name):
                pending.append(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in detectors:
                reached.add(node.attr)
    return reached


def _names_v2_imports_from(module: str) -> set[str]:
    return {name for name, (source, _) in _import_sources(_v2_tree(), _V2_PACKAGE).items() if source == module}


def test_v2_imports_no_text_detector() -> None:
    tree = _v2_tree()
    sources = _import_sources(tree, _V2_PACKAGE)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }

    detectors = sorted(name for name, source in sources.items() if _is_detector(source))
    assert not detectors, detectors
    assert not used & _TEXT_DETECTORS, used & _TEXT_DETECTORS
    assert sources["is_no_message_abstention"][1] == "is_no_message_abstention"  # el centinela sí


def test_the_v1_helpers_v2_reuses_read_no_text() -> None:
    """V2 reutiliza funciones puras del V1 (traza, capas ①③, debounce, CAPI):
    ni ellas ni lo que llaman del mismo módulo lee el texto. Importar la
    clase del V1 (con todas sus reglas) tampoco pasaría."""
    reused = _names_v2_imports_from(_V1_MODULE)
    v1 = V2_FILE.with_name("sales_session.py").read_text(encoding="utf-8")

    assert {"_reply_as_sent", "_text_outbound", "_note_guard"} <= reused
    assert not _detectors_reached(v1, _V2_PACKAGE, reused)
    assert _detectors_reached(v1, _V2_PACKAGE, {"HubaraSalesSessionWorkflow"})  # control


def test_the_facade_functions_v2_applies_read_no_text() -> None:
    """Las funciones de la fachada que V2 usa solo aplican lo que el motor
    grabó. Por nombre: la fachada arma el plan con los tipos de `plan`, cuyo
    detector (`uncovered_topics`) es de la capa ② que V2 no usa; y la segunda
    puerta (F6) sí es un `TurnPolicy`, pero decide por nombres de tools."""
    applied = _names_v2_imports_from(_FACADE_MODULE)
    facade = _FACADE.read_text(encoding="utf-8")
    package = _FACADE_MODULE.rsplit(".", 1)[0]
    container = frozenset({"TurnPolicy"})

    assert {"contract_policy_of", "delivered_components", "complement_note_of", "plan_of"} <= applied
    assert not _detectors_reached(facade, package, applied, by_module=False, allowed=container)
    assert "uncovered_topics" in _detectors_reached(
        facade, package, {"turn_policy_of"}, by_module=False, allowed=container
    )  # control: la capa ② sí lee texto


def test_every_v2_turn_goes_through_the_engine_egress() -> None:
    """El egreso (activity `decide_egress`) corre adentro de CADA turno, antes
    de grabarlo: sin él, el turno de V2 sería el de hoy sin reglas."""
    tree = _v2_tree()
    turns = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _called(n) == "run_agent_turn"]

    assert turns, "V2 corre turnos"
    assert _uses(tree, "run_agent_turn") == len(turns), "el turno solo aparece llamado"
    assert not _turns_without_the_egress(tree), _turns_without_the_egress(tree)


def test_every_v2_send_says_the_engine_decided() -> None:
    """V2 no lee el texto: lo decidió el motor (el egreso o el saludo). Cada
    envío lo dice con el 3.er argumento (`True`), y el envío no se usa de
    otra forma (nada de alias que se salten este guard)."""
    tree = _v2_tree()
    sends = _send_calls(tree)

    assert sends, "V2 envía texto"
    assert _uses(tree, "send_whatsapp_message_activity") == len(sends), "el envío solo aparece agendado"
    assert not _sends_without_the_flag(tree), _sends_without_the_flag(tree)


_WORKFLOWS_WITHOUT_ENGINE = {
    "v1": V2_FILE.with_name("sales_session.py"),
    "remarketing": V2_FILE.parents[2] / "remarketing" / "workflows" / "remarketing.py",
    "eta": V2_FILE.parents[4] / "eta" / "agent" / "eta" / "workflows" / "eta_session.py",
}


@pytest.mark.parametrize("name", list(_WORKFLOWS_WITHOUT_ENGINE))
def test_only_v2_says_the_engine_decided_the_send(name: str) -> None:
    """V1, remarketing y ETA no pasan por el motor: envían con dos argumentos
    y el detector del envío sigue siendo su última línea (byte a byte)."""
    path = _WORKFLOWS_WITHOUT_ENGINE[name]
    sends = _send_calls(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))

    assert sends, f"{name} envía texto"
    assert [len(_send_args(call)) for call in sends] == [2] * len(sends), [ast.unparse(c) for c in sends]


def _called(call: ast.Call) -> str:
    return call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", "")


def _uses(tree: ast.Module, name: str) -> int:
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == name)


def _send_calls(tree: ast.Module) -> list[ast.Call]:
    """Cada `workflow.execute_activity(send_whatsapp_message_activity, …)`."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _called(node) in {"execute_activity", "start_activity"}
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "send_whatsapp_message_activity"
    ]


def _send_args(call: ast.Call) -> list[ast.expr]:
    args = next((k.value for k in call.keywords if k.arg == "args"), None)
    return list(args.elts) if isinstance(args, (ast.List, ast.Tuple)) else []


def _sends_without_the_flag(tree: ast.Module) -> list[str]:
    def says_so(call: ast.Call) -> bool:
        args = _send_args(call)
        return len(args) == 3 and isinstance(args[2], ast.Constant) and args[2].value is True

    return [ast.unparse(call) for call in _send_calls(tree) if not says_so(call)]


def _turns_without_the_egress(tree: ast.Module) -> list[str]:
    def with_egress(call: ast.Call) -> bool:
        egress = next((k.value for k in call.keywords if k.arg == "egress"), None)
        return egress is not None and not (isinstance(egress, ast.Constant) and egress.value is None)

    turns = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _called(n) == "run_agent_turn"]
    return [ast.unparse(call) for call in turns if not with_egress(call)]


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


@pytest.mark.parametrize(
    "line",
    [
        # Por una fachada que V2 ya importa (hoy pasaba).
        "from src.sdk.messagingkit import is_opt_out_text",
        # Con alias: cuenta el nombre original.
        "from src.sdk.agentkit import looks_like_admin_leak as leak",
        # El módulo del egreso, como submódulo o con un import relativo.
        "from src.plugins.chats.agent.sales.decisions import egress",
        "from ..decisions.egress import Destinatario",
        "from ..first_contact_greeting import GREETING",
    ],
)
def test_the_guard_catches_a_detector_by_any_import(line: str) -> None:
    sources = _import_sources(ast.parse(line), _V2_PACKAGE)

    assert any(_is_detector(source) for source in sources.values()), sources


def test_the_guard_catches_a_detector_behind_a_reused_helper() -> None:
    """Un helper del V1 que V2 reutiliza y que, dos llamadas más abajo, lee
    el texto con un detector importado con alias."""
    helper = (
        "from src.sdk.textkit import salvage_customer_text as _rescue\n"
        "def _reply_as_sent(sent, outgoing):\n    return _join(sent, outgoing)\n"
        "def _join(sent, outgoing):\n    return _rescue(outgoing)\n"
    )

    assert _detectors_reached(helper, _V2_PACKAGE, {"_reply_as_sent"}) == {"_rescue"}


def test_the_guard_catches_a_send_without_the_flag_and_a_turn_without_the_egress() -> None:
    tree = ast.parse(
        "async def turn():\n"
        "    await workflow.execute_activity(send_whatsapp_message_activity, args=[sid, text])\n"
        "    await workflow.execute_activity(send_whatsapp_message_activity, args=[sid, text, False])\n"
        "    await run_agent_turn(session, msg)\n"
        "    await run_agent_turn(session, msg, egress=None)\n"
    )

    assert len(_sends_without_the_flag(tree)) == 2
    assert len(_turns_without_the_egress(tree)) == 2


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


async def test_v2_names_the_missing_required_tool_before_closing_with_text(tmp_path: Path) -> None:
    """Segunda puerta (F6): el motor grabó que el envío pide
    `send_shipping_rates`; el LLM iba a cerrar con un texto sin usarla y sin
    haber mostrado nada → UNA ronda más con la nota. El cliente no recibe el
    borrador; recibe lo que salió de la tool."""
    import dataclasses

    from tests.test_sales_perception_layers import LLM, Classifier, _tool

    classifier = Classifier()
    base = classifier.decisions

    def with_contract(profile: str):
        return dataclasses.replace(
            base(profile),
            tools={"required": [{"topic": "envio", "any_of": ["send_shipping_rates"],
                                 "nudge": "Para el costo del envío usa send_shipping_rates."}]},
        )

    classifier.decisions = with_contract
    draft = "El envío a Bogotá te sale en $9.000 🤍"
    llm = LLM([_final(draft), _tool("send_shipping_rates")])
    tracker = await _run(
        HubaraSalesSessionWorkflowV2,
        tmp_path,
        responses=[],
        tool_results={"send_shipping_rates": json.dumps({"queued": True})},
        customer_text="¿y el envío a Bogotá cuánto sale?",
        meta={"perception_mode": "on", "perception_profile": "jev-v1", "ts_ms": 1_000},
        replace=[llm.activity()],
        extra=classifier.activities(),
    )

    steps = _customer_trace(tracker)["steps"]
    assert any(s.get("name") == "contract_extra_round" for s in steps)
    assert draft not in _sent(tracker)
    assert any(m.get("content", "").startswith("[CONTRATO DEL TURNO]") for m in llm.inputs[1] if m.get("role") == "system")


def _final(text: str) -> LLMResponseData:
    return LLMResponseData(content=text, finish_reason="stop", has_tool_calls=False, tool_calls=[])


async def test_v2_asks_the_engine_how_to_close_an_abandoned_conversation(tmp_path: Path) -> None:
    """Cierre por abandono (F8): el V2 le pasa la sesión al aviso de ghosting
    (el motor decide la etiqueta); el V1 lo llama sin sesión (el aviso de hoy)."""
    responses = [_final("¡Hola! ¿En qué te ayudo? 🤍")]
    v2 = await _run(HubaraSalesSessionWorkflowV2, tmp_path, responses=list(responses))
    v1 = await _run(HubaraSalesSessionWorkflow, tmp_path, responses=list(responses))

    assert v2.ghosting_sessions == ["wa_v2"]
    assert v1.ghosting_sessions == [""]


# ── 5 · El envío respeta lo que decidió el motor (capacidad `destinatario`) ──
#
# El diseño (§07) dice que `destinatario` reemplaza al detector de fugas en sus
# sitios, el envío incluido. En V2 el texto ya lo decidió el motor (el egreso,
# con Jev o con la regla de respaldo): el envío no lo vuelve a juzgar. V1,
# remarketing y ETA no pasan por el motor y el detector del envío sigue siendo
# su última línea.

COUPON = "Usa el código VELAS_10 al pagar 🤍"
_FOR_THE_CUSTOMER = TypedAnswer(
    id="egreso.destinatario", kind="choice", choice="mensaje_al_cliente",
    probs=(("mensaje_al_cliente", 0.96),), confidence=0.96,
)


@pytest.fixture
def jev_decides_the_recipient(_isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch) -> FakePerceptionAdapter:
    """`destinatario` en `jev` para la conversación (su interruptor, dentro
    del techo de Terraform); las demás capacidades, con la regla. Jev dice
    que el texto es un mensaje para el cliente."""
    from src.sdk import connectorkit

    jev = FakePerceptionAdapter({"egreso.destinatario": _FOR_THE_CUSTOMER})
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    monkeypatch.setenv("SALES_CAPABILITIES_CEILING", "on")
    bots.write_capability_modes(_isolate_vault_dir, {"destinatario": "on"})
    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: jev)
    return jev


@pytest.fixture
def whatsapp(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """El puerto de WhatsApp falso detrás de la activity REAL de envío: lo que
    de verdad le llega al cliente, burbuja por burbuja."""
    from src.platform.whatsapp import activities as wa_activities

    received: list[str] = []

    async def send_text(phone_number_id: str, to: str, text: str, reply_to_message_id: str | None = None) -> OutboundResult:
        received.append(text)
        return OutboundResult(wa_message_id=f"wamid.{len(received)}", ok=True)

    async def no_pause(_seconds: float) -> None:
        return None

    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_TEST")
    monkeypatch.setattr(wa_activities.whatsapp_client, "send_text", send_text)
    # Solo la pausa entre burbujas de ESTE módulo (no el asyncio del worker).
    monkeypatch.setattr(wa_activities, "asyncio", SimpleNamespace(sleep=no_pause))
    return received


def _said(tracker: Tracker, turn: int = 0) -> list[str]:
    """Lo que el LLM recuerda haberle dicho al cliente en el turno: sus textos
    finales y el `text` de cada llamada a `send_reply`."""
    said: list[str] = []
    for message in tracker.record_turn_new_messages[turn]:
        if message.get("role") == "assistant" and message.get("content") and not message.get("tool_calls"):
            said.append(message["content"])
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if function.get("name") == "send_reply":
                said.append(json.loads(function.get("arguments") or "{}").get("text"))
    return said


def _replied(tracker: Tracker, turn: int = 0) -> list[str]:
    """Lo que el historial dice que devolvió cada `send_reply` (`reply.text`:
    «mensaje listo para el cliente»)."""
    replied: list[str] = []
    for message in tracker.record_turn_new_messages[turn]:
        if message.get("role") == "tool" and message.get("name") == "send_reply":
            reply = json.loads(message.get("content") or "{}").get("reply")
            if isinstance(reply, dict):
                replied.append(reply.get("text"))
    return replied


async def test_v2_delivers_the_coupon_code_the_engine_approved_with_jev(
    tmp_path: Path, whatsapp: list[str], jev_decides_the_recipient: FakePerceptionAdapter
) -> None:
    """Caso verificado: con `destinatario=jev`, «Usa el código VELAS_10 al
    pagar» pasaba el egreso (Jev: es para el cliente) y moría en silencio en
    el envío, cuyo detector lo toma por un token interno, mientras el panel y
    el historial del LLM lo daban por enviado. Ahora el cliente lo recibe y
    los tres cuentan lo mismo."""
    tracker = await _run(
        HubaraSalesSessionWorkflowV2, tmp_path,
        responses=[_final_resp(COUPON)],
        replace=[send_whatsapp_message_activity],
    )

    assert whatsapp == [COUPON]
    assert _persisted(tracker) == [COUPON]
    assert _said(tracker) == [COUPON]
    assert jev_decides_the_recipient.calls, "lo decidió Jev, no la regla"


async def test_v2_delivers_a_send_reply_the_engine_approved_with_jev(
    tmp_path: Path, whatsapp: list[str], jev_decides_the_recipient: FakePerceptionAdapter
) -> None:
    """Lo mismo por el canal normal (`send_reply`): la tool dejó pasar el
    cupón y el egreso también (Jev); el envío lo respeta."""
    tracker = await _run(
        HubaraSalesSessionWorkflowV2, tmp_path,
        responses=[_calls(("send_reply", {"text": COUPON}))],
        tool_results={"send_reply": _reply(COUPON)},
        replace=[send_whatsapp_message_activity],
    )

    assert whatsapp == _persisted(tracker) == [COUPON]
    assert _said(tracker) == _replied(tracker) == [COUPON]
    assert jev_decides_the_recipient.calls


async def test_v1_is_unchanged_even_with_jev_it_still_filters_the_coupon_code(
    tmp_path: Path, whatsapp: list[str], jev_decides_the_recipient: FakePerceptionAdapter
) -> None:
    """V1 no pasa por el egreso del motor: con el mismo control encendido, su
    guarda y el detector del envío siguen decidiendo como hoy."""
    tracker = await _run(
        HubaraSalesSessionWorkflow, tmp_path,
        responses=[_final_resp(COUPON)],
        replace=[send_whatsapp_message_activity],
    )

    assert (whatsapp, _persisted(tracker)) == ([], [])
    assert not jev_decides_the_recipient.calls


async def test_v2_with_rules_the_engine_rejects_the_coupon_code_and_nothing_claims_it_went_out(
    tmp_path: Path, whatsapp: list[str]
) -> None:
    """Con la regla de hoy (`reglas`) el motor rechaza el texto en el egreso:
    V2 no depende del detector del envío para frenarlo. No llega a WhatsApp,
    el panel no lo muestra y el LLM no lo recuerda."""
    tracker = await _run(
        HubaraSalesSessionWorkflowV2, tmp_path,
        responses=[_final_resp(COUPON)],
        replace=[send_whatsapp_message_activity],
    )

    assert (whatsapp, _persisted(tracker), _said(tracker)) == ([], [], [])
    trace = _customer_trace(tracker)
    assert trace["suppressed_reason"] == "admin_text_guard"
    # El egreso decide varias capacidades (primero la muletilla del modelo):
    # el rechazo es el de `destinatario`, con la regla.
    decided = next(v for v in trace["egress"]["verdicts"] if v["capability"] == "destinatario")
    assert (decided["by"], decided["value"]) == ("reglas", True)


# `send_reply` es el canal normal del texto: el historial guarda su llamada y
# su resultado con el texto que «salió». Ese texto también lo decide el egreso
# (en la tool y después en el egreso: dos preguntas a Jev pueden no coincidir,
# y el portavelas solo lo ve el egreso). El LLM recuerda lo que de verdad salió.


async def test_a_send_reply_the_engine_blocks_is_not_remembered_as_said(tmp_path: Path) -> None:
    """La tool dejó pasar el texto y el egreso lo frenó: nada sale, el panel
    no lo muestra y el historial del LLM no dice que lo mandó."""
    verdict = EgressOutput(
        text="", blocked=True, llm_text=COUPON, final_text=COUPON,
        guards=[{"name": "admin_text_guard", "before": COUPON, "after": ""}],
    )

    tracker = await _run(
        HubaraSalesSessionWorkflowV2, tmp_path,
        responses=[_calls(("send_reply", {"text": COUPON}))],
        tool_results={"send_reply": _reply(COUPON)},
        replace=[_scripted_egress(verdict, [])],
    )

    assert (_sent(tracker), _persisted(tracker)) == ([], [])
    assert (_said(tracker), _replied(tracker)) == ([], [])
    assert tracker.execute_tool_calls == ["send_reply"]  # la tool sí corrió


async def test_the_llm_remembers_a_send_reply_farewell_as_it_went_out(tmp_path: Path) -> None:
    """La despedida por `send_reply` de un pedido sin portavelas: el egreso
    quita la oración del portavelas; el LLM recuerda la despedida que salió,
    no la que escribió."""
    tracker = await _run(
        HubaraSalesSessionWorkflowV2, tmp_path,
        responses=[
            _calls(("register_order", {"confirmado": True})),
            _calls(("send_reply", {"text": _PORTAVELAS_FAREWELL})),
        ],
        tool_results={"register_order": _register_payload(), "send_reply": _reply(_PORTAVELAS_FAREWELL)},
        payment_closure_result=PaymentPendingClosureResult(acted=False, escalated=True),
        customer_text="Sí, confirmo",
    )

    expected = "Listo, tu pedido quedó registrado 🤍 Gracias por elegir a Hubara."
    assert _sent(tracker) == _persisted(tracker) == [expected]
    assert _said(tracker) == _replied(tracker) == [expected]


async def test_two_send_replies_the_engine_changed_are_remembered_as_the_one_message_that_went_out(
    tmp_path: Path,
) -> None:
    """Dos `send_reply` en el mismo paso salen como UN mensaje; si el egreso
    lo cambió, el historial guarda ese mensaje una vez (en la primera)."""
    part = "¡Qué bueno! La Cubo Love cuesta $45.000 🤍"
    went_out = "La Cubo Love cuesta $45.000 🤍"
    verdict = EgressOutput(text=went_out, llm_text=went_out, final_text=went_out, salvaged=True)

    tracker = await _run(
        HubaraSalesSessionWorkflowV2, tmp_path,
        responses=[_calls(("send_reply", {"text": part}), ("send_reply", {"text": part}))],
        tool_results={"send_reply": _reply(part)},
        replace=[_scripted_egress(verdict, [])],
    )

    assert _sent(tracker) == [went_out]
    assert _said(tracker) == _replied(tracker) == [went_out]


async def test_a_send_reply_of_an_administrative_turn_is_not_remembered_as_said(tmp_path: Path) -> None:
    """En el cierre por abandono (turno administrativo) nada sale: si el LLM
    llamó `send_reply`, el historial no puede decir que el cliente lo leyó."""
    tracker = await _run(
        HubaraSalesSessionWorkflowV2, tmp_path,
        responses=[_final_resp("¡Hola! ¿En qué te ayudo? 🤍"), _calls(("send_reply", {"text": "¿Sigues ahí? 🤍"}))],
        tool_results={"send_reply": _reply("¿Sigues ahí? 🤍")},
    )

    assert _sent(tracker) == ["¡Hola! ¿En qué te ayudo? 🤍"]
    ghost_turn = 1
    assert (_said(tracker, ghost_turn), _replied(tracker, ghost_turn)) == ([], [])


async def test_a_send_reply_the_escalation_replaced_is_not_remembered_as_said(tmp_path: Path) -> None:
    """[send_reply, escalate_to_human] en el mismo paso: la escalación termina
    el turno y sale su despedida; el texto del `send_reply` nunca salió."""
    draft = "Déjame revisar y te cuento 🤍"
    farewell = "Te comunico con una asesora para ayudarte mejor 🤍"
    escalated = json.dumps(
        {
            "escalated": True,
            "escalation_decision": {"session_id": "wa_v2", "reason_category": "OTHER", "summary": "pide una asesora"},
            "customer_message": farewell,
        },
        ensure_ascii=False,
    )

    tracker = await _run(
        HubaraSalesSessionWorkflowV2, tmp_path,
        responses=[_calls(("send_reply", {"text": draft}), ("escalate_to_human", {"reason_category": "OTHER"}))],
        tool_results={"send_reply": _reply(draft), "escalate_to_human": escalated},
    )

    assert _sent(tracker) == _persisted(tracker) == [farewell]
    assert (_said(tracker), _replied(tracker)) == ([farewell], [])

