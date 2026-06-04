"""Generador del tablero SigNoz "Calidad del LLM (Asesor de Ventas)".

Emite `05-calidad-llm.json`. Visualiza los scores de evaluación que el harness
emite a SigNoz como **histograma OTel** `gen_ai.eval.score` /
`gen_ai.eval.conversation`. SigNoz explota el histograma en componentes
(`.sum` y `.count` = Sum acumulativo; `.min`/`.max` = Gauge; `.bucket`).

Claves de las queries (descubiertas verificando contra la API de SigNoz):
  * El score PROMEDIO por métrica = `sum(.sum) / sum(.count)` agrupado por
    `metric.name` → fórmula `A/B`.
  * Se usa `timeAggregation: "latest"` (NO `rate`/`increase`): los datos son
    acumulativos y pueden venir de UN solo run (rate necesita ≥2 puntos → vacío).
    `latest` toma el valor acumulado y funciona con data esparsa o continua.

Uso:  cd hubara_agency && uv run python deploy/signoz/dashboards/gen_calidad_llm.py
Importar a SigNoz:  SIGNOZ_API_KEY=<PAT> python deploy/signoz/dashboards/import_dashboards.py
"""
from __future__ import annotations

import json
from pathlib import Path

_OUT = Path(__file__).parent / "05-calidad-llm.json"


def tag_key(key: str, dtype: str = "string") -> dict:
    return {"key": key, "dataType": dtype, "type": "tag", "isColumn": False,
            "isJSON": False, "id": f"{key}--{dtype}--tag--false"}


def metric_attr(metric: str, mtype: str) -> dict:
    return {"key": metric, "dataType": "float64", "type": mtype, "isColumn": True,
            "isJSON": False, "id": f"{metric}--float64--{mtype}--true"}


def _filter_items(filters):
    out = []
    for i, (k, op, val) in enumerate(filters or []):
        out.append({"id": f"f-{k}-{i}", "key": tag_key(k), "op": op, "value": val})
    return out


def comp(qn: str, metric: str, mtype: str, time_agg: str, space_agg: str,
         group=None, filters=None, disabled=False, legend="") -> dict:
    """Query de un componente de métrica (Sum/Gauge)."""
    return {
        "queryName": qn, "dataSource": "metrics", "aggregateOperator": time_agg,
        "aggregateAttribute": metric_attr(metric, mtype),
        "temporality": "Cumulative" if mtype == "Sum" else "",
        "timeAggregation": time_agg, "spaceAggregation": space_agg, "functions": [],
        "filters": {"op": "AND", "items": _filter_items(filters)},
        "expression": qn, "disabled": disabled, "stepInterval": 60, "having": [],
        "limit": None, "orderBy": [], "groupBy": [tag_key(g) for g in (group or [])],
        "legend": legend, "reduceTo": "last",
    }


def trace_cost() -> dict:
    a = {"key": "gen_ai.usage.cost", "dataType": "float64", "type": "tag",
         "isColumn": False, "isJSON": False, "id": "gen_ai.usage.cost--float64--tag--false"}
    return {
        "queryName": "A", "dataSource": "traces", "aggregateOperator": "sum",
        "aggregateAttribute": a, "timeAggregation": "sum", "spaceAggregation": "sum",
        "functions": [], "filters": {"op": "AND", "items": _filter_items(
            [("serviceName", "=", "sales-eval-agent")])},
        "expression": "A", "disabled": False, "stepInterval": 60, "having": [],
        "limit": None, "orderBy": [], "groupBy": [], "legend": "costo juez",
        "reduceTo": "sum",
    }


