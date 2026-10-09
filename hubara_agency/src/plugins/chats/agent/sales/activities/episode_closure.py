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
from src.plugins.chats.agent.sales.catalog_scope import WHOLE_CATALOG
from src.plugins.chats.agent.sales.metadata_reads import read_retrying_transient_errors_sync

import copy
import json
import time
from typing import Any

from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import ROUTE_HUMANO, ROUTE_VENTAS
from src.platform.contracts import PaymentPendingClosureResult
from src.sdk.runtime import FilesystemMetadataStore

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

    if not (WORKSPACE_VAULT_DIR / session_id / "metadata.json").exists():
        # Sin metadata no hay episodio que cerrar coherentemente. No debería
        # pasar (register_order escribe metadata antes de devolver). No-op.
        activity.logger.warning(
            "ensure_payment_pending_closure: metadata ausente — no-op",
            extra={"session_id": session_id, "order_id": order_id},
        )
        return PaymentPendingClosureResult(acted=False, escalated=False)
    # Por el store: un metadata dañado se lee de la última copia buena
    # (decisión del operador, 2026-10-06), así la red no se calla por eso.
    data: dict[str, Any] = read_retrying_transient_errors_sync(FilesystemMetadataStore(WORKSPACE_VAULT_DIR), session_id)
    base = copy.deepcopy(data)

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
        _write_own_changes(session_id, base, data)

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
    if not (WORKSPACE_VAULT_DIR / session_id / "metadata.json").exists():
        activity.logger.warning(
            "ensure_closing_escalation: metadata ausente — no-op",
            extra={"session_id": session_id},
        )
        return False
    # Por el store: un metadata dañado se lee de la última copia buena.
    data: dict[str, Any] = read_retrying_transient_errors_sync(FilesystemMetadataStore(WORKSPACE_VAULT_DIR), session_id)
    base = copy.deepcopy(data)

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
        _write_own_changes(session_id, base, data)
    return escalated


def _catalog_client() -> Any:
    """El catálogo del worker (el snapshot de `catalog_sync`)."""
    from src.sdk.catalogkit import get_catalog_client

    return get_catalog_client()


#: Motivo con el que la red deja la conversación en la bandeja humana.
_PROMISED_HANDOFF_REASON = "OTHER"
_PROMISE_IN_MOTIVO = 300


@activity.defn(name="ensure_promised_handoff")
async def ensure_promised_handoff_activity(session_id: str, text: str) -> bool:
    """Las promesas del texto final del turno, antes de enviarlo.

    Si promete el formulario de envío y no salió, lo encola
    (`_ensure_promised_shipping_form`; el flush lo manda después del texto).

    Red de seguridad del relevo prometido (laboratorio caso-cortesia-1001,
    2026-09-30): el bot le dijo al cliente «un colega del equipo coordina
    contigo la entrega» sin llamar `escalate_to_human`, y la conversación
    siguió en la ruta del bot. Nadie la veía en la bandeja humana.

    Antes de enviar el texto final del turno: si promete el relevo (capacidad
    `relevo`, con el bot de la conversación) y la sesión no está escalada, la
    escala como `escalate_to_human`, con la promesa en el motivo para el
    colega. Idempotente: una sesión ya escalada no se toca y no se le
    pregunta a nadie. Devuelve True si escaló.

    DEHA: R-STATELESS / R-JSON (in str×2, out bool) / R-DIP (no temporal
    client). Timestamp idempotente entre retries vía `scheduled_time`.
    """
    from src.plugins.chats.agent.sales.decisions.guards import promised_handoff

    if not (text or "").strip():
        return False
    if not (WORKSPACE_VAULT_DIR / session_id / "metadata.json").exists():
        return False
    # Por el store: un metadata dañado se lee de la última copia buena.
    data: dict[str, Any] = read_retrying_transient_errors_sync(FilesystemMetadataStore(WORKSPACE_VAULT_DIR), session_id)
    base = copy.deepcopy(data)
    if data.get("active_route") == ROUTE_HUMANO:
        return False
    if not await promised_handoff(text, session_id=session_id, vault_dir=WORKSPACE_VAULT_DIR):
        # Las otras promesas del texto (2026-10-09): el formulario y las
        # tarifas, si aun después de la ronda del turno no salieron.
        # Best-effort: una falla de la red nunca tumba el envío del texto.
        for net in (_ensure_promised_shipping_form, _ensure_promised_rates):
            try:
                await net(session_id, text, data)
            except Exception:  # noqa: BLE001
                activity.logger.exception(
                    "%s: la red falló — el texto sale igual", net.__name__, extra={"session_id": session_id}
                )
        return False
    try:
        now_ms = int(activity.info().scheduled_time.timestamp() * 1000)
    except RuntimeError:
        now_ms = int(time.time() * 1000)
    promise = " ".join(text.split())[:_PROMISE_IN_MOTIVO]
    escalated = _apply_human_escalation(
        data,
        reason_category=_PROMISED_HANDOFF_REASON,
        motivo=f"El asesor le dijo al cliente que alguien del equipo lo atiende y no escaló: «{promise}»",
        now_ms=now_ms,
    )
    if escalated:
        activity.logger.warning(
            "ensure_promised_handoff: el texto prometía el relevo sin escalar — escalado (red de seguridad)",
            extra={"session_id": session_id},
        )
        _write_own_changes(session_id, base, data)
    return escalated




