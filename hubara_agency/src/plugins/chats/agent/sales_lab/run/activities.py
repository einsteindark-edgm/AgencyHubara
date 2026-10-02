"""Activities de la corrida en la CAJA del laboratorio (Temporal local).

Leen `orders/` y `bench/` del S3 del laboratorio, trabajan en `LAB_ROOT`
(/lab) y escriben SOLO en `runs/<corrida>/`. El avance va a
`runs/<corrida>/progress.json`, que el lanzador de producción sigue.
"""
from __future__ import annotations

import asyncio
import json
import uuid
import shutil
import os
import time
from dataclasses import asdict
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.plugins.chats.agent.sales_lab.arms import arm_profile
from src.plugins.chats.agent.sales_lab.cases import build_cases
from src.plugins.chats.agent.sales_eval.scorecard.claude_judge import ClaudeCodeJudge, JudgeQueue
from src.plugins.chats.agent.sales_lab.launch.costs import AGENT_USD_PER_TURN, JUDGE_USD_PER_TURN
from src.plugins.chats.agent.sales_lab.run.contracts import (
    ArmPublishInput,
    ArmPublishResult,
    CaseOutcome,
    EvaluateInput,
    EvaluateResult,
    EvaluationPlan,
    ProgressUpdate,
    PublishResult,
    RunPlan,
    SimulateInput,
    SmokeResult,
    SummarizeInput,
    SummarizeResult,
)
from src.plugins.chats.agent.sales_lab.run.arena import jev_fallback_notes
from src.plugins.chats.agent.sales_lab.run.evaluate import evaluation_chunk, score_arm
from src.plugins.chats.agent.sales_lab.run.publish import publish_arm, publish_control
from src.plugins.chats.agent.sales_lab.run.summary import build_summary, with_verdicts
from src.plugins.chats.agent.sales_lab.sandbox.process import run_case_in_subprocess
from src.sdk.labkit import LabStorePort, bench_catalog_client, get_lab_store
from src.sdk.runtime import with_heartbeat

SALES_WORKSPACE = "app-hubara-agency-src-plugins-chats-agent-sales-workspace"
SMOKE_TIMEOUT_S = 600.0
CASE_TIMEOUT_S = 600.0
#: Turnos por pedazo de la evaluación (sesiones enteras): con juez, ~3–5 min;
#: la caja reporta avance al terminar cada uno (`LAB_EVAL_CHUNK_TURNS`).
EVAL_CHUNK_TURNS = 12


def _cases(run_id: str) -> list[dict]:
    path = _lab_root() / "runs" / run_id / "cases.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _results_dir(run_id: str, arm: str, rep: int) -> Path:
    return _lab_root() / "runs" / run_id / "results" / arm / str(rep)


def _lab_root() -> Path:
    return Path(os.getenv("LAB_ROOT") or "/lab")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _store() -> LabStorePort:
    store = get_lab_store()
    if store is None:
        raise ApplicationError("la caja no tiene LAB_BUCKET", non_retryable=True)
    return store


def _download_bench(store: LabStorePort, bench_id: str, dest: Path) -> None:
    """Baja el banco a una carpeta aparte y la renombra al final: un corte a
    mitad (S3, timeout) nunca queda como banco completo para los reintentos ni
    para las corridas siguientes de la caja."""
    prefix = f"bench/{bench_id}/"
    if (dest / "manifest.json").is_file():
        return  # ya bajado en una corrida anterior de esta caja
    keys = store.list_keys(prefix)
    if f"{prefix}manifest.json" not in keys:
        raise ApplicationError(f"el banco {bench_id} no tiene manifiesto: está incompleto", non_retryable=True)
    partial = dest.with_name(f"{dest.name}.partial-{uuid.uuid4().hex[:8]}")
    try:
        for key in keys:
            target = partial / key[len(prefix):]
            target.parent.mkdir(parents=True, exist_ok=True)
            data = store.get_bytes(key)
            if data is not None:
                target.write_bytes(data)
        if dest.exists() and not (dest / "manifest.json").is_file():
            shutil.rmtree(dest)  # restos de una bajada anterior a medias
        try:
            partial.rename(dest)
        except OSError:
            if not (dest / "manifest.json").is_file():
                raise
            # otro intento lo bajó completo primero: el nuestro sobra
    finally:
        shutil.rmtree(partial, ignore_errors=True)


