"""Tool: RegisterOrderTool.

Registra un pedido formalmente cuando la venta cierra con éxito
(`COMPRA_EXITOSA`). El backend de verdad es **Medusa v2 Draft Orders**
(POST /admin/draft-orders) — mismo Medusa que sirve el catálogo. La tool
delega al `OrderRegistrationPort` (DEHA-style hexagonal): el adapter live
es `MedusaOrderRegistration`, con fallback a `StubOrderRegistration` si
las env vars (`MEDUSA_REGION_ID`, `MEDUSA_SALES_CHANNEL_ID`) no están
seteadas.

DEHA:
  * Tool INERTE — no importa `httpx`, no importa `temporal_client`. Solo
    depende de `OrderRegistrationPort` (abstraccion en
    `src/platform/orders/port.py`).
  * R-JSON: input/output JSON-serializable (envelope textual al LLM).
  * R-DIP: el port se inyecta por constructor desde el composition root
    (`src/plugins/chats/workers/sales.py`), no se construye aqui.

Patrón LLM (ver TOOLS.md §"Reglas de cierre de venta"):
  1. Cliente completa el Flow (`nfm_reply`) o manda los datos por texto.
  2. LLM llama `verify_order_for_checkout(items)` → verified=True.
  3. LLM llama `present_order_confirmation(items, shipping, payment)`.
  4. Cliente apreta "✅ Confirmar".
  5. LLM llama `register_order(...)` con los datos finales.   ← ESTA TOOL
  6. Si `registered=true` → LLM llama
     `manage_conversation_tag(tag="COMPRA_EXITOSA", motivo=...)`.
  6. Si `registered=false` (Medusa caído / config rota) → LLM llama
     `escalate_to_human(reason_category="ORDER_REGISTRATION_FAILED")`.
  7. LLM despide al cliente con un mensaje cálido.

Esta secuencia es la única forma legítima de cerrar una venta. El LLM NO
debe poner `COMPRA_EXITOSA` sin haber llamado `register_order` antes (y
sin haber recibido `registered=true`).

Resiliencia (la pregunta del usuario sobre Temporal):
  * `HttpMedusaClient._request` ya tiene tenacity-with-backoff sobre
    `httpx.TransportError` (transient network).
  * Si Medusa devuelve 5xx persistente o esta caido, el adapter convierte
    a `OrderRegistrationResult(success=False)` — la activity de Temporal
    (`execute_tool`) NO falla (no levantamos excepciones), asi el LLM
    recibe el envelope con `registered=false` + instruccion explicita de
    escalar. El humano cierra desde el dashboard con los datos guardados.
  * Si quisieramos hard-retry (Temporal-native), bastaria con eliminar el
    `try/except` del adapter y dejar burbujear `MedusaAPIError` — Temporal
    reintentaria la activity completa segun su `RetryPolicy`. Por ahora
    preferimos el envelope explicito para que el LLM SIEMPRE pueda
    comunicarse con el cliente (vs un hard-failure que dejaria al cliente
    colgado mientras los retries pasan).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext
from loguru import logger

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.orders.port import (
    OrderItem,
    OrderRegistrationPort,
    OrderRegistrationResult,
    OrderShipping,
)
from src.platform.orders.reconciliation import STATUS_PENDING
from src.platform.orders.stub import StubOrderRegistration
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    attach_order_to_active_episode,
)


def _order_reference(raw_payload: dict[str, Any] | None) -> str | None:
    """Referencia humana del pedido para mensajes al cliente: "#22 (Plegaria
    de Luz)" — mismo estilo que la notificación ETA. El cliente no sabe qué
    es `order_01KXV...`.

    Medusa asigna `display_id` AL CREAR el draft (POST /admin/draft-orders),
    así que existe desde el registro — no depende de que el humano agende la
    entrega. `None` si el provider no lo trae (stub) → el renderer cae al
    order_id crudo.
    """
    raw = raw_payload or {}
    display_id = raw.get("display_id")
    if display_id is None:
        return None
    items = raw.get("items") or []
    parts: list[str] = []
    for it in items[:3]:
        title = (it.get("title") or "").strip()
        qty = it.get("quantity") or 0
        if title:
            parts.append(f"{qty}× {title}" if qty > 1 else title)
    if len(items) > 3:
        parts.append(f"y {len(items) - 3} más")
    label = ", ".join(parts)
    reference = f"#{display_id}"
    if label:
        reference = f"{reference} ({label})"
    return reference[:120]


class RegisterOrderTool(ToolBase):
    """Registra un pedido al cierre exitoso de la venta vía OrderRegistrationPort.

    Default `port = StubOrderRegistration()` para que la tool sea
    constructible sin DI explicito en tests legacy y dev local sin Medusa.
    En produccion, el composition root inyecta `MedusaOrderRegistration`
    via `get_order_registration_port()`.
    """

    name = "register_order"
    description = (
        "Registra formalmente el pedido del cliente en Medusa cuando la "
        "venta cerró exitosamente. Llámala SOLO después de que el cliente "
        "confirmó el pedido (vía `present_order_confirmation`) Y ya tienes "
        "todos los datos de envío (ciudad, barrio, dirección, teléfono, "
        "nombre de quien recibe, método de pago; cédula opcional). Esta "
        "tool crea un Draft Order en Medusa para que "
        "el equipo de Hubara lo procese. Lee el campo `registered` de la "
        "respuesta: si es `true`, llama `manage_conversation_tag"
        "(tag='COMPRA_EXITOSA', motivo=...)` y despide al cliente. Si es "
        "`false` (Medusa caído / config rota), llama `escalate_to_human"
        "(reason_category='ORDER_REGISTRATION_FAILED')` para que un humano "
        "registre manualmente — los datos quedan persistidos en metadata "
        "para que el humano los reconstruya. Si el cliente confirmó pero "
        "NO completó los datos de envío, NO uses esta tool — usa "
        "`escalate_to_human(reason_category='ORDER_PENDING_SHIPPING_DETAILS')`."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Items del pedido. Cada item: handle (snapshot), "
                    "quantity, unit_price_cop, opcionalmente "
                    "variant_label (ej. 'Lavanda', 'Azul')."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "handle": {"type": "string", "minLength": 1},
                        "quantity": {"type": "integer", "minimum": 1},
                        "unit_price_cop": {"type": "integer", "minimum": 0},
                        "variant_label": {
                            "type": "string",
                            "maxLength": 80,
                        },
                    },
                    "required": ["handle", "quantity", "unit_price_cop"],
                },
            },
            "shipping": {
                "type": "object",
                "description": "Datos de envío completos.",
                "properties": {
                    "city": {"type": "string", "minLength": 1},
                    "neighborhood": {"type": "string", "minLength": 1},
                    "address": {"type": "string", "minLength": 1},
                    "phone": {"type": "string", "minLength": 7},
                    "receiver_name": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Nombre completo de quien recibe el pedido "
                            "(obligatorio — lo exige la transportadora)."
                        ),
                    },
                    "national_id": {
                        "type": "string",
                        "description": (
                            "Cédula de quien recibe (OPCIONAL — solo si el "
                            "cliente la dio; no insistas)."
                        ),
                    },
                },
                "required": [
                    "city",
                    "neighborhood",
                    "address",
                    "phone",
                    "receiver_name",
                ],
            },
            "payment_method": {
                "type": "string",
                "enum": ["transfer", "payment_link", "cash_on_delivery"],
                "description": (
                    "Método de pago elegido por el cliente: 'transfer' = "
                    "pago anticipado (Nequi/llave), 'payment_link' = link "
                    "de pago (con recargo), 'cash_on_delivery' = contra "
                    "entrega (solo pedidos > $45.000 COP)."
                ),
            },
            "subtotal_cop": {"type": "integer", "minimum": 0},
            "shipping_cop": {"type": "integer", "minimum": 0},
            "total_cop": {"type": "integer", "minimum": 1},
            "currency": {
                "type": "string",
                "default": "COP",
                "description": "Por ahora siempre 'COP'.",
            },
        },
        "required": [
            "items",
            "shipping",
            "payment_method",
            "subtotal_cop",
            "shipping_cop",
            "total_cop",
        ],
    }

    def __init__(
        self,
        workspace: str | Path,
        vault_dir: str | Path | None = None,
        port: OrderRegistrationPort | None = None,
    ) -> None:
        # Mismo patrón que `ManageConversationTagTool`: el `workspace` que
        # llega es el RUNTIME WORKSPACE CANONICO compartido — NO se usa
        # para metadata. `vault_dir` (DI-friendly): default vault canónico.
        self._workspace = Path(workspace)
        self._vault_dir = (
            Path(vault_dir) if vault_dir is not None else WORKSPACE_VAULT_DIR
        )
        # DI: el composition root pasa el port real
        # (`MedusaOrderRegistration`). Si nadie inyecta, usamos el stub —
        # esto permite que la tool sea constructible en tests sin tener que
        # mockear Medusa, y permite dev sin Medusa configurado.
        self._port: OrderRegistrationPort = port or StubOrderRegistration()

    def _session_attribution(self, session_key: str) -> dict[str, Any] | None:
        """Atribución CTWA de la sesión (`origin` de metadata.json): el
        `source_id` del referral es el AD ID de Meta. Best-effort: sesión
        directa / metadata ausente o rota → None (la orden se registra igual)."""
        metadata_file = self._vault_dir / session_key / "metadata.json"
        try:
            origin = json.loads(metadata_file.read_text(encoding="utf-8")).get("origin") or {}
        except (OSError, json.JSONDecodeError):
            return None
        source_id = origin.get("source_id")
        if not source_id:
            return None
        return {
            "meta_ad_id": str(source_id),
            "attribution_channel": origin.get("channel"),
        }

    async def execute_with_context(
        self,
        ctx: ToolContext,
        items: list[dict[str, Any]],
        shipping: dict[str, Any],
        payment_method: str,
        subtotal_cop: int,
        shipping_cop: int,
        total_cop: int,
        currency: str = "COP",
    ) -> str:
        # Convertir JSON-schema params a frozen DTOs del port.
        order_items = [
            OrderItem(
                handle=str(it["handle"]),
                quantity=int(it["quantity"]),
                unit_price_cop=int(it["unit_price_cop"]),
                variant_label=str(it["variant_label"])
                if it.get("variant_label") is not None else None,
            )
            for it in items
        ]
        # Requisito 2026-08-31: la transportadora exige el nombre de quien
        # recibe — sin él NO se registra (el LLM debe recolectarlo primero).
        # La cédula es opcional y viaja solo si el cliente la dio.
        receiver_name = str(shipping.get("receiver_name") or "").strip()
        if not receiver_name:
            return json.dumps(
                {
                    "registered": False,
                    "order_id": None,
                    "error_detail": "missing_receiver_name",
                    "summary": (
                        "Falta el NOMBRE DE QUIEN RECIBE el pedido (la "
                        "transportadora lo exige). Pregúntale al cliente "
                        "quién recibe el paquete, guárdalo con "
                        "`set_order_slot(nombre_recibe=...)` y vuelve a "
                        "llamar `register_order` incluyendo "
                        "`shipping.receiver_name`. La cédula es opcional."
                    ),
                },
                ensure_ascii=False,
            )
        national_id_raw = str(shipping.get("national_id") or "").strip()
        order_shipping = OrderShipping(
            city=str(shipping["city"]),
            neighborhood=str(shipping["neighborhood"]),
            address=str(shipping["address"]),
            phone=str(shipping["phone"]),
            receiver_name=receiver_name,
            national_id=national_id_raw or None,
        )

        # SEC-07: consistencia de montos server-side. El LLM manda los precios;
        # recomputamos el subtotal desde los line items para que un total
        # inventado (o manipulado por el cliente vía prompt injection) NO cree un
        # draft order. NO depende del catálogo — coupon-ready: cuando lleguen
        # cupones, `discount_cop` deja de ser 0. NOTA: esto caza el total
        # desconectado de los ítems, NO un unit_price bajado proporcionalmente
        # (eso requiere comparar contra catálogo); el gate humano de pago sigue
        # siendo el control primario de integridad de precio.
        computed_subtotal = sum(it.unit_price_cop * it.quantity for it in order_items)
        discount_cop = 0  # placeholder coupon-ready (hoy sin descuentos)
        expected_total = computed_subtotal + shipping_cop - discount_cop
        if subtotal_cop != computed_subtotal or total_cop != expected_total:
            logger.warning(
                "🧾 [TOOL register_order] AMOUNT_MISMATCH session={} "
                "subtotal_recibido={} subtotal_computado={} total_recibido={} total_esperado={}",
                ctx.session_key,
                subtotal_cop,
                computed_subtotal,
                total_cop,
                expected_total,
            )
            return json.dumps(
                {
                    "registered": False,
                    "order_id": None,
                    "error_detail": "amount_mismatch",
                    "summary": (
                        "Los montos NO cuadran con los ítems del pedido: "
                        f"subtotal esperado={computed_subtotal} (recibido {subtotal_cop}), "
                        f"total esperado={expected_total} (recibido {total_cop}). "
                        "Recalcula el pedido con los precios REALES del catálogo "
                        "(subtotal = suma de unit_price×cantidad; total = subtotal "
                        "+ envío) y llama de nuevo `register_order` con los montos "
                        "correctos. No inventes precios ni totales."
                    ),
                },
                ensure_ascii=False,
            )

        # Delegar al port (Medusa adapter o stub). La atribución CTWA viaja a
        # la metadata de la orden Medusa — es el join venta↔campaña del
        # dashboard (2026-07-09; sin esto Medusa no sabe de qué ad vino la venta).
        result: OrderRegistrationResult = await self._port.register_order(
            session_key=ctx.session_key,
            items=order_items,
            shipping=order_shipping,
            payment_method=payment_method,
            subtotal_cop=subtotal_cop,
            shipping_cop=shipping_cop,
            total_cop=total_cop,
            currency=currency,
            attribution=self._session_attribution(ctx.session_key),
        )

        # Generar un fallback order_id si el port no devolvio uno (no
        # deberia pasar — el stub siempre devuelve uno, el adapter Medusa
        # solo deja None si fallo). Si fallo, igual queremos un id de
        # auditoria local para reconciliacion manual.
        local_audit_id = (
            result.order_id
            or f"AUDIT-{ctx.session_key}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        )

        logger.info(
            "🧾 [TOOL register_order] session={} success={} provider={} order_id={} total={} {}",
            ctx.session_key,
            result.success,
            result.provider,
            result.order_id or "(none)",
            total_cop,
            currency,
        )

        # ------------------------------------------------------------
        # Persist en metadata.json — siempre, aun si fallo (audit log).
        # ------------------------------------------------------------
        registered_record: dict[str, Any] = {
            "order_id": result.order_id or local_audit_id,
            "session_key": ctx.session_key,
            "provider": result.provider,
            "success": result.success,
            "error_detail": result.error_detail,
            "customer_id": result.customer_id,
            "items": items,
            "shipping": shipping,
            "payment_method": payment_method,
            "subtotal_cop": subtotal_cop,
            "shipping_cop": shipping_cop,
            "total_cop": total_cop,
            "currency": currency,
            "registered_at_ms": int(time.time() * 1000),
            "raw_provider_payload": result.raw_payload,
        }

        metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
        metadata_file.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if metadata_file.exists():
            try:
                data = json.loads(metadata_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}

        if result.success:
            data["registered_order"] = registered_record
            # Episode lifecycle: anotar order_id en el episodio activo (NO
            # cerrar — el cierre lo hace manage_conversation_tag(COMPRA_EXITOSA)
            # que el agente invoca justo después). Defensivo: si no hay
            # episodio activo, attach_order_to_active_episode crea uno.
            attach_order_to_active_episode(
                data,
                order_id=registered_record["order_id"],
                now_ms=registered_record["registered_at_ms"],
                # Congela el ingreso de la venta en el episodio (major units
                # COP) — lo agrega `list_ads_campaigns` como `revenue` por
                # campaña sin tener que consultar Medusa en read-time (R-DIP).
                order_total_cop=total_cop,
                currency=currency,
            )
            # Pago anticipado (transfer) → el SISTEMA manda la llave Nequi y
            # los datos bancarios, no el LLM (caso wa_573125671604: el LLM
            # alucinó cuenta y NIT). Link de pago → el SISTEMA avisa el link
            # y su recargo (el link real lo genera el humano tras la
            # escalación). Encolamos el intent acá — determinista, pasa
            # aunque el LLM no emita ninguna tool más. El flush renderiza la
            # plantilla fija según `method`; los params NO llevan datos
            # bancarios (nunca pasan por el LLM ni por metadata).
            if payment_method in ("transfer", "payment_link"):
                intents = data.setdefault("pending_ui_intents", [])
                # Referencia humana ("#22 (Plegaria de Luz)") — el display_id
                # ya existe acá: Medusa lo asigna al crear el draft. El
                # order_id interno sigue viajando (idempotency key + audit).
                params: dict[str, Any] = {
                    "order_id": registered_record["order_id"],
                    "total_cop": total_cop,
                    "currency": currency,
                    "method": payment_method,
                }
                reference = _order_reference(result.raw_payload)
                if reference:
                    params["order_reference"] = reference
                intents.append({
                    "id": f"payinstr-{registered_record['order_id']}",
                    "kind": "payment_instructions",
                    "params": params,
                    "analytics": {
                        "component_id": "payment_instructions",
                        "component_kind": "text",
                    },
                    "queued_at_ms": registered_record["registered_at_ms"],
                })
        else:
            # Falla: NO sobrescribimos `registered_order` (preserva exitosos
            # previos) pero apendiamos a `failed_order_registrations[]` para
            # que el humano sepa que hubo intento + tenga el payload completo.
            # `status=pending` habilita el loop de reconciliación
            # (platform/orders/reconciliation.py): un reintento automático
            # (script/cron) o manual (dashboard) lo marcará resolved/abandoned.
            registered_record["status"] = STATUS_PENDING
            failed_log = data.setdefault("failed_order_registrations", [])
            failed_log.append(registered_record)
        history = data.setdefault("registered_orders_history", [])
        history.append({
            "order_id": registered_record["order_id"],
            "provider": result.provider,
            "success": result.success,
            "ts_ms": registered_record["registered_at_ms"],
        })

        metadata_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # ------------------------------------------------------------
        # Envelope para el LLM.
        # ------------------------------------------------------------
        if result.success:
            # Motivo sintético: lo lee la RED DE SEGURIDAD del workflow
            # (`ensure_payment_pending_closure_activity`) si el LLM no completa
            # la secuencia de cierre, para que el `status_history` / la
            # escalación tengan un texto coherente.
            order_motivo = (
                f"Cliente confirmó pedido {result.order_id} por "
                f"${total_cop} {currency}, método {payment_method}; "
                "falta verificación humana del pago. Pendiente: definir "
                "con el cliente el color del portavelas (según "
                "disponibilidad)."
            )
            envelope = {
                "registered": True,
                "order_id": result.order_id,
                "provider": result.provider,
                # Fix integridad orden↔tag (ADR-001): decisión que el workflow
                # levanta en `TurnResult.order_registered_decision`. Hace
                # VISIBLE el registro al workflow para que garantice el cierre
                # "pago pendiente" + escalación aunque el LLM no emita las
                # tools siguientes.
                "order_registered": {
                    "session_id": ctx.session_key,
                    "order_id": result.order_id,
                    "payment_method": payment_method,
                    "total_cop": total_cop,
                    "currency": currency,
                    "motivo": order_motivo,
                },
                "summary": (
                    f"Pedido registrado en Medusa con ID {result.order_id}. "
                    f"Total: ${total_cop:,} {currency}. ".replace(",", ".")
                    + (
                        "El SISTEMA ya le envía al cliente los datos del "
                        "pago anticipado (llave Nequi y banco) — NO escribas "
                        "números de cuenta ni banco ni NIT ni ningún dato "
                        "de pago en tu mensaje (cualquier dato que escribas "
                        "tú es inventado). "
                        if payment_method == "transfer"
                        else ""
                    )
                    + (
                        "El SISTEMA ya le avisó al cliente que el link de "
                        "pago le llega por este chat, con su recargo — NO "
                        "inventes links ni montos con recargo. "
                        if payment_method == "payment_link"
                        else ""
                    )
                    + "Llama ahora "
                    "`manage_conversation_tag(tag='CONFIRMADO_PAGO_PENDIENTE', "
                    "motivo=...)` y luego `escalate_to_human(reason_category="
                    "'PAYMENT_VERIFICATION_PENDING', summary=...)` para que un "
                    "humano verifique el pago; incluye en el summary la nota "
                    "'definir con el cliente el color del portavelas (según "
                    "disponibilidad)'. Despide al cliente con un mensaje "
                    "breve de agradecimiento que además le avise que al "
                    "finalizar el pago del pedido se escogen los colores del "
                    "portavelas, según disponibilidad. NO menciones la "
                    "verificación del pago "
                    "ni que alguien va a revisar nada (el humano se encarga por "
                    "detrás). NO uses "
                    "`COMPRA_EXITOSA` — esa tag la pone el humano tras verificar "
                    "el pago."
                ),
            }
        else:
            envelope = {
                "registered": False,
                "order_id": None,
                "provider": result.provider,
                "error_detail": result.error_detail,
                "audit_id": local_audit_id,
                "summary": (
                    "El sistema de pedidos (Medusa) NO aceptó el registro: "
                    f"{result.error_detail or 'unknown error'}. Los datos "
                    f"quedaron guardados localmente con audit_id={local_audit_id} "
                    "para que un humano pueda completarlo manualmente. "
                    "Llama AHORA `escalate_to_human"
                    "(reason_category='ORDER_REGISTRATION_FAILED', "
                    "summary='cliente cerró pedido pero Medusa rechazó el "
                    "registro')` y despide al cliente con: 'Tu pedido quedó "
                    "tomado y un humano te confirma en unos minutos.' NO uses "
                    "`manage_conversation_tag(COMPRA_EXITOSA)` — la venta NO "
                    "está formalmente cerrada hasta que el humano la registre."
                ),
            }

        return json.dumps(envelope, ensure_ascii=False)
