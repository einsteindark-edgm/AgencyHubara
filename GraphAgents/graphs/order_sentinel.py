"""Capability `order-sentinel` (analyzer). Lee las conversaciones WhatsApp
escaladas a HUMANO con orden vinculada (el snapshot lo deposita hubara — el
agente NO pega red, G-PORT) y emite intents de transición de estado del pedido.

La CLASIFICACIÓN la hace el nodo LLM marcado por `ports["llm"]` (molde
ctwa-report.narrate: FixtureLLM en golden, LiteLLMProxy en real, temperature=0,
structured output). Los GUARDRAILS son deterministas y viven en el nodo plan
PURO — el LLM propone, el código dispone:

  1. `to_stage` permitidos SOLO {preparing, ready, shipping, delivered} —
     `cancelled` NUNCA por inferencia (el humano cancela a mano).
  2. Forward-only ADYACENTE en el DAG new→preparing→ready→shipping→delivered.
  3. Solo `confidence == "high"` despacha.
  4. Máx 1 intent por order_id (dos sesiones, misma orden → gana la primera).
  5. `confirm_payment` solo si `payment_confirmed == false` (idempotencia).

SIN HITL ni tools outward (G-DUR sin sujeto): el output del run() ES la lista
de intents. Hubara la ejecuta re-validando contra el DAG real del API de orders
(`invalid_transition` benigno) con `notify_customer=false` (el humano ya avisó
por chat — la cascada ETA duplicaría el mensaje).

LECTOR DE JEV (motor de decisiones de hubara, F8): con `payload.reader` en
`sombra` o `jev`, cada conversación trae la lectura de Jev que depositó hubara
(`reading.verdict`: la MISMA forma que el veredicto del LLM, o None si Jev
dudó, cayó o tardó — el agente sigue sin pegar red). El LLM se llama igual que
hoy (es la regla de hoy). En `sombra` actúa el LLM; en `jev` actúa el
veredicto de Jev cuando lo hay, y si no, el LLM. Las guardas son las MISMAS
para las dos fuentes. El result suma `reader`, `by` en cada intent/suppressed
y `shadow`: lo que cada uno haría despachar (después de las guardas) para la
cola de desacuerdos que califica Claude Code. Sin lector (o con otro valor),
la lectura se ignora y el run es el de hoy.

- run(input, *, ports, tools) — PURA salvo el port llm (G-RUN-SIG).
- build(*, llm=None)          — el StateGraph LangGraph, reusa la lógica (L-11).
"""
from __future__ import annotations

import json

#: stages que el sentinel puede proponer. `cancelled` EXCLUIDO por diseño.
_ALLOWED_STAGES = frozenset({"preparing", "ready", "shipping", "delivered"})

#: el DAG forward-only (copia estática del dominio de orders): sólo el paso
#: ADYACENTE es legal — saltos y retrocesos se suprimen.
_NEXT_STAGE = {
    "new": "preparing",
    "preparing": "ready",
    "ready": "shipping",
    "shipping": "delivered",
}

_SYSTEM = (
    "Sos un analista de conversaciones de WhatsApp de una tienda en Colombia. "
    "La conversación es entre el cliente (customer), el operador humano de la "
    "tienda (human_operator) y a veces el bot. Tu ÚNICA tarea: decidir si la "
    "conversación indica que el PEDIDO cambió de estado o que el PAGO quedó "
    "confirmado. Estados posibles del pedido: new → preparing → ready → "
    "shipping → delivered.\n"
    "Respondé SOLO un JSON (sin prosa, sin markdown) con esta forma exacta:\n"
    '{"action": "transition" | "confirm_payment" | "none", '
    '"to_stage": "preparing|ready|shipping|delivered", '
    '"evidence": ["cita textual del mensaje que lo prueba"], '
    '"confidence": "high" | "medium" | "low"}\n'
    "Reglas DURAS: (1) `evidence` son citas TEXTUALES de los mensajes, nunca "
    "paráfrasis. (2) `confidence: high` SOLO si la señal es explícita e "
    "inequívoca y viene del human_operator (autoridad) o es una confirmación "
    "clara del cliente (ej. 'ya me llegó'). Anuncios a futuro ('mañana te lo "
    "envío'), dudas ('creo que...') o señales ambiguas → medium/low. "
    "(3) `confirm_payment` SOLO cuando el operador confirma que el pago quedó "
    "verificado — que el cliente diga que pagó NO basta. (4) Sin señal clara "
    "de cambio de estado → action `none`. (5) NUNCA propongas cancelaciones."
)


