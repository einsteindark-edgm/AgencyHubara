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

    if qa_passed is not None:
        lines += ["", f"**QA (no-self-review):** {'reconcilia' if qa_passed else 'NO reconcilia — revisar'}"]
    return {"markdown": "\n".join(lines), "verdict": verdict, "qa_passed": qa_passed}


def build():
    try:
        from langgraph.graph import StateGraph  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e
    raise NotImplementedError("build(): cablear el StateGraph + el nodo LLM (G1+); el run puro ya está")
