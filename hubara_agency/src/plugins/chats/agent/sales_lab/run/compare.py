"""Comparación entre bots del laboratorio (plan §5 puntos 4, 6 y 7; PR 13). PURO.

Entrada: registros del scorecard en MODO TURNO (`service.score_turns`), uno
por episodio, brazo y repetición.

  * `arm_row`: la fila de las gráficas de Calidad LLM (`stats.compute_stats`),
    con cada check agregado sobre los turnos (`aggregate_checks`).
  * `paired_bootstrap`: diferencia pareada por conversación (la unidad que se
    re-muestrea es la conversación, no el turno: los turnos de una misma
    conversación no son independientes). Semilla fija: la misma corrida da
    siempre el mismo intervalo. Si el intervalo de 95 % cruza el cero, la
    diferencia es "aún no concluyente".
  * `pass_k`: el episodio pasa en TODAS las repeticiones.
  * `fidelity`: el simulador del bot actual (A1) contra lo que pasó (A0),
    turno por turno y solo checks de código; la vara es 90 %.
  * `changed_turns`: los turnos cuyo veredicto cambió entre dos bots.
"""
from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from src.plugins.chats.agent.sales_eval.scorecard.service import aggregate_checks

FIDELITY_THRESHOLD = 0.9
_DECIDED = ("pasa", "falla")
_ITERATIONS_CHECKS = 1000


def arm_row(record: Mapping[str, Any]) -> dict[str, Any]:
    row = {k: v for k, v in record.items() if k not in ("results", "by_turn")}
    row["checks"] = aggregate_checks(r for r in record.get("results") or [] if isinstance(r, Mapping))
    return row


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _percentile(ordered: list[float], q: float) -> float:
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


def paired_bootstrap(
    base: Mapping[str, list[float]],
    cand: Mapping[str, list[float]],
    *,
    iterations: int = 2000,
    seed: int = 7,
) -> dict[str, Any]:
    """Media de (candidato − base) por conversación, con intervalo de 95 %."""
    sessions = sorted(s for s in base.keys() & cand.keys() if base[s] and cand[s])
    if not sessions:
        return {"delta": None, "low": None, "high": None, "conclusive": False, "sessions": 0}
    diffs = [_mean(list(cand[s])) - _mean(list(base[s])) for s in sessions]
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(_mean([diffs[rng.randrange(n)] for _ in range(n)]) for _ in range(iterations))
    low, high = _percentile(means, 0.025), _percentile(means, 0.975)
    return {
        "delta": round(_mean(diffs), 4),
        "low": round(low, 4),
        "high": round(high, 4),
        "conclusive": low > 0 or high < 0,
        "sessions": n,
    }


def _key(rec: Mapping[str, Any]) -> tuple[str, str]:
    return str(rec.get("session_id")), str(rec.get("episode_id"))


def pass_k(reps: list[list[Mapping[str, Any]]]) -> dict[str, Any]:
    by_rep = [{_key(r): str(r.get("verdict")) for r in rep} for rep in reps]
    common = set.intersection(*(set(m) for m in by_rep)) if by_rep else set()
    passed = sum(1 for k in common if all(m[k] == "PASA" for m in by_rep))
    return {"k": len(reps), "episodes": len(common), "rate": (passed / len(common)) if common else None}


def _turn_checks(rec: Mapping[str, Any]) -> dict[int, tuple[str, dict[str, str]]]:
    out: dict[int, tuple[str, dict[str, str]]] = {}
    for entry in rec.get("by_turn") or []:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("turn"), int):
            continue
        checks = {str(r.get("check_id")): str(r.get("verdict")) for r in entry.get("results") or [] if isinstance(r, Mapping)}
        out[entry["turn"]] = (str(entry.get("verdict")), checks)
    return out


def fidelity(
    a0: Iterable[Mapping[str, Any]],
    a1_reps: list[list[Mapping[str, Any]]],
    *,
    code_checks: set[str],
) -> dict[str, Any]:
    real: dict[tuple[str, str, int, str], str] = {}
    for rec in a0:
        for turn, (_, checks) in _turn_checks(rec).items():
            for cid, v in checks.items():
                if cid in code_checks and v in _DECIDED:
                    real[(*_key(rec), turn, cid)] = v
    pairs = 0
    agree = 0
    by_check: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for rep in a1_reps:
        for rec in rep:
            for turn, (_, checks) in _turn_checks(rec).items():
                for cid, v in checks.items():
                    ref = real.get((*_key(rec), turn, cid))
                    if ref is None or v not in _DECIDED:
                        continue
                    pairs += 1
                    agree += ref == v
                    by_check[cid][0] += 1
                    by_check[cid][1] += ref == v
    agreement = agree / pairs if pairs else None
    return {
        "n": pairs,
        "agreement": agreement,
        "threshold": FIDELITY_THRESHOLD,
        "ok": agreement is not None and agreement >= FIDELITY_THRESHOLD,
        "by_check": {cid: {"n": n, "agreement": a / n} for cid, (n, a) in sorted(by_check.items())},
    }


def changed_turns(base: Iterable[Mapping[str, Any]], cand: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cand_by = {_key(r): _turn_checks(r) for r in cand}
    out: list[dict[str, Any]] = []
    for rec in base:
        other = cand_by.get(_key(rec))
        if other is None:
            continue
        for turn, (verdict, checks) in sorted(_turn_checks(rec).items()):
            if turn not in other:
                continue
            c_verdict, c_checks = other[turn]
            if verdict == c_verdict:
                continue
            flipped = sorted(cid for cid in checks.keys() | c_checks.keys() if checks.get(cid) != c_checks.get(cid))
            out.append(
                {"session_id": _key(rec)[0], "episode_id": _key(rec)[1], "turn": turn,
                 "base": verdict, "cand": c_verdict, "checks": flipped}
            )
    return out


def _episode_pass(reps: list[list[Mapping[str, Any]]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for rep in reps:
        for rec in rep:
            out[str(rec.get("session_id"))].append(1.0 if rec.get("verdict") == "PASA" else 0.0)
    return out


def _check_pass(reps: list[list[Mapping[str, Any]]]) -> dict[str, dict[str, list[float]]]:
    out: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for rep in reps:
        for rec in rep:
            for cid, v in arm_row(rec)["checks"].items():
                if v in _DECIDED:
                    out[cid][str(rec.get("session_id"))].append(1.0 if v == "pasa" else 0.0)
    return out


def diff_entry(
    base_arm: str,
    cand_arm: str,
    base_reps: list[list[Mapping[str, Any]]],
    cand_reps: list[list[Mapping[str, Any]]],
) -> dict[str, Any]:
    base_checks, cand_checks = _check_pass(base_reps), _check_pass(cand_reps)
    checks = []
    for cid in sorted(base_checks.keys() | cand_checks.keys()):
        boot = paired_bootstrap(base_checks.get(cid, {}), cand_checks.get(cid, {}), iterations=_ITERATIONS_CHECKS)
        if boot["sessions"]:
            checks.append({"check_id": cid, **boot})
    return {
        "base": base_arm,
        "cand": cand_arm,
        "episode_pass": paired_bootstrap(_episode_pass(base_reps), _episode_pass(cand_reps)),
        "pass_k": {"base": pass_k(base_reps), "cand": pass_k(cand_reps)},
        "checks": checks,
        "changed_turns": changed_turns(base_reps[0] if base_reps else [], cand_reps[0] if cand_reps else []),
    }
