"""Activity: ensure_payment_pending_closure — red de seguridad orden↔tag.

PROBLEMA (fix integridad): la secuencia canónica de cierre de venta
(`register_order` → `manage_conversation_tag(CONFIRMADO_PAGO_PENDIENTE)` →
`escalate_to_human(PAYMENT_VERIFICATION_PENDING)`) son 3 tool calls separadas
que decide el LLM, coordinadas SOLO por el prompt. Temporal garantiza que el
turno se reanude tras un crash de worker, pero NO que el LLM emita las 3
tools. Si el LLM registra la orden pero no emite el tag/escalación (alucina,
agota `max_iterations`, responde texto directo), quedaba una orden huérfana:
creada en Medusa pero sin episodio cerrado, sin tag de pago pendiente, sin
`EpisodeClosedEvent` (watchdog sigue vivo) y sin señal al humano para
verificar el pago.

SOLUCIÓN: cuando el workflow ve `order_registered_decision` (emitida por
`RegisterOrderTool`), corre esta activity. Es IDEMPOTENTE y CONVERGE al estado
final correcto, lo haya completado el LLM o no:
  * Si el LLM YA cerró el episodio (no hay episodio activo) → NO re-cierra.
  * Si el episodio sigue ABIERTO → lo cierra con `CONFIRMADO_PAGO_PENDIENTE`
    (reusa `close_episode`, que ya es idempotente).
  * Garantiza la escalación `PAYMENT_VERIFICATION_PENDING`
    (`active_route=humano`) salvo que la sesión ya esté en ruta humana — una
    orden registrada SIEMPRE necesita verificación humana del pago hasta que
    haya pasarela integrada.

Devuelve `PaymentPendingClosureResult` para que el WORKFLOW (no la activity,
R-DIP) emita el `EpisodeClosedEvent` + CAPI cuando la red haya tenido que
cerrar el episodio (evita doble emisión con el path del LLM).

DEHA:
  * R-STATELESS: sin cache module-level — todo desde args + metadata.json.
  * R-JSON: in (str, str, str) / out (`PaymentPendingClosureResult` frozen,
    campos planos bool/str).
  * R-DIP: no importa temporal client ni workflow classes.
  * Timestamp idempotente entre retries vía `activity.info().scheduled_time`
    (mismo patrón que `flush_ui_intents._mark_flow_awaiting_reply`): un retry
    NO debe cambiar el `closed_at_ms` del episodio.
"""
from __future__ import annotations

import json
import time
from typing import Any

from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import ROUTE_HUMANO, ROUTE_VENTAS
from src.platform.contracts import PaymentPendingClosureResult

_PAYMENT_PENDING_TAG = "CONFIRMADO_PAGO_PENDIENTE"
_PAYMENT_VERIFICATION_REASON = "PAYMENT_VERIFICATION_PENDING"


def _apply_human_escalation(
    data: dict[str, Any], *, reason_category: str, motivo: str, now_ms: int
) -> bool:
    """Escala la sesión a humano (espejo de `EscalateToHumanTool`). Muta `data`.

    Idempotente: si la sesión YA está en ruta humana, NO pisa (devuelve False)
    — respeta a un humano que ya tomó el caso o una escalación previa del LLM.
    Devuelve True si efectivamente escaló. El `tag=HUMANO` pisa el tag visible
    pero el `closing_tag` del episodio (puesto por el tag de cierre) preserva el
    estado real para el dashboard / CAPI.

    Helper compartido por las dos redes de seguridad
    (`ensure_payment_pending_closure` y `ensure_closing_escalation`) para que la
    lógica de escalación NO diverja.
    """
    if data.get("active_route") == ROUTE_HUMANO:
        return False
    data["active_route"] = ROUTE_HUMANO
    data["tag"] = "HUMANO"
    data["motivo"] = motivo
    data["escalation_reason"] = reason_category
    data.setdefault("status_history", []).append(
        {
            "tag": "HUMANO",
            "motivo": motivo,
            "active_route": ROUTE_HUMANO,
            "reason_category": reason_category,
            "timestamp": now_ms / 1000.0,
            "source": "safety_net",
        }
    )
    return True