async def _read_bench_photos(bench_dir: Path) -> None:
    """Las fotos de producto del banco, leídas con la visión de hoy (visión,
    índice de fotos del catálogo del banco y verificador) antes de simular:
    dentro del caso el sandbox no abre conexiones, solo lee esta lectura
    (`sandbox/photos.py`). Nunca frena la corrida: una foto sin lectura usa el
    texto de producción."""
    from src.plugins.chats.agent.sales.use_cases.photo_product import PhotoIdentifier
    from src.plugins.chats.agent.sales_lab.sandbox.photos import read_bench_photos
    from src.sdk.catalogkit import CatalogPhotoIndex
    from src.sdk.connectorkit import get_image_embedding_port, get_image_vision_port, get_photo_match_port

    if not any((bench_dir / "vault").glob("*/media/*")):
        return
    embedder = get_image_embedding_port()
    identifier = PhotoIdentifier(
        catalog=bench_catalog_client(bench_dir / "catalog"),
        index=CatalogPhotoIndex(bench_dir / "catalog" / "photo_index", model=embedder.model, dimensions=embedder.dimensions),
        embedder=embedder,
        matcher=get_photo_match_port(),
    )
    try:
        read = await read_bench_photos(bench_dir, vision=get_image_vision_port(), identifier=identifier)
    except Exception as exc:  # noqa: BLE001 — sin lecturas, los casos usan el texto de producción
        activity.logger.warning("lab_run_prepare: fotos del banco sin leer (%s)", type(exc).__name__)
        return
    activity.logger.info("lab_run_prepare: %d fotos del banco leídas con la visión de hoy", read)


@activity.defn(name="lab_run_prepare")
async def prepare_run_activity(run_id: str) -> RunPlan:
    store = _store()
    raw = await asyncio.to_thread(store.get_bytes, f"orders/{run_id}.json")
    if raw is None:
        raise ApplicationError(f"no hay orden para la corrida {run_id}", non_retryable=True)
    order = json.loads(raw)
    bench_id = str(order["bench_id"])
    await asyncio.to_thread(_download_bench, store, bench_id, _lab_root() / "bench" / bench_id)
    await _read_bench_photos(_lab_root() / "bench" / bench_id)
    return RunPlan(
        run_id=run_id,
        bench_id=bench_id,
        arms=[str(a) for a in order.get("arms") or []],
        reps=int(order.get("reps") or 1),
        spend_limit_usd=float(order.get("spend_limit_usd") or 0.0),
        image=str(order.get("image") or ""),
        judge_usd_per_turn=judge_usd_per_turn(),
    )


@activity.defn(name="lab_run_evaluation_plan")
async def evaluation_plan_activity(plan: RunPlan) -> EvaluationPlan:
    """Modo `evaluate`: lo que la corrida ya simuló (repeticiones con métricas
    publicadas), los casos (se restauran de S3 si la caja no los tiene) y el
    gasto que ya llevaba (el avance no lo pierde: cuenta para el tope del mes)."""
    store = _store()
    prefix = f"runs/{plan.run_id}"
    local = _lab_root() / "runs" / plan.run_id / "cases.jsonl"
    if not local.is_file():
        raw = await asyncio.to_thread(store.get_bytes, f"{prefix}/cases.jsonl")
        if raw is None:
            raise ApplicationError(f"la corrida {plan.run_id} no tiene casos publicados", non_retryable=True)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(raw)
    reps: dict[str, int] = {}
    for arm in plan.arms:
        if arm == "A0":
            continue
        n = 0
        while await asyncio.to_thread(store.get_bytes, f"{prefix}/metrics/{arm}/{n}.json") is not None:
            n += 1
        if n:
            reps[arm] = n
    progress = json.loads(await asyncio.to_thread(store.get_bytes, f"{prefix}/progress.json") or b"{}")
    return EvaluationPlan(
        cases=len(_cases(plan.run_id)),
        reps_by_arm=reps,
        spent_usd=float(progress.get("spent_usd") or 0.0) if isinstance(progress, dict) else 0.0,
    )


