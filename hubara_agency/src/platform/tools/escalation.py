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
from src.platform.llm_text_sanitizer import (
    keep_customer_safe_sentences,
    sanitize_llm_text,
)

# Despedidas aprobadas del relevo (run 5ed9af2d). Regla del operador: el
# cliente nunca debe notar cuándo lo atiende el bot y cuándo una persona — el
# relevo se nombra "un colega del equipo", jamás "un humano". Se usan cuando el
# LLM no mandó `customer_message` (sesión en vuelo con el schema viejo) o
# cuando lo que mandó rompe la persona / huele a reporte interno.
# Sin marca a propósito: esta tool es de plataforma (multi-tenant); la
# despedida con marca la redacta el LLM desde el guion de su workspace.
_DEFAULT_FAREWELL = "Un colega del equipo te responde en este mismo chat 🤍"
_FAREWELL_BY_REASON: dict[str, str] = {
    "PAYMENT_VERIFICATION_PENDING": (
        "Listo, tu pedido quedó registrado 🤍. Gracias por elegirnos."
    ),
    "ORDER_REGISTRATION_FAILED": (
        "Tu pedido quedó tomado 🤍. Un colega del equipo te confirma por "
        "este mismo chat."
    ),
}


# Bajo este largo lo que sobrevive al filtrado ya no es una despedida ("Claro
# 🤍.") — va la aprobada.
_MIN_FAREWELL_WORDS = 4


def resolve_customer_farewell(reason_category: str, customer_message: str | None) -> str:
    """El texto FINAL que el cliente lee al escalar. Nunca vacío.

    Del texto del LLM se caen SOLO las oraciones que rompen la persona
    (`breaks_human_persona`) o huelen a reporte interno
    (`looks_like_admin_leak`) — así el aviso del portavelas sobrevive a un "un
    humano verificará tu pago". Si falta o lo que queda es muy poco, va la
    despedida aprobada de la categoría. Reemplazo, no bloqueo: un falso
    positivo cuesta una oración, nunca un cliente sin respuesta.
    """
    text = keep_customer_safe_sentences(sanitize_llm_text(customer_message or "").text)
    if len(text.split()) >= _MIN_FAREWELL_WORDS:
        return text
    return _FAREWELL_BY_REASON.get(reason_category, _DEFAULT_FAREWELL)


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
    # La definición NO usa el vocabulario que después rompe la persona (guard:
    # test_tool_definition_does_not_prime_human_wording): el LLM redacta
    # `customer_message` con esto en frente.
    description = (
        "Pasa la conversación a un colega del equipo Hubara y TERMINA tu "
        "turno: lo único que el cliente lee es `customer_message`. Úsala "
        "cuando el caso lo debe llevar un colega: pedidos al por mayor (>20 "
        "unidades), descuentos, B2B/distribuidores, eventos corporativos, "
        "personalización, problemas post-venta, salud/seguridad, o cuando el "
        "cliente pide hablar con alguien más. Ver la sección 'Cuándo "
        "escalar' en TOOLS.md para la taxonomía completa."
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
                    "conversación — un colega cierra pidiendo los datos), "
                    "ORDER_REGISTRATION_FAILED (Medusa rechazó el "
                    "register_order — un colega registra manualmente con los "
                    "datos guardados en metadata.failed_order_registrations), "
                    "PAYMENT_VERIFICATION_PENDING (orden registrada OK pero "
                    "el LLM no puede confirmar si el pago se efectuó — "
                    "obligatorio para los 3 métodos de pago hasta que haya "
                    "pasarela integrada; usar SIEMPRE en combo con "
                    "manage_conversation_tag('CONFIRMADO_PAGO_PENDIENTE')), "
                    "EXPLICIT_REQUEST (cliente pide hablar con alguien más "
                    "del equipo o está frustrado), OTHER."
                ),
            },
            "summary": {
                "type": "string",
                "description": (
                    "Resumen INTERNO de 1-2 líneas para el colega que toma el "
                    "caso: qué pidió el cliente, qué intentaste, y qué "
                    "necesita confirmación. Sin información sensible. El "
                    "cliente nunca lo ve."
                ),
                "minLength": 1,
            },
            "customer_message": {
                "type": "string",
                "description": (
                    "OBLIGATORIO. El ÚNICO texto que el cliente lee en este "
                    "turno (tu content se descarta y después de esta tool ya "
                    "no escribes más). Una línea cálida, de tú y en primera "
                    "persona, como quien le pasa el caso a un compañero de "
                    "trabajo: nombra el relevo como 'un colega del equipo' o "
                    "'un compañero' que le responde en este mismo chat. Sin "
                    "prometer tiempos y sin mencionar procesos internos. "
                    "Ejemplo: 'Para esa cantidad te coordino con un colega "
                    "del equipo, que maneja esos pedidos y te responde en "
                    "este mismo chat 🤍'. Tras registrar un pedido va la "
                    "despedida de tu guion de cierre ('Listo, tu pedido quedó "
                    "registrado 🤍…')."
                ),
            },
        },
        # `customer_message` NO va en required a propósito: una sesión en vuelo
        # (tool_definitions del bootstrap pre-deploy) llama sin el param y no
        # debe rebotar en validate_params — cae a la despedida aprobada.
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
        customer_message: str = "",
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
            # Texto FINAL para el cliente: `run_agent_turn` lo usa como
            # final_content y TERMINA el turno sin otro llm_chat (run
            # 5ed9af2d: el llm_chat forzado tras este result produjo el acuse
            # "Listo, la conversación quedó en manos del equipo humano.").
            "customer_message": resolve_customer_farewell(
                reason_category, customer_message
            ),
            # Queda en el historial que verá el LLM en sesiones futuras: sin
            # vocabulario que oponga persona vs. sistema y sin órdenes que
            # pidan un acuse.
            "message": (
                "Hecho: un colega del equipo continúa la atención en este "
                "chat. La despedida ya se le envió al cliente."
            ),
        }
        return json.dumps(decision_payload, ensure_ascii=False)
