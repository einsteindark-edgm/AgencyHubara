"""Generador del tablero SigNoz "Calidad del LLM (Asesor de Ventas)".

Emite `05-calidad-llm.json` (mismo formato builder-query que los otros 4 tableros).
Visualiza los scores de evaluación que el harness emite a SigNoz:
  * métrica `gen_ai.eval.score`        (por métrica de calidad, atributo `metric.name`)
  * métrica `gen_ai.eval.conversation` (promedio por conversación, atributo `candidate`)
  * spans del servicio `sales-eval-agent` (costo del juez + drill-down por conversación)

⚠️ VERIFICACIÓN: las métricas `gen_ai.eval.*` son NUEVAS y no existen en SigNoz
hasta la primera corrida del harness. Tras ese primer run, abrí cada panel en la
UI de SigNoz y confirmá que el `metric type` (Histogram) y el space aggregation
quedaron bien autodetectados (SigNoz los completa solo). Es un scaffold listo
para importar; el fine-tuning final del panel se hace en la UI en 5 minutos.

Uso:  cd hubara_agency && uv run python deploy/signoz/dashboards/gen_calidad_llm.py
"""
from __future__ import annotations

import json
from pathlib import Path

_OUT = Path(__file__).parent / "05-calidad-llm.json"


def metric_attr(key: str, mtype: str = "Histogram") -> dict:
    return {
        "key": key, "dataType": "float64", "type": mtype,
        "isColumn": True, "isJSON": False,
        "id": f"{key}--float64--{mtype}--true",
    }


def tag_key(key: str, dtype: str = "string") -> dict:
    return {
        "key": key, "dataType": dtype, "type": "tag",
        "isColumn": False, "isJSON": False,
        "id": f"{key}--{dtype}--tag--false",
    }


def _filter_items(filters: list[tuple[str, str, str]]) -> list[dict]:
    out = []
    for i, (k, op, val) in enumerate(filters):
        out.append({"id": f"f-{k}-{i}", "key": tag_key(k), "op": op, "value": val})
    return out


def metric_q(metric: str, op: str, *, group=None, filters=None,
             legend="", reduce="avg", mtype="Histogram") -> dict:
    return {
        "dataSource": "metrics", "queryName": "A", "aggregateOperator": op,
        "aggregateAttribute": metric_attr(metric, mtype),
        "timeAggregation": op, "spaceAggregation": op, "functions": [],
        "filters": {"op": "AND", "items": _filter_items(filters or [])},
        "expression": "A", "disabled": False, "stepInterval": 60, "having": [],
        "limit": None, "orderBy": [],
        "groupBy": [tag_key(g) for g in (group or [])],
        "legend": legend, "reduceTo": reduce,
    }


def trace_q(attr: str, op: str, *, filters=None, legend="", reduce="sum") -> dict:
    a = {"key": attr, "dataType": "float64", "type": "tag",
         "isColumn": False, "isJSON": False, "id": f"{attr}--float64--tag--false"}
    return {
        "dataSource": "traces", "queryName": "A", "aggregateOperator": op,
        "aggregateAttribute": a, "timeAggregation": op, "spaceAggregation": op,
        "functions": [],
        "filters": {"op": "AND", "items": _filter_items(filters or [])},
        "expression": "A", "disabled": False, "stepInterval": 60, "having": [],
        "limit": None, "orderBy": [], "groupBy": [], "legend": legend,
        "reduceTo": reduce,
    }


def widget(wid: str, title: str, desc: str, panel: str, qdata: dict) -> dict:
    return {
        "id": wid, "title": title, "description": desc, "isStacked": False,
        "nullZeroValues": "zero", "opacity": "1", "panelTypes": panel,
        "query": {
            "queryType": "builder",
            "builder": {"queryData": [qdata], "queryFormulas": []},
            "promql": [{"name": "A", "query": "", "legend": "", "disabled": False}],
            "clickhouse_sql": [{"name": "A", "query": "", "legend": "", "disabled": False}],
            "id": wid,
        },
        "timePreferance": "GLOBAL_TIME", "softMax": 0, "softMin": 0,
        "selectedLogFields": [], "selectedTracesFields": [],
    }


_WIDGETS = [
    widget("v-score-por-metrica", "Score promedio por métrica",
           "Promedio de gen_ai.eval.score agrupado por métrica de calidad en el tiempo.",
           "graph", metric_q("gen_ai.eval.score", "avg", group=["metric.name"],
                             legend="{{metric.name}}")),
    widget("v-adherencia-guion", "Adherencia al guion (promedio)",
           "Score promedio de la métrica central script_adherence.",
           "value", metric_q("gen_ai.eval.score", "avg",
                             filters=[("metric.name", "=", "script_adherence")],
                             reduce="avg")),
    widget("v-score-conversacion", "Score global por conversación",
           "Promedio de gen_ai.eval.conversation (score agregado por conversación).",
           "graph", metric_q("gen_ai.eval.conversation", "avg",
                             legend="score promedio")),
    widget("v-passrate", "Conteo por veredicto y métrica",
           "Cantidad de evaluaciones pass/fail por métrica (pass rate).",
           "table", metric_q("gen_ai.eval.score", "count",
                             group=["metric.name", "verdict"],
                             legend="{{metric.name}} {{verdict}}", reduce="sum")),
    widget("v-candidatas", "Conversaciones candidatas a golden",
           "Conversaciones que cayeron bajo el umbral (candidate=true) → auto-curación.",
           "value", metric_q("gen_ai.eval.conversation", "count",
                             filters=[("candidate", "=", "true")], reduce="sum")),
    widget("v-costo-juez", "Costo del juez (USD)",
           "Costo LLM del servicio sales-eval-agent (el juez también se mide).",
           "value", trace_q("gen_ai.usage.cost", "sum",
                            filters=[("serviceName", "=", "sales-eval-agent")],
                            reduce="sum")),
]


def _layout() -> list[dict]:
    # 2 columnas (w=6), filas de h=4.
    lay = []
    for i, w in enumerate(_WIDGETS):
        lay.append({"i": w["id"], "x": (i % 2) * 6, "y": (i // 2) * 4, "w": 6, "h": 4,
                    "moved": False, "static": False})
    return lay


def build() -> dict:
    return {
        "title": "Calidad del LLM — Asesor de Ventas",
        "description": (
            "Evaluación de calidad del agente de ventas (DeepEval → SigNoz). "
            "Adherencia al guion, estilo, no-alucinación, handoff y avance de "
            "conversión, sobre tráfico real. ⚠️ Verificar metric type en la UI tras "
            "la primera corrida del harness (las métricas gen_ai.eval.* nacen ahí)."
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