@activity.defn(name="lab_run_publish_control")
async def publish_control_activity(plan: RunPlan) -> PublishResult:
    bench_dir = _lab_root() / "bench" / plan.bench_id
    case_set = await asyncio.to_thread(build_cases, bench_dir, sales_workspace=SALES_WORKSPACE)
    local = _lab_root() / "runs" / plan.run_id
    local.mkdir(parents=True, exist_ok=True)
    (local / "cases.jsonl").write_text(
        "\n".join(json.dumps(c.to_dict(), ensure_ascii=False) for c in case_set.cases) + "\n", encoding="utf-8"
    )
    manifest = await asyncio.to_thread(
        publish_control, bench_dir, case_set, _store(), run_id=plan.run_id, order=asdict(plan)
    )
    return PublishResult(sessions=int(manifest["counts"]["sessions"]), cases=int(manifest["counts"]["cases"]))


def _classifier_fallback(trace: dict) -> str | None:
    step = next((s for s in trace.get("steps") or [] if isinstance(s, dict) and s.get("kind") == "perception"), None)
    return str(step["fallback"]) if step is not None and step.get("fallback") else None


def _charged_usd(result: dict) -> float:
    """Lo que un caso le carga al tope: su costo reportado y, si el LLM del
    agente no reportó nada (tabla de precios ausente, modelo fuera de ella,
    proceso muerto), la tarifa medida por turno. Subcontar deja el tope ciego."""
    reported = result.get("cost_usd")
    total = float(reported) if isinstance(reported, (int, float)) and not isinstance(reported, bool) else 0.0
    llm = result.get("llm_cost_usd", reported)
    if not isinstance(llm, (int, float)) or isinstance(llm, bool) or llm <= 0:
        total += AGENT_USD_PER_TURN
    return total


@activity.defn(name="lab_run_smoke_turn")
@with_heartbeat(every=10)
async def smoke_turn_activity(plan: RunPlan) -> SmokeResult:
    """Plan §3.3: antes de simular, el primer caso del banco corre de punta a
    punta en el sandbox (el mismo camino que los brazos). Si no pasa, la
    corrida no arranca: es más barato fallar acá que a mitad de 3.600 turnos.

    También con cada bot nuevo (B, C): su clasificador falla abierto, así que
    con la llave del laboratorio en placeholder o la API cambiada responderían
    igual que A1 y la corrida gastaría dos tercios de su tope en nada."""
    cases_path = _lab_root() / "runs" / plan.run_id / "cases.jsonl"
    lines = [line for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()] if cases_path.is_file() else []
    if not lines:
        return SmokeResult(ok=False, error="el banco no tiene casos para el turno de humo")
    case = json.loads(lines[0])
    case_id = str(case.get("case_id") or "")
    cost = 0.0
    sent: list[str] = []
    for arm in ("A1", *(a for a in plan.arms if arm_profile(a) is not None)):
        result = await run_case_in_subprocess(
            case,
            bench_dir=_lab_root() / "bench" / plan.bench_id,
            sandbox_dir=_lab_root() / "runs" / plan.run_id / "smoke" / arm / "case",
            timeout_s=SMOKE_TIMEOUT_S,
            arm=arm,
        )
        cost += _charged_usd(result)
        trace = result.get("trace") or {}
        error = result.get("error") or (None if trace else "el turno no dejó traza")
        fallback = _classifier_fallback(trace) if error is None and arm_profile(arm) is not None else None
        if fallback:
            error = f"el bot {arm} no pudo usar su clasificador ({fallback}): revisa la llave de OpenRouter del laboratorio"
        if error:
            return SmokeResult(ok=False, case_id=case_id, error=f"{arm}: {error}" if arm != "A1" else error, cost_usd=cost)
        if arm == "A1":
            sent = [str(t) for t in trace.get("sent_texts") or []]
    return SmokeResult(ok=True, case_id=case_id, sent_texts=sent, cost_usd=cost)