@activity.defn(name="ensure_payment_pending_closure")
async def ensure_payment_pending_closure_activity(
    session_id: str, order_id: str, motivo: str
) -> PaymentPendingClosureResult:
    """Garantiza el cierre `pago pendiente` + escalación tras `register_order`.

    Idempotente: segura de re-ejecutar (retry de Temporal) y segura de correr
    cuando el LLM YA completó la secuencia (no pisa ni duplica).
    """
    # Import lazy (runtime): `episode_lifecycle` arrastra `use_cases/__init__`,
    # que importa el workflow → `activities/__init__`. Importarlo al top de
    # este módulo (que SÍ se carga eager desde `activities/__init__`) crea un
    # ciclo. Mismo patrón de imports diferidos que `flush_ui_intents`.
    from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
        close_episode,
        count_session_jsonl_lines,
        get_active_episode,
    )

    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    if not metadata_file.exists():
        # Sin metadata no hay episodio que cerrar coherentemente. No debería
        # pasar (register_order escribe metadata antes de devolver). No-op.
        activity.logger.warning(
            "ensure_payment_pending_closure: metadata ausente — no-op",
            extra={"session_id": session_id, "order_id": order_id},
        )
        return PaymentPendingClosureResult(acted=False, escalated=False)

    try:
        data: dict[str, Any] = json.loads(
            metadata_file.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as exc:
        activity.logger.error(
            "ensure_payment_pending_closure: metadata corrupto — no-op",
            extra={"session_id": session_id, "error": str(exc)},
        )
        return PaymentPendingClosureResult(acted=False, escalated=False)

    # Timestamp idempotente entre retries: un retry de esta activity NO debe
    # mover el `closed_at_ms` del episodio. `scheduled_time` es estable entre
    # attempts de la misma activity (SDK-native).
    try:
        now_ms = int(activity.info().scheduled_time.timestamp() * 1000)
    except RuntimeError:
        now_ms = int(time.time() * 1000)

    # 1) ¿El LLM ya cerró el episodio? Señal robusta: si hay episodio ACTIVO,
    #    el LLM no cerró (`attach_order_to_active_episode` anota la venta pero
    #    NO cierra). Si no hay activo, el cierre ya ocurrió.
    active_ep = get_active_episode(data)
    acted = False
    closed_episode_id = ""
    closing_tag = ""

    if active_ep is not None:
        # El LLM no completó la secuencia — la red cierra el episodio.
        msgs_at_close = count_session_jsonl_lines(WORKSPACE_VAULT_DIR, session_id)
        safety_motivo = motivo or (
            "Cierre garantizado por el workflow (red de seguridad pago "
            "pendiente) — el agente registró la orden pero no marcó el tag."
        )
        closed_ep = close_episode(
            data,
            closing_tag=_PAYMENT_PENDING_TAG,
            closing_motivo=safety_motivo,
            now_ms=now_ms,
            order_id=order_id,
            msgs_count_at_close=msgs_at_close,
        )
        if closed_ep is not None:
            acted = True
            closed_episode_id = str(closed_ep.get("episode_id") or "")
            closing_tag = _PAYMENT_PENDING_TAG
            data["tag"] = _PAYMENT_PENDING_TAG
            data.setdefault("status_history", []).append(
                {
                    "tag": _PAYMENT_PENDING_TAG,
                    "motivo": safety_motivo,
                    "active_route": data.get("active_route", ROUTE_VENTAS),
                    "timestamp": now_ms / 1000.0,
                    "source": "safety_net",
                }
            )
            activity.logger.warning(
                "ensure_payment_pending_closure: el LLM NO cerró el episodio "
                "tras registrar la orden — red de seguridad aplicó "
                "CONFIRMADO_PAGO_PENDIENTE",
                extra={
                    "session_id": session_id,
                    "order_id": order_id,
                    "episode_id": closed_episode_id,
                },
            )

    # 2) Garantizar escalación PAYMENT_VERIFICATION_PENDING (idempotente).
    #    `_apply_human_escalation` no pisa si ya está en ruta humana — respeta
    #    a un humano que ya tomó el caso o una escalación previa.
    escalated = _apply_human_escalation(
        data,
        reason_category=_PAYMENT_VERIFICATION_REASON,
        motivo=motivo,
        now_ms=now_ms,
    )
    if escalated:
        activity.logger.warning(
            "ensure_payment_pending_closure: garantizando escalación "
            "PAYMENT_VERIFICATION_PENDING (red de seguridad)",
            extra={"session_id": session_id, "order_id": order_id},
        )

    if acted or escalated:
        metadata_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return PaymentPendingClosureResult(
        acted=acted,
        escalated=escalated,
        closed_episode_id=closed_episode_id,
        closing_tag=closing_tag,
    )


@activity.defn(name="ensure_closing_escalation")
async def ensure_closing_escalation_activity(
    session_id: str, reason_category: str, motivo: str
) -> bool:
    """Red de seguridad para closing tags que EXIGEN escalación a humano.

    Caso paradigmático: el LLM marca `CONFIRMADO_SIN_DATOS` (el cliente confirmó
    el pedido pero no completó los datos de envío) — eso cierra el episodio —
    pero NO llama `escalate_to_human(ORDER_PENDING_SHIPPING_DETAILS)`. Sin la
    escalación, el cliente queda sin que ningún humano le pida los datos
    faltantes. Mismo patrón que `ensure_payment_pending_closure`, pero acá el
    episodio YA está cerrado por el tag del LLM — solo falta garantizar la
    escalación (no forzamos cierre).

    Idempotente: no pisa si la sesión ya está en ruta humana (el LLM escaló, o
    un humano ya tomó el caso). Devuelve True si efectivamente escaló.

    DEHA: R-STATELESS / R-JSON (in str×3, out bool) / R-DIP (no temporal
    client). Timestamp idempotente entre retries vía `scheduled_time`.
    """
    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    if not metadata_file.exists():
        activity.logger.warning(
            "ensure_closing_escalation: metadata ausente — no-op",
            extra={"session_id": session_id},
        )
        return False

    try:
        data: dict[str, Any] = json.loads(
            metadata_file.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as exc:
        activity.logger.error(
            "ensure_closing_escalation: metadata corrupto — no-op",
            extra={"session_id": session_id, "error": str(exc)},
        )
        return False

    try:
        now_ms = int(activity.info().scheduled_time.timestamp() * 1000)
    except RuntimeError:
        now_ms = int(time.time() * 1000)

    escalated = _apply_human_escalation(
        data,
        reason_category=reason_category,
        motivo=motivo or "Escalación garantizada por el workflow (red de seguridad).",
        now_ms=now_ms,
    )
    if escalated:
        activity.logger.warning(
            "ensure_closing_escalation: garantizando escalación %s "
            "(red de seguridad — el LLM cerró el episodio pero no escaló)",
            reason_category,
            extra={"session_id": session_id},
        )
        metadata_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return escalated
