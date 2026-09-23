"""Activities del harness de evaluación (no-deterministas: corren DeepEval).

R-DET: la evaluación LLM-as-judge es I/O + no-determinismo → vive ACÁ, NUNCA en
el workflow. `deepeval` se usa solo en runtime (worker con extra `evals`); los
imports del paquete `evals` son lazy/guarded, así que este módulo es importable
sin el extra (gate de arquitectura no rompe).

R-HEARTBEAT: `evaluate_sales_conversation_activity` corre ~9 métricas (varias con
llamadas al juez, >10s worst-case) → `@with_heartbeat`.
R-JSON: in (str, EvalWindowInput) / out (ConversationEvalResult) — frozen scalars.
R-DIP: importa `platform/` + el propio plugin sales; NO importa plugins siblings
ni `temporalio.client`.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from temporalio import activity

from src.platform.observability.eval_metrics import (
    emit_conversation_verdict,
    emit_eval_score,
)
from src.platform.temporal.heartbeat import with_heartbeat
from src.plugins.chats.agent.sales_eval.evals import (
    composition,
    curation,
    history,
    reconstruct,
    scenario,
    script_rubric,
)
from src.plugins.chats.agent.sales_eval.evals import metrics as M
from src.plugins.chats.agent.sales_eval.evals.contracts import (
    ConversationEvalResult,
    EvalWindowInput,
    GoldenEvalInput,
    GoldenSuiteResult,
    ScorecardSummary,
)
from src.plugins.chats.agent.sales_eval.evals.select import select_eval_units

# hubara_agency/ (raíz del paquete): activities/ -> sales_eval -> agent -> chats ->
# plugins -> src -> hubara_agency
_REPO_ROOT = Path(__file__).resolve().parents[6]
_GOLDEN_SCRIPT = _REPO_ROOT / "scripts" / "golden_eval.py"

# Builder COMPARTIDO con el golden del GitHub Action (paridad de criterio):
# hechos del sistema + catálogo real entran al juez vía el Scenario.
_SCENARIO = scenario.BASE_SCENARIO


def _env() -> str:
    return os.getenv("ENVIRONMENT", "dev")


@activity.defn(name="select_conversations_to_eval")
async def select_conversations_to_eval_activity(window: EvalWindowInput) -> list[str]:
    """Enumera + prioriza las unidades a evaluar (lee vault).

    Devuelve unit ids `<session>::<episode>` — UNA entrada por episodio elegible
    (sesiones legacy sin episodes[] → el session id pelado, sesión entera). El
    workflow no interpreta el string: lo pasa tal cual a la activity de eval.
    """
    units = select_eval_units(window, vault_dir=composition.get_vault_dir())
    activity.logger.info(
        "eval.select: %d episodios/conversaciones seleccionados (ventana %dh, tope %d)",
        len(units), window.lookback_hours, window.max_conversations,
    )
    return units


@activity.defn(name="evaluate_sales_conversation")
@with_heartbeat(every=10)
async def evaluate_sales_conversation_activity(
    unit_id: str, window: EvalWindowInput
) -> ConversationEvalResult:
    """Evalúa UN episodio (o sesión legacy): reconstruye → puntúa → SigNoz → curación.

    `unit_id` = `<session>::<episode>` (o el session id pelado para sesiones sin
    episodes[] / runs viejos en replay — L-9: el shape viejo sigue siendo válido).
    El detalle por-métrica se emite a SigNoz dentro de esta activity (span attrs +
    métrica). El return es escalar (lo agrega el workflow). Si la conversación es
    candidata a golden y `draft_goldens`, el juez redacta el `expected_outcome`.
    """
    vault_dir = composition.get_vault_dir()
    session_id, episode_id = reconstruct.parse_eval_unit_id(unit_id)
    wa_number = reconstruct.whatsapp_number_from_session(session_id)

    events, _episode = reconstruct.read_episode_events(vault_dir, session_id, episode_id)
    turns = reconstruct.to_evaluable_turns(events, redact=window.redact_pii)
    if len(turns) < window.min_turns:
        return ConversationEvalResult(
            session_id=session_id, episode_id=episode_id, whatsapp_number=wa_number,
            num_turns=len(turns), skipped=True, error="too_few_turns",
        )

    # Scenario enriquecido: hechos verificados del sistema (cierre/orden/
    # escalada desde metadata — fix del falso negativo de correct_handoff en
    # ep_010) + catálogo real (ground truth de no_hallucination). Llega al
    # prompt del juez vía TurnParams.SCENARIO.
    metadata = reconstruct.read_session_metadata(vault_dir, session_id)
    scenario_text = scenario.build_scenario(
        _episode,
        metadata,
        episode_is_last=(
            reconstruct.is_last_episode(metadata, episode_id) if episode_id else True
        ),
        catalog_block=await scenario.catalog_ground_truth(),
    )
    test_case = reconstruct.build_conversational_test_case(
        turns, scenario=scenario_text, context=[script_rubric.SCRIPT_CONTEXT],
        name=unit_id,
    )

    judge = composition.get_judge()
    metrics = M.all_sales_metrics(judge)

    # scores: [(metric_key, score, success, reason)]
    scores: list[tuple[str, float, bool, str]] = []
    for metric in metrics:
        key = M.metric_key(metric)
        try:
            await metric.a_measure(test_case)
            score = float(getattr(metric, "score", 0.0) or 0.0)
            success = bool(metric.is_successful())
            reason = str(getattr(metric, "reason", "") or "")
        except Exception as exc:  # noqa: BLE001 — una métrica que falla no tumba la corrida
            activity.logger.warning("eval métrica %s falló: %s", key, exc)
            continue
        scores.append((key, score, success, reason))
        emit_eval_score(
            metric_name=key, score=score, threshold=float(getattr(metric, "threshold", 0.5)),
            reason=reason, session_id=session_id, whatsapp_number=wa_number,
            agent="sales-agent", environment=_env(),
        )
        activity.heartbeat(key)

    if not scores:
        return ConversationEvalResult(
            session_id=session_id, episode_id=episode_id, whatsapp_number=wa_number,
            num_turns=len(turns), error="no_metrics_evaluated",
        )

    n = len(scores)
    n_pass = sum(1 for (_, _, ok, _) in scores if ok)
    avg = sum(s for (_, s, _, _) in scores) / n
    overall_pass = n_pass == n
    # Candidata a golden: el promedio cae bajo el umbral (la conversación está por
    # debajo de la barra global) O falló una métrica CRÍTICA (ep_010: alucinación
    # de catálogo con avg 0.72 — el promedio no la salvaba de ser material de
    # regresión). Tunable vía `window.candidate_threshold` / `CRITICAL_METRICS`.
    is_candidate = avg < window.candidate_threshold or curation.has_critical_failure(
        scores
    )

    emit_conversation_verdict(
        session_id=session_id, avg_score=avg, overall_pass=overall_pass,
        is_candidate=is_candidate, num_metrics=n, environment=_env(),
    )

    # Histórico para el frontend (un registro por episodio por corrida) — alimenta
    # la tendencia Y la vista por-conversación ("¿cuál falló?, ¿mejoró?").
    # Activity -> puede usar now(); best-effort (no rompe la eval si falla).
    try:
        from datetime import datetime, timezone

        now_utc = datetime.now(timezone.utc)
        history.append_history_record(
            composition.get_eval_history_dir(),
            run_date=now_utc.strftime("%Y-%m-%d"),
            session_id=session_id, suite="online", scores=scores,
            episode_id=episode_id, ts=now_utc.isoformat(timespec="seconds"),
            is_candidate=is_candidate,
        )
    except Exception as exc:  # noqa: BLE001
        activity.logger.warning("eval history append falló: %s", exc)

    candidate_path = ""
    if is_candidate and window.draft_goldens:
        try:
            failed = [(k, s, r) for (k, s, ok, r) in scores if not ok]
            expected = await curation.propose_expected_outcome(judge, turns, failed)
            golden = curation.build_candidate_golden(
                session_id=session_id, turns=turns, scenario=_SCENARIO,
                expected_outcome=expected, scores=scores, episode_id=episode_id,
            )
            path = curation.write_candidate(
                composition.get_candidates_dir(), unit_id, golden
            )
            candidate_path = str(path)
            activity.logger.info("eval.candidate escrito: %s", candidate_path)
        except Exception as exc:  # noqa: BLE001 — la curación no rompe la evaluación
            activity.logger.warning("eval golden draft falló: %s", exc)

    return ConversationEvalResult(
        session_id=session_id, episode_id=episode_id, whatsapp_number=wa_number,
        num_turns=len(turns), metrics_evaluated=n, metrics_passed=n_pass,
        avg_score=round(avg, 4), overall_pass=overall_pass, is_candidate=is_candidate,
        candidate_path=candidate_path,
    )


# --- golden suite (el MISMO eval del GitHub Action) -> SigNoz -----------------


def _golden_env() -> dict[str, str]:
    """Env del subprocess golden: HEREDA el del worker (OTEL->SigNoz, litellm,
    EVAL_JUDGE_MODEL) + overrides de aislamiento (vault temp, Medusa dummy/stub).

    El subprocess es la forma más limpia de aislar: el runner setea el env ANTES de
    importar el código de prod (vault temp, dummy Medusa) — algo que el worker
    long-lived, ya importado con config real, no puede hacer in-process.

    El vault temp viaja solo como GOLDEN_EVAL_VAULT: el runner deriva de ahí (y
    PISA) WORKSPACE_VAULT_DIR / EXOCLAW_STATE_DIR / CATALOG_SNAPSHOT_DIR, que el env
    heredado trae apuntando al vault real."""
    env = dict(os.environ)
    env["GOLDEN_EVAL_VAULT"] = tempfile.mkdtemp(prefix="golden_suite_")
    env["GOLDEN_EVAL_SIGNOZ"] = "1"
    env["MEDUSA_BASE_URL"] = "http://localhost:1"
    env["MEDUSA_ADMIN_TOKEN"] = "dummy-eval-token"
    env.pop("OTEL_SDK_DISABLED", None)  # el golden DEBE emitir a SigNoz
    for k in ("MEDUSA_REGION_ID", "MEDUSA_SALES_CHANNEL_ID"):
        env.pop(k, None)
    return env


def _summarize_golden(summary: list) -> tuple[int, int, int]:
    """(scenarios, behaviors_ok, errored) desde el json del runner."""
    scenarios = len(summary)
    ok = 0
    errored = 0
    for s in summary:
        with_data = [r for r in s.get("runs", []) if "behaviors" in r]
        if not with_data:
            errored += 1
            continue
        p, _, t = with_data[-1]["behaviors"].partition("/")
        if p and p == t:
            ok += 1
    return scenarios, ok, errored


@activity.defn(name="run_golden_suite")
@with_heartbeat(every=15)
async def run_golden_suite_activity(inp: GoldenEvalInput) -> GoldenSuiteResult:
    """Corre el GOLDEN set (el MISMO `scripts/golden_eval.py --signoz` del GitHub
    Action) como subprocess y agrega un resultado escalar. El subprocess emite los
    scores a SigNoz por su cuenta (eval.suite=golden, mismo dashboard que el online).

    Por qué subprocess y no in-process: el runner setea el env de aislamiento ANTES
    de importar prod; un proceso fresco puede, el worker long-lived no. Además es
    DRY: es literalmente el mismo eval que corre el CI."""
    import time

    run_id = activity.info().workflow_run_id[:8]
    json_out = str(Path(tempfile.gettempdir()) / f"golden_suite_{run_id}.json")
    cmd = [sys.executable, str(_GOLDEN_SCRIPT), "--signoz", "--json", json_out,
           "--environment", _env()]
    if inp.scenario:
        cmd += ["--scenario", inp.scenario]
    if inp.category:
        cmd += ["--category", inp.category]
    if inp.repeat and inp.repeat > 1:
        cmd += ["--repeat", str(inp.repeat)]
    if inp.no_judge:
        cmd += ["--no-judge"]

    activity.logger.info("golden suite -> %s", " ".join(cmd))
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(_REPO_ROOT), env=_golden_env(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    async for raw in proc.stdout:  # drena el pipe + loguea progreso
        line = raw.decode("utf-8", "replace").rstrip()
        if line and line[0] in "[=📊📝 ":
            activity.logger.info("golden| %s", line[:200])
    await proc.wait()
    dur = int(time.monotonic() - t0)

    if proc.returncode != 0:
        raise RuntimeError(f"golden_eval.py salió con código {proc.returncode}")
    try:
        summary = json.loads(Path(json_out).read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"no pude leer el json del golden: {exc}") from exc

    scenarios, ok, errored = _summarize_golden(summary)
    activity.logger.info(
        "golden suite OK: %d escenarios · %d behaviors-ok · %d errores · %ds",
        scenarios, ok, errored, dur,
    )
    return GoldenSuiteResult(
        scenarios=scenarios, behaviors_ok=ok, errored=errored,
        judge=not inp.no_judge, duration_s=dur,
    )


# --------------------------------------------------------------------------- #
# HU-SC-1 — Scorecard por etapa (reemplaza al promedio holístico como titular).
# --------------------------------------------------------------------------- #

def _scorecard_judge_enabled() -> bool:
    return os.getenv("SCORECARD_JUDGE_ENABLED", "true").strip().lower() not in ("0", "false", "no")


def _already_alerted(previous: dict | None, record: dict) -> bool:
    """El mismo episodio ya quedó guardado en FALLA con la misma huella: un
    recálculo (dashboard, ScoreEpisodeWorkflow) no vuelve a comentar el issue."""
    from src.plugins.chats.agent.sales_eval.scorecard import alerts

    return (
        previous is not None
        and previous.get("verdict") == "FALLA"
        and alerts.fingerprint(previous) == alerts.fingerprint(record)
    )


@activity.defn(name="select_scorecard_units")
async def select_scorecard_units_activity(window: EvalWindowInput) -> list[str]:
    """Episodios que el barrido diario califica con el scorecard (HU-SC-8).

    Abiertos con actividad en la ventana y cerrados sin scorecard posterior al
    cierre, sin mínimo de turnos (ver `service.daily_scorecard_units`).
    """
    from src.plugins.chats.agent.sales_eval.scorecard import service

    units = service.daily_scorecard_units(composition.get_vault_dir(), window)
    activity.logger.info("scorecard.daily: %d episodios a calificar (ventana %dh)", len(units), window.lookback_hours)
    return units


@activity.defn(name="score_episode_scorecard")
@with_heartbeat(every=10)
async def score_episode_scorecard_activity(
    session_id: str, episode_id: str, with_judge: bool
) -> ScorecardSummary:
    """Scorecard de UN episodio: checks de código + juez aislado por check.

    Guarda el registro completo en `<vault>/_evals/scorecards/<fecha>.jsonl`,
    emite un punto por check a SigNoz (`check.<id>`, suite `scorecard`) y, si
    el veredicto es FALLA, abre o comenta el issue de alerta (dedup). Nunca
    lanza: un error queda en `error` y el workflow sigue con la eval legada.
    """
    from src.plugins.chats.agent.sales_eval.scorecard import (
        alerts,
        calibration,
        service,
        store,
    )
    from src.plugins.chats.agent.sales_eval.scorecard.catalog_context import (
        build_check_context,
    )
    from src.plugins.chats.agent.sales_eval.evals.redaction import redact_pii
    from src.plugins.chats.agent.sales_eval.scorecard.judge_checks import run_judge_checks

    vault = composition.get_vault_dir()
    try:
        # El evento de cierre sale antes de que el turno de cierre persista su
        # traza: sin ella el episodio se evaluaría sin su último turno.
        if not await service.await_closing_trace(vault, session_id, episode_id):
            activity.logger.warning(
                "scorecard: la traza del turno de cierre de %s::%s no llegó en %.0fs; se evalúa sin ella",
                session_id, episode_id, service.CLOSING_TRACE_TIMEOUT_S,
            )
        traj = service.load_trajectory(vault, session_id, episode_id)
        ctx = await build_check_context()
        cards_dir = store.scorecards_dir(vault)
        # Las etiquetas traen el veredicto del juez que vio el humano: sin
        # releer meses de scorecards en cada cierre.
        calibrated = calibration.calibrated_checks(
            calibration.compute_calibration((), store.read_labels(store.labels_path(vault)))
        )
        previous = store.find_latest(cards_dir, session_id, episode_id)
        judge_results = []
        if with_judge and traj.turns and _scorecard_judge_enabled():
            judge_results = await run_judge_checks(traj, ctx, composition.get_judge())
        record = store.append_scorecard(
            cards_dir,
            service.score_trajectory(traj, ctx, judge_results=judge_results, calibrated=calibrated),
        )
    except Exception as exc:  # noqa: BLE001 — el scorecard nunca tumba la eval legada
        activity.logger.warning(
            "scorecard: no se pudo evaluar %s::%s: %r", session_id, episode_id, exc
        )
        return ScorecardSummary(session_id=session_id, episode_id=episode_id, error=repr(exc)[:300])

    for r in record["results"]:
        if r["verdict"] not in ("pasa", "falla"):
            continue
        emit_eval_score(
            metric_name=f"check.{r['check_id']}",
            score=1.0 if r["verdict"] == "pasa" else 0.0,
            threshold=1.0,
            # La evidencia cita al cliente: a SigNoz va redactada, como la
            # eval legada (`redact_pii` sobre los turnos).
            reason=redact_pii(r.get("evidence") or ""),
            session_id=session_id,
            episode_id=episode_id,
            environment=_env(),
            suite="scorecard",
        )
    if record["verdict"] == "FALLA" and not _already_alerted(previous, record):
        try:
            await alerts.notify_failure(record)
        except Exception as exc:  # noqa: BLE001 — la alerta es best-effort
            activity.logger.warning("scorecard: alerta no enviada: %r", exc)

    first = record.get("first_failure") or {}
    counts = record.get("counts") or {}
    activity.logger.info(
        "scorecard %s::%s → %s (críticos=%s mayores=%s juez=%s)",
        session_id, episode_id, record["verdict"], counts.get("critico"), counts.get("mayor"),
        record.get("judge"),
    )
    return ScorecardSummary(
        session_id=session_id,
        episode_id=episode_id,
        verdict=str(record["verdict"]),
        fidelity=str(record.get("fidelity") or ""),
        critical=int(counts.get("critico", 0)),
        major=int(counts.get("mayor", 0)),
        minor=int(counts.get("menor", 0)),
        unknown=int(counts.get("desconocido", 0)),
        first_failure_check=str(first.get("check_id") or ""),
        first_failure_turn=int(first["turn"]) if isinstance(first.get("turn"), int) else -1,
        judge=bool(record.get("judge")),
        stored=True,
    )
