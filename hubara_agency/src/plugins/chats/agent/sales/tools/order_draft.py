"""Tool: SetOrderSlotTool -- captura determinista de datos del pedido.

El cliente va diciendo datos del pedido a lo largo de la conversacion (color,
aroma, cantidad, ciudad, ...). Sin respaldo determinista, el LLM depende de
rebuscar en su historial y a veces re-pregunta algo ya dicho. Esta tool fija
cada dato en `episodes[-1].order_draft` apenas el cliente lo confirma; el
ingest lo RE-INYECTA en el prompt cada turno (ver
`use_cases/order_draft.build_order_draft_note` + `ingest_inbound_message`), asi
el LLM lee una tabla pineada en vez de rebuscar.

ADVISORY -- NO es la fuente de verdad de la orden. La orden real es lo que
`register_order` manda a Medusa. Si el draft y los args de `register_order`
divergen, gana `register_order`. NUNCA armar la orden desde el draft.

DEHA:
  * Tool de borde (no inerte pura): escribe `metadata.json` igual que
    `register_order` -- delega la atomicidad a `FilesystemMetadataStore`
    (`atomic_write_json`). No importa `httpx` ni `temporal_client`.
  * R-JSON: input/output JSON-serializable (envelope textual al LLM).
  * Idempotente: setear un slot al mismo valor no cambia nada; setearlo a otro
    valor sobreescribe (overwrite discipline para "cambie de idea").

Patron LLM (ver `workspace/TOOLS.md`):
  * Llamala APENAS el cliente confirma un dato del pedido (producto, aroma,
    color, cantidad, ciudad, barrio, direccion, telefono, metodo de pago).
  * Podes mandar varios campos en una sola llamada (batch).
  * Si el cliente cambia un dato, volve a llamarla con el nuevo valor. Para
    borrar un dato que quedo indefinido, mandalo como string vacio.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext
from loguru import logger

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.use_cases.order_draft import update_order_draft


class SetOrderSlotTool(ToolBase):
    """Fija datos del pedido en el order_draft del episodio activo (advisory)."""

    name = "set_order_slot"
    description = (
        "Guarda de forma persistente un dato del pedido que el cliente YA "
        "confirmo (color, aroma, cantidad, ciudad, barrio, direccion, "
        "telefono, metodo de pago, producto). Llamala APENAS el cliente lo "
        "dice -- estos datos se te re-inyectan automaticamente cada turno para "
        "que NO los vuelvas a preguntar. Podes mandar varios campos juntos en "
        "una sola llamada. Si el cliente cambia un dato, volve a llamarla con "
        "el nuevo valor (sobreescribe). Para borrar un dato que el cliente dejo "
        "indefinido, mandalo como string vacio. NO reemplaza a `register_order` "
        "(esta tool es solo memoria de la conversacion; la orden formal la cierra "
        "`register_order`). Para texto libre que no entra en los campos fijos "
        "(ej. pedido de varios productos distintos), usa `notas`."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "producto": {
                "type": "string",
                "description": "Producto elegido (ej. 'Luz Serena').",
            },
            "aroma": {
                "type": "string",
                "description": "Aroma elegido (ej. 'Lavanda').",
            },
            "color": {
                "type": "string",
                "description": "Color elegido (ej. 'Blanco').",
            },
            "cantidad": {
                "type": "string",
                "description": "Cantidad de unidades (ej. '2').",
            },
            "ciudad": {"type": "string", "description": "Ciudad de envio."},
            "barrio": {"type": "string", "description": "Barrio de envio."},
            "direccion": {
                "type": "string",
                "description": "Direccion de envio.",
            },
            "telefono": {
                "type": "string",
                "description": "Telefono de contacto del cliente.",
            },
            "metodo_pago": {
                "type": "string",
                "description": (
                    "Metodo de pago elegido (ej. 'transferencia', 'tarjeta', "
                    "'contraentrega')."
                ),
            },
            "notas": {
                "type": "string",
                "description": (
                    "Texto libre para datos que no entran en los campos fijos "
                    "(ej. 'pedido: 2x Luz Serena lavanda/blanco + 1x Cruz de Vida')."
                ),
            },
        },
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace: str | Path,
        vault_dir: str | Path | None = None,
    ) -> None:
        # Mismo patron que `RegisterOrderTool`: el `workspace` es el runtime
        # workspace canonico compartido (no se usa para metadata). `vault_dir`
        # DI-friendly: default al vault canonico.
        self._workspace = Path(workspace)
        self._vault_dir = (
            Path(vault_dir) if vault_dir is not None else WORKSPACE_VAULT_DIR
        )
        self._store = FilesystemMetadataStore(self._vault_dir)

    async def execute_with_context(
        self,
        ctx: ToolContext,
        producto: str | None = None,
        aroma: str | None = None,
        color: str | None = None,
        cantidad: str | None = None,
        ciudad: str | None = None,
        barrio: str | None = None,
        direccion: str | None = None,
        telefono: str | None = None,
        metodo_pago: str | None = None,
        notas: str | None = None,
    ) -> str:
        # Solo los campos que el LLM mando (None = no lo toco esta llamada).
        # OJO: string vacio SI viaja -> `update_order_draft` lo interpreta como
        # "borrar este slot" (cliente lo dejo indefinido).
        provided = {
            key: value
            for key, value in {
                "producto": producto,
                "aroma": aroma,
                "color": color,
                "cantidad": cantidad,
                "ciudad": ciudad,
                "barrio": barrio,
                "direccion": direccion,
                "telefono": telefono,
                "metodo_pago": metodo_pago,
                "notas": notas,
            }.items()
            if value is not None
        }

        if not provided:
            return json.dumps(
                {
                    "updated": False,
                    "summary": (
                        "No mandaste ningun dato para guardar. Llama "
                        "set_order_slot con los campos que el cliente confirmo."
                    ),
                },
                ensure_ascii=False,
            )

        now_ms = int(time.time() * 1000)
        data = self._store.read(ctx.session_key)
        draft = update_order_draft(data, slots=provided, now_ms=now_ms)
        self._store.write(ctx.session_key, data)

        current_slots = draft.get("slots", {})
        logger.info(
            "📝 [TOOL set_order_slot] session={} captured={} draft_now={}",
            ctx.session_key,
            provided,
            current_slots,
        )

        envelope = {
            "updated": True,
            "captured": provided,
            "order_draft": current_slots,
            "summary": (
                "Datos del pedido guardados. Se te recuerdan automaticamente "
                "cada turno: NO vuelvas a preguntar lo que ya esta aca. Si el "
                "cliente cambia algo, volve a llamar set_order_slot."
            ),
        }
        return json.dumps(envelope, ensure_ascii=False)
