"""Tool: RegisterOrderTool.

Registra un pedido formalmente cuando la venta cierra con éxito
(`COMPRA_EXITOSA`). Esta tool es por ahora un **stub**: escribe el pedido a
`metadata.registered_order` + emite un log estructurado, y devuelve un
`order_id` placeholder. En una iteración futura se conectará con el sistema
de gestión de pedidos (Medusa / ERP / cola de cumplimiento), pero la
firma + ubicación de la llamada queda definida desde ya para que el LLM
forme el hábito de invocarla al cierre.

DEHA:
  * Tool INERTE — escribe a metadata.json via FilesystemMetadataStore, sin
    httpx, sin temporal client, sin imports de workflows.
  * Idempotente: si el LLM la llama 2 veces en la misma sesión, sobrescribe
    `registered_order` (no genera duplicados externos porque hoy no toca
    sistemas externos).
  * R-JSON: input/output JSON-serializable.

Patrón LLM (ver TOOLS.md §"Reglas de cierre de venta"):
  1. Cliente completa el Flow (`nfm_reply`) o manda los datos por texto.
  2. LLM llama `verify_order_for_checkout(items)` → verified=True.
  3. LLM llama `present_order_confirmation(items, shipping, payment)`.
  4. Cliente apreta "✅ Confirmar".
  5. LLM llama `register_order(...)` con los datos finales.   ← ESTA TOOL
  6. LLM llama `manage_conversation_tag(tag="COMPRA_EXITOSA", motivo=...)`.
  7. LLM despide al cliente con un mensaje cálido.

Esta secuencia es la única forma legítima de cerrar una venta. El LLM NO
debe poner `COMPRA_EXITOSA` sin haber llamado `register_order` antes.
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


class RegisterOrderTool(ToolBase):
    """Registra un pedido al cierre exitoso de la venta.

    Por ahora es un stub que persiste a `metadata.registered_order` + loguea.
    Futuro: conectar con Medusa Orders / ERP / cola de cumplimiento.
    """

    name = "register_order"
    description = (
        "Registra formalmente el pedido del cliente cuando la venta cerró "
        "exitosamente. Llámala SOLO después de que el cliente confirmó el "
        "pedido (vía `present_order_confirmation`) Y ya tenés todos los "
        "datos de envío (ciudad, barrio, dirección, teléfono, método de "
        "pago). Esta tool persiste el pedido para que el equipo de Hubara "
        "lo procese — sin esta llamada, el pedido queda en estado "
        "'verbalmente confirmado pero no registrado' y se puede perder. "
        "Justo después de invocarla, llamá `manage_conversation_tag"
        "(tag='COMPRA_EXITOSA', motivo=...)` para cerrar la conversación. "
        "Si el cliente confirmó pero NO completó los datos de envío, NO "
        "uses esta tool — usá `escalate_to_human"
        "(reason_category='ORDER_PENDING_SHIPPING_DETAILS')` para que un "
        "humano cierre."
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
                },
                "required": ["city", "neighborhood", "address", "phone"],
            },
            "payment_method": {
                "type": "string",
                "enum": ["card", "transfer", "cash_on_delivery"],
                "description": "Método de pago elegido por el cliente.",
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
    ) -> None:
        # Mismo patrón que `ManageConversationTagTool`: el `workspace` que
        # llega es el RUNTIME WORKSPACE CANONICO compartido — NO se usa
        # para metadata. `vault_dir` (DI-friendly): default vault canónico.
        self._workspace = Path(workspace)
        self._vault_dir = (
            Path(vault_dir) if vault_dir is not None else WORKSPACE_VAULT_DIR
        )

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
        # Stub: en el futuro, este `order_id` vendría del sistema externo
        # (Medusa Orders / ERP / cola). Por ahora lo generamos local y se
        # persiste en metadata para auditoría + reconciliación posterior.
        order_id = f"HUB-{ctx.session_key}-{int(time.time())}-{uuid.uuid4().hex[:6]}"

        registered_order: dict[str, Any] = {
            "order_id": order_id,
            "session_key": ctx.session_key,
            "items": items,
            "shipping": shipping,
            "payment_method": payment_method,
            "subtotal_cop": subtotal_cop,
            "shipping_cop": shipping_cop,
            "total_cop": total_cop,
            "currency": currency,
            "registered_at_ms": int(time.time() * 1000),
            "registered_via": "stub_v1",
        }

        logger.info(
            "🧾 [TOOL register_order] session={} order_id={} total={} {}",
            ctx.session_key,
            order_id,
            total_cop,
            currency,
        )

        # Persistir en metadata.json para auditoría + para que el dashboard
        # frontend lo pueda mostrar en el detalle de la conversación.
        metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
        metadata_file.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if metadata_file.exists():
            try:
                data = json.loads(metadata_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # metadata corrupto — reiniciamos en blanco con order
                # registrado para no perder esta info crítica.
                data = {}

        data["registered_order"] = registered_order
        # Historial — si el LLM llama register_order 2 veces (caso raro,
        # idempotency client-side), mantenemos el log de intentos.
        history = data.setdefault("registered_orders_history", [])
        history.append({"order_id": order_id, "ts_ms": registered_order["registered_at_ms"]})

        metadata_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return json.dumps(
            {
                "registered": True,
                "order_id": order_id,
                "summary": (
                    f"Pedido registrado con ID {order_id}. Total: "
                    f"${total_cop:,} {currency}. Llamá ahora "
                    "`manage_conversation_tag(tag='COMPRA_EXITOSA', "
                    "motivo=...)` para cerrar la conversación, y despedí "
                    "al cliente con un mensaje cálido (sin repetir los "
                    "datos del pedido — ya los confirmó)."
                ).replace(",", "."),
            },
            ensure_ascii=False,
        )
