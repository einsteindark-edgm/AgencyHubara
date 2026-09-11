"""CAST mba→chats (``session-actions@v1`` → ``session-ref``) — canal 3, PLUGIN_CONTRACT §5.3.

Las 4 tools de ESCRITURA que Meta Business Agent invoca (``set_order_slot``,
``register_order``, ``manage_conversation_tag``, ``escalate_to_human``) son
lógica de ``chats`` (draft del episodio activo, registro + cierre + escalación,
etiquetas, ruta humana). P-3 prohíbe importarla: ``mba`` consume el contrato
HTTP que chats publica en ``/api/chats/session-actions/{session_key}/*``.

Declarado en el manifest de mba::

    depends_on: [chats, orders, catalog]
    consumes:
      - { provider: chats, contract: session-actions@v1, into: session-ref,
          cast: api/chats_cast }

Por qué ``auth="service"``: el edge es Meta con ``X-API-Key`` — no hay bearer
de operador que portar al 2º hop. El castkit manda ``HUBARA_SERVICE_TOKEN``
(paso 1 de ``require_auth``). Timeout dimensionado por el UPSTREAM del provider
(L-1): ``/order`` registra en Medusa (30 s × reintentos) y envía las
instrucciones de pago por WhatsApp; default 90 s, override ``CHATS_CAST_TIMEOUT_S``.
Base del provider: loopback IPv4 (``CHATS_API_BASE`` para override).
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

from fastapi import Request

from src.sdk import castkit

__all__ = ["session_action"]

CAST_LABEL = "mba→chats"
_DEFAULT_TIMEOUT_S = 90.0


def _chats_base() -> str:
    return os.environ.get("CHATS_API_BASE", "http://127.0.0.1:8000").rstrip("/")


def _timeout_s() -> float:
    return float(os.environ.get("CHATS_CAST_TIMEOUT_S", _DEFAULT_TIMEOUT_S))


async def session_action(
    request: Request, session_key: str, action: str, body: dict[str, Any]
) -> dict[str, Any]:
    """``POST /api/chats/session-actions/{session_key}/{action}`` con identidad de servicio."""
    path = f"/api/chats/session-actions/{quote(session_key, safe='')}/{quote(action, safe='')}"
    return await castkit.forward(
        request,
        "POST",
        path,
        base_url=_chats_base(),
        timeout=_timeout_s(),
        cast_label=CAST_LABEL,
        body=body,
        auth="service",
    )
