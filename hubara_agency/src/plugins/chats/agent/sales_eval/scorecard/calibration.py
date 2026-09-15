"""Calibración del juez contra etiquetas humanas (HU-SC-4).

"Validar a los validadores" (Shankar et al., EvalGen; Hamel Husain): un check
de juez es un clasificador y se mide como tal contra lo que etiqueta el
operador. Positivo = `falla`.

  * TPR (recall de fallos) y TNR por separado: los fallos son raros y la
    precisión cruda engaña.
  * Kappa de Cohen: acuerdo corregido por azar.
  * Estado: `confiable` (n ≥ MIN_LABELS y kappa ≥ KAPPA_THRESHOLD) — solo
    entonces un check de juez crítico puede reprobar un episodio;
    `revisar` (n ≥ REVIEW_MIN y kappa bajo); `sin_datos` en otro caso.

Solo entran a la matriz los pares donde el juez decidió (`pasa`/`falla`) y el
humano también. La última etiqueta humana de un check en un episodio gana.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from src.plugins.chats.agent.sales_eval.scorecard.registry import CHECKS, SPECS_BY_ID
from src.plugins.chats.agent.sales_eval.scorecard.store import latest_by_unit, latest_labels

MIN_LABELS = 50
REVIEW_MIN = 10
KAPPA_THRESHOLD = 0.6

_DECIDED = ("pasa", "falla")


def _judge_verdicts(records: Iterable[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for rec in latest_by_unit(records):
        for r in rec.get("results") or []:
            if isinstance(r, dict) and r.get("source") == "judge":
                key = (str(rec.get("session_id")), str(rec.get("episode_id")), str(r.get("check_id")))
                out[key] = r
    return out


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _kappa(tp: int, fp: int, tn: int, fn: int) -> float | None:
    n = tp + fp + tn + fn
    if not n:
        return None
    po = (tp + tn) / n
    pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
    if pe >= 1:
        return 1.0 if po == 1 else None
    return round((po - pe) / (1 - pe), 4)


def compute_calibration(
    records: Iterable[dict[str, Any]], labels: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    judged = _judge_verdicts(records)
    human = latest_labels(labels)
    rows: list[dict[str, Any]] = []
    for spec in CHECKS:
        if spec.kind != "judge":
            continue
        tp = fp = tn = fn = 0
        for key, result in judged.items():
            if key[2] != spec.id:
                continue
            label = human.get(key)
            if label is None:
                continue
            j, h = result.get("verdict"), label.get("verdict")
            if j not in _DECIDED or h not in _DECIDED:
                continue
            if j == "falla" and h == "falla":
                tp += 1
            elif j == "falla":
                fp += 1
            elif h == "pasa":
                tn += 1
            else:
                fn += 1
        n = tp + fp + tn + fn
        kappa = _kappa(tp, fp, tn, fn)
        if n >= MIN_LABELS and kappa is not None and kappa >= KAPPA_THRESHOLD:
            status = "confiable"
        elif n >= REVIEW_MIN and (kappa is None or kappa < KAPPA_THRESHOLD):
            status = "revisar"
        else:
            status = "sin_datos"
        rows.append(
            {
                "check_id": spec.id,
                "name": spec.name,
                "level": spec.level,
                "n": n,
                "tp": tp,
                "fp": fp,
                "tn": tn,
                "fn": fn,
                "tpr": _rate(tp, tp + fn),
                "tnr": _rate(tn, tn + fp),
                "kappa": kappa,
                "status": status,
            }
        )
    return rows


def calibrated_checks(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {r["check_id"] for r in rows if r.get("status") == "confiable"}


def _in_sample(key: tuple[str, str, str], sample_every: int) -> bool:
    digest = hashlib.sha256("|".join(key).encode("utf-8")).digest()
    return digest[0] % max(1, sample_every) == 0


def label_queue(
    records: Iterable[dict[str, Any]],
    labels: Iterable[dict[str, Any]],
    *,
    limit: int = 20,
    sample_every: int = 5,
) -> list[dict[str, Any]]:
    """Qué etiquetar primero: dudas del juez, luego sus fallos, luego una
    muestra determinista de sus `pasa` (sin muestra no se mide la TNR)."""
    human = latest_labels(labels)
    buckets: dict[str, list[dict[str, Any]]] = {"desconocido": [], "falla": [], "muestra": []}
    for key, result in sorted(_judge_verdicts(records).items()):
        if key in human:
            continue
        verdict = result.get("verdict")
        if verdict == "desconocido":
            reason = "desconocido"
        elif verdict == "falla":
            reason = "falla"
        elif verdict == "pasa" and _in_sample(key, sample_every):
            reason = "muestra"
        else:
            continue
        spec = SPECS_BY_ID.get(key[2])
        buckets[reason].append(
            {
                "session_id": key[0],
                "episode_id": key[1],
                "check_id": key[2],
                "check_name": spec.name if spec else key[2],
                "judge_verdict": verdict,
                "reason": reason,
                "evidence": result.get("evidence") or "",
                "critique": result.get("critique") or "",
            }
        )
    ordered = buckets["desconocido"] + buckets["falla"] + buckets["muestra"]
    return ordered[: max(0, limit)]
