"""Capability `ctwa-report` (reporter). El render determinista (tabla markdown + verdict
del periodo) es un nodo PURO; la narrativa interpretativa es un nodo LLM marcado (G1+,
temperature=0, structured output) que NUNCA computa — cita los números del analyzer. Acá
va el run PURO (sin LLM): números undefined → "—", nunca un valor adivinado. Las fechas
sin match se LISTAN (no se ocultan).

- run(input, *, ports, tools) — PURA (G-RUN-SIG).
- build()                     — StateGraph con el nodo LLM aislado (G1+).
"""
from __future__ import annotations

import re

_DASH = "—"

# El nodo LLM narrativo: cita los números del analyzer, NUNCA computa. temperature=0.
_NARRATE_SYSTEM = (
    "Sos un analista de ads CTWA. Escribí una interpretación BREVE (máx 4 frases) en español "
    "rioplatense de los números que te paso. Reglas DURAS: (1) CITÁ solo los números EXACTOS que "
    "están en los datos; (2) NUNCA inventes ni calcules un número nuevo; (3) no uses voseo agresivo; "
    "(4) si el QA no reconcilia, advertí que los números no son confiables. Devolvé solo la prosa."
)
_NUM = re.compile(r"\d[\d.,]*\d|\d")


def _to_value(tok: str) -> float | None:
    """Parsea un token numérico a su VALOR, resolviendo separador de miles vs decimal (es/us)."""
    t = re.sub(r"[^\d.,]", "", tok)
    if not t:
        return None
    if "." in t and "," in t:                       # ambos: el ÚLTIMO separador es el decimal
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:                                   # un solo ',': grupos de 3 → miles, si no decimal
        parts = t.split(",")
        t = t.replace(",", "") if (len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0]) <= 3)) else t.replace(",", ".")
    elif "." in t:                                   # un solo '.': 120.000 = miles; 5.0 / 0.40 = decimal
        parts = t.split(".")
        if len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0]) <= 3):
            t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def _narrate_user(input: dict) -> str:
    """Los números del analyzer, serializados para el prompt (la MISMA data que renderiza run())."""
    period = input.get("period")
    verdict = period["diagnosis"]["recommendation"] if period else "insufficient_data"
    pm = period["metrics"] if period else {}
    lines = [f"diagnóstico del periodo: {verdict}",
             f"MER del periodo: {_fmt(pm.get('mer'))}",
             f"drop-off del periodo: {_fmt(pm.get('drop_off_rate'))}",
             f"QA reconcilia: {input.get('qa_passed')}"]
    for d in input.get("days", []):
        lines.append(f"- {d['date']}: spend {d['spend_cop']}, conversaciones {d['conversations_started']}, "
                     f"órdenes {d['total_orders']}, recomendación {d['diagnosis']['recommendation']}")
    for c in input.get("campaigns", []):
        lines.append(f"- campaña {c.get('campaign_name', '')}: spend {c.get('spend_cop', 0)}, "
                     f"clicks {c.get('link_clicks', 0)}, conversaciones {c.get('conversations', 0)} "
                     f"(fuente {c.get('conversation_source', 'none')})")
    return "\n".join(lines)


def invented_numbers(narrative: str, source: str) -> list[str]:
    """Guard anti-alucinación: los tokens numéricos del narrative cuyo VALOR no está en la fuente.
    Compara VALORES, no dígitos — así `0.40`↔`40%` y `120000`↔`$120.000`/`120.000,00` son el MISMO
    número, pero un `50%` inventado NO colisiona con MER `5.0` (el bug del guard por-dígitos, L-20).
    Tolera |v|<10 (ruido de prosa: '5 días', '3 campañas' — daño bajo). Es lo que hace CONFIABLE el
    nodo LLM: si inventa una cifra financiera, la caza."""
    src: set[float] = set()
    for tok in _NUM.findall(source):
        v = _to_value(tok)
        if v is not None:
            src |= {round(v, 6), round(v * 100, 6), round(v / 100, 6)}  # valor + ratio↔%
    out = []
    for tok in _NUM.findall(narrative):
        v = _to_value(tok)
        if v is None or abs(v) < 10:  # números chicos: ruido de prosa, se toleran
            continue
        if not any(abs(v - s) < 0.01 for s in src):
            out.append(tok)
    return sorted(set(out))


def narrate(input: dict, *, llm) -> dict:
    """Nodo LLM MARCADO: le pasa los números del analyzer al LLM (temperature=0) y devuelve la
    prosa. NO computa — el LLM solo interpreta. `llm` es el port (FixtureLLM en golden, LiteLLMProxy
    en real). El guard `invented_numbers` (en el test + disponible para el caller) prueba que no
    alucinó un número."""
    user = _narrate_user(input)
    text = llm.complete(system=_NARRATE_SYSTEM, user=user, temperature=0.0)
    return {"narrative": text.strip()}


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


def build(*, llm=None):
    """`StateGraph` LangGraph. El nodo `report` REUSA el `run()` PURO (render determinista: tabla
    + verdict + embudo). Si se inyecta un `llm` (port), agrega el nodo MARCADO `narrative` después
    — el único no-determinista, temperature=0, que cita los números y NO computa (G-DET intacto: el
    esqueleto es puro, el LLM está aislado en su nodo). **Opt-in**: sin `llm` el grafo es single-node
    (idéntico al previo) → el supervisor compuesto y el golden del render no cambian. El vendor real
    (`LiteLLMProxy`, deepseek-v4-flash vía el proxy del central) se instancia lazy si no se inyecta."""
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
        narrative: str      # ← narrate() (nodo LLM; solo si se inyecta `llm`)

    def report(state: State) -> dict:
        return run(dict(state))

    g = StateGraph(State)
    g.add_node("report", report)
    g.add_edge(START, "report")
    if llm is not None:
        def narrative_node(state: State) -> dict:
            return narrate(dict(state), llm=llm)
        g.add_node("narrative", narrative_node)
        g.add_edge("report", "narrative")
        g.add_edge("narrative", END)
    else:
        g.add_edge("report", END)
    return g.compile(name="ctwa-report")
