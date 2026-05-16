"""Tool: VerifyOrderForCheckoutTool.

DEHA-compliant tool que verifica precio y disponibilidad LIVE contra Medusa
JUSTO antes de cerrar la venta. Es el unico momento de la conversacion donde
la fuente de la verdad NO es el snapshot — el snapshot puede tener dias o
semanas (es valido durante la charla), pero al cobrar verificamos contra la
API real para detectar cambios de precio que hubieran ocurrido.

El envelope que devuelve es leido por el LLM, no parseado al workflow (no es
un "decision tool" como escalation/transfer). Las acciones derivadas
(escalar si verify falla, avisar al cliente si hay discrepancia) las dicta
el prompt `TOOLS.md`.

Inerte respecto a Temporal (ADR-001). I/O real via `CheckoutVerificationPort`
inyectado por el composition root del worker.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext
from loguru import logger

from src.platform.catalog.checkout_port import (
    CheckoutItem,
    CheckoutVerificationPort,
)


class VerifyOrderForCheckoutTool(ToolBase):
    """Verifica items contra la API live de Medusa antes de cerrar venta.

    Llamada OBLIGATORIA antes de confirmar pedido al cliente (ver
    `sales_whatsapp/workspace/TOOLS.md` -> "Verificación en checkout").
    """

    name = "verify_order_for_checkout"
    description = (
        "Verifica precio y disponibilidad EN VIVO contra Medusa para los "
        "items del pedido ANTES de cerrar la venta. Úsala una sola vez, "
        "justo después de tener producto + cantidad + datos del cliente y "
        "ANTES de confirmar el pedido. Si hay discrepancia de precio contra "
        "el snapshot que ya le mostraste, avísale al cliente honestamente y "
        "pídele confirmación con el precio actualizado. Si la API no "
        "responde (error: catalog_unavailable), escala a humano con "
        "reason_category=CHECKOUT_VERIFY_FAILED."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Lista de items a verificar. Cada item: {handle, "
                    "quantity}. Los handles deben ser EXACTAMENTE los "
                    "vistos previamente en search_products / "
                    "get_product_by_handle — NO los inventes."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "handle": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        },
                        "quantity": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 999,
                        },
                    },
                    "required": ["handle", "quantity"],
                },
            },
        },
        "required": ["items"],
    }

    def __init__(
        self,
        workspace: str | Path,
        verifier: CheckoutVerificationPort,
    ) -> None:
        self._workspace = Path(workspace)
        self._verifier = verifier

    async def execute_with_context(
        self,
        ctx: ToolContext,
        items: list[dict[str, Any]],
    ) -> str:
        parsed = [
            CheckoutItem(handle=str(it["handle"]), quantity=int(it["quantity"]))
            for it in items
        ]
        logger.info(
            "🧾 [TOOL verify_order_for_checkout] session={} items={}",
            ctx.session_key,
            [(p.handle, p.quantity) for p in parsed],
        )

        result = await self._verifier.verify_items(parsed)

        if not result.catalog_available:
            logger.warning(
                "🧾 [TOOL verify_order_for_checkout] catalog_unavailable: {}",
                result.error_detail,
            )
            return json.dumps(
                {
                    "error": "catalog_unavailable",
                    "detail": result.error_detail,
                    "message": (
                        "No se pudo verificar el precio en vivo. Si esto se "
                        "repite, escala con escalate_to_human "
                        "(reason_category='CHECKOUT_VERIFY_FAILED')."
                    ),
                },
                ensure_ascii=False,
            )

        items_payload = [asdict(vi) for vi in result.items]
        any_discrepancy = any(vi.discrepancy for vi in result.items)

        logger.info(
            "🧾 [TOOL verify_order_for_checkout] verified={} discrepancy={}",
            result.verified, any_discrepancy,
        )

        envelope: dict[str, Any] = {
            "verified": result.verified,
            "discrepancy": any_discrepancy,
            "items": items_payload,
        }
        if any_discrepancy:
            envelope["message"] = (
                "Hay diferencias de precio o disponibilidad entre el snapshot "
                "y la fuente live. Comunícale al cliente con honestidad cada "
                "discrepancia (producto + precio anterior → precio actualizado) "
                "y pídele que confirme con el precio nuevo antes de cerrar el "
                "pedido. Si no acepta, registra tag RECHAZO."
            )
        else:
            envelope["message"] = (
                "Verificación OK: snapshot y live coinciden. Procede a "
                "confirmar el pedido normalmente."
            )

        return json.dumps(envelope, ensure_ascii=False)
