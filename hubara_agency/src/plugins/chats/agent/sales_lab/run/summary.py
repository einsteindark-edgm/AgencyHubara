"""Resumen de una corrida del laboratorio (plan §5, PR 13). PURO.

Lo publica la caja en `runs/<corrida>/summary.json`; la API lo sirve por
`/lab/runs/{corrida}/summary?arm=` y `/diff?base=&cand=`.

  arms.<brazo>     la MISMA forma que Calidad LLM (`stats.compute_stats`) en
                   modo turno, + pass^k sobre las repeticiones. Un episodio
                   sin turnos calificados (no se alcanzó a simular) no cuenta
  arms_pending     bots pedidos que la corrida no alcanzó a simular (tope de
                   gasto o límite de la corrida): pendientes, no fallas
  production       lo que decía el scorecard de producción (modo episodio),
                   como referencia (lo publicó el control, PR 8; un reintento
                   del resumen la conserva)
  diffs            "A0:A1" (fidelidad) y "A1:B", "A1:C" (base = bot actual)
  fidelity         A1 contra A0 en checks de código, turno por turno
  arena.<brazo>    bots nuevos: métricas de la corrida (PR 15) y acuerdo /
                   calibración contra los asuntos del juez (EST-08)
  validation       plan §5.3: el scorecard de producción (modo episodio)
                   contra A0 re-medido en modo turno, check por check. Solo
                   conteos: ningún dato del cliente sale en esta sección
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.plugins.chats.agent.sales_eval.scorecard import stats
from src.plugins.chats.agent.sales_eval.scorecard.service import aggregate_checks
from src.plugins.chats.agent.sales_lab.arms import ARM_PROFILES
from src.plugins.chats.agent.sales_lab.run.arena import judge_topic_codes, perceived_topics, topic_arena
from src.plugins.chats.agent.sales_lab.run.compare import arm_row, diff_entry, fidelity, pass_k

CONTROL = "A0"
CURRENT = "A1"
_JUDGE_TOPICS_CHECK = "EST-08"

Records = list[list[dict[str, Any]]]


def _arm_stats(reps: Records) -> dict[str, Any]:
    rows = [arm_row(r) for r in (reps[0] if reps else []) if r.get("by_turn")]
    dates = sorted(str(r.get("episode_date") or "") for r in rows if r.get("episode_date"))
    weeks = stats.weeks_between(dates[0], dates[-1]) if dates else []
    return {"reps": len(reps), "mode": "turn", **stats.compute_stats(rows, weeks=weeks), "pass_k": pass_k(reps)}


def _judge_topics(records: list[dict[str, Any]]) -> dict[tuple[str, str, int], list[dict[str, Any]]]:
    out: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for rec in records:
        for entry in rec.get("by_turn") or []:
            for row in entry.get("results") or []:
                if row.get("check_id") == _JUDGE_TOPICS_CHECK and row.get("topics"):
                    out[(str(rec.get("session_id")), str(rec.get("episode_id")), int(entry.get("turn") or 0))] = list(row["topics"])
    return out


def _arena(arm: str, scores: Records, rows: Records, metrics: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = []
    unmapped = 0
    for rep, records in enumerate(scores):
        judged = _judge_topics(records)
        for row in rows[rep] if rep < len(rows) else []:
            key = (str(row.get("session_id")), str(row.get("episode_id")), int(row.get("turn") or 0))
            perceived = perceived_topics(row)
            if perceived is None or key not in judged:
                continue
            codes, missed = judge_topic_codes(judged[key])
            unmapped += missed
            pairs.append((perceived, codes))
    return {"profile": ARM_PROFILES.get(arm), "metrics": metrics, "topics": topic_arena(pairs), "unmapped_judge_topics": unmapped}


def build_summary(
    *,
    run_id: str,
    registry_version: int,
    previous: Mapping[str, Any],
    scores: Mapping[str, Records],
    metrics: Mapping[str, list[dict[str, Any]]],
    rows: Mapping[str, Records],
    code_checks: set[str],
    production_records: list[dict[str, Any]] | None = None,
    arms_pending: list[str] | None = None,
) -> dict[str, Any]:
    arms = {arm: _arm_stats(reps) for arm, reps in scores.items()}
    base = CURRENT if CURRENT in scores else CONTROL
    diffs: dict[str, Any] = {}
    if CONTROL in scores and CURRENT in scores:
        diffs[f"{CONTROL}:{CURRENT}"] = diff_entry(CONTROL, CURRENT, scores[CONTROL], scores[CURRENT])
    for cand in ARM_PROFILES:
        if cand in scores and base in scores:
            diffs[f"{base}:{cand}"] = diff_entry(base, cand, scores[base], scores[cand])
    previous_arms = previous.get("arms") or {}
    # El control publicó `arms.A0` (producción); un resumen ya escrito la trae
    # en `production` y su `arms.A0` es el re-medido: el reintento no la pisa.
    if "production" in previous:
        production = previous.get("production")
    else:
        production = previous_arms.get(CONTROL) if isinstance(previous_arms, Mapping) else None
    return {
        "run_id": run_id,
        "registry_version": registry_version,
        "mode": "turn",
        "arms": arms,
        "arms_pending": [a for a in (arms_pending or []) if a not in scores],
        "production": production,
        "diffs": diffs,
        "fidelity": fidelity(scores[CONTROL][0], scores[CURRENT], code_checks=code_checks)
        if CONTROL in scores and CURRENT in scores and scores[CONTROL]
        else None,
        "arena": {
            arm: _arena(arm, scores[arm], list(rows.get(arm) or []), list(metrics.get(arm) or []))
            for arm in ARM_PROFILES
            if arm in scores
        },
        "validation": validation(production_records, scores[CONTROL][0], code_checks=code_checks)
        if production_records and CONTROL in scores and scores[CONTROL]
        else None,
        "judge": {
            "used": any(r.get("judge") for reps in scores.values() for rep in reps for r in rep),
            "errors": sum(int(r.get("judge_errors") or 0) for reps in scores.values() for rep in reps for r in rep),
        },
    }


def with_verdicts(index: list[dict[str, Any]], scores: Mapping[str, Records]) -> list[dict[str, Any]]:
    """El índice de conversaciones con el veredicto (modo turno, repetición 0)
    de cada brazo; el de producción queda aparte como referencia."""
    by_sid: dict[str, dict[str, dict[str, str]]] = {}
    for arm, reps in scores.items():
        for rec in reps[0] if reps else []:
            by_sid.setdefault(str(rec.get("session_id")), {}).setdefault(arm, {})[str(rec.get("episode_id"))] = str(
                rec.get("verdict") or "SIN_DATOS"
            )
    out = []
    for row in index:
        new = dict(row)
        if "verdicts_production" not in new:
            new["verdicts_production"] = (row.get("verdicts") or {}).get(CONTROL, {})
        new["verdicts"] = by_sid.get(str(row.get("session_id")), {})
        out.append(new)
    return out


_DECIDED = ("pasa", "falla")


def validation(
    prod: list[dict[str, Any]], turn: list[dict[str, Any]], *, code_checks: set[str]
) -> dict[str, Any]:
    """¿El modo turno reproduce las fallas del scorecard de producción?

    Por episodio y check de código decidido en los dos lados (pasa/falla):
    coinciden, fallan los dos, falla solo en producción o solo en modo turno.
    Las diferencias esperables (registro nuevo, EST-08 v2, checks `future`)
    se leen en `only_*`; `prod_registry_versions` dice con qué versión
    calificó producción."""
    turn_by = {(str(r.get("session_id")), str(r.get("episode_id"))): r for r in turn}
    checks: dict[str, dict[str, int]] = {}
    pairs = agree = 0
    verdict_pairs = verdict_agree = 0
    episodes = 0
    versions: set[int] = set()
    for rec in prod:
        other = turn_by.get((str(rec.get("session_id")), str(rec.get("episode_id"))))
        if other is None:
            continue
        episodes += 1
        if isinstance(rec.get("registry_version"), int):
            versions.add(rec["registry_version"])
        verdict_pairs += 1
        verdict_agree += rec.get("verdict") == other.get("verdict")
        prod_checks = {str(r.get("check_id")): str(r.get("verdict")) for r in rec.get("results") or [] if isinstance(r, dict)}
        turn_checks = aggregate_checks(r for r in other.get("results") or [] if isinstance(r, dict))
        for cid in sorted(code_checks):
            pv, tv = prod_checks.get(cid), turn_checks.get(cid)
            if pv not in _DECIDED or tv not in _DECIDED:
                continue
            c = checks.setdefault(cid, {"pairs": 0, "agree": 0, "both_fail": 0, "only_production": 0, "only_turn": 0})
            c["pairs"] += 1
            pairs += 1
            if pv == tv:
                c["agree"] += 1
                agree += 1
                c["both_fail"] += pv == "falla"
            elif pv == "falla":
                c["only_production"] += 1
            else:
                c["only_turn"] += 1
    return {
        "episodes": episodes,
        "prod_registry_versions": sorted(versions),
        "agreement": agree / pairs if pairs else None,
        "verdict_agreement": verdict_agree / verdict_pairs if verdict_pairs else None,
        "checks": checks,
    }