def _prompt_user(convo: dict) -> str:
    """El prompt con la conversación CRUDA (el contrato del golden: los textos
    de los mensajes viajan tal cual — sin ellos el LLM no tiene qué clasificar)."""
    lines = [
        f"estado actual del pedido: {convo.get('current_stage')}",
        f"pago confirmado: {convo.get('payment_confirmed')}",
        "conversación (en orden):",
    ]
    for m in convo.get("messages") or []:
        media = " [adjuntó imagen]" if m.get("has_media") else ""
        lines.append(f"- {m.get('who')}: {m.get('text')}{media}")
    return "\n".join(lines)


def _parse_verdict(text: str) -> dict | None:
    """La respuesta del LLM → verdict dict, tolerando fences markdown. `None`
    si no es JSON válido con un `action` conocido (→ unparseable_llm_output)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    try:
        verdict = json.loads(t)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(verdict, dict):
        return None
    # Normalización (PM-018): el LLM real capitaliza ("Transition"/"High") —
    # eso no es un verdict inválido, es ruido de superficie.
    for key in ("action", "to_stage", "confidence"):
        if isinstance(verdict.get(key), str):
            verdict[key] = verdict[key].strip().lower()
    if verdict.get("action") not in ("transition", "confirm_payment", "none"):
        return None
    return verdict


def _classify(conversations: list[dict], llm) -> list[dict]:
    """El nodo LLM MARCADO (G-DET): UNA llamada por conversación, temperature=0.
    Nunca crashea — el proxy caído degrada VISIBLE a un entry de error (L-26)."""
    out: list[dict] = []
    for convo in conversations:
        entry = {"session_id": convo.get("session_id"), "verdict": None, "error": None}
        if llm is None:
            entry["error"] = "llm_port_missing: el manifest consume `llm` pero el runtime no inyectó el vendor"
            out.append(entry)
            continue
        try:
            text = llm.complete(system=_SYSTEM, user=_prompt_user(convo), temperature=0.0)
        except Exception as e:  # noqa: BLE001 — un LLM caído NO tumba el ciclo:
            # el error viaja COMPLETO a llm_errors (trace/observabilidad) y la
            # conversación se salta. Molde ctwa-report / L-26 (Errno 99).
            entry["error"] = f"{type(e).__name__}: {e}"
            out.append(entry)
            continue
        entry["verdict"] = _parse_verdict(text)  # None = unparseable
        out.append(entry)
    return out


#: los lectores del motor de decisiones de hubara (F8). Cualquier otro valor
#: (o ninguno) = el run de hoy: la lectura de Jev se ignora.
_READERS = frozenset({"sombra", "jev"})


def _reading_verdict(convo: dict) -> dict | None:
    """El veredicto de Jev que depositó hubara (misma forma que el del LLM), o
    None si Jev dudó, cayó o tardó, o si la lectura tiene otra forma."""
    reading = convo.get("reading")
    verdict = reading.get("verdict") if isinstance(reading, dict) else None
    if not isinstance(verdict, dict):
        return None
    if verdict.get("action") not in ("transition", "confirm_payment", "none"):
        return None
    return verdict


def _guard(convo: dict, verdict: dict) -> str | None:
    """Las guardas DETERMINISTAS de UN veredicto (del LLM o de Jev), en orden de
    precedencia: evidencia textual (PM-008) → stage permitido (1) → adyacencia
    del DAG (2) → confidence high (3) → orden cancelada y pago ya confirmado
    (5, PM-012). None = despachable (falta solo el dedup por order_id, 4)."""
    # Anti-alucinación determinista (PM-008): la evidencia debe ser cita
    # TEXTUAL de la conversación — si ninguna cita aparece en un mensaje,
    # el verdict se descarta (el LLM inventó la prueba).
    texts = [m.get("text") or "" for m in convo.get("messages") or []]
    evidence = [e for e in (verdict.get("evidence") or []) if isinstance(e, str)]
    if not any(e and any(e in t for t in texts) for e in evidence):
        return "evidence_not_found"
    confidence = verdict.get("confidence")
    if verdict["action"] == "transition":
        to_stage = verdict.get("to_stage")
        if to_stage not in _ALLOWED_STAGES:
            return "stage_not_allowed"
        if _NEXT_STAGE.get(convo.get("current_stage")) != to_stage:
            return "invalid_transition"
        if confidence != "high":
            return "low_confidence"
        return None
    # confirm_payment
    if confidence != "high":
        return "low_confidence"
    if convo.get("current_stage") == "cancelled":
        # PM-012: pagada+cancelada es un estado contradictorio.
        return "order_cancelled"
    if convo.get("payment_confirmed") is not False:
        return "payment_already_confirmed"
    return None


def _proposal(convo: dict, verdict: dict) -> dict:
    """Lo que un veredicto haría despachar después de las guardas (lo que la
    sombra compara entre el LLM y Jev)."""
    action = verdict.get("action")
    if action == "none" or _guard(convo, verdict) is not None:
        return {"action": "none"}
    out = {"action": action}
    if action == "transition":
        out["to_stage"] = verdict.get("to_stage")
    return out


def _plan(payload: dict, classified: list[dict]) -> dict:
    """Los guardrails DETERMINISTAS post-LLM, en orden de precedencia:
    stage permitido (1) → adyacencia del DAG (2) → confidence high (3) →
    idempotencia del pago (5) → dedup por order_id (4). Con lector de Jev,
    el veredicto que actúa sale del lector (ver el docstring del módulo)."""
    dispatch: list[dict] = []
    suppressed: list[dict] = []
    llm_errors: list[dict] = []
    shadow: list[dict] = []
    seen_transition: set = set()
    seen_payment: set = set()
    reader = payload.get("reader") if payload.get("reader") in _READERS else None

    by_index = payload.get("conversations") or []
    for convo, entry in zip(by_index, classified):
        sid = convo.get("session_id")
        oid = convo.get("order_id")
        jev = _reading_verdict(convo) if reader else None
        llm = entry["verdict"] if entry["error"] is None else None
        if jev is not None and llm is not None:
            llm_wants, jev_wants = _proposal(convo, llm), _proposal(convo, jev)
            shadow.append(
                {
                    "session_id": sid,
                    "order_id": oid,
                    "llm": llm_wants,
                    "jev": jev_wants,
                    "agree": llm_wants == jev_wants,
                }
            )

        def _suppress(reason: str, by: str) -> None:
            row = {"session_id": sid, "order_id": oid, "reason": reason}
            if reader:
                row["by"] = by
            suppressed.append(row)

        if reader == "jev" and jev is not None:
            verdict, by = jev, "jev"
        else:
            if entry["error"] is not None:
                llm_errors.append({"session_id": sid, "error": entry["error"]})
                continue
            verdict, by = entry["verdict"], "llm"
            if verdict is None:
                _suppress("unparseable_llm_output", by)
                continue
        action = verdict["action"]
        if action == "none":
            continue  # silencio normal: ni dispatch ni suppressed

        reason = _guard(convo, verdict)
        if reason is not None:
            _suppress(reason, by)
            continue
        seen = seen_transition if action == "transition" else seen_payment
        if oid in seen:
            _suppress("duplicate_order_intent", by)
            continue
        seen.add(oid)
        intent = {"kind": "order_stage_intent", "session_id": sid, "order_id": oid, "action": action}
        if action == "transition":
            intent["to_stage"] = verdict.get("to_stage")
        intent["evidence"] = list(verdict.get("evidence") or [])
        intent["confidence"] = verdict.get("confidence")
        if reader:
            intent["by"] = by
        dispatch.append(intent)

    result = {
        "schema_version": 1,
        "snapshot_now_ms": payload.get("now_ms"),
        "dispatch": dispatch,
        "suppressed": suppressed,
        "llm_errors": llm_errors,
    }
    if reader:
        result["reader"] = reader
        result["shadow"] = shadow
    return result


def _require_payload(input: dict) -> dict:
    if "payload" not in input:  # MF-7: el seam hubara→agente falla con error de dominio
        raise ValueError(
            "order-sentinel: falta 'payload' (el snapshot de conversaciones "
            "HUMANO que deposita hubara)"
        )
    return input["payload"]


def run(input: dict, *, ports: dict | None = None, tools: dict | None = None) -> dict:
    payload = _require_payload(input)
    llm = (ports or {}).get("llm")
    classified = _classify(payload.get("conversations") or [], llm)
    return _plan(payload, classified)


def build(*, llm=None):
    """`StateGraph` LangGraph — cadena LINEAL ingest→classify→plan→dispatch
    (sin conditional edges, L-15; sin reducers custom, L-14). Cada nodo reusa
    los MISMOS helpers que `run()` (la lógica vive una vez, L-11). El port
    `llm` lo inyecta el runtime durable según `consumes:` (FixtureLLM en
    replay); sin vendor, cada conversación degrada VISIBLE a llm_errors."""
    try:
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("instalá deps: `uv sync` (langgraph).") from e

    class State(TypedDict, total=False):
        payload: dict     # el snapshot que deposita hubara (now_ms adentro, G-DET)
        classified: list  # ← classify (nodo LLM marcado por el port)
        result: dict      # ← plan/dispatch (la lista de intents = el output)

    def ingest(state: State) -> dict:
        _require_payload(state)
        return {}

    def classify(state: State) -> dict:
        payload = state["payload"]
        return {"classified": _classify(payload.get("conversations") or [], llm)}

    def plan(state: State) -> dict:
        return {"result": _plan(state["payload"], state["classified"])}

    def dispatch(state: State) -> dict:
        # No ejecuta nada: la lista de intents ya ES el resultado; hubara la
        # lee del run.result al pollear y ejecuta con autoridad (DAG real +
        # notify_customer=false). Nodo terminal explícito, espeja el plan.
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
    return g.compile(name="order-sentinel")
