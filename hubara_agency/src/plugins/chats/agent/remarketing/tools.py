"""Tools del agente Remarketing con contexto de dominio.

`transfer_to_sales_agent`: el resumen del handoff lo escribe el LLM, y en el
run 01a0a0eb (2026-09-14) convirtió "Voy apenas en camino a casa" en
"siguiente paso: confirmar el pedido y tomar datos de envío". Ventas confía en
ese resumen por diseño, así que acá se corrige de forma determinista: si el
último inbound fue un aplazamiento, el handoff lo dice primero y prohíbe
avanzar el cierre. `deferral_aware_transfer_tool` envuelve la tool de
plataforma que el worker (composition root) ya importa — P-28.
"""
from __future__ import annotations

import json
from typing import Any

from exoclaw.agent.tools import ToolContext

from src.plugins.chats.shared.purchase_signals import current_signal


def deferral_aware_transfer_tool(base: type) -> type:
    """Subclase de `base` (la `TransferToSalesAgentTool` de plataforma)."""

    class RemarketingTransferToSalesTool(base):  # type: ignore[misc,valid-type]
        async def execute_with_context(self, ctx: ToolContext, resumen: str) -> str:
            signal = current_signal(self._read_metadata(ctx))
            if signal and signal.get("kind") == "deferral":
                quoted = str(signal.get("text") or "").strip()
                resumen = (
                    f"[EL CLIENTE APLAZÓ] Escribió \"{quoted}\": NO confirmó nada. "
                    "Responde con UNA frase breve y cálida, NO pidas datos de envío "
                    "ni confirmes el pedido; espera a que retome. | Contexto: "
                    f"{resumen}"
                )
            return await super().execute_with_context(ctx, resumen=resumen)

        def _read_metadata(self, ctx: ToolContext) -> dict[str, Any]:
            metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
            if not metadata_file.exists():
                return {}
            try:
                return json.loads(metadata_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}

    RemarketingTransferToSalesTool.__name__ = "RemarketingTransferToSalesTool"
    RemarketingTransferToSalesTool.__qualname__ = "RemarketingTransferToSalesTool"
    return RemarketingTransferToSalesTool


__all__ = ["deferral_aware_transfer_tool"]
