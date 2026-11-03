"""CAST reviews→chats (``session-actions@v1`` → ``session-ref``) — canal 3.

Con 1–3 estrellas la conversación pasa al equipo («alguien del equipo te
escribe hoy»). Pasar a humano es lógica de ``chats`` (route + tag juntos,
invariante del handoff): P-3 prohíbe importarla, así que el worker de reseñas
llama al contrato HTTP que chats publica en
``/api/chats/session-actions/{session_key}/escalate`` con identidad de
servicio (``HUBARA_SERVICE_TOKEN``), igual que el ETA con mba. Base
``HUBARA_API_BASE_URL`` (nombre del servicio de la compose; el gate
``castkit_loopback`` prohíbe el loopback).

Declarado en el manifest de reviews::

    depends_on: [chats]
    consumes:
      - { provider: chats, contract: session-actions@v1, into: session-ref,
          cast: api/chats_cast }
"""
from __future__ import annotations

import os
from urllib.parse import quote

from src.sdk import castkit

__all__ = ["escalate_low_rating"]

CAST_LABEL = "reviews→chats.escalate"
#: Una escalación es una escritura local del vault: 15 s sobra (L-1).
_TIMEOUT_S = 15.0
#: La categoría que ve el equipo en la bandeja humana.
REASON_CATEGORY = "LOW_RATING"


def _api_base() -> str:
    return os.environ.get("HUBARA_API_BASE_URL", "http://hubara-api:8000").rstrip("/")


async def escalate_low_rating(session_id: str, summary: str) -> bool:
    """La conversación pasa al equipo con el motivo (la calificación). True si
    chats confirma que quedó con el equipo (la pasó ahora o ya la tenía un
    humano con tag HUMANO)."""
    path = f"/api/chats/session-actions/{quote(session_id, safe='')}/escalate"
    answer = await castkit.forward(
        None,  # sin request entrante: identidad de servicio
        "POST",
        path,
        base_url=_api_base(),
        timeout=_TIMEOUT_S,
        cast_label=CAST_LABEL,
        body={"reason_category": REASON_CATEGORY, "summary": summary[:2000]},
        auth="service",
    )
    return bool(answer.get("escalated") or answer.get("already_human"))
