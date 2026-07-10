"""Capability `window-strategist` (analyzer). Esqueleto PURO (G-DET): recibe el
snapshot de conversaciones (lo deposita hubara — el agente NO pega red, G-PORT),
clasifica con la tool `parse-conversations` y arma la lista de intents de
reactivación bajo el guardrail autónomo (cadencia + tope de toques pagos).

SIN HITL: el output del run() ES la lista de dispatch — cero tools outward
(G-DUR sin sujeto). El gasto real lo ejecuta hubara re-validando cada intent
con `decide_reengagement` (la autoridad). `now` viaja en el payload (L-1).

- run(input, *, ports, tools) — PURA, para LocalRuntime / golden-replay (G-RUN-SIG).
- build()                     — el StateGraph LangGraph (G1+), reusa la lógica (L-11).
"""
from __future__ import annotations

#: Guardrail por default (override por `payload.policy`). Conservador: pocas
#: repeticiones por lead y tope duro de toques PAGOS por ciclo (no runaway).
DEFAULT_MAX_TOUCHES_PER_24H = 2
DEFAULT_MAX_PAID_PER_CYCLE = 5

_DAY_MS = 24 * 60 * 60 * 1000

#: Orden por valor esperado (menor = primero): gancho transaccional y carril
#: gratis de 72h primero; el marketing pago opt-in al final.
_REASON_RANK = {
    "transactional_hook": 0,
    "ctwa_72h_free": 1,
    "csw_free_form": 2,
    "phase_b_high_value": 3,
}

#: Reasons que cuestan plata (fase B — fuera de ventanas gratis). Consumen el
#: presupuesto de toques pagos del ciclo.
_PAID_REASONS = frozenset({"transactional_hook", "phase_b_high_value"})


def _recent_touch_count(convo: dict, now_ms: int) -> int:
    return sum(
        1
        for t in (convo.get("recent_touches") or [])
        if isinstance(t.get("at_ms"), int) and t["at_ms"] > now_ms - _DAY_MS
    )


def _plan(payload: dict, classified: list[dict]) -> dict:
    """El guardrail autónomo (reemplazo del approval humano): cadencia por
    lead + tope duro de pagos por ciclo + orden por valor esperado. Lo
    truncado se reporta SIEMPRE (`suppressed` + `truncated_by_budget`)."""
    now_ms = payload["now_ms"]
    policy = payload.get("policy") or {}
    max_touches = policy.get("max_touches_per_24h", DEFAULT_MAX_TOUCHES_PER_24H)
    paid_budget = policy.get(
        "max_paid_dispatches_per_cycle", DEFAULT_MAX_PAID_PER_CYCLE
    )

    by_session = {c.get("session_id"): c for c in payload.get("conversations") or []}
    suppressed: list[dict] = []
    candidates: list[tuple[int, int, dict]] = []

    for idx, c in enumerate(classified):
        sid = c["session_id"]
        if c["action"] == "suppress":
            suppressed.append({"session_id": sid, "reason": c["reason"]})
            continue
        # Cadencia: no martillar al mismo lead aunque la ventana sea gratis.
        if _recent_touch_count(by_session.get(sid) or {}, now_ms) >= max_touches:
            suppressed.append({"session_id": sid, "reason": "cadence_cap"})
            continue
        candidates.append((_REASON_RANK.get(c["reason"], 9), idx, c))

    candidates.sort(key=lambda t: (t[0], t[1]))

    dispatch: list[dict] = []
    truncated = 0
    for _, _, c in candidates:
        if c["reason"] in _PAID_REASONS:
            if paid_budget <= 0:
                truncated += 1
                suppressed.append(
                    {"session_id": c["session_id"], "reason": "paid_budget_truncated"}
                )
                continue
            paid_budget -= 1
        dispatch.append(
            {
                "kind": "reengagement_dispatch",
                "session_id": c["session_id"],
                "recommended_channel": c["recommended_channel"],
                "recommended_category": c["recommended_category"],
                "reason": c["reason"],
                "priority": len(dispatch) + 1,
            }
        )

    return {
        "schema_version": 1,
        "snapshot_now_ms": now_ms,
        "dispatch": dispatch,
        "suppressed": suppressed,
        "truncated_by_budget": truncated,
    }


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    if "payload" not in input:  # MF-7: el seam hubara→agente falla con error de dominio
        raise ValueError(
            "window-strategist: falta 'payload' (el snapshot de conversaciones "
            "que deposita hubara)"
        )
    tools = tools or {}
    classify = tools["parse-conversations"]  # inyectada por el binding (G-BIND, L-24)
    payload = input["payload"]
    classified = classify(payload=payload)["conversations"]
    return _plan(payload, classified)


def build():
    """`StateGraph` LangGraph (G1) — cadena LINEAL ingest→classify→plan→dispatch
    (sin conditional edges, L-15; sin reducers custom, L-14). Cada nodo reusa
    los MISMOS helpers que `run()` (la lógica vive una vez, L-11). State
    anotada con builtins (L-13)."""
    try:
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e

    from tools.parse_conversations.impl import run as classify_tool

    class State(TypedDict, total=False):
        payload: dict     # el snapshot que deposita hubara (now_ms adentro, G-DET)
        classified: list  # ← classify (tool parse-conversations)
        result: dict      # ← plan/dispatch (la lista de intents = el output)

    def ingest(state: State) -> dict:
        if "payload" not in state:
            raise ValueError(
                "window-strategist: falta 'payload' (el snapshot de "
                "conversaciones que deposita hubara)"
            )
        return {}

    def classify(state: State) -> dict:
        out = classify_tool(payload=state["payload"])
        return {"classified": out["conversations"]}

    def plan(state: State) -> dict:
        return {"result": _plan(state["payload"], state["classified"])}

    def dispatch(state: State) -> dict:
        # No ejecuta nada: la lista de intents ya ES el resultado; hubara la
        # lee del run.result al pollear. Nodo terminal explícito para que el
        # grafo espeje la forma del plan (ingest→classify→plan→dispatch).
        return {"result": state["result"]}

    g = StateGraph(State)
    g.add_node("ingest", ingest)
    g.add_node("classify", classify)
    g.add_node("plan", plan)
    g.add_node("dispatch", dispatch)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "classify")
    g.add_edge("classify", "plan")
    g.add_edge("plan", "dispatch")
    g.add_edge("dispatch", END)
    return g.compile(name="window-strategist")