def _delivered_rows(session_id: str) -> list[dict[str, Any]]:
    """Las filas del registro de entregas de la sesión (vacío si no hay)."""
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import _DELIVERED_LOG

    path = WORKSPACE_VAULT_DIR / session_id / _DELIVERED_LOG
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


async def _ensure_promised_shipping_form(session_id: str, text: str, data: dict[str, Any]) -> bool:
    """Red del formulario prometido (incidente 2026-10-09): el bot cerró con
    «te paso el formulario para los datos de envío» sin llamar
    `request_shipping_details`, dos turnos seguidos, y el operador lo mandó a
    mano. Si el texto lo promete y en el episodio no salió ni está en la cola,
    lo pide con la misma tool (sus guardas: un cliente que acaba de aplazar no
    lo recibe) y los ítems del borrador. Devuelve True si quedó en la cola.
    Una falla (catálogo caído, producto que no está) deja el texto como está.
    """
    from exoclaw.agent.tools import ToolContext

    from src.plugins.chats.agent.sales.tools.ui_intents import RequestShippingDetailsTool
    from src.plugins.chats.agent.sales.use_cases.promised_shipping_form import (
        promises_shipping_form,
        shipping_form_in_episode,
        shipping_form_items,
    )

    if not promises_shipping_form(text):
        return False
    if shipping_form_in_episode(data, _delivered_rows(session_id)):
        return False
    catalog = _catalog_client()
    try:
        page = await catalog.search("", limit=WHOLE_CATALOG)
    except Exception as exc:  # noqa: BLE001 — sin catálogo no se adivina el pedido
        activity.logger.warning(
            "ensure_promised_shipping_form: catálogo no disponible (%s)", exc, extra={"session_id": session_id}
        )
        return False
    products = [p for p in page.results if getattr(p, "status", "published") == "published"]
    items = shipping_form_items(data, products)
    if not items:
        activity.logger.warning(
            "ensure_promised_shipping_form: el borrador no se cruza con el catálogo — no se manda",
            extra={"session_id": session_id},
        )
        return False
    tool = RequestShippingDetailsTool(WORKSPACE_VAULT_DIR, catalog=catalog)
    ctx = ToolContext(session_key=session_id, channel="whatsapp", chat_id=session_id)
    envelope = json.loads(await tool.execute_with_context(ctx, items=items))
    queued = envelope.get("queued") is True
    activity.logger.warning(
        "ensure_promised_shipping_form: el texto prometía el formulario sin la tool — %s",
        "encolado (red de seguridad)" if queued else f"no salió ({envelope.get('error')})",
        extra={"session_id": session_id},
    )
    return queued


async def _ensure_promised_rates(session_id: str, text: str, data: dict[str, Any]) -> bool:
    """Las tarifas prometidas («te comparto las tarifas de envío») que no están
    en la cola del turno: la tarjeta no lleva argumentos, se encola tal cual.
    Devuelve True si quedó en la cola."""
    from exoclaw.agent.tools import ToolContext

    from src.plugins.chats.agent.sales.tools.ui_intents import SendShippingRatesTool
    from src.plugins.chats.agent.sales.use_cases.promised_actions import promised_kinds

    if "tarifas" not in promised_kinds(text):
        return False
    queued = data.get("pending_ui_intents") or []
    if any(isinstance(i, dict) and i.get("kind") == "shipping_rates" for i in queued):
        return False
    ctx = ToolContext(session_key=session_id, channel="whatsapp", chat_id=session_id)
    envelope = json.loads(await SendShippingRatesTool(WORKSPACE_VAULT_DIR).execute_with_context(ctx))
    queued_now = envelope.get("queued") is True
    activity.logger.warning(
        "ensure_promised_rates: el texto prometía las tarifas sin la tarjeta — %s",
        "encoladas (red de seguridad)" if queued_now else f"no salieron ({envelope.get('error')})",
        extra={"session_id": session_id},
    )
    return queued_now


def _write_own_changes(session_id: str, base: dict[str, Any], data: dict[str, Any]) -> None:
    """Escribe SOLO lo que la red de seguridad cambió en `data` frente a lo
    que leyó (`base`), sobre lo que hay en disco ahora y bajo el candado del
    store (incidente 2026-10-06: la copia entera pisaba lo que otro escritor
    puso entre la lectura y la escritura — acá media una consulta al motor)."""
    FilesystemMetadataStore(WORKSPACE_VAULT_DIR).write_merged(session_id, base=base, ours=data)
