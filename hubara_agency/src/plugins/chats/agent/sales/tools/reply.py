"""`send_reply` — el canal del texto del asesor al cliente.

Run 28a8e407 (2026-09-23): con thinking apagado el modelo razona en su texto
libre ("El cliente pregunta si… Le aclaro y le pregunto…") y ese texto ERA el
mensaje al cliente: cada paráfrasis que el detector no conocía llegaba al
WhatsApp. Ahora el cliente lee solo lo que el modelo pasa en `text` (igual
que ya pasaba con `intro_text` / `customer_message`); el texto libre es
borrador y nunca sale.

La tool NO envía: valida y devuelve `reply.text`, que el workflow manda al
terminar el turno — y es lo que queda en el historial del LLM (L-21: la
decisión vive en el resultado grabado, no en regex del workflow). Si el texto
es vacío o solo nota interna, no hay `reply` y el modelo lo reescribe en el
mismo turno.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext
from loguru import logger

from src.plugins.chats.agent.sales.decisions.guards import clean_llm_text, customer_reply_text

_REJECTED_MESSAGE = (
    "No se envió nada: el texto estaba vacío o era nota interna (hablar del "
    "cliente en tercera persona, narrar lo que haces). Escribe SOLO lo que el "
    "cliente debe leer, hablándole a él, y vuelve a llamar send_reply."
)


class SendReplyTool(ToolBase):
    name = "send_reply"
    description = (
        "Envía tu mensaje al cliente: es la forma normal de hablarle (las "
        "tools con texto propio, como `intro_text` de present_products o "
        "`customer_message` de escalate_to_human, llevan el suyo). Todo lo "
        "que escribas fuera de las tools es tu borrador interno y NUNCA le "
        "llega. `text` se envía palabra por palabra: háblale a él en segunda "
        "persona, sin narrar lo que haces ni describirlo en tercera persona. "
        "Llámala al FINAL: tu turno termina ahí y esperas su respuesta."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "El mensaje exacto para el cliente. Varias ideas: sepáralas "
                    "con una línea en blanco (cada una sale como burbuja)."
                ),
            }
        },
        "required": ["text"],
    }

    def __init__(self, workspace: str | Path, vault_dir: str | Path | None = None) -> None:
        self._workspace = Path(workspace)
        # Vault del motor de decisiones (registro de bots, cola de
        # desacuerdos). Sin él: el bot fijado del laboratorio o la regla de hoy.
        self._vault_dir = Path(vault_dir) if vault_dir is not None else None

    async def execute_with_context(
        self, ctx: ToolContext, text: str = "", **_: Any
    ) -> str:
        # Motor de decisiones (F5): la muletilla del modelo al principio la
        # decide `preambulo`; si el texto no es para el cliente lo deciden
        # `destinatario` y `rescate`, con el proveedor del bot de la
        # conversación (la regla de hoy por defecto: idéntico a antes).
        cleaned = await customer_reply_text(
            await clean_llm_text(text or "", session_id=ctx.session_key, vault_dir=self._vault_dir),
            session_id=ctx.session_key,
            vault_dir=self._vault_dir,
        )
        if not cleaned:
            logger.warning(
                "💬 [TOOL send_reply] rechazado (vacío o nota interna) session={} text={!r}",
                ctx.session_key,
                (text or "")[:120],
            )
            return json.dumps(
                {"sent": False, "error": "internal_text", "message": _REJECTED_MESSAGE},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply": {"text": cleaned},
                "summary": (
                    "Mensaje listo para el cliente. Tu turno termina aquí: "
                    "espera su respuesta."
                ),
            },
            ensure_ascii=False,
        )
