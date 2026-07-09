"""Lógica PURA de `parse-conversations` — G-AGNOSTIC: NO importa ningún runtime
(langgraph/agentspan/langchain). Determinista e idempotente.

Espejo MÍNIMO de la matriz de precedencia de `decide_reengagement` (hubara,
`send_policy.py`) para PLANEAR reactivaciones. La AUTORIDAD del costo/permiso
es siempre hubara al ejecutar (el gate re-valida cada envío); este espejo solo
clasifica y prioriza. Paridad garantizada por el golden compartido
`fixtures/reengagement_matrix_golden.json` (checksum en ambos repos).

El LeadState llega PRE-DIGERIDO por hubara en `lead` (tag, has_order_draft,
has_registered_order, is_ctwa_lead, engaged, allow_paid_marketing) — acá NO se
re-deriva warmth desde metadata cruda (Decisión #2 del plan).

`now_ms` viene en el payload (G-DET: nada de datetime.now()).
"""
from __future__ import annotations

# Tags terminales (espejo de send_policy.TAG_HUMAN / TAG_CONVERTED /
# TAG_PAYMENT_PENDING — strings duplicados a propósito: la frontera del
# monorepo impide el import; el golden compartido caza el drift).
_TAG_HUMAN = "HUMANO"
_TAG_CONVERTED = "COMPRA_EXITOSA"
_TAG_PAYMENT_PENDING = "CONFIRMADO_PAGO_PENDIENTE"


def _in_window(now_ms: int, expires_at_ms: object) -> bool:
    # Misma frontera que window.py: entero válido y estrictamente now < exp.
    return isinstance(expires_at_ms, int) and now_ms < expires_at_ms


def _classify(now_ms: int, convo: dict) -> dict:
    lead = convo.get("lead") or {}
    tag = lead.get("tag")
    base = {"session_id": convo.get("session_id")}

    # 1. Terminales — ningún bot interviene.
    if tag == _TAG_HUMAN:
        return {**base, "action": "suppress", "reason": "human_owned"}
    if tag == _TAG_CONVERTED:
        return {**base, "action": "suppress", "reason": "already_converted"}

    in_csw = _in_window(now_ms, convo.get("service_window_expires_at_ms"))
    in_ctwa = _in_window(now_ms, convo.get("ctwa_window_expires_at_ms"))
    hook = (
        bool(lead.get("has_order_draft"))
        or bool(lead.get("has_registered_order"))
        or tag == _TAG_PAYMENT_PENDING
    )

    # 2. Fase A — CSW abierta (cliente activo) → free-form.
    if in_csw:
        return {
            **base,
            "action": "dispatch",
            "recommended_channel": "free_form",
            "recommended_category": "service",
            "reason": "csw_free_form",
            "window_phase": "csw_open",
        }

    # 3. Fase A — 72h CTWA abierta, CSW cerrada → template gratis.
    if in_ctwa:
        return {
            **base,
            "action": "dispatch",
            "recommended_channel": "template",
            "recommended_category": "utility" if hook else "marketing",
            "reason": "ctwa_72h_free",
            "window_phase": "ctwa_free",
        }

    # 4. Fase B — ambas cerradas.
    if hook:
        return {
            **base,
            "action": "dispatch",
            "recommended_channel": "template",
            "recommended_category": "utility",
            "reason": "transactional_hook",
            "window_phase": "expired",
        }
    if lead.get("allow_paid_marketing"):
        return {
            **base,
            "action": "dispatch",
            "recommended_channel": "template",
            "recommended_category": "marketing",
            "reason": "phase_b_high_value",
            "window_phase": "expired",
        }
    return {**base, "action": "suppress", "reason": "fase_b_cold_suppressed"}


def run(*, payload: dict) -> dict:
    """Clasifica cada conversación del snapshot: ventana × warmth × gancho.

    Input: `{"now_ms": int, "conversations": [{session_id, *_expires_at_ms,
    lead{...}, recent_touches?, tz?}]}`. Output: `{"now_ms", "conversations":
    [{session_id, action, reason, recommended_*?, window_phase?}]}` en el
    MISMO orden del input (el orden por valor lo decide el nodo `plan`).
    """
    now_ms = payload.get("now_ms")
    if not isinstance(now_ms, int):  # MF-7: error de dominio, no KeyError
        raise ValueError(
            "parse-conversations: falta 'now_ms' (int) en el payload — el reloj "
            "entra por el seed, nunca datetime.now() (G-DET)"
        )
    conversations = payload.get("conversations") or []
    return {
        "now_ms": now_ms,
        "conversations": [_classify(now_ms, c) for c in conversations],
    }
