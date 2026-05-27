"""Tool: EscalateToHumanTool.

DEHA-compliant tool que escala una conversacion a un humano. Patron espejo
de `TransferToSalesAgentTool` (`platform/tools/routing.py`): inerte respecto
a Temporal, escribe la decision en `metadata.json` y devuelve un envelope
JSON con `escalation_decision` que el workflow lee.

Trigger desde el LLM cuando el caso cae en cualquiera de las reglas de
escalacion documentadas en `sales_whatsapp/workspace/TOOLS.md`. La taxonomia
`reason_category` es enforced por el JSON schema `enum`.

Side effects:
  * `metadata.json[tag] = "HUMANO"` — el frontend ya soporta este tag
    (`frontend_dashboard/src/entities/chat/model.ts:10`,
    `entities/chat/api.ts:44-57`).
  * `metadata.json[active_route] = "humano"` — `LoadOrStartSalesSession`
    chequea esta ruta y omite el dispatch al workflow (no LLM mas en este chat).
  * `metadata.json[motivo] = summary`.
  * `status_history` appended con timestamp.

ADR-001: NO importa `temporal_client`, NO llama `start_workflow`. Devuelve
una `escalation_decision` que `run_agent_turn` parsea a `EscalationDecision`
y el workflow termina con `_force_shutdown`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import ROUTE_HUMANO


_REASON_CATEGORIES: list[str] = [
    # Volumen / B2B
    "BULK_ORDER",          # >20 unidades en una sola orden
    "DISCOUNT_REQUEST",    # cliente pide descuento explicito
    "WHOLESALE_B2B",       # distribuidor, reventa, mayorista
    "CORPORATE_EVENT",     # evento, boda, regalo corporativo, feria
    # Producto
    "CUSTOMIZATION",       # aroma/etiqueta/color custom fuera de catalogo
    "CATALOG_GAP",         # cliente busca algo que no aparece y persiste
    # Post-venta / logistica
    "POST_SALE_ISSUE",     # roto, defectuoso, devolucion, reembolso
    "SHIPPING_ISSUE",      # demora, tracking, cambio direccion
    # Riesgo
    "HEALTH_SAFETY",       # alergia, embarazo, bebe, mascotas, toxicidad
    "RITUAL_GUIDANCE",     # guia espiritual mas alla del nombre del producto
    "INTERNATIONAL",       # fuera de Colombia que insiste
    "PAYMENT_EDGECASE",    # tarjeta extranjera, divisa, factura regimen especial
    # Operacional
    "CHECKOUT_VERIFY_FAILED",  # verify_order_for_checkout fallo / catalog unavailable
    # Sesión c4e3416f: cliente confirmó el pedido (present_order_confirmation
    # con "Confirmar") pero NO completó el Flow de datos de envío en ~10 min.
    # El humano cierra manualmente pidiendo los datos faltantes por chat o WA.
    "ORDER_PENDING_SHIPPING_DETAILS",
    # Medusa rechazó / no respondió al `register_order` (network down, 5xx
    # persistente, settings inválidos como MEDUSA_REGION_ID erróneo). El
    # cliente confirmó el pedido, los datos están guardados en
    # `metadata.failed_order_registrations[]`, y el humano debe registrarlo
    # manualmente en Medusa Admin con esos datos. Para que el dashboard
    # pueda hacer pop fácil de la cola, ver `audit_id` en el envelope.
    "ORDER_REGISTRATION_FAILED",
    # HU "verificación humana de pago" (operativo hasta tener pasarela):
    # `register_order` devolvió `registered=true` (orden creada en Medusa)
    # pero el LLM NO puede confirmar si el pago se efectuó. Los 3 métodos
    # (card, transfer, cash_on_delivery) requieren verificación humana
    # del pago antes de marcar la venta como cerrada. El humano confirma
    # o rechaza el pago desde el dashboard de orders y, según el resultado,
    # cambia la tag a COMPRA_EXITOSA o aborta el pedido. SIEMPRE usar en
    # combo con `manage_conversation_tag("CONFIRMADO_PAGO_PENDIENTE")`.
    "PAYMENT_VERIFICATION_PENDING",
    "EXPLICIT_REQUEST",        # cliente pide humano o muestra frustracion
    "OTHER",
]


class EscalateToHumanTool(ToolBase):
    """Escala la conversacion a un asesor humano.

    Una vez llamada, `active_route=humano` queda persistido en metadata.json
    y todos los mensajes entrantes posteriores del cliente NO seran procesados
    por el LLM — el humano los lee desde el dashboard y responde manualmente.
    """

    name = "escalate_to_human"
    description = (
        "Transfiere la conversación a un asesor humano del equipo Hubara. "
        "Úsala cuando el caso requiera intervención humana: pedidos al por "
        "mayor (>20 unidades), descuentos, B2B/distribuidores, eventos "
        "corporativos, personalización, problemas post-venta, salud/seguridad, "
        "o cuando el cliente lo pide explícitamente. Ver la sección 'Cuándo "
        "escalar a humano' en TOOLS.md para la taxonomía completa."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reason_category": {
                "type": "string",
                "enum": _REASON_CATEGORIES,
                "description": (
                    "Categoría del trigger de escalación. Una de: "
                    "BULK_ORDER (>20 unidades), DISCOUNT_REQUEST (cliente "
                    "pidió descuento), WHOLESALE_B2B (distribuidor/reventa), "
                    "CORPORATE_EVENT (boda/evento/empresa), CUSTOMIZATION "
                    "(aroma/etiqueta custom), POST_SALE_ISSUE (queja/"
                    "devolución), SHIPPING_ISSUE (demora/tracking), "
                    "HEALTH_SAFETY (alergia/embarazo/bebé/mascotas), "
                    "RITUAL_GUIDANCE (guía espiritual), INTERNATIONAL "
                    "(fuera de Colombia insiste), PAYMENT_EDGECASE (pago "
                    "no estándar), CHECKOUT_VERIFY_FAILED (no se pudo "
                    "verificar precio live), CATALOG_GAP (producto no "
                    "aparece pero cliente persiste), "
                    "ORDER_PENDING_SHIPPING_DETAILS (cliente confirmó el "
                    "pedido pero no completó los datos de envío y dejó la "
                    "conversación — humano cierra pidiendo los datos), "
                    "ORDER_REGISTRATION_FAILED (Medusa rechazó el "
                    "register_order — humano registra manualmente con los "
                    "datos guardados en metadata.failed_order_registrations), "
                    "PAYMENT_VERIFICATION_PENDING (orden registrada OK pero "
                    "el LLM no puede confirmar si el pago se efectuó — "
                    "obligatorio para los 3 métodos de pago hasta que haya "
                    "pasarela integrada; usar SIEMPRE en combo con "
                    "manage_conversation_tag('CONFIRMADO_PAGO_PENDIENTE')), "
                    "EXPLICIT_REQUEST (cliente pide humano o está "
                    "frustrado), OTHER."
                ),
            },
            "summary": {
                "type": "string",
                "description": (
                    "Resumen de 1-2 líneas para el humano que va a tomar el "
                    "caso: qué pidió el cliente, qué intentaste, y qué "
                    "necesita confirmación. Sin información sensible."
                ),
                "minLength": 1,
            },
        },
        "required": ["reason_category", "summary"],
    }

    def __init__(
        self,
        workspace: str | Path,
        vault_dir: str | Path | None = None,
    ) -> None:
        # Mismo patron que TransferToSalesAgentTool / ManageConversationTagTool:
        # el `workspace` que llega es el RUNTIME WORKSPACE CANONICO compartido,
        # no se usa para metadata. `vault_dir` (DI-friendly): default =
        # WORKSPACE_VAULT_DIR.
        self._workspace = Path(workspace)
        self._vault_dir = (
            Path(vault_dir) if vault_dir is not None else WORKSPACE_VAULT_DIR
        )

    async def execute_with_context(
        self,
        ctx: ToolContext,
        reason_category: str,
        summary: str,
    ) -> str:
        metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
        metadata_file.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if metadata_file.exists():
            try:
                data = json.loads(metadata_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}

        data["active_route"] = ROUTE_HUMANO
        data["tag"] = "HUMANO"
        data["motivo"] = summary
        data["escalation_reason"] = reason_category

        history = data.setdefault("status_history", [])
        history.append(
            {
                "tag": "HUMANO",
                "motivo": summary,
                "active_route": ROUTE_HUMANO,
                "reason_category": reason_category,
                "timestamp": time.time(),
            }
        )

        metadata_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        decision_payload = {
            "escalation_decision": {
                "session_id": ctx.session_key,
                "reason_category": reason_category,
                "summary": summary,
            },
            "message": (
                "Escalación registrada. El cliente queda en la cola humana; "
                "NO generes más respuestas en este chat."
            ),
        }
        return json.dumps(decision_payload, ensure_ascii=False)