@activity.defn(name="lab_run_simulate_case")
@with_heartbeat(every=10)
async def simulate_case_activity(inp: SimulateInput) -> CaseOutcome:
    """Un caso de un brazo simulado, en su sandbox (un proceso). El resultado
    queda en disco de la caja hasta que `lab_run_publish_arm` lo sube."""
    cases = _cases(inp.run_id)
    if not 0 <= inp.index < len(cases):
        return CaseOutcome(case_id="", ok=False, error=f"no hay caso {inp.index}")
    case = cases[inp.index]
    result = await run_case_in_subprocess(
        case,
        bench_dir=_lab_root() / "bench" / inp.bench_id,
        sandbox_dir=_lab_root() / "runs" / inp.run_id / inp.arm / str(inp.rep) / str(inp.index),
        timeout_s=CASE_TIMEOUT_S,
        arm=inp.arm,
    )
    out = _results_dir(inp.run_id, inp.arm, inp.rep)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{inp.index}.json").write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    error = result.get("error") or (None if result.get("trace") else "el turno no dejó traza")
    return CaseOutcome(
        case_id=str(case.get("case_id") or ""),
        ok=error is None,
        error=error,
        cost_usd=_charged_usd(result),
    )


@activity.defn(name="lab_run_publish_arm")
async def publish_arm_activity(inp: ArmPublishInput) -> ArmPublishResult:
    cases = _cases(inp.run_id)
    folder = _results_dir(inp.run_id, inp.arm, inp.rep)
    results: dict[int, dict] = {}
    for index in range(len(cases)):
        path = folder / f"{index}.json"
        if path.is_file():
            results[index] = json.loads(path.read_text(encoding="utf-8"))
    published, missing = await asyncio.to_thread(
        publish_arm, _store(), run_id=inp.run_id, arm=inp.arm, rep=inp.rep, cases=cases, results=results
    )
    return ArmPublishResult(published=published, missing=missing)


@activity.defn(name="lab_run_progress")
async def write_progress_activity(update: ProgressUpdate) -> None:
    store = _store()
    key = f"runs/{update.run_id}/progress.json"
    raw = await asyncio.to_thread(store.get_bytes, key)
    try:
        previous = json.loads(raw) if raw else {}
    except ValueError:
        previous = {}
    now = _now_ms()
    progress = {
        **asdict(update),
        "started_at_ms": previous.get("started_at_ms") or now,
        "updated_at_ms": now,
    }
    await asyncio.to_thread(store.put_bytes, key, json.dumps(progress, ensure_ascii=False).encode())


@activity.defn(name="lab_run_cancel_requested")
async def cancel_requested_activity(run_id: str) -> bool:
    return (_lab_root() / "runs" / run_id / "CANCEL").exists()


# `LAB_JUDGE` (solo en la caja): el juez de la corrida. Por defecto Claude Code
# (decisión del operador, 2026-09-28): califica fuera de la caja y no gasta API.
# Estos valores vuelven al juez de pago (el alias Gemini del proxy); `off` = sin juez.
PAID_JUDGE_VALUES = frozenset({"litellm", "gemini", "on"})


def judge_usd_per_turn() -> float:
    """Tarifa del juez por turno calificado: 0 con Claude Code o sin juez."""
    return JUDGE_USD_PER_TURN if (os.getenv("LAB_JUDGE") or "").strip().lower() in PAID_JUDGE_VALUES else 0.0


def _judge_dir(run_id: str) -> Path:
    return _lab_root() / "runs" / run_id / "judge"


def _judge(run_id: str = ""):
    """El juez del scorecard en la caja. Por defecto Claude Code (decisión del
    operador, 2026-09-28): los prompts quedan en la cola de la corrida
    (`runs/<corrida>/judge/`) hasta que Claude Code los califica.
    `LAB_JUDGE=off` lo apaga (tests, o una corrida sin juez);
    `LAB_JUDGE=litellm` vuelve al alias de pago del proxy."""
    choice = (os.getenv("LAB_JUDGE") or "").strip().lower()
    if choice in {"off", "0", "false"}:
        return None
    if choice in PAID_JUDGE_VALUES:
        from src.plugins.chats.agent.sales_eval.evals import composition

        return composition.get_judge()
    return ClaudeCodeJudge(JudgeQueue(_judge_dir(run_id)))


_JUDGE_FILES = ("pending.jsonl", "answers.jsonl")