def widget(wid: str, title: str, desc: str, panel: str, qdata: list[dict],
           formulas: list[dict] | None = None) -> dict:
    return {
        "id": wid, "title": title, "description": desc, "isStacked": False,
        "nullZeroValues": "zero", "opacity": "1", "panelTypes": panel,
        "query": {
            "queryType": "builder",
            "builder": {"queryData": qdata, "queryFormulas": formulas or []},
            "promql": [{"name": "A", "query": "", "legend": "", "disabled": False}],
            "clickhouse_sql": [{"name": "A", "query": "", "legend": "", "disabled": False}],
            "id": wid,
        },
        "timePreferance": "GLOBAL_TIME", "softMax": 1, "softMin": 0,
        "selectedLogFields": [], "selectedTracesFields": [],
    }


def _avg_formula(legend: str) -> list[dict]:
    return [{"queryName": "F1", "expression": "A/B", "legend": legend,
             "disabled": False}]


_WIDGETS = [
    # Score promedio por métrica = sum(score.sum)/sum(score.count) by metric.name.
    widget(
        "v-score-metrica", "Score promedio por métrica de calidad",
        "Promedio del score (0..1) por métrica. A=sum, B=count, F1=A/B.",
        "table",
        [comp("A", "gen_ai.eval.score.sum", "Sum", "latest", "sum", ["metric.name"], disabled=True),
         comp("B", "gen_ai.eval.score.count", "Sum", "latest", "sum", ["metric.name"], disabled=True)],
        _avg_formula("{{metric.name}}"),
    ),
    # Conteo de evaluaciones por métrica y veredicto (pass/fail).
    widget(
        "v-conteo-veredicto", "Evaluaciones por métrica y veredicto",
        "Cantidad de evals pass/fail por métrica (pass rate).",
        "table",
        [comp("A", "gen_ai.eval.score.count", "Sum", "latest", "sum",
              ["metric.name", "verdict"], legend="{{metric.name}} · {{verdict}}")],
    ),
    # Score promedio global por conversación.
    widget(
        "v-score-conversacion", "Score promedio por conversación",
        "Promedio agregado por conversación (gen_ai.eval.conversation).",
        "value",
        [comp("A", "gen_ai.eval.conversation.sum", "Sum", "latest", "sum", [], disabled=True),
         comp("B", "gen_ai.eval.conversation.count", "Sum", "latest", "sum", [], disabled=True)],
        _avg_formula("score promedio"),
    ),
    # Conversaciones candidatas a golden (candidate=true).
    widget(
        "v-candidatas", "Conversaciones evaluadas (por candidato a golden)",
        "Conteo de conversaciones; candidate=true → fueron a auto-curación.",
        "table",
        [comp("A", "gen_ai.eval.conversation.count", "Sum", "latest", "sum",
              ["candidate"], legend="candidate={{candidate}}")],
    ),
    # Costo del juez (traces — el juez también se mide).
    widget(
        "v-costo-juez", "Costo del juez (USD)",
        "Costo LLM del servicio sales-eval-agent (el juez también se instrumenta).",
        "value", [trace_cost()],
    ),
]


def _layout() -> list[dict]:
    lay = []
    for i, w in enumerate(_WIDGETS):
        lay.append({"i": w["id"], "x": (i % 2) * 6, "y": (i // 2) * 4, "w": 6, "h": 4,
                    "moved": False, "static": False})
    return lay


def build() -> dict:
    return {
        "title": "Calidad del LLM — Asesor de Ventas",
        "description": (
            "Evaluación de calidad del agente de ventas (DeepEval → SigNoz): "
            "adherencia al guion, estilo, no-alucinación, handoff y conversión. "
            "Las métricas gen_ai.eval.* nacen cuando corre el harness (worker "
            "sales_eval). Si un panel se ve vacío, ampliá el rango de tiempo."
        ),
        "tags": ["hubara", "llm", "evals", "calidad", "sales"],
        "layout": _layout(),
        "widgets": _WIDGETS,
        "variables": {},
        "version": "v4",
    }


if __name__ == "__main__":
    _OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[gen] escrito {_OUT} ({_OUT.stat().st_size} bytes, {len(_WIDGETS)} widgets)")
