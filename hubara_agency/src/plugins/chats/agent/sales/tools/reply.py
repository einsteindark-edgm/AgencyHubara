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

from src.plugins.chats.agent.sales.catalog_scope import WHOLE_CATALOG
from src.plugins.chats.agent.sales.decisions.guards import (
    clean_llm_text,
    customer_reply_text,
    option_list,
)
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.use_cases.photo_product import (
    PROMISE_MESSAGE,
    denies_availability,
    promises_to_follow_up,
    verified_denial_message,
    verified_photo_products,
)
from src.plugins.chats.agent.sales.variant_enumeration import (
    catalog_variant_labels,
    intro_before,
    option_list_message,
)

_REJECTED_MESSAGE = (
    "No se envió nada: el texto estaba vacío o era nota interna (hablar del "
    "cliente en tercera persona, narrar lo que haces). Escribe SOLO lo que el "
    "cliente debe leer, hablándole a él, y vuelve a llamar send_reply."
)


#: El mensaje del cliente en que `send_reply` ya retuvo una negación (o una
#: promesa de revisar después): el segundo intento sale (UNA retención por
#: mensaje, nunca un bucle).
_VERIFIED_CHECK_KEY = "verified_photos_denial_checked"
_PROMISE_CHECK_KEY = "promise_later_checked"
_LIST_CHECK_KEY = "option_list_checked"


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

    def __init__(
        self, workspace: str | Path, vault_dir: str | Path | None = None, catalog: Any = None
    ) -> None:
        self._workspace = Path(workspace)
        # Vault del motor de decisiones (registro de bots, cola de
        # desacuerdos). Sin él: el bot fijado del laboratorio o la regla de hoy.
        self._vault_dir = Path(vault_dir) if vault_dir is not None else None
        # Catálogo (aromas y colores) para ver si el texto es una lista para
        # escoger. Sin él, el texto sale y la protección decide después.
        self._catalog = catalog

    def _hold_once(self, session_key: str, key: str, error: str, message: str) -> str | None:
        """Retiene el texto si todavía no se retuvo uno por `key` en este
        mensaje del cliente (`last_inbound_message_id`) y lo anota. Sin vault,
        sin mensaje del cliente o si no se puede anotar: el texto sale."""
        if self._vault_dir is None:
            return None
        store = FilesystemMetadataStore(self._vault_dir)
        try:
            metadata = store.read(session_key) or {}
        except Exception:  # noqa: BLE001 — sin metadata legible: el texto sale
            return None
        inbound = metadata.get("last_inbound_message_id")
        if not inbound or metadata.get(key) == inbound:
            return None

        def _mark(fresh: dict[str, Any]) -> dict[str, Any] | None:
            if not fresh:
                return None
            fresh[key] = inbound
            return fresh

        try:
            if store.update(session_key, _mark) is None:
                return None
        except Exception:  # noqa: BLE001 — sin la marca podría repetirse: el texto sale
            return None
        logger.info("💬 [TOOL send_reply] retenido ({}) session={}", error, session_key)
        return json.dumps({"sent": False, "error": error, "message": message}, ensure_ascii=False)

    def _held_for_promise(self, session_key: str, text: str) -> str | None:
        """Laboratorio caso-fotos-0930-r9, 4567 t13 (bot nuevo): «Déjame
        revisar bien las cuatro… Dame un momento y te confirmo», y el turno
        terminó. El bot no puede volver a escribir (AGENTS.md lo prohíbe; nadie
        lo hacía cumplir): se retiene UNA vez para que revise ahora."""
        if not promises_to_follow_up(text):
            return None
        return self._hold_once(session_key, _PROMISE_CHECK_KEY, "promise_later", PROMISE_MESSAGE)

    def _held_for_verified_photos(self, session_key: str, text: str) -> str | None:
        """Laboratorio caso-fotos-0930-r8, 4567 t13 (los dos bots): con las
        fotos del cliente ya verificadas como productos nuestros, el bot
        contestó «la única que manejamos es el Velón Gorrión». Si la respuesta
        dice que no tenemos un producto y el episodio tiene fotos verificadas,
        se retiene UNA vez por mensaje del cliente con lo verificado; el
        segundo intento sale (si habla de otro producto, lo reenvía igual).
        Sin vault, sin fotos verificadas o si no se puede anotar la retención,
        el texto sale como siempre."""
        if self._vault_dir is None or not denies_availability(text):
            return None
        try:
            metadata = FilesystemMetadataStore(self._vault_dir).read(session_key) or {}
        except Exception:  # noqa: BLE001 — sin metadata legible: el texto sale
            return None
        products = verified_photo_products(metadata)
        if not products:
            return None
        return self._hold_once(
            session_key, _VERIFIED_CHECK_KEY, "verified_photos", verified_denial_message(products)
        )

    def _unkept_promises(self, session_key: str, text: str) -> list[dict[str, Any]]:
        """Incidente del 2026-10-09 (bot V2): «Te paso el formulario para los
        datos de envío» salió dos turnos seguidos y `request_shipping_details`
        nunca se llamó. Lo que el texto promete y el estado todavía no cumple:
        un componente que la cola del turno no trae (`pending_ui_intents`, lo
        que ya encolaron las tools de este turno; el flush va después del
        texto) o «tu pedido quedó registrado» sin una orden registrada.

        La tool NO retiene: no ve las otras tools de su mismo paso (pueden
        correr después que ella y un retenido se perdía si una tarjeta cortaba
        el turno). Lo graba en su resultado y `run_agent_turn` decide después
        del paso completo, con las tools que de verdad salieron. Sin vault no
        hay cola que mirar: se graba todo lo que promete."""
        from src.plugins.chats.agent.sales.use_cases.promised_actions import broken_promises_in_queue
        from src.plugins.chats.shared.purchase_signals import has_registered_order
        from src.plugins.chats.agent.sales.use_cases.promised_shipping_form import (
            SHIPPING_FORM_KIND,
            delivered_rows,
            shipping_form_in_episode,
        )

        metadata: dict[str, Any] = {}
        delivered: list[dict[str, Any]] = []
        if self._vault_dir is not None:
            try:
                metadata = FilesystemMetadataStore(self._vault_dir).read(session_key) or {}
            except Exception:  # noqa: BLE001 — sin metadata legible: se graba todo lo prometido
                metadata = {}
            delivered = delivered_rows(self._vault_dir / session_key)
        queued = [str(i.get("kind")) for i in metadata.get("pending_ui_intents") or [] if isinstance(i, dict)]
        # El formulario sale una vez por episodio: el que ya salió también
        # cumple (revisión del premortem: si no, la ronda pedía un segundo).
        if shipping_form_in_episode(metadata, delivered):
            queued.append(SHIPPING_FORM_KIND)
        # Un pedido de una compra anterior no registra la de hoy (2026-10-09).
        registered = has_registered_order(metadata)
        return [
            {"kind": p.kind, "tools": list(p.tools), "nudge": p.nudge}
            for p in broken_promises_in_queue(text, queued, registered=registered)
        ]

    def _held_before(self, session_key: str, key: str) -> bool:
        """¿Ya se retuvo un texto por `key` en este mensaje del cliente? Evita
        repetir la consulta (catálogo, Jev) en el segundo intento."""
        try:
            metadata = FilesystemMetadataStore(self._vault_dir).read(session_key) or {}
        except Exception:  # noqa: BLE001 — ilegible: `_hold_once` decide
            return False
        inbound = metadata.get("last_inbound_message_id")
        return bool(inbound) and metadata.get(key) == inbound

    async def _held_for_option_list(self, session_key: str, text: str) -> str | None:
        """Laboratorio caso-fotos-0930-r10, 4567 t19 y t20 (los dos bots): el
        bot escribió los aromas como lista de texto y la protección los cambió
        por el selector después del turno. Si el texto es una lista para
        escoger (capacidad `enumeracion`: la regla en el bot actual, Jev en el
        nuevo), se devuelve UNA vez por mensaje del cliente para que el modelo
        mande el selector él mismo, con el producto y lo que iba a decir. Sin
        catálogo, sin vault o si algo falla: el texto sale y la protección
        sigue detrás."""
        if self._catalog is None or self._vault_dir is None or self._held_before(session_key, _LIST_CHECK_KEY):
            return None
        try:
            result = await self._catalog.search(q="", limit=WHOLE_CATALOG)
            aromas, colors = catalog_variant_labels(result.results)
            listed = await option_list(
                text, aromas=aromas, colors=colors, session_id=session_key, vault_dir=self._vault_dir
            )
        except Exception as exc:  # noqa: BLE001 — nunca frena la respuesta
            logger.warning("💬 [TOOL send_reply] sin revisar la lista ({}) session={}", exc, session_key)
            return None
        if not listed:
            return None
        variant_type, labels = str(listed[0]), list(listed[1])
        return self._hold_once(
            session_key,
            _LIST_CHECK_KEY,
            "option_list",
            option_list_message(variant_type, labels, intro_before(text, labels)),
        )

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
        held = (
            self._held_for_verified_photos(ctx.session_key, cleaned)
            or self._held_for_promise(ctx.session_key, cleaned)
            or await self._held_for_option_list(ctx.session_key, cleaned)
        )
        if held is not None:
            return held
        envelope: dict[str, Any] = {
            "reply": {"text": cleaned},
            "summary": (
                "Mensaje listo para el cliente. Tu turno termina aquí: "
                "espera su respuesta."
            ),
        }
        promises = self._unkept_promises(ctx.session_key, cleaned)
        if promises:
            envelope["promises"] = promises
        return json.dumps(envelope, ensure_ascii=False)