def _pull_judge_queue(store: LabStorePort, run_id: str) -> None:
    """La cola de S3 a la caja: las respuestas de Claude Code (las escribe él,
    nunca la caja) y lo pendiente, si la caja no lo tiene (modo `evaluate`)."""
    local = _judge_dir(run_id)
    local.mkdir(parents=True, exist_ok=True)
    for name in _JUDGE_FILES:
        raw = store.get_bytes(f"runs/{run_id}/judge/{name}")
        if raw is not None and (name == "answers.jsonl" or not (local / name).exists()):
            (local / name).write_bytes(raw)


def _push_pending(store: LabStorePort, run_id: str) -> None:
    path = _judge_dir(run_id) / "pending.jsonl"
    if path.exists():
        store.put_bytes(f"runs/{run_id}/judge/pending.jsonl", path.read_bytes())


def _judge_pending(store: LabStorePort, run_id: str) -> int:
    def ids(name: str) -> set[str]:
        raw = store.get_bytes(f"runs/{run_id}/judge/{name}") or b""
        out = set()
        for line in raw.decode("utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                out.add(item["id"])
        return out

    return len(ids("pending.jsonl") - ids("answers.jsonl"))


def _jsonl_rows(raw: bytes | None) -> list[dict]:
    out = []
    for line in (raw or b"").decode("utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _put_by_session(store: LabStorePort, prefix: str, records: list[dict]) -> None:
    by_sid: dict[str, list[dict]] = {}
    for rec in records:
        by_sid.setdefault(str(rec.get("session_id")), []).append(rec)
    for sid, rows in by_sid.items():
        store.put_bytes(f"{prefix}/{sid}.jsonl", ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode())


def _eval_chunk_turns() -> int:
    try:
        return max(1, int(os.getenv("LAB_EVAL_CHUNK_TURNS") or EVAL_CHUNK_TURNS))
    except ValueError:
        return EVAL_CHUNK_TURNS


@activity.defn(name="lab_run_evaluate_arm")
@with_heartbeat(every=10)
async def evaluate_arm_activity(inp: EvaluateInput) -> EvaluateResult:
    """Califica un PEDAZO de un brazo en una repetición (sesiones enteras
    desde `offset`) con el scorecard en modo turno y publica
    `scores/<brazo>/<rep>/<sesión>.jsonl`. A0 se re-mide con sus propios
    turnos (el registro de producción queda en el resumen). Por pedazos: con
    juez, un brazo entero tarda ~1 h, y la caja tiene que reportar avance
    antes de que el lanzador la dé por caída; un reintento repite solo el
    pedazo. El juez se cobra a la tarifa medida por turno calificado."""
    from src.plugins.chats.agent.sales_eval.scorecard.catalog_context import build_check_context

    store = _store()
    cases, next_offset = evaluation_chunk(_cases(inp.run_id), inp.offset, max_turns=_eval_chunk_turns())
    bench_dir = _lab_root() / "bench" / inp.bench_id
    prefix = f"runs/{inp.run_id}"
    rows = None
    if inp.arm != "A0":
        sids = sorted({str(c["session_id"]) for c in cases})
        rows = {
            sid: _jsonl_rows(await asyncio.to_thread(store.get_bytes, f"{prefix}/turns/{inp.arm}/{inp.rep}/{sid}.jsonl"))
            for sid in sids
        }
    ctx = await build_check_context(catalog=bench_catalog_client(bench_dir / "catalog"))
    judge = _judge(inp.run_id) if inp.judge else None
    claude = isinstance(judge, ClaudeCodeJudge)
    if claude:
        await asyncio.to_thread(_pull_judge_queue, store, inp.run_id)
    records = await score_arm(bench_dir, cases, arm=inp.arm, rep=inp.rep, rows=rows, ctx=ctx, judge=judge)
    if claude:
        await asyncio.to_thread(_push_pending, store, inp.run_id)
    await asyncio.to_thread(_put_by_session, store, f"{prefix}/scores/{inp.arm}/{inp.rep}", records)
    turns = sum(len(r.get("by_turn") or []) for r in records)
    return EvaluateResult(
        episodes=len(records),
        judge_errors=sum(int(r.get("judge_errors") or 0) for r in records),
        turns=turns,
        # Claude Code califica fuera de la caja: no gasta API.
        judge_usd=round(JUDGE_USD_PER_TURN * turns, 6) if judge is not None and not claude else 0.0,
        next_offset=next_offset,
    )


def _read_arm(store: LabStorePort, prefix: str) -> list[dict]:
    rows: list[dict] = []
    for key in sorted(store.list_keys(prefix + "/")):
        if key.endswith(".jsonl"):
            rows.extend(_jsonl_rows(store.get_bytes(key)))
    return rows


def _summarize(store: LabStorePort, inp: SummarizeInput) -> SummarizeResult:
    from src.plugins.chats.agent.sales_eval.scorecard.registry import CHECKS, REGISTRY_VERSION

    prefix = f"runs/{inp.run_id}"
    scores: dict[str, list[list[dict]]] = {}
    rows: dict[str, list[list[dict]]] = {}
    metrics: dict[str, list[dict]] = {}
    for arm in inp.arms:
        reps = inp.reps_by_arm.get(arm, 1 if arm == "A0" else inp.reps)
        scores[arm] = [_read_arm(store, f"{prefix}/scores/{arm}/{rep}") for rep in range(reps)]
        if arm != "A0":
            rows[arm] = [_read_arm(store, f"{prefix}/turns/{arm}/{rep}") for rep in range(reps)]
            metrics[arm] = [
                json.loads(raw) for rep in range(reps) if (raw := store.get_bytes(f"{prefix}/metrics/{arm}/{rep}.json"))
            ]
    previous = json.loads(store.get_bytes(f"{prefix}/summary.json") or b"{}")
    production = _read_arm(store, f"{prefix}/production/scores")
    code_checks = {c.id for c in CHECKS if c.kind == "code" and getattr(c, "focus", "turn") != "future"}
    summary = build_summary(run_id=inp.run_id, registry_version=REGISTRY_VERSION, previous=previous, scores=scores,
                            metrics=metrics, rows=rows, code_checks=code_checks, production_records=production,
                            arms_pending=list(inp.arms_pending))
    pending = _judge_pending(store, inp.run_id)
    summary["judge"] = {**(summary.get("judge") or {}), "pending": pending}
    store.put_bytes(f"{prefix}/summary.json", json.dumps(summary, ensure_ascii=False).encode())
    index = json.loads(store.get_bytes(f"{prefix}/conversations.json") or b"[]")
    store.put_bytes(f"{prefix}/conversations.json", json.dumps(with_verdicts(index, scores), ensure_ascii=False).encode())
    notes = []
    fid = summary.get("fidelity") or {}
    if fid.get("agreement") is not None and not fid.get("ok"):
        notes.append(
            f"fidelidad del simulador {fid['agreement'] * 100:.0f} % (vara 90 %): A1 no reproduce bien a A0, "
            "la comparación pierde valor"
        )
    # Jev caído en silencio (sin llave, tardanza, error): lo que decidió la
    # regla no puede pasar por Jev.
    for arm, arm_metrics in metrics.items():
        notes.extend(jev_fallback_notes(arm, arm_metrics))
    if pending:
        notes.append(
            f"{pending} calificaciones del juez esperan a Claude Code: esos checks quedan desconocidos "
            "hasta aplicarlas (corrida en modo solo evaluar)"
        )
    if summary["judge"].get("errors"):
        notes.append(f"{summary['judge']['errors']} llamadas al juez fallaron (esos checks quedan desconocidos)")
    return SummarizeResult(notes=notes)


@activity.defn(name="lab_run_summarize")
async def summarize_activity(inp: SummarizeInput) -> SummarizeResult:
    """Resumen de la corrida: gráficas por brazo, diferencias con intervalo,
    fidelidad y arena (`summary.json`), y el veredicto de cada brazo en el
    índice de conversaciones."""
    return await asyncio.to_thread(_summarize, _store(), inp)


LAB_RUN_ACTIVITIES = [
    prepare_run_activity,
    evaluation_plan_activity,
    publish_control_activity,
    smoke_turn_activity,
    simulate_case_activity,
    publish_arm_activity,
    write_progress_activity,
    cancel_requested_activity,
    evaluate_arm_activity,
    summarize_activity,
]
