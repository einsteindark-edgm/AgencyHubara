"""Capability `ctwa-report` (reporter). El render determinista (tabla markdown + verdict
del periodo) es un nodo PURO; la narrativa interpretativa es un nodo LLM marcado (G1+,
temperature=0, structured output) que NUNCA computa — cita los números del analyzer. Acá
va el run PURO (sin LLM): números undefined → "—", nunca un valor adivinado. Las fechas
sin match se LISTAN (no se ocultan).

- run(input, *, ports, tools) — PURA (G-RUN-SIG).
- build()                     — StateGraph con el nodo LLM aislado (G1+).
"""
from __future__ import annotations

_DASH = "—"


def _fmt(v: str | None) -> str:
    return _DASH if v is None else str(v)


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    days = input.get("days", [])
    period = input.get("period")
    unmatched = input.get("unmatched", {"meta_only": [], "sales_only": []})
    qa_passed = input.get("qa_passed")  # MF-4: el verdict del no-self-review gobierna la CONFIANZA
    campaigns = input.get("campaigns", [])  # el embudo por-campaña (complementado); opcional

    lines = ["## Hubara — Ads Analytics (CTWA)", ""]
    if qa_passed is False:  # el QA detectó que los números no reconcilian → marcar el reporte
        lines += ["> **[ALERTA] QA NO RECONCILIA — números no confiables; no actúes sobre este reporte.**", ""]
    lines.append("| Fecha | Spend | Conv | Drop-off | MER | Órdenes | Recomendación |")
    lines.append("|---|--:|--:|--:|--:|--:|---|")
    for d in days:
        m = d["metrics"]
        lines.append(
            f"| {d['date']} | {d['spend_cop']} | {d['conversations_started']} | "
            f"{_fmt(m['drop_off_rate'])} | {_fmt(m['mer'])} | {d['total_orders']} | "
            f"{d['diagnosis']['recommendation']} |"
        )

    verdict = period["diagnosis"]["recommendation"] if period else "insufficient_data"
    if period:
        m = period["metrics"]
        lines += ["", f"**Diagnóstico del periodo:** {verdict} "
                  f"(MER {_fmt(m['mer'])}, drop-off {_fmt(m['drop_off_rate'])})"]
    else:
        lines += ["", "**Diagnóstico del periodo:** sin días en común (no hay blend posible)."]

    if unmatched["meta_only"] or unmatched["sales_only"]:
        lines += ["", f"**Fechas sin match (excluidas del blend):** "
                  f"solo-Meta {unmatched['meta_only']} · solo-Ventas {unmatched['sales_only']}"]

    if campaigns:  # el embudo por-campaña: cada fila con su FUENTE auditable (no oculta el complemento)
        lines += ["", "## Embudo por campaña (CTWA)", "",
                  "| Campaña | Objetivo | Spend | Clicks | Conversaciones | Fuente |",
                  "|---|---|--:|--:|--:|---|"]
        for c in campaigns:
            src = c.get("conversation_source", "none")
            flag = "" if src in ("insights", "entities") else " ⚠ sin señal"
            lines.append(
                f"| {c.get('campaign_name', '')} | {c.get('objective', '')} | "
                f"{c.get('spend_cop', 0)} | {c.get('link_clicks', 0)} | "
                f"{c.get('conversations', 0)} | {src}{flag} |"
            )

    if qa_passed is not None:
        lines += ["", f"**QA (no-self-review):** {'reconcilia' if qa_passed else 'NO reconcilia — revisar'}"]
    return {"markdown": "\n".join(lines), "verdict": verdict, "qa_passed": qa_passed}


def build():
    """`StateGraph` LangGraph (G1) — single-node que REUSA el `run()` puro: el render
    determinista (tabla + verdict + embudo por-campaña). El nodo LLM narrativo (cita los
    números, no computa) es **G1.x** — todavía no se cablea. La lógica vive UNA vez (G-DET);
    AgentSpan lo corre por passthrough. Durabilidad: el grafo entero como UNA task (L-11)."""
    try:
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e

    class State(TypedDict, total=False):
        days: list          # blended-economics
        period: dict        # blended-economics (None si no hay días en común)
        unmatched: dict     # blended-economics
        qa_passed: bool     # numbers-qa (gobierna la CONFIANZA del reporte, MF-4)
        campaigns: list     # ctwa-campaign-funnel (el embudo por-campaña; opcional)
        markdown: str       # ← run()
        verdict: str        # ← run()

    def report(state: State) -> dict:
        return run(dict(state))

    g = StateGraph(State)
    g.add_node("report", report)
    g.add_edge(START, "report")
    g.add_edge("report", END)
    return g.compile(name="ctwa-report")
