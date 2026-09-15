"""Agregados del scorecard (HU-SC-3) — lo que escala a cientos de episodios.

  * **Pareto**: fallos por check, de mayor a menor (paso 4 del análisis de
    errores: contar cuántas veces ocurre cada modo de fallo).
  * **Tendencia**: tasa de cumplimiento semanal por check (lunes ISO de la
    fecha del episodio, o de la evaluación si el registro no la trae). Solo
    cuentan `pasa` y `falla`; sin episodios decididos la tasa es `None`.
  * **Embudo**: etapa final del episodio × veredicto.

Entrada: filas de `store.list_scorecards` (último registro por episodio, con
el mapa `checks`). Funciones puras.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date as _date
from datetime import timedelta
from typing import Any

from src.plugins.chats.agent.sales_eval.scorecard.registry import SPECS_BY_ID

STAGE_ORDER = (
    "descubrimiento", "variantes", "confirmacion", "datos_envio", "cierre", "postcierre", "sin_etapa",
)
VERDICTS = ("FALLA", "ALERTA", "PASA", "SIN_DATOS")


def week_start(iso_date: str) -> str:
    d = _date.fromisoformat(iso_date[:10])
    return (d - timedelta(days=d.weekday())).isoformat()


def weeks_between(start_iso: str, end_iso: str) -> list[str]:
    cur = _date.fromisoformat(week_start(start_iso))
    end = _date.fromisoformat(week_start(end_iso))
    out: list[str] = []
    while cur <= end:
        out.append(cur.isoformat())
        cur += timedelta(days=7)
    return out


def compute_stats(rows: list[dict[str, Any]], *, weeks: list[str]) -> dict[str, Any]:
    failures: Counter[str] = Counter()
    per_week: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    funnel: dict[str, Counter[str]] = defaultdict(Counter)
    verdicts: Counter[str] = Counter()
    seen_checks: set[str] = set()

    for row in rows:
        verdict = str(row.get("verdict") or "SIN_DATOS")
        verdicts[verdict] += 1
        funnel[str(row.get("stage_final") or "sin_etapa")][verdict] += 1
        # Semana del EPISODIO (cuándo ocurrió), no de la evaluación: el
        # backfill califica hoy conversaciones de hace meses.
        week = week_start(str(row.get("episode_date") or row.get("date") or "1970-01-01"))
        for check_id, v in (row.get("checks") or {}).items():
            if check_id not in SPECS_BY_ID:
                continue
            seen_checks.add(check_id)
            if v == "falla":
                failures[check_id] += 1
            if v in ("pasa", "falla"):
                bucket = per_week[check_id][week]
                bucket[0] += 1
                bucket[1] += 1 if v == "pasa" else 0

    pareto = [
        {
            "check_id": cid,
            "name": SPECS_BY_ID[cid].name,
            "level": SPECS_BY_ID[cid].level,
            "failures": n,
        }
        for cid, n in sorted(failures.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    trend = []
    for cid in sorted(seen_checks):
        points = []
        for w in weeks:
            applicable, passed = per_week[cid].get(w, [0, 0])
            points.append(
                {
                    "week": w,
                    "applicable": applicable,
                    "passed": passed,
                    "rate": round(passed / applicable, 4) if applicable else None,
                }
            )
        trend.append(
            {"check_id": cid, "name": SPECS_BY_ID[cid].name, "level": SPECS_BY_ID[cid].level, "weeks": points}
        )
    stage_rank = {s: i for i, s in enumerate(STAGE_ORDER)}
    funnel_rows = [
        {"stage": stage, **{v: counts.get(v, 0) for v in VERDICTS}}
        for stage, counts in sorted(funnel.items(), key=lambda kv: stage_rank.get(kv[0], 99))
    ]
    return {
        "episodes": len(rows),
        "verdicts": {v: verdicts.get(v, 0) for v in VERDICTS},
        "pareto": pareto,
        "trend": trend,
        "funnel": funnel_rows,
    }
