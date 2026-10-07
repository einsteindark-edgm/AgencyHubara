"""El cliente acaba de tocar «Confirmar» en el resumen (2026-10-07).

Desde las guardas del cierre, `register_order` con un episodio activo exige
una confirmación (`purchase_signals.closing_blocker`). Las pruebas que
registran un pedido para verificar OTRA cosa (cupos, CAPI, colores, precios)
parten de esta metadata: el último mensaje del cliente es la confirmación.
"""
from __future__ import annotations

from typing import Any

CONFIRMED_NOW: dict[str, Any] = {
    "last_inbound_message_id": "wamid.CONFIRMA",
    "last_inbound_signal": {"kind": "affirmation", "at_ms": 1, "message_id": "wamid.CONFIRMA", "text": "Confirmar"},
}
