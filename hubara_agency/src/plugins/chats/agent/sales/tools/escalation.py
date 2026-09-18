"""Escalación a humano del agente Sales con precondiciones de dominio.

`ORDER_PENDING_SHIPPING_DETAILS` significa "el cliente confirmó el pedido pero
no mandó los datos de envío". Run 01a0a0f1 (2026-09-14): el LLM la usó sin
que el cliente hubiera dicho que sí → la sesión quedó en ruta humano (bot
mudo) por un pedido que no existía. `guarded_escalation_tool` envuelve la
tool de plataforma (que el worker, composition root, ya importa — P-28: el
plugin no importa `src.platform`) y rechaza esa razón cuando no hay
confirmación de compra registrada; el resto de razones pasa intacto.
"""
from __future__ import annotations

import json
from typing import Any

from exoclaw.agent.tools import ToolContext

from src.plugins.chats.shared.purchase_signals import has_purchase_confirmation

GUARDED_REASON = "ORDER_PENDING_SHIPPING_DETAILS"


def guarded_escalation_tool(base: type) -> type:
    """Subclase de `base` (la `EscalateToHumanTool` de plataforma) con la guarda."""

    class SalesEscalateToHumanTool(base):  # type: ignore[misc,valid-type]
        async def execute_with_context(
            self,
            ctx: ToolContext,
            reason_category: str,
            summary: str,
            customer_message: str = "",
        ) -> str:
            if reason_category == GUARDED_REASON and not self._purchase_confirmed(ctx):
                return json.dumps(
                    {
                        "error": (
                            "precondition_failed: ORDER_PENDING_SHIPPING_DETAILS "
                            "exige que el cliente haya confirmado la compra (dijo "
                            "que sí a un producto con precio, o tocó Confirmar). En "
                            "este episodio no hay confirmación registrada: NO se "
                            "escaló. Etiqueta INTERESADO y deja que remarketing "
                            "retome; el bot sigue a cargo."
                        ),
                        "escalated": False,
                    },
                    ensure_ascii=False,
                )
            return await super().execute_with_context(
                ctx,
                reason_category=reason_category,
                summary=summary,
                customer_message=customer_message,
            )

        def _purchase_confirmed(self, ctx: ToolContext) -> bool:
            metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
            data: dict[str, Any] = {}
            if metadata_file.exists():
                try:
                    data = json.loads(metadata_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    data = {}
            return has_purchase_confirmation(data)

    SalesEscalateToHumanTool.__name__ = "SalesEscalateToHumanTool"
    SalesEscalateToHumanTool.__qualname__ = "SalesEscalateToHumanTool"
    return SalesEscalateToHumanTool


__all__ = ["GUARDED_REASON", "guarded_escalation_tool"]
