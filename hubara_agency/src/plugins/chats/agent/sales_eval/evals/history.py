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


_REASON_MAX_CHARS = 400
"""Las razones del juez pueden ser párrafos — se truncan para que el JSONL del
histórico no engorde sin límite (la razón completa vive en SigNoz)."""


def append_history_record(
    history_dir: Path,
    *,
    run_date: str,
    session_id: str,
    suite: str,
    scores: list[tuple[str, float, bool, str]],
    episode_id: str = "",
    ts: str = "",
    is_candidate: bool = False,
) -> None:
    """Appendea UN registro (una evaluación de un episodio/conversación) al JSONL
    del día. Best-effort: si falla la escritura, NO rompe la evaluación (lo llama
    una activity).

    Además del mapa `metrics` (compat con registros viejos), persiste lo que la
    UI por-episodio necesita: `episode_id` (vacío = sesión entera legacy), `ts`
    (hora ISO de la corrida — distingue re-evals del mismo día), `avg`/`passed`
    derivados, `is_candidate`, y las razones del juez de las métricas FALLADAS
    (`failed`) para responder "¿por qué puntuó bajo?" sin ir a SigNoz.
    """
    metrics = {k: round(float(s), 4) for (k, s, _ok, _r) in scores}
    avg = round(sum(metrics.values()) / len(metrics), 4) if metrics else 0.0
    record = {
        "date": run_date,
        "ts": ts,
        "session_id": session_id,
        "episode_id": episode_id,
        "suite": suite,
        "metrics": metrics,
        "avg": avg,
        "passed": all(ok for (_k, _s, ok, _r) in scores) if scores else False,
        "is_candidate": is_candidate,
        "failed": [
            {"metric": k, "score": round(float(s), 4), "reason": r[:_REASON_MAX_CHARS]}
            for (k, s, ok, r) in scores
            if not ok
        ],
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


def read_evaluated_session_episodes(history_dir: Path) -> set[tuple[str, str]]:
    """`(session_id, episode_id)` de TODOS los registros del histórico (cualquier día).

    Para el backfill: saber qué episodios ya tienen al menos una eval, y NO
    re-evaluarlos (ni gastar juez ni duplicar registros). Escanea todos los
    `*.jsonl` del dir sin filtrar por fecha. `episode_id` ausente = "" (sesión
    entera legacy). Solo stdlib (la API/scripts lo importan sin el extra deepeval).
    """
    out: set[tuple[str, str]] = set()
    if not history_dir.exists():
        return out
    for p in sorted(history_dir.glob("*.jsonl")):
        try:
            for line in p.read_text("utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                sid = str(r.get("session_id", ""))
                if sid:
                    out.add((sid, str(r.get("episode_id", "") or "")))
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


def _record_avg(record: dict[str, Any]) -> float | None:
    """`avg` del registro; registros legacy (sin `avg`) lo derivan de `metrics`."""
    avg = record.get("avg")
    if isinstance(avg, (int, float)):
        return round(float(avg), 4)
    metrics = record.get("metrics") or {}
    vals = [float(v) for v in metrics.values() if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 4) if vals else None


def _trend(avgs: list[float], *, epsilon: float = 0.02) -> str:
    """Evolución del score de la conversación entre su primera y última eval.

    `single` = una sola eval (sin historia todavía); `up`/`down` solo si el
    delta supera `epsilon` (mismo criterio que la flecha del trend chart).
    """
    if len(avgs) < 2:
        return "single"
    delta = avgs[-1] - avgs[0]
    if delta > epsilon:
        return "up"
    if delta < -epsilon:
        return "down"
    return "flat"


def is_flagged_conversation(conv: dict[str, Any], threshold: float) -> bool:
    """¿La conversación amerita atención del operador?

    `last_avg < threshold` (score genuinamente bajo) O `is_candidate` (el último
    eval marcó candidato a golden — incluye fallas de métrica CRÍTICA como
    no_hallucination=0, que el promedio podía esconder; ver `curation`).

    Reemplaza el viejo criterio `not last_passed` ("falló cualquiera de las 9
    métricas estrictas"), que era ruidoso: un episodio de 0.96 con un tono
    apenas bajo (role_adherence 0.66) marcaba "falla" sin ser un problema real.
    Caso ep_011 (run 24d061f6): happy path impecable, avg 0.963, aparecía como
    falla. UNA definición que comparten el orden (acá), el badge de alerta y el
    filtro "solo con fallas" del frontend (`isFlaggedEpisode`)."""
    avg = conv.get("last_avg")
    below = isinstance(avg, (int, float)) and avg < threshold
    return below or bool(conv.get("is_candidate"))


def read_conversation_evals(
    history_dir: Path,
    *,
    dates: list[str],
    suite: str = "online",
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Las evaluaciones agrupadas POR CONVERSACIÓN (sesión + episodio).

    Es la vista que responde las preguntas del operador que el agregado por día
    no puede: ¿QUÉ conversación generó los malos scores? ¿mejoró entre evals o
    quedó igual? Cada grupo trae su timeline de evals (ordenado cronológicamente)
    y derivados listos para la UI (`last_avg`, `trend`, `failed_metrics`).

    Tolerante a registros legacy (sin episode_id/avg/failed): episodio "" se
    presenta como sesión entera; `passed` se deriva de avg vs threshold.
    """
    records = [r for r in _read_records(history_dir, dates=dates) if r.get("suite") == suite]

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in records:
        session_id = str(r.get("session_id", ""))
        if not session_id:
            continue
        episode_id = str(r.get("episode_id", "") or "")
        avg = _record_avg(r)
        if avg is None:
            continue
        passed = r.get("passed")
        evaluation = {
            "date": str(r.get("date", "")),
            "ts": str(r.get("ts", "") or ""),
            "avg": avg,
            "passed": bool(passed) if isinstance(passed, bool) else avg >= threshold,
            "is_candidate": bool(r.get("is_candidate", False)),
            "metrics": {
                k: round(float(v), 4)
                for k, v in (r.get("metrics") or {}).items()
                if isinstance(v, (int, float))
            },
            "failed": [
                {
                    "metric": str(f.get("metric", "")),
                    "score": float(f.get("score", 0.0)),
                    "reason": str(f.get("reason", "")),
                }
                for f in (r.get("failed") or [])
                if isinstance(f, dict)
            ],
        }
        groups.setdefault((session_id, episode_id), []).append(evaluation)

    conversations = []
    for (session_id, episode_id), evals in groups.items():
        evals.sort(key=lambda e: (e["date"], e["ts"]))
        avgs = [e["avg"] for e in evals]
        last = evals[-1]
        conversations.append(
            {
                "session_id": session_id,
                "episode_id": episode_id,
                "evals": evals,
                "evals_count": len(evals),
                "first_avg": avgs[0],
                "last_avg": last["avg"],
                "last_date": last["date"],
                "last_ts": last["ts"],
                "last_passed": last["passed"],
                "is_candidate": last["is_candidate"],
                "trend": _trend(avgs),
                "failed_metrics": [f["metric"] for f in last["failed"]],
            }
        )

    # Más recientes primero; luego los flagged arriba (avg bajo umbral o
    # candidato a golden — los que el operador necesita ver). NO `last_passed`:
    # ese marcaba arriba a un 0.96 que falló una métrica de tono (ruido).
    conversations.sort(key=lambda c: (c["last_date"], c["last_ts"]), reverse=True)
    conversations.sort(key=lambda c: not is_flagged_conversation(c, threshold))

    return {
        "threshold": threshold,
        "suite": suite,
        "count": len(conversations),
        "conversations": conversations,
    }
