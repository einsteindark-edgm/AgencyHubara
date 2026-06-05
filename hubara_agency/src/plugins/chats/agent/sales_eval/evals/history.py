"""Histórico de scores de evaluación — para la TENDENCIA en el frontend.

Append-only: cada conversación evaluada deja un registro `{date, session_id, suite,
metrics:{...}}` en `<history_dir>/<date>.jsonl`. La API lo lee y agrega por métrica
por día (avg/min/n/n_below) para que el frontend dibuje "tal fecha la métrica estuvo
bajo, tal otra fecha mejoró/empeoró".

Es DELIBERADAMENTE simple (JSONL local en el volumen del vault) y desacopla el
frontend de SigNoz: el front pega al backend, el backend lee este store. SigNoz sigue
siendo la observabilidad profunda; esto es la vista liviana de tendencia.

Solo stdlib (json/pathlib) — sin deepeval, así que la API lo importa sin el extra.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_THRESHOLD = 0.7


def append_history_record(
    history_dir: Path,
    *,
    run_date: str,
    session_id: str,
    suite: str,
    scores: list[tuple[str, float, bool, str]],
) -> None:
    """Appendea UN registro (una conversación) al JSONL del día. Best-effort: si
    falla la escritura, NO rompe la evaluación (lo llama una activity)."""
    record = {
        "date": run_date,
        "session_id": session_id,
        "suite": suite,
        "metrics": {k: round(float(s), 4) for (k, s, _ok, _r) in scores},
    }
    try:
        history_dir.mkdir(parents=True, exist_ok=True)
        with (history_dir / f"{run_date}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _read_records(history_dir: Path, *, dates: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for d in dates:
        p = history_dir / f"{d}.jsonl"
        if not p.exists():
            continue
        try:
            for line in p.read_text("utf-8").splitlines():
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def read_trend(
    history_dir: Path,
    *,
    dates: list[str],
    suite: str = "online",
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Agrega el histórico en una serie por métrica por día.

    `dates` = lista de fechas (YYYY-MM-DD) a incluir (las calcula el caller; lo
    dejamos inyectable para que sea determinista y testeable, sin `now()` acá).

    Devuelve:
        {threshold, suite, metrics: [...], series: [
            {metric, points: [{date, avg, min, n, n_below}, ...]}, ...]}
    """
    records = [r for r in _read_records(history_dir, dates=dates) if r.get("suite") == suite]

    # acumular: (metric, date) -> [scores]
    bucket: dict[str, dict[str, list[float]]] = {}
    for r in records:
        date = r.get("date", "")
        for metric, score in (r.get("metrics") or {}).items():
            if not isinstance(score, (int, float)):
                continue
            bucket.setdefault(metric, {}).setdefault(date, []).append(float(score))

    series = []
    for metric in sorted(bucket):
        points = []
        for date in sorted(bucket[metric]):
            vals = bucket[metric][date]
            n = len(vals)
            points.append({
                "date": date,
                "avg": round(sum(vals) / n, 3),
                "min": round(min(vals), 3),
                "n": n,
                "n_below": sum(1 for v in vals if v < threshold),
            })
        series.append({"metric": metric, "points": points})

    return {
        "threshold": threshold,
        "suite": suite,
        "metrics": [s["metric"] for s in series],
        "series": series,
    }
