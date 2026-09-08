"""D1.7 — etiquetado por silencio cuando Meta Business Agent responde en el hilo.

Con MBA al frente no corre el workflow Sales, así que nadie etiqueta el
episodio cuando el cliente se calla (con Hubara lo hace el trigger de
ghosting al LLM). El reloj es el mismo watchdog de la ventana de servicio
(lo programa cada inbound ``standby``); al disparar, en vez de un template,
propone ``INTERESADO`` al contrato ``session-actions@v1`` de chats
(``POST /tag``): la reconciliación de D1.3 lo vuelve ``CONFIRMADO_SIN_DATOS``
+ escalación (cierra el episodio) si el episodio activo tiene datos de envío
sin orden, lo descarta si hay orden registrada, y lo aplica tal cual si solo
hubo producto/interés. Igual que el ghosting de Sales: ``INTERESADO`` NO es
un tag de cierre — el episodio queda abierto (lo cierra el siguiente inbound
por timeout de 14 días o un tag de cierre) y el primer apply encola CAPI
``QualifiedLead``. Idempotente: un segundo disparo es ``already_applied``.

Por qué HTTP y no un import: el worker de remarketing NO puede importar la
reconciliación ni el ciclo de episodios de Sales (contrato
``agents-independent`` del import-linter); el contrato HTTP ya serializa por
sesión (lock + flock), emite ``EpisodeClosedEvent`` (cancela este mismo
watchdog y dispara la eval) y anota CAPI — exactamente lo que hace el
connector de mba. Identidad de servicio (``HUBARA_SERVICE_TOKEN``), base
``HUBARA_API_BASE_URL`` (nombre del servicio de la compose; el gate
``castkit_loopback`` prohíbe el loopback en plugins).
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

from src.sdk import castkit

__all__ = ["SILENCE_MOTIVO", "api_base_url", "close_by_silence"]

SILENCE_MOTIVO = "silencio del cliente con Meta Business Agent al frente (watchdog de la ventana de servicio)"
#: Por debajo del `start_to_close_timeout` (15 s) de la activity de eligibility
#: del watchdog: si el API tarda más, la activity vencería con el POST aún
#: en vuelo y Temporal la reintentaría (el contrato /tag es idempotente, pero
#: el workflow fallaría sin persistir outcome). Sin heartbeat (R-HEARTBEAT).
_TIMEOUT_S = 8.0


def api_base_url() -> str:
    return os.environ.get("HUBARA_API_BASE_URL", "http://hubara-api:8000").rstrip("/")


async def close_by_silence(session_id: str) -> dict[str, Any]:
    """``POST /api/chats/session-actions/{session_id}/tag`` con INTERESADO.
    Devuelve la respuesta del contrato; levanta ``HTTPException`` (castkit)
    si el provider rechaza o no responde — el caller decide qué loguear."""
    path = f"/api/chats/session-actions/{quote(session_id, safe='')}/tag"
    return await castkit.forward(
        None,  # sin request entrante: identidad de servicio
        "POST",
        path,
        base_url=api_base_url(),
        timeout=_TIMEOUT_S,
        cast_label="chats.watchdog→chats.session-actions",
        body={"tag": "INTERESADO", "motivo": SILENCE_MOTIVO},
        auth="service",
    )
