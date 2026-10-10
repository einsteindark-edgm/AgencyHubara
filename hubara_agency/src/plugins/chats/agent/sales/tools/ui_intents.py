"""Decision tools que emiten "intents" de UI rica de WhatsApp.

Patrón (HU-002 / A.0.4):

  1. El LLM decide QUÉ mostrar (foto del producto, lista, datos de envío, etc.)
     llamando a una de estas tools.
  2. La tool VALIDA (closed-list contra catalog), construye el payload tipado
     y persiste un `ui_intent` en `metadata.json[pending_ui_intents]`.
  3. El workflow, después de la iteración LLM, lee `pending_ui_intents` y
     dispatch a la activity correspondiente (send_image, send_interactive_list,
     send_flow, send_reaction, etc.). Limpia el array tras enviar.
  4. El LLM recibe en el tool_result una confirmación textual ("imagen
     enviada") + el resumen del producto — para que su próximo turno
     razone con ese contexto.

Por qué no llamamos a `send_*` desde la tool: las tools son **inertes**
respecto a Temporal y a I/O (ADR-001). El cliente HTTP de WhatsApp vive en
`platform/whatsapp/client.py` y se llama desde activities. Inyectar
`httpx` en una tool rompe la separación DEHA y los tests.

Tools incluidas (1 por cada intent del PLAN.md A.0.4):

  * `present_product_detail(handle)` — A.1/A.10
  * `present_products(handles, intent)` — A.3/A.11
  * `request_location(reason)` — A.4
  * `request_shipping_details(order_total_cop)` — A.9
  * `present_order_confirmation(items, shipping, payment_provider)` — A.12
  * `react_to_message(emoji)` — A.6
  * `send_contact_card(reason)` — A.7
  * `send_cta_url(button_text, url, body, reason)` — A.8

Cada tool devuelve un envelope JSON con:
  * `queued: True` — confirma que se encoló el intent
  * `kind` — discriminador
  * `summary` — texto para el LLM
"""
from __future__ import annotations
from src.plugins.chats.agent.sales.catalog_scope import WHOLE_CATALOG
from src.plugins.chats.agent.sales.metadata_reads import read_retrying_transient_errors_sync

import contextlib
import contextvars
import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext
from loguru import logger

from src.sdk.connectorkit import has_real_variants, product_retailer_id
from src.platform.catalog import CatalogPort, ProductNotFoundError, deslugify
from src.platform.config import WORKSPACE_VAULT_DIR
from src.plugins.chats.agent.sales.decisions.guards import catalog_choice_buttons
from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp import limits as wa_limits
from src.plugins.chats.agent.sales.card_messages import (
    SHIPPING_FORM_CTA,
    order_card_record,
    shipping_fields_text,
    shipping_form_text,
)
from src.plugins.chats.agent.sales.config.shipping import (
    CASH_ON_DELIVERY_MIN_PRODUCTS_COP,
    SHIPPING_COP_PARAM_DESCRIPTION,
    SHIPPING_RATE_BOGOTA_COP,
    SHIPPING_RATE_NATIONAL_COP,
    SHIPPING_FLOW_PLACEHOLDER,
    SHIPPING_RATE_RULE,
    SHIPPING_RATES_MESSAGE,
    cash_on_delivery_available,
    is_published_rate_for_zone,
    shipping_flow_id,
)
from src.plugins.chats.shared.draft_items import draft_items
from src.plugins.chats.agent.sales.decisions.guards import (
    CiudadDeEnvio,
    capability,
    decide_for_session,
)
from src.plugins.chats.agent.sales.pricing import (
    accepted_prices,
    format_cop,
    quoted_amounts_mismatch,
)
from src.plugins.chats.agent.sales.config.payments import (
    PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA,
    PAYMENT_LINK_SURCHARGE_OTHER_BANKS,
    get_nequi_number,
)
from src.sdk.mediakit import derive_image_label
from src.plugins.chats.shared.store_pack import vocabulary
from src.plugins.chats.agent.sales.catalog_menu import (
    UNCATEGORIZED,
    UNCATEGORIZED_LABEL,
    category_from_row_id,
    category_menu_tail,
    category_row_id,
)
from src.plugins.chats.agent.sales.tools.catalog import decided_category

#: Los ejemplos de la tienda que el LLM ve en estas tools salen del dominio
#: del paquete activo (`domain.yaml: vocabulary`, PAQUETES_DE_DECISION.md F5).
_V = vocabulary()


def no_more_photos_message(title: str) -> str:
    """Lo que la galería le dice al LLM cuando no quedan fotos del producto."""
    return f"No tengo más fotos de {title}. Continúa en texto — pregúntale al cliente {_V['variant_question']}."


#: Lo que la galería le pide al LLM después de mandar las fotos.
PHOTOS_SENT_NEXT = (
    " NO le mandes el link a la web — el cliente ya las está viendo en el chat. Tu próximo "
    f"mensaje: invítalo a elegir {_V['variant_dimensions']} o cerrar la compra."
)


def picker_sent_summary(variant_type: str, total_options: int) -> str:
    """Lo que el selector de variantes le dice al LLM cuando ya salió."""
    return (
        f"Picker de {variant_type} con {total_options} opciones "
        "enviado como UN solo mensaje de texto con emojis curados. "
        "Ese mensaje YA incluye el texto introductorio y la invitación "
        "a elegir — NO escribas texto adicional en este turno; el "
        "picker es tu mensaje completo. Espera la respuesta libre del "
        f"cliente (ej: {_V['variant_reply_examples']})."
    )


def _product_designs(product) -> list[str]:
    """Diseños únicos derivados de los filenames de las fotos (rank order)."""
    designs: list[str] = []
    for img in product.images or []:
        label = derive_image_label(img.url)
        if label and label not in designs:
            designs.append(label)
    return designs


def _image_for_design(product, design: str) -> tuple[str | None, str | None]:
    """Resuelve `design` (case-insensitive) contra los labels de las fotos.

    Devuelve `(url, label_canonico)` de la primera foto cuyo label matchea,
    o `(None, None)` si el diseño no existe — el caller responde con la
    lista cerrada, NUNCA adivina.
    """
    wanted = design.strip().lower()
    if not wanted:
        return (None, None)
    for img in product.images or []:
        label = derive_image_label(img.url)
        if label and label.lower() == wanted:
            return (img.url, label)
    return (None, None)


#: Ids que encolan las tools dentro de `collect_enqueued_intent_ids()`. Es por tarea: lo que encola un turno
#: del bot en otra tarea o en otro proceso (el worker de Ventas) nunca aparece aquí.
_enqueued_ids: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("ui_intents_enqueued", default=None)


@contextlib.contextmanager
def collect_enqueued_intent_ids() -> Iterator[list[str]]:
    """Junta los ids de los intents que encolan las tools corridas dentro del bloque, y solo esos.

    Lo usa la app del operador para mandar y firmar como humano SOLO lo que produjo su toque. Comparar la
    cola antes y después no sirve: un turno del bot en otro proceso puede encolar en medio."""
    ids: list[str] = []
    token = _enqueued_ids.set(ids)
    try:
        yield ids
    finally:
        _enqueued_ids.reset(token)


def _append_intent(session_key: str, intent: dict[str, Any]) -> str:
    """Persiste un UI intent en `metadata.json[pending_ui_intents]` y devuelve su id.

    El workflow lo consume después de cada iteración LLM y dispara la
    activity correspondiente (ver `workflow_helpers.flush_pending_ui_intents`,
    a integrar en follow-up).

    Cada intent lleva:
      * id: uuid (el del caller si trae uno) — el flush lo anota al entregarlo
        y no lo vuelve a mandar aunque una escritura vieja lo devuelva a la
        cola (incidente 2026-10-06); también correlaciona analytics
      * kind: discriminador
      * payload: serializable JSON con los args del send_*
      * queued_at_ms
      * analytics: metadata para emitir wa_outbound event tras send_*

    Agrega con `update()` sobre la lectura fresca (candado del store): solo
    toca la cola, nunca el resto de `metadata.json`. El id también permite
    flushear SOLO lo que encoló una acción (la app del operador:
    `collect_enqueued_intent_ids`), sin arrastrar otros intents de la cola.
    """
    queued = {
        **intent,
        "id": intent.get("id") or uuid.uuid4().hex,
        "queued_at_ms": int(time.time() * 1000),
    }

    def _enqueue(fresh: dict[str, Any]) -> dict[str, Any]:
        fresh["pending_ui_intents"] = [*(fresh.get("pending_ui_intents") or []), queued]
        return fresh

    FilesystemMetadataStore(WORKSPACE_VAULT_DIR).update(session_key, _enqueue)
    sink = _enqueued_ids.get()
    if sink is not None:
        sink.append(queued["id"])
    return queued["id"]


def _already_queued(session_key: str, kind: str) -> bool:
    """¿La cola del turno ya trae un componente de este `kind`?"""
    data = read_retrying_transient_errors_sync(FilesystemMetadataStore(WORKSPACE_VAULT_DIR), session_key)
    return any(isinstance(i, dict) and i.get("kind") == kind for i in data.get("pending_ui_intents") or [])


def _meta_retailer_id(product) -> str:
    """Retailer id VIGENTE del producto en Meta Catalog.

    Es el SKU (platform/catalog/identity.py): Meta se sincroniza por SKU desde
    2026-09-14, y referenciar otra llave hace que WhatsApp dropee el producto
    del MPM en silencio (caso Duo Zodiacal, 2026-07-16, sesión
    wa_573125671604). Producto con variantes reales → la PRIMERA variante
    (determinista); sin SKU cargado todavía → los ids de Medusa de siempre.
    """
    return product_retailer_id(product)


def _first_price(product) -> tuple[str | None, str | None]:
    if not product.variants:
        return (None, None)
    v = product.variants[0]
    if not v.prices:
        return (None, None)
    # COP gana sobre otras monedas (misma regla que tools/catalog.py — caso
    # wa_573125671604: caption "$35.000 usd" por agarrar prices[0] ciego).
    for price in v.prices:
        if price.currency_code.lower() == "cop":
            return (price.amount, price.currency_code)
    return (v.prices[0].amount, v.prices[0].currency_code)


# =============================================================================
# A.1/A.10 — Present product detail (1 producto con foto)
# =============================================================================


class PresentProductDetailTool(ToolBase):
    """Envía UN producto al cliente con foto + caption (precio + título).

    Si el producto tiene Meta Catalog entry (cuando Parte B esté activo),
    el workflow renderiza un `interactive.product` nativo (A.10). Si no,
    fallback transparente a `image + caption` (A.1).

    Closed-list: el handle DEBE existir en el snapshot — si no, devuelve
    error y NO encola intent.
    """

    name = "present_product_detail"
    description = (
        "Envía UN producto al cliente con foto + título + precio. Usa esto "
        "cuando el cliente pide ver un producto específico O cuando "
        "queremos enfocar su atención en una opción concreta. "
        "El handle debe ser EXACTAMENTE el que devolvió search_products / "
        "get_product_by_handle — closed-list strict. Después de llamar "
        "esta tool, NO repitas el precio en texto: ya se mostró al cliente "
        "en la imagen. Continúa con tu mensaje (ej: '¿Te interesa esta?') "
        "como respuesta natural."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "handle": {
                "type": "string",
                "description": (
                    "Handle exacto del producto en el snapshot. "
                    "Closed-list — solo handles vistos en search_products."
                ),
                "minLength": 1,
                "maxLength": 200,
            },
            "caption_suffix": {
                "type": "string",
                "description": (
                    "Texto opcional para añadir al caption después del "
                    "título y precio. Mantén breve (<200 chars). "
                    f"Ej: {_V['caption_example']}."
                ),
                "maxLength": 400,
            },
            "design": {
                "type": "string",
                "description": (
                    "Diseño específico a mostrar (closed-list: SOLO valores "
                    "de `designs` del producto en search_products / "
                    "get_product_by_handle). Manda LA foto de ese diseño en "
                    "vez de la portada. Ej: cliente pide 'una de leo' y "
                    "'Leo' está en designs → design='Leo'."
                ),
                "maxLength": 80,
            },
        },
        "required": ["handle"],
    }

    def __init__(self, workspace: str | Path, catalog: CatalogPort) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(
        self,
        ctx: ToolContext,
        handle: str,
        caption_suffix: str | None = None,
        design: str | None = None,
    ) -> str:
        logger.info(
            "🖼️ [TOOL present_product_detail] session={} handle={!r} design={!r}",
            ctx.session_key, handle, design,
        )
        try:
            product = await self._catalog.get_by_handle(handle)
        except ProductNotFoundError:
            logger.warning("present_product_detail: handle not found: {!r}", handle)
            return json.dumps({
                "queued": False,
                "error": "handle_not_found",
                "message": (
                    f"El handle '{handle}' no existe. Usa search_products primero."
                ),
            }, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001 — catalog unavailable
            logger.error("present_product_detail: catalog error: {}", e)
            return json.dumps({
                "queued": False,
                "error": "catalog_unavailable",
                "detail": str(e),
            }, ensure_ascii=False)

        price, currency = _first_price(product)
        design_label: str | None = None
        if design:
            design_url, design_label = _image_for_design(product, design)
            if not design_url:
                available = _product_designs(product)
                return json.dumps({
                    "queued": False,
                    "error": "design_not_found",
                    "available_designs": available,
                    "message": (
                        f"El diseño '{design}' no existe en las fotos de "
                        f"{product.title}. Diseños disponibles (closed-list): "
                        f"{', '.join(available) if available else 'ninguno'}. "
                        "NO inventes diseños fuera de esa lista."
                    ),
                }, ensure_ascii=False)
            thumbnail = design_url
        else:
            thumbnail = product.thumbnail or (
                product.images[0].url if product.images else None
            )
        if not thumbnail:
            logger.warning("present_product_detail: no thumbnail for {!r}", handle)
            return json.dumps({
                "queued": False,
                "error": "no_image",
                "message": (
                    f"El producto {product.title} no tiene imagen disponible. "
                    "Continúa en modo texto."
                ),
            }, ensure_ascii=False)

        # Caption: "Vela Cruz de Vida · $23.000 COP"
        if price and currency:
            try:
                price_int = int(float(price))
                price_formatted = f"${price_int:,}".replace(",", ".")
                caption = f"{product.title} · {price_formatted} {currency}"
            except (ValueError, TypeError):
                caption = f"{product.title} · {price} {currency}"
        else:
            caption = product.title
        if design_label:
            caption = f"{caption} · {design_label}"
        if caption_suffix:
            caption = f"{caption}\n{caption_suffix}"
        # Recorta al límite Meta defensivamente
        caption = wa_limits.truncate(caption, wa_limits.MAX_CAPTION)

        intent = {
            "kind": "product_detail",
            "params": {
                "handle": handle,
                "image_url": thumbnail,
                "caption": caption,
                "title": product.title,
                "price": price,
                "currency": currency,
                "product_id": product.id,
                # Identidad VIGENTE en Meta (SKU): el flush la usa como
                # `content_ids` del ViewContent de CAPI.
                "retailer_id": _meta_retailer_id(product),
                # Label del diseño mostrado (o derivado del filename de la
                # portada) — el dispatch lo persiste en outbound_media_index
                # para resolver replies que citan esta foto.
                "design": design_label or derive_image_label(thumbnail),
            },
            "analytics": {
                "component_id": "product_detail",
                "component_kind": "image",
                "handle": handle,
            },
            "fallback": {
                # Si Meta Catalog está activo y este producto está en él,
                # el workflow puede preferir `interactive.product` nativo.
                # Si no, queda en image+caption.
                "prefer_native_product_card": True,
            },
        }
        _append_intent(ctx.session_key, intent)

        return json.dumps({
            "queued": True,
            "kind": "product_detail",
            "handle": handle,
            "title": product.title,
            "design": design_label,
            "summary": (
                f"Producto enviado al cliente: {product.title}"
                + (f" (diseño {design_label})" if design_label else "")
                + f" ({price} {currency}). "
                f"NO repitas el precio en tu próximo mensaje (ya se mostró en la "
                f"imagen). Continúa con una pregunta natural sobre la compra."
            ),
        }, ensure_ascii=False)


# =============================================================================
# A.3/A.11 — Present multiple products
# =============================================================================


class PresentProductsTool(ToolBase):
    """Envía productos al cliente en UN mensaje: la lista de productos de
    Meta (`interactive.product_list`, A.11) acepta hasta 30 productos en
    hasta 10 secciones. Sin el catálogo de Meta (o si Meta lo rechaza), el
    flush manda la lista de respaldo (`interactive.list`, A.3) en páginas de
    a 10 filas.

    Tres formas (incidente 2026-10-06, decisión del operador):
      * `handles`: esos productos (closed-list de search_products).
      * `category`: los de esa categoría, hasta 30. El id de una fila del
        menú (`categoria:<slug>`) ya dice cuál es; lo que escribió el cliente
        lo decide el motor (capacidad `categoria`, igual que search_products).
      * nada: el catálogo completo de la copia local. Si cabe en un mensaje
        sale entero, por categoría; si no, el cliente recibe primero el menú
        de categorías (`catalog_menu.py`) y elige una. Con una sola
        categoría el menú sobra: salen sus primeros 30.

    Un producto sin precio no entra al mensaje (no se cotiza ni se vende).
    Con el catálogo de WhatsApp conectado (`META_CATALOG_ID`) tampoco entra
    uno sin foto: es el criterio del push (`map_products_batch`), y Meta
    rechazaría el mensaje (saldría la lista de respaldo, el síntoma del
    incidente) o lo descartaría en silencio. La lista de respaldo, que es
    texto, no necesita foto. Queda un warning en el log y el LLM sabe cuál
    quedó afuera y por qué (`incomplete`).
    """

    name = "present_products"
    description = (
        "Envía productos al cliente en UN mensaje del catálogo de WhatsApp "
        "(hasta 30, agrupados por categoría): los ve dentro del chat y elige "
        "ahí. Tres usos: (1) pide ver qué tienen o el catálogo: llámala SIN "
        "handles y sin search_products antes; si todo el catálogo cabe, sale "
        "entero, y si no, el cliente recibe primero sus categorías para "
        "elegir una. (2) Eligió o pidió una categoría: `category`, sin "
        "handles. (3) 4 o más productos puntuales: `handles` de "
        "search_products (closed-list strict). Para 1-3 productos, mejor "
        "descríbelos en texto. Para UN producto con foto, usa "
        "present_product_detail."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "handles": {
                "type": "array",
                "minItems": 1,
                "maxItems": 30,
                "items": {"type": "string", "minLength": 1, "maxLength": 200},
                "description": (
                    "Handles de los productos a mostrar, en el orden que "
                    "quieres que aparezcan. Solo handles vistos en "
                    "search_products. Sin handles (ni category) se muestra "
                    "el catálogo completo."
                ),
            },
            "category": {
                "type": "string",
                "maxLength": 100,
                "description": (
                    "Categoría cuyos productos quieres mostrar: el `category` "
                    "que trae '[el cliente eligió la categoría: …]', tal cual "
                    "(«categoria:…»), o el nombre como lo escribió el cliente. "
                    "Úsala sin handles."
                ),
            },
            "intro_text": {
                "type": "string",
                "maxLength": 400,
                "description": (
                    "Texto corto que acompaña la lista — es lo ÚNICO que "
                    "el cliente leerá con el menú (el content fuera de la "
                    f"tool no se envía). Ej: {_V['list_intro_example']}. Máx 1024 chars Meta."
                ),
            },
            "group_by": {
                "type": "string",
                "enum": ["categories", "tags", "none"],
                "default": "categories",
                "description": (
                    "Cómo agrupar los productos en sections. 'categories' "
                    "agrupa por categories[] real del producto. 'tags' usa "
                    "tags[]. 'none' los pone todos en una sección."
                ),
            },
        },
        "required": ["intro_text"],
    }

    def __init__(self, workspace: str | Path, catalog: CatalogPort) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(
        self,
        ctx: ToolContext,
        handles: list[str] | None = None,
        intro_text: str = "",
        group_by: str = "categories",
        category: str | None = None,
    ) -> str:
        # Un handle repetido sería una fila repetida: WhatsApp rechaza la lista.
        wanted = list(dict.fromkeys(handles or []))
        logger.info(
            "📋 [TOOL present_products] session={} count={} group_by={} category={!r}",
            ctx.session_key, len(wanted), group_by, category,
        )
        if wanted:
            return await self._present_handles(ctx, wanted, intro_text, group_by)
        if category and category.strip():
            return await self._present_category(ctx, category.strip(), intro_text)
        return await self._present_catalog(ctx, intro_text, group_by)

    async def _present_handles(
        self, ctx: ToolContext, handles: list[str], intro_text: str, group_by: str
    ) -> str:
        """Los productos que eligió el LLM (closed-list de search_products)."""
        products = []
        missing = []
        for h in handles:
            try:
                p = await self._catalog.get_by_handle(h)
                products.append(p)
            except ProductNotFoundError:
                missing.append(h)
            except Exception:  # noqa: BLE001
                continue

        if not products:
            return json.dumps({
                "queued": False,
                "error": "no_valid_handles",
                "missing": missing,
                "message": "Ningún handle resolvió. Llama search_products primero.",
            }, ensure_ascii=False)
        products, incomplete = _complete_and_incomplete(products)
        if not products:
            return _nothing_complete(incomplete)

        sections = _product_sections(products, group_by)
        messages = self._enqueue_products(ctx, sections, intro_text)
        shown = sum(len(s["rows"]) for s in sections)
        envelope: dict[str, Any] = {
            "queued": True,
            "kind": "products_list",
            "count": shown,
            "pages": messages,
            "missing": missing,
            "summary": f"Lista de {shown} productos enviada al cliente.{_delivery_note(sections)}",
        }
        if shown < len(products):
            envelope["left_out"] = [p.handle for p in products[shown:]]
            envelope["summary"] += (
                f" No salieron {len(products) - shown} (un mensaje lleva hasta "
                f"{_MAX_ROWS}): son los de `left_out`."
            )
        _note_incomplete(envelope, incomplete)
        return json.dumps(envelope, ensure_ascii=False)

    async def _present_catalog(self, ctx: ToolContext, intro_text: str, group_by: str) -> str:
        """Sin handles ni category: el catálogo completo de la copia local. Si
        cabe, en UN mensaje; si no, el menú de categorías (o, con una sola
        categoría, sus primeros 30)."""
        try:
            everything = await self._all_products()
            categories = (
                list(await self._catalog.list_categories())
                if len(everything) > _MAX_ROWS
                else []
            )
        except Exception as e:  # noqa: BLE001 — la copia del catálogo no se pudo leer
            return _catalog_unavailable(e)
        products, incomplete = _complete_and_incomplete(everything)
        if not products and incomplete:
            return _nothing_complete(incomplete)
        if not products:
            return json.dumps({
                "queued": False,
                "error": "empty_catalog",
                "message": (
                    "El catálogo no tiene productos completos (con foto y precio): "
                    "no se mostró nada al cliente."
                ),
            }, ensure_ascii=False)
        if len(products) > _MAX_ROWS and categories:
            entries = _category_entries(products, categories)
            if len(entries) == 1:
                _slug, label, members = entries[0]
                return self._present_group(ctx, label, members, intro_text, incomplete)
            return self._enqueue_category_menu(ctx, intro_text, products, entries, incomplete)

        # Cabe en un mensaje (o el catálogo no tiene categorías para armar un
        # menú: salen los primeros 30 y se dice que hay más).
        sections = _product_sections(products, group_by)
        messages = self._enqueue_products(ctx, sections, intro_text)
        shown = sum(len(s["rows"]) for s in sections)
        summary = (
            f"Catálogo completo enviado al cliente: {shown} productos en "
            f"{', '.join(s['title'] for s in sections)}.{_delivery_note(sections)} "
            "Ya lo está viendo: no se lo repitas en texto."
        )
        if shown < len(products):
            summary += (
                f" Hay {len(products) - shown} productos más que no caben (un "
                f"mensaje lleva hasta {_MAX_ROWS}): si busca uno que no vio, "
                "búscalo con search_products."
            )
        envelope: dict[str, Any] = {
            "queued": True,
            "kind": "products_list",
            "count": shown,
            "total": len(products),
            "pages": messages,
            "summary": summary,
            "products": _shown_products(sections),
        }
        _note_incomplete(envelope, incomplete)
        return json.dumps(envelope, ensure_ascii=False)

    async def _present_category(self, ctx: ToolContext, category: str, intro_text: str) -> str:
        """Los productos de UNA categoría, hasta 30. El id de una fila del
        menú (`categoria:<slug>`) ya dice cuál es; lo que escribió el cliente
        lo decide el motor (capacidad `categoria`) con el mismo camino de
        search_products (`decided_category`): la regla de hoy resuelve typos,
        plurales, nombre o slug, y Jev, lo que el texto no dice («velas de
        santos» son las religiosas)."""
        from_catalog = True
        try:
            everything = await self._all_products()
            categories = list(await self._catalog.list_categories())
            row_slug = category_from_row_id(category)
            if row_slug is not None:
                picked = _row_members(row_slug, everything, categories)
                if picked is None:
                    return _category_not_found(category, None, [c.label for c in categories])
                label, members = picked
            else:
                found = await self._catalog.search(q="", limit=_WHOLE_CATALOG, category=category)
                try:
                    found = await decided_category(
                        self._catalog, q="", limit=_WHOLE_CATALOG, category=category, result=found,
                        session_id=ctx.session_key, vault_dir=Path(WORKSPACE_VAULT_DIR),
                    )
                except Exception as e:  # noqa: BLE001 — sin el motor decide la regla de hoy
                    logger.warning(
                        "📋 [TOOL present_products] el motor no decidió la categoría {!r} ({}): "
                        "queda la regla de hoy",
                        category, e,
                    )
                resolution = found.category
                matched = resolution.matched if resolution is not None else None
                if matched is not None and not (
                    _is_others(matched.label) and any(not p.categories for p in everything)
                ):
                    label, members = matched.label, list(found.results)
                elif matched is not None or _is_others(category):
                    # «Otros» es una sola fila: con los productos sin categoría.
                    label, members = UNCATEGORIZED_LABEL, _others(everything, categories)
                elif resolution is not None and resolution.confidence == "no_categories":
                    # Sin categorías cargadas: lo que pasó el LLM no va al
                    # mensaje (no es texto del código ni pasa por la guarda).
                    label, members, from_catalog = category, list(found.results), False
                else:
                    return _category_not_found(category, resolution, [c.label for c in categories])
        except Exception as e:  # noqa: BLE001 — la copia del catálogo no se pudo leer
            return _catalog_unavailable(e)
        products, incomplete = _complete_and_incomplete(members)
        if not products and incomplete:
            return _nothing_complete(incomplete, category=label)
        if not products:
            return json.dumps({
                "queued": False,
                "error": "category_empty",
                "message": (
                    f"La categoría {label} no tiene productos que se puedan mostrar: "
                    "no se mostró nada al cliente."
                ),
            }, ensure_ascii=False)
        return self._present_group(ctx, label, products, intro_text, incomplete, from_catalog=from_catalog)

    def _present_group(
        self,
        ctx: ToolContext,
        label: str,
        products: list[Any],
        intro_text: str,
        incomplete: list[tuple[Any, str]],
        *,
        from_catalog: bool = True,
    ) -> str:
        """Los primeros 30 productos de una categoría, en una sección con su
        nombre (que también va de encabezado del mensaje). Sin categorías
        cargadas (`from_catalog` False) el nombre es lo que pasó el LLM: no
        va al mensaje, que sale con la sección «Productos»."""
        shown = products[:_MAX_ROWS]
        sections = [{"title": label if from_catalog else "Productos", "rows": [_product_row(p) for p in shown]}]
        messages = self._enqueue_products(
            ctx, sections, intro_text, category=label if from_catalog else None
        )
        summary = (
            f"Productos de la categoría {label} enviados al cliente: {len(shown)}."
            f"{_delivery_note(sections)}"
        )
        if len(products) > len(shown):
            summary += (
                f" Se mostraron los primeros {len(shown)} de {len(products)}: hay "
                f"{len(products) - len(shown)} más. Si busca uno que no vio, "
                "búscalo con search_products."
            )
        envelope: dict[str, Any] = {
            "queued": True,
            "kind": "products_list",
            "count": len(shown),
            "category": label,
            "total": len(products),
            "pages": messages,
            "summary": summary,
            "products": _shown_products(sections),
        }
        _note_incomplete(envelope, incomplete)
        return json.dumps(envelope, ensure_ascii=False)

    async def _all_products(self) -> list[Any]:
        """El catálogo completo de la copia local: el mismo cliente que
        search_products (`q=""` lista todo)."""
        return list((await self._catalog.search(q="", limit=_WHOLE_CATALOG)).results)

    def _enqueue_products(
        self,
        ctx: ToolContext,
        sections: list[dict[str, Any]],
        intro_text: str,
        *,
        category: str | None = None,
    ) -> int:
        """UN intent con todas las secciones (el flush lo manda en un mensaje,
        o en páginas de a 10 si cae a la lista de respaldo). Devuelve cuántos
        mensajes le llegan al cliente. `category`: el nombre de la categoría,
        que el flush pone de encabezado y deja en la nota del historial (lo
        arma el código: no pasa por la guarda del texto del LLM)."""
        params: dict[str, Any] = {
            "intro_text": wa_limits.truncate(intro_text, wa_limits.MAX_LIST_BODY),
            "sections": sections,
            "button_label": "Ver opciones",
            "page": 1,
            "total_pages": 1,
        }
        if category:
            params["category"] = category
        _append_intent(ctx.session_key, {
            "kind": "products_list",
            "params": params,
            "analytics": {
                "component_id": "catalog_browse",
                "component_kind": "list",
                "handles": [r["id"] for s in sections for r in s["rows"]],
                "page": 1,
                "total_pages": 1,
            },
            "fallback": {"prefer_native_product_list": True},
        })
        return _messages_for(sections)

    def _enqueue_category_menu(
        self,
        ctx: ToolContext,
        intro_text: str,
        products: list[Any],
        entries: list[tuple[str, str, list[Any]]],
        incomplete: list[tuple[Any, str]],
    ) -> str:
        """El catálogo no cabe en un mensaje: el cliente recibe primero sus
        categorías, una fila por categoría con cuántos productos tiene."""
        in_menu, in_text = _menu_entries(entries)
        intro = wa_limits.truncate(intro_text, wa_limits.MAX_LIST_BODY)
        more = [label for _, label, _ in in_text]
        _append_intent(ctx.session_key, {
            "kind": "categories",
            "params": {
                "intro_text": intro,
                "sections": [{
                    "title": "Categorías",
                    "rows": [
                        {
                            "id": category_row_id(slug),
                            "title": wa_limits.truncate(label, wa_limits.MAX_LIST_ROW_TITLE),
                            "description": _products_count(len(members)),
                        }
                        for slug, label, members in in_menu
                    ],
                }],
                "button_label": "Ver categorías",
                "more_categories": more,
            },
            "analytics": {
                "component_id": "catalog_categories",
                "component_kind": "list",
                "categories": [slug for slug, _, _ in in_menu],
            },
        })
        named = (
            " (las que no caben en el menú van nombradas en su texto: "
            f"{', '.join(more)})"
            if more
            else ""
        )
        envelope: dict[str, Any] = {
            "queued": True,
            "kind": "categories",
            "count": len(products),
            # Lo que el CÓDIGO escribió en el menú (la guía y las categorías
            # nombradas): la convención de las tarjetas que arma el código. El
            # texto del asesor no va acá: ya lo leen de los argumentos de la
            # tool (`card_texts`) y el flush puede cambiarlo por el neutro.
            "customer_text": category_menu_tail(more, max_len=wa_limits.MAX_LIST_BODY),
            "summary": (
                f"El catálogo tiene {len(products)} productos y no cabe en un solo "
                f"mensaje (lleva hasta {_MAX_ROWS}): le envié al cliente un menú con "
                f"sus categorías para que elija una{named}. Cuando la elija recibirás "
                "'[el cliente eligió la categoría: <nombre> (category=\"categoria:<id>\")]': "
                "llama present_products(category=...) con ese valor tal cual para "
                "mostrarle sus productos. Si escribe el nombre de una categoría, "
                "pásalo tal cual en category. No le repitas las categorías en texto."
            ),
            "categories": [
                {"name": label, "products": len(members)} for _, label, members in entries
            ],
        }
        _note_incomplete(envelope, incomplete)
        return json.dumps(envelope, ensure_ascii=False)


#: Lo que cabe en UN mensaje del catálogo de WhatsApp (la lista de productos
#: de Meta): hasta 30 productos en hasta 10 secciones.
_MAX_ROWS = wa_limits.MAX_PRODUCT_LIST_ITEMS_TOTAL
_MAX_SECTIONS = wa_limits.MAX_PRODUCT_LIST_SECTIONS
#: Tope de la lectura del catálogo completo de la copia local.
_WHOLE_CATALOG = WHOLE_CATALOG


def _group_title(product: Any, group_by: str) -> str:
    if group_by == "categories":
        # Nombre real de la categoría: el título de sección lo LEE el cliente;
        # el slug ("velas-religiosas") no es texto de venta.
        if product.categories:
            return _category_label(product, product.categories[0])
        return UNCATEGORIZED_LABEL
    if group_by == "tags":
        return product.tags[0] if product.tags else UNCATEGORIZED_LABEL
    return "Productos"


def _product_sections(products: list[Any], group_by: str) -> list[dict[str, Any]]:
    """Las secciones de UN mensaje: los primeros 30 productos, agrupados, en
    hasta 10 secciones (los topes de la lista de productos de Meta). No hay
    tope por sección; de la décima sección en adelante van juntas en «Otros»,
    así ningún producto se pierde en silencio."""
    groups: dict[str, list[Any]] = {}
    for p in products[:_MAX_ROWS]:
        groups.setdefault(_group_title(p, group_by), []).append(p)
    if len(groups) > _MAX_SECTIONS:
        overflow = list(groups)[_MAX_SECTIONS - 1:]
        merged = [p for title in overflow for p in groups.pop(title)]
        groups.setdefault(UNCATEGORIZED_LABEL, []).extend(merged)
    return [
        {"title": title, "rows": [_product_row(p) for p in prods]}
        for title, prods in groups.items()
    ]


def _product_row(product: Any) -> dict[str, Any]:
    price, currency = _first_price(product)
    desc = None
    if price and currency:
        try:
            price_int = int(float(price))
            desc = f"${price_int:,} {currency}".replace(",", ".")
        except (ValueError, TypeError):
            desc = f"{price} {currency}"
    return {
        "id": product.handle,  # closed-list: row id = handle
        "title": product.title,
        "description": desc,
        # Para A.11 product_list: id del item VIGENTE en Meta (variante para
        # productos con options, ver helper).
        "product_retailer_id": _meta_retailer_id(product),
    }


def _shown_products(sections: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Lo que vio el cliente, en su orden: con esto el LLM anota con su handle
    el producto que el cliente elija."""
    return [{"handle": r["id"], "title": r["title"]} for s in sections for r in s["rows"]]


def _meta_catalog_configured() -> bool:
    """Hay catálogo de WhatsApp (Meta) conectado: `META_CATALOG_ID`."""
    import os

    return bool((os.environ.get("META_CATALOG_ID") or "").strip())


def _meta_catalog_expected(sections: list[dict[str, Any]]) -> bool:
    """La misma condición del flush para mandar el catálogo de Meta:
    `META_CATALOG_ID` y todas las filas con su id de Meta."""
    return _meta_catalog_configured() and all(
        r.get("product_retailer_id") for s in sections for r in s["rows"]
    )


def _list_pages(sections: list[dict[str, Any]]) -> int:
    """Cuántas páginas de la lista de respaldo (WhatsApp acepta 10 filas)."""
    return len(wa_limits.paginate_list_rows(sections, wa_limits.MAX_LIST_ROWS_TOTAL))


def _messages_for(sections: list[dict[str, Any]]) -> int:
    """Cuántos mensajes le llegan al cliente si todo va bien: UNO con el
    catálogo de Meta; sin él, una página de la lista por cada 10 filas."""
    return 1 if _meta_catalog_expected(sections) else _list_pages(sections)


def _delivery_note(sections: list[dict[str, Any]]) -> str:
    """Cómo le llega al cliente, en los dos casos: en el catálogo de Meta no
    elige una fila (agrega al carrito o escribe desde la ficha de un
    producto); si Meta lo rechaza, o no está configurado, le llega la lista
    de respaldo y ahí sí elige una fila."""
    pages = _list_pages(sections)
    as_list = (
        f"una lista de texto ({'un mensaje' if pages == 1 else f'{pages} mensajes'}) "
        "y elige con '[el cliente seleccionó: <título>]'"
    )
    if _meta_catalog_expected(sections):
        return (
            " Le llega en un mensaje del catálogo de WhatsApp: desde ahí agrega "
            "productos al carrito (te llega '[el cliente armó un carrito con: …]') "
            "o te escribe desde la ficha de uno. Si WhatsApp lo rechaza, le llega "
            f"como {as_list}."
        )
    return f" Le llega como {as_list}."


def _products_count(count: int) -> str:
    return "1 producto" if count == 1 else f"{count} productos"


def _meta_price(prices: list[Any]) -> bool:
    """Precio publicable en Meta: el de COP (o el primero) con monto y moneda
    (`_price_meta_format` de `platform/meta_catalog/mapper.py`)."""
    if not prices:
        return False
    chosen = next((p for p in prices if (p.currency_code or "").lower() == "cop"), prices[0])
    return bool(chosen.amount and chosen.currency_code)


def _lacks(product: Any, *, meta: bool = True) -> str | None:
    """Lo que le falta al producto para salir en el mensaje del catálogo; None
    si nada. El precio, siempre: sin precio no se cotiza ni se vende. La foto,
    solo para el catálogo de WhatsApp (`meta`): la lista de respaldo es texto.
    Con `meta` es el criterio del push (`map_products_batch`): foto principal
    y precio; con variantes reales la fila es la primera variante, que
    necesita su propio precio. Una prueba lo compara con el push para que no
    se separen."""
    if meta and not (product.thumbnail or (product.images and product.images[0].url)):
        return "foto"
    variants = product.variants or []
    if has_real_variants(product):
        return None if _meta_price(variants[0].prices) else "precio"
    return None if any(_meta_price(v.prices) for v in variants) else "precio"


def _complete_and_incomplete(products: list[Any]) -> tuple[list[Any], list[tuple[Any, str]]]:
    """`(completos, [(incompleto, lo que le falta)])`, con un warning para el
    operador: un producto incompleto se arregla en Medusa. La foto cuenta solo
    si hay catálogo de WhatsApp conectado (`META_CATALOG_ID`)."""
    meta = _meta_catalog_configured()
    complete: list[Any] = []
    incomplete: list[tuple[Any, str]] = []
    for p in products:
        lacks = _lacks(p, meta=meta)
        if lacks is None:
            complete.append(p)
        else:
            incomplete.append((p, lacks))
    if incomplete:
        logger.warning(
            "📋 [TOOL present_products] fuera del mensaje del catálogo (sin precio, o sin "
            "foto con el catálogo de WhatsApp): {}",
            [f"{p.handle} (sin {lacks})" for p, lacks in incomplete],
        )
    return complete, incomplete


def _note_incomplete(envelope: dict[str, Any], incomplete: list[tuple[Any, str]]) -> None:
    if not incomplete:
        return
    envelope["incomplete"] = [
        {"handle": p.handle, "title": p.title, "lacks": lacks} for p, lacks in incomplete
    ]
    envelope["summary"] += (
        f" No van {len(incomplete)} producto(s) incompleto(s) "
        f"({', '.join(p.title for p, _ in incomplete)}): en `incomplete` está qué le "
        "falta a cada uno."
    )


def _nothing_complete(incomplete: list[tuple[Any, str]], *, category: str | None = None) -> str:
    """Ninguno puede ir en el mensaje: el LLM sabe que existen y qué les falta
    (para no decir «no tenemos» de algo que existe)."""
    where = f"Los productos de la categoría {category}" if category else "Esos productos"
    return json.dumps({
        "queued": False,
        "error": "incomplete_products",
        **({"category": category} if category else {}),
        "incomplete": [
            {"handle": p.handle, "title": p.title, "lacks": lacks} for p, lacks in incomplete
        ],
        "message": (
            f"{where} existen, pero no pueden ir en el mensaje del catálogo: en "
            "`incomplete` está qué le falta a cada uno (sin precio no se puede vender; "
            "sin foto no van al catálogo de WhatsApp). No se mostró nada al cliente. "
            "No digas que no los tenemos: los que tienen precio los puedes ofrecer en "
            "texto (búscalos con search_products)."
        ),
    }, ensure_ascii=False)


def _is_others(label: str) -> bool:
    return label.strip().casefold() == UNCATEGORIZED_LABEL.casefold()


def _others(products: list[Any], categories: list[Any]) -> list[Any]:
    """La fila «Otros»: los productos sin categoría y, con ellos, los de una
    categoría «Otros» real (una sola fila, no dos)."""
    others = {c.slug for c in categories if _is_others(c.label)}
    return [p for p in products if not p.categories or others.intersection(p.categories)]


def _category_entries(
    products: list[Any], categories: list[Any]
) -> list[tuple[str, str, list[Any]]]:
    """`(slug, nombre, productos)` de cada fila del menú, en el orden de
    `list_categories`; los productos sin categoría van en «Otros», al final,
    junto con los de una categoría «Otros» real si la hay."""
    loose = any(not p.categories for p in products)
    entries: list[tuple[str, str, list[Any]]] = []
    for c in categories:
        if loose and _is_others(c.label):
            continue  # va en la fila «Otros», con los sin categoría
        members = [p for p in products if c.slug in p.categories]
        if members:
            entries.append((c.slug, c.label, members))
    if loose:
        entries.append((UNCATEGORIZED, UNCATEGORIZED_LABEL, _others(products, categories)))
    return entries


def _row_members(
    slug: str, products: list[Any], categories: list[Any]
) -> tuple[str, list[Any]] | None:
    """`(nombre, productos)` de la fila del menú `categoria:<slug>`; None si
    esa categoría ya no existe (la copia cambió después del menú)."""
    if slug == UNCATEGORIZED:
        return UNCATEGORIZED_LABEL, _others(products, categories)
    label = next((c.label for c in categories if c.slug == slug), None)
    if label is None:
        return None
    return label, [p for p in products if slug in p.categories]


def _menu_entries(
    entries: list[tuple[str, str, list[Any]]],
) -> tuple[list[tuple[str, str, list[Any]]], list[tuple[str, str, list[Any]]]]:
    """`(en el menú, nombradas en el texto)`. La lista de WhatsApp acepta 10
    filas: con más categorías van las que tienen más productos (en su orden
    de siempre, y «Otros» siempre: no es una categoría del catálogo) y las
    demás quedan nombradas en el texto del menú: el cliente puede escribir
    cualquiera y present_products(category=…) la resuelve."""
    cap = wa_limits.MAX_LIST_ROWS_TOTAL
    if len(entries) <= cap:
        return entries, []
    others = [e for e in entries if e[0] == UNCATEGORIZED]
    named = [e for e in entries if e[0] != UNCATEGORIZED]
    biggest = {e[0] for e in sorted(named, key=lambda e: -len(e[2]))[: cap - len(others)]}
    return (
        [e for e in named if e[0] in biggest] + others,
        [e for e in named if e[0] not in biggest],
    )


def _category_not_found(
    category: str, resolution: Any, available: list[str]
) -> str:
    candidates = [c.label for c in getattr(resolution, "candidates", None) or []]
    if candidates:
        return json.dumps({
            "queued": False,
            "error": "category_ambiguous",
            "candidates": candidates,
            "message": (
                f"«{category}» puede ser varias categorías: pregúntale al cliente "
                f"cuál quiere ({', '.join(candidates)}). No se mostró nada al cliente."
            ),
        }, ensure_ascii=False)
    return json.dumps({
        "queued": False,
        "error": "category_not_found",
        "available": available,
        "message": (
            f"No reconocí la categoría «{category}». Las que existen: "
            f"{', '.join(available)}. Ofrécele esas, o llama present_products "
            "sin category para mandarle el menú. No se mostró nada al cliente."
        ),
    }, ensure_ascii=False)


def _catalog_unavailable(error: Exception) -> str:
    logger.error("📋 [TOOL present_products] catalog_unavailable: {}", error)
    return json.dumps({
        "queued": False,
        "error": "catalog_unavailable",
        "message": (
            "El catálogo no está disponible en este momento. Pídele al cliente "
            "unos minutos y reintenta. No se mostró nada al cliente."
        ),
        "detail": str(error),
    }, ensure_ascii=False)


# =============================================================================
# A.9 — Request shipping details (WA Flow)
# =============================================================================
#
# NOTA (sesión adc6400c): el tool `request_location` fue removido. Pedir al
# cliente compartir ubicación nativa demostró ser un anti-patrón:
#   * El botón nativo "Compartir ubicación" abre el mapa, NO el formulario.
#   * Los clientes no entienden por qué tienen que abrir el mapa para una
#     compra.
#   * Se perdió la venta en pruebas reales por abandono.
# Reemplazado por la recolección conversacional (texto plano) que pide
# ciudad/barrio/dirección/teléfono/pago en un solo mensaje.


def _shipping_precondition_rejection(session_key: str) -> dict[str, Any] | None:
    """Envelope de rechazo para `request_shipping_details`, o None si procede.

    Lee el metadata de la sesión (mismo store que `_append_intent`). Solo
    frena un aplazamiento: si el ÚLTIMO inbound fue un «después» (incidente
    2026-09-14, «Voy apenas en camino a casa») → `customer_deferred`.

    No exige una confirmación de compra (criterio del operador, incidente del
    2026-10-09): el cliente había elegido producto, ciudad y «el contra
    entrega», el bot le escribió «Te paso el formulario» y la guarda lo frenó
    porque nadie anotó un «sí». El formulario no compromete nada: la compra se
    confirma en la tarjeta ✅ y en `register_order` (`closing_blocker`).
    """
    from src.plugins.chats.shared.purchase_signals import current_signal

    data = read_retrying_transient_errors_sync(FilesystemMetadataStore(WORKSPACE_VAULT_DIR), session_key)
    signal = current_signal(data)
    if signal and signal.get("kind") == "deferral":
        quoted = str(signal.get("text") or "").strip()
        return {
            "queued": False,
            "error": "customer_deferred",
            "message": (
                f"El cliente acaba de aplazar (\"{quoted}\"): NO pidas datos de "
                "envío ahora ni confirmes el pedido. Responde UNA frase cálida y "
                "breve y espera a que retome; no se mostró nada al cliente."
            ),
        }
    return None


def _draft_items_of(session_key: str) -> list[dict[str, Any]]:
    """Los ítems del borrador del pedido en curso (producto y variantes) para
    el mensaje del formulario. Sin borrador proyectable (pedido ya
    registrado, episodio nuevo): ninguno."""
    from src.plugins.chats.agent.sales.use_cases.order_draft import get_projectable_draft

    slots = get_projectable_draft(FilesystemMetadataStore(WORKSPACE_VAULT_DIR).read(session_key)) or {}
    items = slots.get("items")
    return draft_items({"items": items} if isinstance(items, list) else {"slots": slots})


class RequestShippingDetailsTool(ToolBase):
    """Solicita los datos de envío al cliente.

    Con el WhatsApp Flow configurado (`META_FLOW_ID_SHIPPING`) el cliente
    recibe el formulario nativo y los datos llegan en `nfm_reply`; sin él (o
    si Meta lo rechaza) el flush manda un mensaje de texto que enumera los
    campos y el cliente responde por chat. La tool es agnóstica al modo: el
    dispatcher decide.

    El mensaje con el que sale el formulario lo arma el código
    (`card_messages.shipping_form_text`): producto, variantes del borrador,
    cantidad y subtotal en productos, con los precios del catálogo. El
    envelope lo devuelve en `customer_text` (lo que leyó el cliente: la traza,
    la calificación y la verificación lo leen de ahí). Incidente 2026-10-06
    (bot V2, turno 9): salía sin aroma, color ni subtotal y con guion largo.

    Solo se debe llamar UNA SOLA VEZ por sesión.

    Precio = CATÁLOGO (incidente run ebbc203d, 2026-09-16): la tool recibe
    los `items` del pedido (handle + cantidad) y resuelve precio y título en
    el catálogo. El total, el resumen del header y la disponibilidad de
    contra entrega salen de ahí. Antes recibía `order_total_cop` del LLM y
    lo repetía como "el precio" — el LLM mandó el monto del anuncio ($45.000)
    en vez del catálogo ($49.500) y el formulario ocultó contra entrega. Un
    `order_total_cop` / `items_summary` que todavía mande el LLM (histories
    en vuelo) se ignora y se loguea.
    """

    name = "request_shipping_details"
    description = (
        "Pide al cliente los datos de envío (ciudad, barrio, dirección, "
        "teléfono, nombre de quien recibe, cédula opcional, método de "
        "pago) con el formulario de WhatsApp. Llámala UNA SOLA VEZ por "
        "sesión, después de que el cliente confirmó qué quiere comprar. "
        "Pasa los `items` del pedido (handle EXACTO visto en "
        "search_products / get_product_by_handle + cantidad): el sistema "
        "toma precio y nombre del CATÁLOGO, calcula el total y decide las "
        "formas de pago; tú NUNCA mandas montos. El formulario sale con un "
        "mensaje que arma el sistema: producto, variantes del pedido, "
        "cantidad, subtotal en productos y que el envío va aparte. NO "
        "repitas ese mensaje ni la lista de campos en tu texto. Si el "
        "cliente además preguntó otra cosa, respóndela con `send_reply` "
        "junto con esta tool, en la misma respuesta: la tool termina tu "
        "turno."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Ítems del pedido confirmado: {handle, quantity}. Los "
                    "handles deben ser EXACTAMENTE los vistos en "
                    "search_products / get_product_by_handle — NO los "
                    "inventes. El precio lo pone el sistema desde el catálogo."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "handle": {"type": "string", "minLength": 1, "maxLength": 200},
                        "quantity": {"type": "integer", "minimum": 1, "maximum": 999},
                    },
                    "required": ["handle", "quantity"],
                },
            },
        },
        "required": ["items"],
    }

    def __init__(self, workspace: str | Path, catalog: CatalogPort | None = None) -> None:
        self._workspace = Path(workspace)
        # Fuente del precio. Sin catálogo (dev/tests legacy) la tool NO puede
        # calcular el total → responde `catalog_unavailable` (fail-closed:
        # nunca vuelve a aceptar un monto del LLM).
        self._catalog = catalog

    async def _price_items(
        self, ctx: ToolContext, items: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        """Resuelve `items` contra el catálogo → `{order_total_cop,
        items_summary, lines}` (`lines`: una por ítem con `handle`, `title`,
        `quantity`, `unit_price_cop` y `subtotal_cop`, para el mensaje del
        formulario) o un envelope de rechazo (`{"queued": False, "error": ...}`)."""
        not_shown = " No se mostró nada al cliente."
        if not items:
            return {
                "queued": False,
                "error": "missing_items",
                "message": (
                    "Pasa `items` (handle + quantity) del pedido confirmado: "
                    "el precio y el total los calcula el sistema desde el "
                    "catálogo, tú no mandas montos." + not_shown
                ),
            }
        if self._catalog is None:
            return {
                "queued": False,
                "error": "catalog_unavailable",
                "message": (
                    "El catálogo no está disponible para calcular el total. "
                    "Reintenta en un momento; si persiste, "
                    "escalate_to_human(reason_category='CATALOG_GAP')." + not_shown
                ),
            }
        lines: list[dict[str, Any]] = []
        subtotal = 0
        for it in items:
            handle = str((it or {}).get("handle") or "").strip()
            try:
                qty = int((it or {}).get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            if not handle or qty < 1:
                return {
                    "queued": False,
                    "error": "invalid_item",
                    "message": (
                        "Cada ítem necesita `handle` (del catálogo) y "
                        "`quantity` ≥ 1." + not_shown
                    ),
                }
            try:
                product = await self._catalog.get_by_handle(handle)
            except ProductNotFoundError:
                return {
                    "queued": False,
                    "error": "unknown_handle",
                    "message": (
                        f"El handle '{handle}' no existe en el catálogo. Usa "
                        "EXACTAMENTE el handle del envelope de search_products "
                        "/ get_product_by_handle (no lo inventes) y vuelve a "
                        "llamar." + not_shown
                    ),
                }
            except Exception as e:  # noqa: BLE001 — catálogo caído
                logger.error(
                    "📦 [TOOL request_shipping_details] session={} catálogo no disponible: {}",
                    ctx.session_key, e,
                )
                return {
                    "queued": False,
                    "error": "catalog_unavailable",
                    "message": (
                        "No pude leer el catálogo para calcular el total. "
                        "Reintenta en un momento; si persiste, "
                        "escalate_to_human(reason_category='CATALOG_GAP')." + not_shown
                    ),
                }
            amount, _currency = _first_price(product)
            try:
                unit_price = int(round(float(amount))) if amount is not None else None
            except (TypeError, ValueError):
                unit_price = None
            if unit_price is None:
                return {
                    "queued": False,
                    "error": "price_unavailable",
                    "message": (
                        f"'{product.title}' no tiene precio en el catálogo: "
                        "escalate_to_human(reason_category='CATALOG_GAP')." + not_shown
                    ),
                }
            subtotal += unit_price * qty
            lines.append({
                "handle": handle,
                "title": product.title,
                "quantity": qty,
                "unit_price_cop": unit_price,
                "subtotal_cop": unit_price * qty,
            })
        # `items_summary` es el header del Flow (maxLength 200 en el JSON).
        summary = ", ".join(f"{line['quantity']}× {line['title']}" for line in lines)
        return {"order_total_cop": subtotal, "items_summary": summary[:200], "lines": lines}

    async def execute_with_context(
        self,
        ctx: ToolContext,
        items: list[dict[str, Any]] | None = None,
        order_total_cop: int | None = None,
        items_summary: str | None = None,
    ) -> str:
        # `order_total_cop` / `items_summary` ya NO están en el schema; se
        # aceptan solo para no romper histories en vuelo — y se ignoran.
        priced = await self._price_items(ctx, items)
        if "error" in priced:
            logger.warning(
                "📦 [TOOL request_shipping_details] session={} rechazada: {}",
                ctx.session_key, priced["error"],
            )
            return json.dumps(priced, ensure_ascii=False)
        catalog_total = int(priced["order_total_cop"])
        summary_label = str(priced["items_summary"])
        if order_total_cop is not None and int(order_total_cop) != catalog_total:
            logger.warning(
                "📦 [TOOL request_shipping_details] session={} order_total_cop del "
                "LLM ({}) IGNORADO — el catálogo dice {} COP",
                ctx.session_key, order_total_cop, catalog_total,
            )
        order_total_cop = catalog_total
        items_summary = summary_label
        logger.info(
            "📦 [TOOL request_shipping_details] session={} total={} COP (catálogo)",
            ctx.session_key, order_total_cop,
        )
        # Guarda determinista (2026-09-14, runs 01a0a0eb/01a0a0f1): no sale si
        # el cliente acaba de aplazar ("voy en camino a casa"). Sin exigir un
        # «sí» antes (incidente del 2026-10-09).
        rejection = _shipping_precondition_rejection(ctx.session_key)
        if rejection is not None:
            logger.warning(
                "📦 [TOOL request_shipping_details] session={} rechazada: {}",
                ctx.session_key, rejection["error"],
            )
            return json.dumps(rejection, ensure_ascii=False)
        # El mensaje del formulario lo arma el código: producto, variantes
        # del borrador, cantidad y subtotal del catálogo (incidente
        # 2026-10-06, turno 9). Es lo que el cliente lee con el botón.
        body = shipping_form_text(priced["lines"], _draft_items_of(ctx.session_key))
        # Lo que va a leer el cliente: ese mensaje con el Flow o, sin Flow
        # configurado, la lista de campos que el flush manda por texto (la
        # misma regla del flush, `shipping_flow_id`).
        customer_text = (
            body
            if shipping_flow_id(SHIPPING_FLOW_PLACEHOLDER)
            else shipping_fields_text(order_total_cop, get_nequi_number())
        )
        flow_token = f"shipping_{ctx.session_key}_{int(time.time())}"

        # Opciones de pago dinámicas. El RadioButtonsGroup del Flow JSON
        # bindea `data-source: ${data.payment_options}`, así que con cambiar
        # esta lista se cambia lo que ve el cliente — sin republicar el Flow
        # en Meta. Las TRES formas (requisito 2026-08-31): contra entrega /
        # pago anticipado (Nequi o llave) / link de pago (con recargo).
        # "Contra entrega" solo aparece desde $45.000 COP en productos
        # (política Hubara — pedidos chicos van prepago para asegurar el
        # margen vs el costo del envío). Umbral único e inclusivo:
        # `config/shipping.py`.
        payment_options: list[dict[str, str]] = []
        if cash_on_delivery_available(order_total_cop):
            payment_options.append({
                "id": "cash_on_delivery",
                "title": "💵 Contra entrega",
                "description": "El valor se calcula con la transportadora.",
            })
        payment_options.append({
            "id": "transfer",
            "title": "📲 Pago anticipado (Nequi)",
            "description": f"Nequi o llave {get_nequi_number()}.",
        })
        payment_options.append({
            "id": "payment_link",
            "title": "🔗 Link de pago",
            "description": (
                "Recargo adicional: "
                f"{PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA} con Nequi o "
                f"Bancolombia, {PAYMENT_LINK_SURCHARGE_OTHER_BANKS} con "
                "otros bancos."
            ),
        })

        intent = {
            "kind": "shipping_flow",
            "params": {
                # PLACEHOLDER — el dispatcher resuelve el flow_id real desde
                # la env var `META_FLOW_ID_SHIPPING`. Si esa env está vacía,
                # cae al fallback de texto plano (recolección conversacional
                # turn-by-turn). Operador setup: ver
                # docs/META_CATALOG_SETUP.md §Fase 13.
                "flow_id": SHIPPING_FLOW_PLACEHOLDER,
                "flow_token": flow_token,
                "flow_cta": SHIPPING_FORM_CTA,
                "flow_action": "navigate",
                "flow_action_screen": "SHIPPING_DETAILS",
                "flow_action_data": {
                    "order_total_cop": order_total_cop,
                    "items_summary": items_summary,
                    "show_cash_on_delivery": cash_on_delivery_available(order_total_cop),
                    "payment_options": payment_options,
                },
                "body": body,
                "header_text": "Datos de envío",
                # Usado por la rama de texto-plano del dispatcher para decidir
                # si incluir "contra entrega" como opción de pago.
                "order_total_cop": order_total_cop,
            },
            "analytics": {
                "component_id": "shipping_flow_v1",
                "component_kind": "interactive.flow",
                "order_total_cop": order_total_cop,
            },
        }
        # Una vez por turno (revisión del premortem, 2026-10-09): la ronda de
        # las promesas o la red pueden pedirlo cuando ya está en la cola; con
        # otro id el flush lo mandaba dos veces.
        if not _already_queued(ctx.session_key, "shipping_flow"):
            _append_intent(ctx.session_key, intent)
        return json.dumps({
            "queued": True,
            "kind": "shipping_flow",
            "order_total_cop": order_total_cop,
            "items_summary": items_summary,
            "flow_token": flow_token,
            # Lo que leyó el cliente con el formulario (traza, calificación y
            # verificación ③ lo leen de acá).
            "customer_text": customer_text,
            "summary": (
                "Formulario de datos de envío enviado al cliente (ciudad, "
                "barrio, dirección, teléfono, nombre de quien recibe, cédula "
                "opcional, método de pago) con el mensaje de `customer_text`: "
                "es lo que leyó. NO lo repitas ni vuelvas a pedir los datos; usa "
                "`send_reply` solo si el cliente preguntó otra cosa. Los datos "
                "llegan del formulario o por texto: anótalos con set_order_slot "
                "y, cuando los tengas TODOS, continúa con "
                "verify_order_for_checkout."
            ),
        }, ensure_ascii=False)


# =============================================================================
# A.12 — Present order confirmation
# =============================================================================


class PresentOrderConfirmationTool(ToolBase):
    """Envía la confirmación formal de orden al cliente.

    Cuando Meta Order Details está activo (Parte B + gateway aprobado),
    workflow renderiza `interactive.order_details` con botón Pagar nativo
    (A.12). Si no, fallback a `interactive.button` con [Confirmar][Modificar]
    [Cancelar] (A.2).

    PRE-REQ: `verify_order_for_checkout` debe haberse llamado y el envelope
    devuelto verified=True.
    """

    name = "present_order_confirmation"
    description = (
        "Envía la confirmación formal del pedido al cliente: items, "
        "precios, subtotal de productos, envío, dirección, medio de pago y "
        "botón para confirmar/pagar. Con contra entrega el envío figura "
        "'Por confirmar' (lo recalcula la transportadora antes de "
        "despachar) y NO se muestra total; con pago anticipado o link se "
        "muestra el envío como tarifa mínima + total. `shipping_cop` es la "
        f"tarifa mínima (Bogotá y cercanos {format_cop(SHIPPING_RATE_BOGOTA_COP)} / "
        f"nacional {format_cop(SHIPPING_RATE_NATIONAL_COP)}). "
        "Llámala SOLO después de que `verify_order_for_checkout` retornó "
        "verified=True y discrepancy=False. Si hay discrepancia, primero "
        "informa al cliente honestamente y pídele confirmación con el "
        "precio nuevo, recién después usas esta tool."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "handle": {"type": "string"},
                        "quantity": {"type": "integer", "minimum": 1},
                        "unit_price_cop": {"type": "integer", "minimum": 0},
                        "color": {
                            "type": "string",
                            "maxLength": 60,
                            "description": "Color elegido, de la lista del producto (si tiene).",
                        },
                        "aroma": {
                            "type": "string",
                            "maxLength": 60,
                            "description": "Aroma elegido, de la lista del producto (si tiene).",
                        },
                    },
                    "required": ["handle", "quantity", "unit_price_cop"],
                },
            },
            "shipping_cop": {
                "type": "integer",
                "minimum": 0,
                "description": SHIPPING_COP_PARAM_DESCRIPTION,
            },
            "tax_cop": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": _V["tax_note"],
            },
            "shipping_address_summary": {
                "type": "string",
                "maxLength": 400,
                "description": "Resumen de dirección, ej: 'Cl 100 #15-20, Chapinero, Bogotá'.",
            },
            "payment_method": {
                "type": "string",
                "enum": ["transfer", "payment_link", "cash_on_delivery"],
                "description": "Método de pago elegido por el cliente.",
            },
        },
        "required": ["items", "shipping_cop", "shipping_address_summary", "payment_method"],
    }

    def __init__(
        self,
        workspace: str | Path,
        catalog: CatalogPort,
        quotas: Any = None,
        sales: Any = None,
    ) -> None:
        """`quotas`/`sales`: cupo por unidad (central de cupones). Sin ellos,
        el cupón descuenta como siempre."""
        self._workspace = Path(workspace)
        self._catalog = catalog
        self._quotas = quotas
        self._sales = sales

    async def execute_with_context(
        self,
        ctx: ToolContext,
        items: list[dict[str, Any]],
        shipping_cop: int,
        shipping_address_summary: str,
        payment_method: str,
        tax_cop: int = 0,
    ) -> str:
        # El cliente acaba de aplazar (CON-02, 2026-10-07): el resumen empuja el
        # cierre. Mismo criterio que el formulario y el registro.
        from src.plugins.chats.shared.purchase_signals import closing_blocker

        metadata_at_start = read_retrying_transient_errors_sync(
            FilesystemMetadataStore(WORKSPACE_VAULT_DIR), ctx.session_key
        )
        if closing_blocker(metadata_at_start, require_confirmation=False) == "customer_deferred":
            logger.warning("🧾 [TOOL present_order_confirmation] rechazada session={}: customer_deferred", ctx.session_key)
            return json.dumps({
                "queued": False,
                "error": "customer_deferred",
                "message": (
                    "El cliente acaba de aplazar: NO le muestres el resumen ahora. Responde UNA "
                    "frase cálida y breve y espera a que retome; no se mostró nada al cliente."
                ),
            }, ensure_ascii=False)
        # Precio = CATÁLOGO, exacto (PREMORTEM #5 endurecido tras el run
        # ebbc203d, 2026-09-16, y como defensa contra inyección de precios:
        # "cóbrame 48.500" o un monto sacado del anuncio). El LLM manda
        # `unit_price_cop`; acá se compara contra el precio del catálogo y
        # CUALQUIER diferencia rechaza el intent — antes se toleraba un drift
        # del 5%, por donde pasaba un precio bajado un 2%. Se acepta el
        # precio del snapshot o el precio LIVE que `verify_order_for_checkout`
        # registró en esta sesión (cuando Medusa cambió y el snapshot aún no
        # refrescó). NO bloquea si el catálogo falla (degradado): sin precio
        # de referencia no hay contra qué comparar.
        expected_prices: dict[str, int] = {}
        price_mismatches: list[tuple[str, int, int]] = []
        resolved_items = []
        subtotal = 0
        for it in items:
            handle = str(it["handle"])
            qty = int(it["quantity"])
            unit_price = int(it["unit_price_cop"])
            snapshot_price_cop: int | None = None
            try:
                product = await self._catalog.get_by_handle(handle)
                title = product.title
                retailer_id = _meta_retailer_id(product)
                # Resolver precio del snapshot para validación
                sp, _sc = _first_price(product)
                if sp is not None:
                    try:
                        snapshot_price_cop = int(round(float(sp)))
                    except (ValueError, TypeError):
                        snapshot_price_cop = None
            except Exception:  # noqa: BLE001
                title = handle
                retailer_id = handle
            accepted = accepted_prices(
                WORKSPACE_VAULT_DIR, ctx.session_key, handle, snapshot_price_cop=snapshot_price_cop
            )
            if accepted and unit_price not in accepted:
                expected_prices[handle] = snapshot_price_cop if snapshot_price_cop in accepted else max(accepted)
                price_mismatches.append((handle, unit_price, expected_prices[handle]))
            line_total = qty * unit_price
            subtotal += line_total
            resolved_items.append({
                "handle": handle,
                "retailer_id": retailer_id,
                "title": title,
                "quantity": qty,
                "unit_price_cop": unit_price,
                "line_total_cop": line_total,
            })

        # Cupón aplicado en el episodio (`apply_coupon`): el descuento se
        # RECOMPUTA acá desde el snapshot + precios del catálogo — el LLM no
        # manda montos de descuento. Catálogo caído → sin ids → solo aplica
        # una promo sin filtro de productos. Import local: el paquete
        # use_cases arrastra el workflow → activities → esta tool (ciclo).
        from src.plugins.chats.agent.sales.use_cases.coupon_quota import (
            coupon_note,
            missing_attributes_text,
            remember_confirmed_split,
            resolve_item_variants,
            split_key,
            split_summary,
        )
        from src.plugins.chats.agent.sales.use_cases.coupons import (
            coupon_discount_for_items,
            quota_product_ids,
        )

        from src.plugins.chats.agent.sales.use_cases.order_draft import (
            get_projectable_draft,
            split_lines_mismatch,
        )

        metadata_now = read_retrying_transient_errors_sync(FilesystemMetadataStore(WORKSPACE_VAULT_DIR), ctx.session_key)
        # Color y aroma de cada ítem: en un producto con cupo lo que manda el
        # LLM tiene que existir en las listas del producto; si no, NO hay monto
        # (se corrige primero). Sin cupón no se valida (se acepta como antes).
        variants, invalid_variants = await resolve_item_variants(
            self._catalog, items, metadata_now,
            strict_products=quota_product_ids(metadata_now, self._quotas),
        )
        if invalid_variants:
            return json.dumps({
                "queued": False,
                "error": "invalid_variant_attribute",
                "message": (
                    "; ".join(v.message() for v in invalid_variants)
                    + ". NO se encoló la confirmación: confirma con el cliente una "
                    "opción de la lista y vuelve a llamar present_order_confirmation."
                ),
            }, ensure_ascii=False)
        # Producto repartido en variantes (`set_order_slot(lineas=...)`): la
        # tarjeta lleva una línea por cada una (laboratorio, caso 4567, turno
        # 23: salió con dos lilas para «una lila y otra azul»).
        line_variants = [
            (
                v.color or (str(it.get("color") or "").strip() or None),
                v.aroma or (str(it.get("aroma") or "").strip() or None),
            )
            for it, v in zip(items, variants)
        ]
        mismatch = split_lines_mismatch(
            metadata_now,
            [
                (v.title, int(it["quantity"]), color, aroma)
                for it, v, (color, aroma) in zip(items, variants, line_variants)
            ],
        )
        if mismatch:
            return json.dumps({
                "queued": False,
                "error": "split_lines_mismatch",
                "message": (
                    "El pedido tiene productos en varias líneas, una por variante: "
                    + "; ".join(mismatch)
                    + ". NO se encoló la confirmación: manda una línea por cada una, con "
                    "su `color`, `aroma` y `quantity`. Si el cliente cambió las variantes "
                    "o las cantidades, actualiza primero el borrador con "
                    "set_order_slot(lineas=...)."
                ),
            }, ensure_ascii=False)
        # Un producto que va en varias líneas: la tarjeta dice la variante de
        # cada una (si no, el cliente ve dos renglones iguales).
        handles = [str(it["handle"]) for it in items]
        for resolved, (color, aroma) in zip(resolved_items, line_variants):
            variant = " · ".join(value for value in (color, aroma) if value)
            if variant and handles.count(resolved["handle"]) > 1:
                resolved["variant"] = variant
        draft_city = (get_projectable_draft(metadata_now) or {}).get("ciudad")

        # Los rechazos baratos (precio, envío) van ANTES del descuento: el
        # cupo por unidad relee lo vendido en Medusa (premortem B8).
        if price_mismatches:
            logger.warning(
                "🚨 [TOOL present_order_confirmation] price_mismatch session={} {}",
                ctx.session_key, price_mismatches,
            )
            detail = "; ".join(
                f"{h}: pasaste {format_cop(p)}, catálogo {format_cop(e)}" for h, p, e in price_mismatches
            )
            return json.dumps({
                "queued": False,
                "error": "price_mismatch",
                "expected": expected_prices,
                "message": (
                    "Los precios que pasaste NO son los del catálogo: "
                    f"{detail}. NO se encoló la confirmación. Si le dijiste "
                    "otro precio al cliente, acláraselo con honestidad ANTES "
                    "de continuar (\"el precio vigente es "
                    f"{format_cop(next(iter(expected_prices.values())))}\"), y vuelve a "
                    "llamar present_order_confirmation con los precios EXACTOS "
                    "del catálogo (verify_order_for_checkout te los devuelve "
                    "en unit_price_cop). Nunca inventes ni negocies precios."
                ),
            }, ensure_ascii=False)

        # Contra entrega solo desde el mínimo en productos (premortem
        # 2026-10-09): el mínimo armaba las opciones del formulario, pero si la
        # cantidad bajaba después, la tarjeta salía con contra entrega por
        # debajo y el pedido se registraba así.
        if payment_method == "cash_on_delivery" and not cash_on_delivery_available(subtotal):
            logger.warning(
                "🚨 [TOOL present_order_confirmation] cod_below_minimum session={} subtotal={}",
                ctx.session_key, subtotal,
            )
            return json.dumps({
                "queued": False,
                "error": "cod_below_minimum",
                "message": (
                    f"Contra entrega es desde {format_cop(CASH_ON_DELIVERY_MIN_PRODUCTS_COP)} en productos y este "
                    f"pedido suma {format_cop(subtotal)}. NO se encoló la confirmación. Dile al cliente con "
                    "calidez que con este valor el pago es anticipado (Nequi o llave) o con link de pago, "
                    "pregúntale cuál prefiere y vuelve a llamar present_order_confirmation con ese método."
                ),
            }, ensure_ascii=False)

        # Envío = tarifa mínima publicada, nunca $0 ni inventada (decisión del
        # operador 2026-09-23: sin descuentos ni envío gratis). Con la ciudad
        # del borrador, la misma regla que `register_order` (Bogotá solo la
        # suya): el cliente no confirma un total que el registro rechazaría.
        # La zona de la ciudad la decide el motor de decisiones (capacidad
        # `zona_de_envio`; regla de hoy: Bogotá si la ciudad lo dice).
        zone = await decide_for_session(
            capability("zona_de_envio"),
            CiudadDeEnvio(ciudad=draft_city if isinstance(draft_city, str) else None),
            session_id=ctx.session_key,
            vault_dir=WORKSPACE_VAULT_DIR,
        )
        if not is_published_rate_for_zone(int(shipping_cop), (zone.value or {}).get("zona")):
            logger.warning(
                "🚨 [TOOL present_order_confirmation] shipping_mismatch session={} shipping_cop={}",
                ctx.session_key, shipping_cop,
            )
            return json.dumps({
                "queued": False,
                "error": "shipping_mismatch",
                "message": (
                    f"El envío que pasaste ({format_cop(int(shipping_cop))}) no es "
                    + (
                        f"la tarifa publicada para {draft_city}. "
                        if isinstance(draft_city, str) and draft_city.strip()
                        else "una tarifa publicada. "
                    )
                    + f"NO se encoló la confirmación. {SHIPPING_RATE_RULE} "
                    "Si le dijiste al cliente otro valor de envío, acláraselo con "
                    "honestidad y vuelve a llamar present_order_confirmation."
                ),
            }, ensure_ascii=False)

        discount = await coupon_discount_for_items(
            metadata_now,
            self._catalog,
            items,
            shipping_cop=shipping_cop,
            quotas=self._quotas,
            sales=self._sales,
            variants=variants,
        )
        if discount is not None and discount.quota and discount.missing_attributes:
            # La tarjeta TERMINA el turno: si sale sin el color/aroma de una
            # línea con cupo, el cliente confirma a precio lleno sin que el bot
            # alcance a preguntar. Se pregunta primero.
            return json.dumps({
                "queued": False,
                "error": "missing_variant_attributes",
                "message": (
                    f"El cupón {discount.code} vale solo para ciertas combinaciones y falta "
                    f"elegir: {missing_attributes_text(variants, discount.missing_attributes)}. "
                    "NO se encoló la confirmación: pregúntaselo al cliente "
                    "(`present_variant_picker`) y vuelve a llamar present_order_confirmation "
                    "con `color` y `aroma` en cada ítem."
                ),
            }, ensure_ascii=False)
        discount_cop = discount.discount_cop if discount else 0
        if discount is not None and discount.quota:
            logger.info(
                "🎟️ [TOOL present_order_confirmation] cupo session={} code={} discount={} reason={}",
                ctx.session_key, discount.code, discount_cop, discount.reason,
            )
        total = subtotal + shipping_cop + tax_cop - discount_cop
        reference_id = f"HUB-hubara-{ctx.session_key}-{int(time.time())}"
        # Lo que la tarjeta dice del cupo (qué unidades llevan descuento, o por
        # qué no): la tarjeta termina el turno — el bot no alcanza a decirlo.
        note = coupon_note(discount.code, items, variants, discount) if discount else None

        intent = {
            "kind": "order_confirmation",
            "params": {
                "reference_id": reference_id,
                "items": resolved_items,
                "subtotal_cop": subtotal,
                "shipping_cop": shipping_cop,
                "tax_cop": tax_cop,
                "total_cop": total,
                "currency": "COP",
                "shipping_address_summary": shipping_address_summary,
                "payment_method": payment_method,
                **(
                    {"discount_cop": discount_cop, "coupon_code": discount.code}
                    if discount and discount_cop > 0
                    else {}
                ),
                **({"coupon_note": note} if note else {}),
            },
            "analytics": {
                "component_id": "order_confirmation",
                "component_kind": "interactive.order_details",
                "reference_id": reference_id,
                "total_cop": total,
            },
            "fallback": {
                "prefer_native_order_details": True,
            },
        }
        _append_intent(ctx.session_key, intent)
        if discount is not None and discount.quota:
            # El reparto que ve el cliente: `register_order` lo compara con el
            # que relee bajo el candado (la última unidad no se vende dos veces).
            confirmed = split_key(discount.line_discounts, items, variants)
            FilesystemMetadataStore(WORKSPACE_VAULT_DIR).update(
                ctx.session_key,
                lambda md: remember_confirmed_split(md, discount.code, confirmed),
            )

        # Regla del operador (2026-09-07): con CONTRA ENTREGA el resumen que
        # ve el cliente NO trae el valor del envío ni un total con envío
        # (queda "Por confirmar" con la transportadora) — el envelope tampoco
        # le da al LLM un total para que no lo repita en texto. Con pago
        # anticipado / link el cliente sí ve el envío (tarifa mínima) + total.
        wait_hint = (
            "Espera la respuesta del cliente: si confirma con Order Details "
            "nativo, recibirás el evento order_status:captured. Si fallback "
            "a botones, recibirás '[el cliente tocó el botón: Confirmar]'."
        )
        discount_text = (
            f" − descuento {discount.code} ${discount_cop:,} COP"
            if discount and discount_cop > 0
            else ""
        )
        if payment_method == "cash_on_delivery":
            amounts = {"subtotal_cop": subtotal}
            summary = (
                f"Confirmación enviada: productos ${subtotal:,} COP{discount_text}; el "
                "envío figura 'Por confirmar' (la transportadora lo "
                "recalcula antes de despachar) — no le des al cliente un "
                f"valor de envío ni un total que lo incluya. {wait_hint}"
            )
        else:
            amounts = {"subtotal_cop": subtotal, "total_cop": total}
            summary = (
                f"Confirmación enviada: productos ${subtotal:,} COP{discount_text} + envío "
                f"${shipping_cop:,} COP (tarifa mínima) = total ${total:,} "
                f"COP. Si mencionas el envío, aclara que es tarifa mínima. "
                f"{wait_hint}"
            )
        if discount and discount_cop > 0:
            amounts["discount_cop"] = discount_cop
            amounts["coupon_code"] = discount.code
            summary += (
                f" El total ${total:,} COP YA incluye el descuento del cupón "
                f"{discount.code}: pásalo tal cual a `register_order`."
            )
            if discount.quota:
                # Cupo por unidad: qué unidades llevan descuento y cuáles no
                # (dilo así; register_order necesita el mismo color y aroma).
                summary += (
                    " Con descuento: "
                    + split_summary(discount.code, items, variants, discount)
                    + ". Pasa el mismo `color` y `aroma` de cada ítem a `register_order`."
                )
        elif discount and discount.reason == "min_subtotal":
            summary += (
                f" El cupón {discount.code} NO aplica: requiere compra mínima de "
                f"${discount.min_subtotal_cop or 0:,} COP en productos — díselo."
            )
        elif discount and discount.reason == "quota_unavailable":
            summary += (
                f" No pude confirmar cuántas unidades con descuento de {discount.code} "
                "quedan, así que la confirmación va sin descuento — díselo."
            )
        elif discount and discount.reason == "quota_exhausted":
            summary += (
                f" Las unidades con descuento de {discount.code} ya se agotaron: el "
                "pedido va a precio normal — díselo con honestidad."
            )
        elif discount and discount.reason == "no_applicable_items":
            summary += f" El cupón {discount.code} no aplica a estos productos — díselo."
        elif discount and discount.reason == "shipping_not_supported":
            summary += (
                f" El cupón {discount.code} es de envío y NO aplica: el envío lo "
                "cobra la transportadora a su tarifa, sin descuentos — díselo si "
                "lo menciona."
            )
        # Run ebbc203d: si `verify_order_for_checkout` detectó que el bot le
        # escribió al cliente un precio que no es del catálogo, el resumen
        # correcto NO basta — el LLM debe explicar el cambio en su texto.
        quoted = quoted_amounts_mismatch(WORKSPACE_VAULT_DIR, ctx.session_key)
        if quoted:
            catalog_label = ", ".join(
                format_cop(int(it["unit_price_cop"])) for it in resolved_items
            )
            quoted_label = ", ".join(format_cop(a) for a in quoted)
            summary += (
                f" ⚠️ Antes le escribiste al cliente {quoted_label} y el precio "
                f"vigente del catálogo es {catalog_label}: si todavía no se lo "
                "aclaraste, hazlo en tu texto en UNA línea (\"el precio vigente "
                f"es {catalog_label}\")."
            )
        return json.dumps({
            "queued": True,
            "kind": "order_confirmation",
            "reference_id": reference_id,
            **amounts,
            # La tarjeta que lee el cliente (el mismo texto que manda el
            # flush), con su dirección tapada: va a la traza y a la ③ (Jev).
            "customer_text": order_card_record(intent["params"]),
            "summary": summary.replace(",", "."),
        }, ensure_ascii=False)


# =============================================================================
# Tarifas de envío — mensaje estándar determinista
# =============================================================================


class SendShippingRatesTool(ToolBase):
    """Manda el mensaje ESTÁNDAR de tarifas de envío (regla del operador
    2026-09-07, `config/shipping.py`).

    El valor del envío nunca es definitivo (lo recalcula la transportadora
    según tamaño y peso antes de despachar), así que la respuesta a "¿cuánto
    vale el envío?" es un texto fijo con las tarifas MÍNIMAS + la aclaración.
    Sin parámetros: el LLM no redacta ni reformula tarifas; el flush renderiza
    `SHIPPING_RATES_MESSAGE` tal cual. Corta el turno (L-11): el mensaje ES la
    respuesta.
    """

    name = "send_shipping_rates"
    description = (
        "Envía al cliente el mensaje estándar con las tarifas mínimas de "
        "envío (Bogotá y municipios cercanos / nivel nacional) y la "
        "aclaración de que el valor definitivo se confirma al despachar. "
        "Úsala SIEMPRE que el cliente pregunte cuánto vale, cuánto cuesta o "
        "cuánto cobran el envío/domicilio — en vez de escribir las tarifas "
        "tú. Sin parámetros; el mensaje es fijo y ES tu respuesta (tu turno "
        "termina). Nunca des un valor de envío como definitivo ni sumes el "
        "envío a un total."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)

    async def execute_with_context(self, ctx: ToolContext) -> str:
        logger.info(
            "🚚 [TOOL send_shipping_rates] session={}", ctx.session_key
        )
        intent = {
            "kind": "shipping_rates",
            "params": {},
            "analytics": {
                "component_id": "shipping_rates",
                "component_kind": "text",
            },
        }
        _append_intent(ctx.session_key, intent)
        return json.dumps({
            "queued": True,
            "kind": "shipping_rates",
            # El texto fijo que lee el cliente (traza y verificación ③).
            "customer_text": SHIPPING_RATES_MESSAGE,
            "summary": (
                "Mensaje estándar de tarifas de envío enviado al cliente. "
                "NO repitas ni reformules las tarifas en tu texto; tu turno "
                "termina aquí."
            ),
        }, ensure_ascii=False)


# =============================================================================
# A.6 — React to message
# =============================================================================


class ReactToMessageTool(ToolBase):
    """Reacciona al último mensaje del cliente con un emoji.

    Allowlist Hubara: 🤍 ✨ 👍 🎉 ❤️ 🙏. Útil como ack visual rápido (ej:
    tras submit del Flow → 🤍).

    BILLING: cada reaction cuenta como mensaje Meta. Usar con moderación.
    """

    name = "react_to_message"
    description = (
        "Reacciona al último mensaje del cliente con un emoji. Úsalo como "
        "ack visual breve, no como reemplazo de una respuesta de texto. "
        "Emojis permitidos: 🤍 ✨ 👍 🎉 ❤️ 🙏. Una sola reacción por turno."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "emoji": {
                "type": "string",
                "enum": ["🤍", "✨", "👍", "🎉", "❤️", "🙏"],
                "description": "Emoji a usar. De la allowlist Hubara.",
            },
        },
        "required": ["emoji"],
    }

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)

    async def execute_with_context(
        self, ctx: ToolContext, emoji: str
    ) -> str:
        logger.info(
            "💬 [TOOL react_to_message] session={} emoji={}",
            ctx.session_key, emoji,
        )
        intent = {
            "kind": "reaction",
            "params": {
                "emoji": emoji,
            },
            "analytics": {
                "component_id": "reaction",
                "component_kind": "reaction",
                "emoji": emoji,
            },
        }
        _append_intent(ctx.session_key, intent)
        return json.dumps({
            "queued": True,
            "kind": "reaction",
            "emoji": emoji,
            "summary": f"Reacción {emoji} enviada al último mensaje del cliente.",
        }, ensure_ascii=False)


# =============================================================================
# A.7 — Send contact card
# =============================================================================


class SendContactCardTool(ToolBase):
    """Comparte una vCard del asesor humano con el cliente.

    Solo se debe llamar:
      1. Cuando el cliente PIDE el número del asesor.
      2. Como parte de una escalation, con consentimiento.

    El contacto del asesor viene de `agents_admin` (no hardcoded). Para
    HU-002 inicial usamos un placeholder que el composition root resuelve.
    """

    name = "send_contact_card"
    description = (
        "Comparte la tarjeta de contacto de un asesor humano de Hubara "
        "(nombre + número). Úsala SOLO cuando el cliente lo pide "
        "explícitamente o como parte de una escalación con consentimiento. "
        "NO lo uses como sustituto de seguir la conversación — el cliente "
        "espera que tú sigas atendiéndolo."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "maxLength": 200,
                "description": (
                    "Por qué compartes el contacto. Aparece como nota al "
                    "cliente. Ej: 'para que coordines la entrega manualmente'."
                ),
            },
        },
        "required": ["reason"],
    }

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)

    async def execute_with_context(
        self, ctx: ToolContext, reason: str
    ) -> str:
        intent = {
            "kind": "contact_card",
            "params": {
                "reason": reason,
                # El número/nombre se resuelve en el workflow desde
                # agents_admin plugin (no hardcoded).
            },
            "analytics": {
                "component_id": "contact_card",
                "component_kind": "contacts",
            },
        }
        _append_intent(ctx.session_key, intent)
        return json.dumps({
            "queued": True,
            "kind": "contact_card",
            "summary": (
                "Contacto del asesor enviado al cliente. "
                "Razona en tu próximo turno como continuación natural."
            ),
        }, ensure_ascii=False)


# =============================================================================
# A.8 — Send CTA URL
# =============================================================================


class SendCTAUrlTool(ToolBase):
    """Envía un botón con URL al cliente (whitelist).

    Anti-patrón por defecto: NO sacar al cliente fuera del chat. Solo usar
    si lo pide explícitamente (ej: 'mándame el Instagram') o si es tracking
    URL post-venta.
    """

    name = "send_cta_url"
    description = (
        "Envía un botón con URL al cliente. Úsalo SOLO si el cliente lo "
        "pide explícitamente (ej: 'ver Instagram'). NO lo uses como atajo "
        "para no responder en el chat — el cierre de venta debe ser dentro "
        "de WhatsApp. Dominios permitidos: hubara.com.co, "
        "instagram.com/hubara.com.co. URLs fuera de esa lista se rechazan."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "format": "uri",
                "pattern": "^https://.*",
                "description": "URL HTTPS dentro de la whitelist Hubara.",
            },
            "button_text": {
                "type": "string",
                "maxLength": 20,
                "description": "Texto del botón. ≤20 chars Meta.",
            },
            "body_text": {
                "type": "string",
                "maxLength": 400,
                "description": "Texto que acompaña el botón.",
            },
        },
        "required": ["url", "button_text", "body_text"],
    }

    # Whitelist restrictiva: SOLO home + Instagram. /products/* fue bloqueado
    # a propósito tras el bug de sesión a56bfaa9, donde el LLM mandaba al
    # cliente fuera de WhatsApp a la página del producto cuando pedía "más
    # fotos". Esos casos van por `present_product_gallery`, no por CTA URL.
    _WHITELIST = (
        "https://hubara.com.co/",  # home solo
        "https://www.hubara.com.co/",
        "https://instagram.com/hubara.com.co",
        "https://www.instagram.com/hubara.com.co",
    )
    # Patrones explícitamente bloqueados (pesan más que whitelist).
    _BLOCKED_PATTERNS = (
        "/products/",  # product detail page — usa present_product_detail/gallery
        "/checkout",   # checkout off-platform
        "/cart",
    )

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)

    async def execute_with_context(
        self,
        ctx: ToolContext,
        url: str,
        button_text: str,
        body_text: str,
    ) -> str:
        if any(blocked in url for blocked in self._BLOCKED_PATTERNS):
            return json.dumps({
                "queued": False,
                "error": "url_blocked_pattern",
                "url": url,
                "message": (
                    "Esa URL apunta a un producto/checkout — el cliente "
                    "NO debe salir del chat para eso. Si pide más fotos, "
                    "usa `present_product_gallery`. Si pide ver el "
                    "producto, usa `present_product_detail`. Si quiere "
                    "comprar, sigue el flow de checkout en WhatsApp."
                ),
            }, ensure_ascii=False)
        if not any(url.startswith(prefix) for prefix in self._WHITELIST):
            return json.dumps({
                "queued": False,
                "error": "url_not_whitelisted",
                "url": url,
                "message": (
                    "Esa URL no está en la whitelist Hubara. "
                    "Continúa la conversación en texto."
                ),
            }, ensure_ascii=False)

        intent = {
            "kind": "cta_url",
            "params": {
                "url": url,
                "button_text": button_text,
                "body_text": body_text,
            },
            "analytics": {
                "component_id": "cta_url",
                "component_kind": "interactive.cta_url",
                "url": url,
            },
        }
        _append_intent(ctx.session_key, intent)
        return json.dumps({
            "queued": True,
            "kind": "cta_url",
            "url": url,
            "summary": f"Botón CTA con URL {url} enviado al cliente.",
        }, ensure_ascii=False)


# =============================================================================
# Gallery — más fotos del MISMO producto (anti-bug sesión a56bfaa9)
# =============================================================================


class PresentProductGalleryTool(ToolBase):
    """Envía varias fotos del MISMO producto como secuencia dentro del chat.

    Reemplaza el anti-patrón de mandar al cliente a la web cuando pide "más
    fotos". El catálogo tiene hasta 5 imágenes por producto — esta tool
    encola N intents `product_detail` consecutivos, uno por imagen,
    saltando la primera (que ya se mostró en `present_product_detail`).

    El workflow renderiza cada uno como `send_image` separado — WhatsApp los
    muestra como una secuencia natural de fotos.

    Reglas:
      * Closed-list: handle DEBE existir en snapshot.
      * Skip primera imagen si `skip_first=True` (default) — asume que ya
        viste el detail.
      * Max 4 fotos adicionales (capamos para no spammear).
      * NUNCA se llama send_cta_url cuando un cliente pide más fotos —
        esta es la tool correcta.
    """

    name = "present_product_gallery"
    description = (
        "Envía varias fotos adicionales del MISMO producto al cliente, "
        "como una secuencia de imágenes dentro de WhatsApp. Úsala cuando "
        "el cliente pide 'más fotos', 'otra imagen', 'cómo se ve por "
        "atrás', 'más ángulos', etc. NUNCA mandes al cliente a la web "
        "(ni con send_cta_url ni con texto) para ver fotos — esta tool "
        "es la única forma correcta. El handle debe ser EXACTAMENTE el "
        "que devolvió search_products / get_product_by_handle."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "handle": {
                "type": "string",
                "description": (
                    "Handle exacto del producto (closed-list desde "
                    "search_products / get_product_by_handle)."
                ),
                "minLength": 1,
                "maxLength": 200,
            },
            "max_images": {
                "type": "integer",
                "minimum": 1,
                "maximum": 4,
                "default": 3,
                "description": (
                    "Cuántas fotos adicionales mandar (1-4). Default 3."
                ),
            },
            "skip_first": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Si True (default), salta la primera imagen — asume "
                    "que ya se mostró en present_product_detail. Pone "
                    "False solo si NUNCA mostraste el producto."
                ),
            },
        },
        "required": ["handle"],
    }

    def __init__(self, workspace: str | Path, catalog: CatalogPort) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(
        self,
        ctx: ToolContext,
        handle: str,
        max_images: int = 3,
        skip_first: bool = True,
    ) -> str:
        logger.info(
            "🎞️ [TOOL present_product_gallery] session={} handle={!r} max={}",
            ctx.session_key, handle, max_images,
        )
        try:
            product = await self._catalog.get_by_handle(handle)
        except ProductNotFoundError:
            return json.dumps({
                "queued": False,
                "error": "handle_not_found",
                "message": (
                    f"El handle '{handle}' no existe. Usa search_products "
                    "primero."
                ),
            }, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            logger.error("present_product_gallery: catalog error: {}", e)
            return json.dumps({
                "queued": False,
                "error": "catalog_unavailable",
                "detail": str(e),
            }, ensure_ascii=False)

        # Recolectar URLs de imagen — incluir thumbnail si no está en images.
        all_images: list[str] = []
        if product.images:
            all_images = [img.url for img in product.images if getattr(img, "url", None)]
        if product.thumbnail and product.thumbnail not in all_images:
            all_images.insert(0, product.thumbnail)
        if skip_first:
            # Si solo hay 1 imagen total y skip_first=True, no hay nada
            # adicional que mandar — devolvemos error con un mensaje
            # accionable. Esto es lo que el LLM espera tras pedir "más
            # fotos" para un producto con una sola foto.
            additional = all_images[1:] if len(all_images) > 1 else []
        else:
            additional = all_images
        if not additional:
            return json.dumps({
                "queued": False,
                "error": "no_additional_images",
                "message": (
                    no_more_photos_message(product.title)
                ),
            }, ensure_ascii=False)
        # Cap defensivo
        additional = additional[:max(1, min(max_images, 4))]
        # Label por foto derivado del filename (diseño/signo/motivo). El
        # dispatch lo usa como caption y lo persiste en outbound_media_index
        # para resolver replies del cliente que citan una foto específica.
        labeled = [
            {"url": url, "label": derive_image_label(url)}
            for url in additional
        ]
        sent_designs = [img["label"] for img in labeled if img["label"]]

        # Encolamos UN intent product_gallery con todas las URLs. El
        # dispatch las manda en secuencia. Esto vs N intents separados:
        # menos overhead de I/O en metadata.json y mejor agrupación
        # analytics.
        intent = {
            "kind": "product_gallery",
            "params": {
                "handle": handle,
                "retailer_id": _meta_retailer_id(product),
                "title": product.title,
                "image_urls": additional,
                "images": labeled,
                # Caption solo en la primera de la serie — el resto va
                # sin caption para no romper la idea de "secuencia".
                "lead_caption": product.title,
            },
            "analytics": {
                "component_id": "product_gallery",
                "component_kind": "image_sequence",
                "handle": handle,
                "count": len(additional),
            },
        }
        _append_intent(ctx.session_key, intent)
        designs_note = (
            f" Diseños enviados en orden: {', '.join(sent_designs)}."
            if sent_designs
            else ""
        )
        return json.dumps({
            "queued": True,
            "kind": "product_gallery",
            "handle": handle,
            "count": len(additional),
            "sent_designs": sent_designs,
            "summary": (
                f"{len(additional)} foto(s) adicionales de {product.title} "
                "enviadas en secuencia."
                + designs_note
                + PHOTOS_SENT_NEXT
            ),
        }, ensure_ascii=False)


# =============================================================================
# Quick replies — botones genéricos (saludo, decisiones simples)
# =============================================================================


class SendQuickRepliesTool(ToolBase):
    """Envía 1-3 botones de respuesta rápida al cliente.

    Es la única forma correcta de mostrar opciones tappables fuera de los
    contextos específicos (catálogo → present_products, confirmación de
    orden → present_order_confirmation, datos de envío → Flow).

    Caso de uso #1 (anti-bug saludo sesión a56bfaa9): cuando el cliente
    saluda sin intención clara ("hola", "buenas"), responder texto cálido
    + 2-3 botones para guiar la elección.

    Caso de uso #2: decisiones binarias durante la conversación. Ej:
    "¿continúas con la variante elegida o cambias?".

    Los IDs de botones deben ser semánticos (catalog.browse, order.cancel,
    etc.) — el LLM los verá de vuelta como "[el cliente tocó el botón:
    <título>]" cuando el cliente toque.
    """

    name = "send_quick_replies"
    description = (
        "Envía 1-3 botones de respuesta rápida al cliente. Úsala en el "
        "SALUDO inicial cuando la intención del cliente no está clara "
        "(ej: 'hola', 'buenas'), Y en decisiones binarias durante la "
        "conversación. TODO el texto que el cliente debe leer (saludo "
        "incluido) va en `body` — lo que escribas fuera de la tool NO se "
        "envía. NO repitas las opciones en texto — el cliente las ve "
        "como botones tappables."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "body": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1024,
                "description": (
                    "TODO el texto que el cliente debe leer junto a los "
                    "botones (saludo incluido). Es lo único que verá en "
                    "esta burbuja — el content fuera de la tool no se "
                    "envía."
                ),
            },
            "buttons": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 256,
                            "description": (
                                "ID semántico del botón (snake_case con "
                                "namespace). Ej: 'catalog.browse', "
                                "'catalog.by_scent', 'help.advice'."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 20,
                            "description": (
                                "Texto del botón visible al cliente. "
                                "≤20 chars (límite Meta)."
                            ),
                        },
                    },
                    "required": ["id", "title"],
                },
                "description": "Hasta 3 botones tappables.",
            },
        },
        "required": ["body", "buttons"],
    }

    # Namespaces de id que delatan un selector de catálogo (run 943e6bff:
    # `product.cubo_love`). Se chequean aunque el catálogo esté caído.
    _CATALOG_ID_PREFIXES = (
        "product.", "producto.", "aroma.", "scent.", "color.", "colour.",
        "design.", "diseno.", "diseño.", "variant.", "variante.", "size.",
        "tamano.", "tamaño.", "sku.", "handle.",
    )

    def __init__(
        self, workspace: str | Path, catalog: CatalogPort | None = None
    ) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def _catalog_vocabulary(self) -> dict[str, str]:
        """{label normalizado: tipo} con todo lo elegible del catálogo.

        Tipos: "product" (título y handle), "scent", "color", "design",
        "option" (values de options reales, ej. signos). Vacío si el catálogo
        no está disponible — la guarda degrada al chequeo por id.
        """
        if self._catalog is None:
            return {}
        from src.platform.catalog import parse_variant_tags
        from src.platform.catalog import normalize_label

        try:
            result = await self._catalog.search(q="", limit=_WHOLE_CATALOG)
            products = list(result.results)
        except Exception as exc:  # noqa: BLE001 — catálogo caído: degradar al chequeo por id
            logger.warning(
                "🔘 [TOOL send_quick_replies] catálogo no disponible para la "
                "guarda anti-selector ({}) — solo chequeo por id",
                exc,
            )
            return {}
        vocab: dict[str, str] = {}

        def _add(label: Any, kind: str) -> None:
            key = normalize_label(str(label or ""))
            if key:
                vocab.setdefault(key, kind)

        for p in products:
            _add(getattr(p, "title", ""), "product")
            _add(deslugify(getattr(p, "handle", "") or ""), "product")
            attrs = parse_variant_tags(getattr(p, "tags", None))
            for a in attrs.aromas:
                _add(a, "scent")
            for c in attrs.colors:
                _add(c, "color")
            for values in (getattr(p, "options", None) or {}).values():
                for v in values or []:
                    _add(v, "option")
            for img in getattr(p, "images", None) or []:
                _add(derive_image_label(getattr(img, "url", "")), "design")
        return vocab

    async def _catalog_choices(
        self, buttons: list[dict[str, str]]
    ) -> tuple[list[str], set[str]]:
        """Botones que son elecciones de catálogo → (títulos, tipos)."""
        from src.platform.catalog import normalize_label

        rejected: list[str] = []
        kinds: set[str] = set()
        vocab: dict[str, str] | None = None
        for b in buttons:
            bid = b["id"].casefold()
            if bid.startswith(self._CATALOG_ID_PREFIXES):
                rejected.append(b["title"])
                kinds.add(bid.split(".", 1)[0])
                continue
            if vocab is None:
                vocab = await self._catalog_vocabulary()
            kind = vocab.get(normalize_label(b["title"]))
            if kind:
                rejected.append(b["title"])
                kinds.add(kind)
        return rejected, kinds

    async def execute_with_context(
        self,
        ctx: ToolContext,
        body: str,
        buttons: list[dict[str, Any]],
    ) -> str:
        logger.info(
            "🔘 [TOOL send_quick_replies] session={} count={}",
            ctx.session_key, len(buttons),
        )
        # Validar/normalizar: trim de títulos a 20 chars, IDs a 256.
        normalized: list[dict[str, str]] = []
        for b in buttons[: wa_limits.MAX_REPLY_BUTTONS]:
            bid = str(b.get("id", "")).strip()
            title = str(b.get("title", "")).strip()
            if not bid or not title:
                continue
            normalized.append({
                "id": bid[: wa_limits.MAX_BUTTON_ID],
                "title": wa_limits.truncate(title, wa_limits.MAX_BUTTON_TITLE),
            })
        if not normalized:
            return json.dumps({
                "queued": False,
                "error": "no_valid_buttons",
                "message": "Los botones llegaron vacíos o inválidos.",
            }, ensure_ascii=False)

        # Guarda anti-selector (run 943e6bff): los reply buttons (máx 3) NUNCA
        # sirven para elegir producto/aroma/color/diseño — el LLM recorta la
        # lista para caber y el cliente pierde opciones. Se rechaza ANTES de
        # encolar; el LLM debe re-llamar present_products / present_variant_picker.
        rejected, kinds = await self._catalog_choices(normalized)
        # Motor de decisiones (F5): si los botones eligen del catálogo lo
        # decide la capacidad `selector` con el proveedor del bot de la
        # conversación (la regla de hoy por defecto: idéntico a antes). El
        # namespace del id es piso.
        rejected = list(
            await catalog_choice_buttons(
                body,
                [b["title"] for b in normalized],
                rule_rejected=rejected,
                by_id=[b["title"] for b in normalized if b["id"].casefold().startswith(self._CATALOG_ID_PREFIXES)],
                session_id=ctx.session_key,
                vault_dir=Path(WORKSPACE_VAULT_DIR),
            )
        )
        if rejected:
            product_like = kinds & {"product", "producto", "handle", "sku"}
            use = (
                "present_products (con los handles de search_products)"
                if product_like
                else "present_variant_picker (aroma Y color = DOS llamadas)"
                if kinds
                else "present_products (productos) o present_variant_picker (aromas, colores)"
            )
            logger.warning(
                "🔘 [TOOL send_quick_replies] rechazado como selector de "
                "catálogo session={} rejected={} kinds={}",
                ctx.session_key, rejected, sorted(kinds),
            )
            return json.dumps({
                "queued": False,
                "error": "catalog_choice_not_allowed",
                "rejected_buttons": rejected,
                "message": (
                    "send_quick_replies NO sirve para elegir productos, aromas, "
                    "colores ni diseños (máx 3 botones: recortarías opciones). "
                    f"No se envió nada. Usa {use} con TODAS las opciones, "
                    "aunque sean 2 o 3. Los quick replies son solo para saludo "
                    "sin intención clara y decisiones binarias (sí/no, "
                    "seguir/cambiar)."
                ),
            }, ensure_ascii=False)
        intent = {
            "kind": "quick_replies",
            "params": {
                "body": wa_limits.truncate(body, wa_limits.MAX_BUTTON_BODY),
                "buttons": normalized,
            },
            "analytics": {
                "component_id": "quick_replies",
                "component_kind": "interactive.button",
                "button_ids": [b["id"] for b in normalized],
            },
        }
        _append_intent(ctx.session_key, intent)
        return json.dumps({
            "queued": True,
            "kind": "quick_replies",
            "count": len(normalized),
            "summary": (
                f"{len(normalized)} botón(es) de respuesta rápida enviado(s). "
                "Espera la elección del cliente — recibirás "
                "'[el cliente tocó el botón: <título>]'."
            ),
        }, ensure_ascii=False)


# =============================================================================
# Variant picker — aromas, colores, tamaños (anti-bug sesión 71f479f7)
# =============================================================================


class PresentVariantPickerTool(ToolBase):
    """Muestra opciones de variante (aroma, color, tamaño) al cliente
    como **mensaje de texto plano con emojis curados** por opción,
    agrupado por categoría sensorial.

    Por qué existe (sesión 71f479f7): listar 11 aromas en texto plano con
    el mismo 🌿 al lado de cada uno se ve repetitivo y poco premium.
    Pero la `interactive.list` tappable resultó incómoda en pruebas (sesión
    adc6400c) — el cliente prefiere leer la lista bonita y *escribir* la
    opción. Solución actual: el dispatcher renderiza un mensaje de texto
    con `*Sección*\\n💜 Lavanda\\n🌿 Verde menta\\n…` + cierre invitando a
    escribir la elección.

    Anti-hallucination: el emoji NUNCA viene del LLM. Lo resolvemos vía
    `variant_emoji.scent_emoji()` / `color_emoji()` (closed-list). Si
    agregamos un aroma nuevo al catálogo y no está en el map, sale con
    fallback genérico (🕯️ / ⚪) — el LLM no puede inventarlo.

    Closed-list strict ENFORCED (caso ep_010, run fa1eb974): el LLM mostró
    "Crema" y "Melocotón" como colores AUN habiendo llamado search_products
    en el episodio — la instrucción de prompt no alcanza. Cuando la tool
    recibe `catalog`, valida cada opción contra los aromas/colores REALES
    del producto (match normalizado, case/acentos-insensible) y DESCARTA
    las inventadas antes de que el cliente las vea. Si el catálogo no está
    disponible, degrada abierto (no bloquea la venta) y lo marca en el
    envelope.
    """

    name = "present_variant_picker"
    description = (
        "Envía un mensaje de texto bonito al cliente con las opciones de "
        f"variante ({_V['variant_kinds']}), con un emoji distintivo "
        "por opción agrupadas por categoría. El cliente lee y **responde "
        f"por texto** la que prefiere (ej: {_V['variant_reply_examples']}). Úsala "
        f"cuando vayas a presentar 4 o más opciones de {_V['variant_dimensions']} de un "
        "producto. El emoji por opción se asigna automáticamente desde el "
        "registry Hubara — **tú NO pasas emojis, solo el nombre literal "
        "del envelope**. Con `handle`, el cliente ve TODAS las opciones del "
        "producto: el sistema agrega las que no pases. Si el cliente ya eligió "
        "la variante, NO uses esta tool — continúa hacia el cierre."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "variant_type": {
                "type": "string",
                "enum": ["scent", "color", "size"],
                "description": (
                    "Tipo de variante: 'scent' para aromas, 'color' para "
                    "colores, 'size' para tamaños (este último sin emoji "
                    "automático)."
                ),
            },
            "options": {
                "type": "array",
                "minItems": 2,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 60,
                            "description": (
                                f"Nombre LITERAL del envelope (ej {_V['variant_label_literal_examples']}). "
                                "Closed-list — "
                                "no inventes."
                            ),
                        },
                    },
                    "required": ["label"],
                },
            },
            "intro_text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1024,
                "description": (
                    f"Texto breve que acompaña el picker. Ej: {_V['picker_intro_examples']}. "
                    "Sin listar las opciones en el texto — el cliente las "
                    "ve en la lista tappable."
                ),
            },
            "handle": {
                "type": "string",
                "description": (
                    "Handle del producto al que pertenecen estas variantes. "
                    "PÁSALO SIEMPRE que el picker sea de un producto: la tool "
                    "valida las opciones contra el catálogo real de ese "
                    "producto y elimina cualquier opción que no exista."
                ),
                "maxLength": 200,
            },
        },
        "required": ["variant_type", "options", "intro_text"],
    }

    def __init__(
        self,
        workspace: str | Path,
        catalog: CatalogPort | None = None,
        metadata_store: Any = None,
    ) -> None:
        """`metadata_store`: metadata de la sesión (DI) para ver el cupón
        aplicado y lo ya elegido; sin él, el picker sale como siempre."""
        self._workspace = Path(workspace)
        self._catalog = catalog
        self._metadata_store = metadata_store

    async def _valid_labels_for(
        self, variant_type: str, handle: str | None
    ) -> list[str] | None:
        """Lista cerrada real para validar, o None si no se puede validar.

        Con `handle` → los atributos de ESE producto. Sin handle → la unión de
        todo el catálogo (más laxo, pero igual mata "Melocotón"). `None` si el
        catálogo está caído o el tipo no es scent/color (size no tiene tags).
        """
        if self._catalog is None or variant_type not in ("scent", "color"):
            return None
        from src.platform.catalog import parse_variant_tags

        try:
            if handle:
                product = await self._catalog.get_by_handle(handle)
                products = [product]
            else:
                result = await self._catalog.search(q="", limit=_WHOLE_CATALOG)
                products = list(result.results)
        except Exception as exc:  # noqa: BLE001 — catálogo caído: degradar abierto
            logger.warning(
                "🎨 [TOOL present_variant_picker] catálogo no disponible para "
                "validar ({}) — picker sin validación",
                exc,
            )
            return None
        valid: list[str] = []
        seen: set[str] = set()
        for p in products:
            attrs = parse_variant_tags(p.tags)
            for label in attrs.aromas if variant_type == "scent" else attrs.colors:
                key = label.casefold()
                if key not in seen:
                    seen.add(key)
                    valid.append(label)
        return valid or None

    async def execute_with_context(
        self,
        ctx: ToolContext,
        variant_type: str,
        options: list[dict[str, Any]],
        intro_text: str,
        handle: str | None = None,
    ) -> str:
        from src.platform.catalog import match_option

        logger.info(
            "🎨 [TOOL present_variant_picker] session={} type={} count={}",
            ctx.session_key, variant_type, len(options),
        )

        # Sanity check
        labels = [str(o.get("label", "")).strip() for o in options]
        labels = [lbl for lbl in labels if lbl]
        if len(labels) < 2:
            return json.dumps({
                "queued": False,
                "error": "not_enough_options",
                "message": (
                    "Se necesitan al menos 2 opciones para mostrar un picker. "
                    "Si solo hay 1, mostrala en texto."
                ),
            }, ensure_ascii=False)

        # Validación closed-list contra el catálogo REAL (caso ep_010:
        # "Crema"/"Melocotón" inventados llegaron al cliente). Las opciones
        # inválidas se DESCARTAN; el envelope se lo dice al LLM para que no
        # las vuelva a ofrecer ni las acepte si el cliente las pide.
        removed_invalid: list[str] = []
        valid_labels = await self._valid_labels_for(variant_type, handle)
        if valid_labels is not None:
            canonical: list[str] = []
            seen_canon: set[str] = set()
            for lbl in labels:
                matched = match_option(lbl, valid_labels)
                if matched is None:
                    removed_invalid.append(lbl)
                elif matched.casefold() not in seen_canon:
                    seen_canon.add(matched.casefold())
                    canonical.append(matched)
            if removed_invalid:
                logger.warning(
                    "🎨 [TOOL present_variant_picker] session={} opciones "
                    "INVENTADAS descartadas: {} (válidas: {})",
                    ctx.session_key, removed_invalid, valid_labels,
                )
            labels = canonical
            if len(labels) < 2:
                return json.dumps({
                    "queued": False,
                    "error": "invalid_options",
                    "removed_invalid_options": removed_invalid,
                    "available_options": valid_labels,
                    "message": (
                        "Las opciones que pasaste NO existen en el catálogo "
                        "de este producto — no se mostró nada al cliente. "
                        "Opciones reales: "
                        + ", ".join(valid_labels)
                        + ". Vuelve a llamar la tool usando SOLO esas."
                    ),
                }, ensure_ascii=False)

        # El selector de UN producto muestra todas sus opciones: el cliente lo
        # lee como todo lo que hay (caso del 2026-10-09: salieron 3 de los 11
        # colores, no el café de la foto que tenía delante, y preguntó «¿no
        # viene en este color?»). Lo que el LLM no pasó va al final.
        added: list[str] = []
        if handle and valid_labels is not None:
            shown = {lbl.casefold() for lbl in labels}
            added = [lbl for lbl in valid_labels if lbl.casefold() not in shown]
            labels = labels + added

        # Cupón con cupo en este producto: sus combinaciones van ARRIBA
        # (conversación de prueba del 2026-09-24: salieron los 11 aromas y el
        # cupón valía en 5 combinaciones).
        coupon = self._coupon_block(ctx.session_key, handle, variant_type)

        # Construcción del intent (sections con emoji desde closed-list) —
        # compartida con la guarda de enumeración del workflow.
        intent = build_variant_picker_intent(
            variant_type=variant_type,
            labels=labels,
            intro_text=intro_text,
            handle=handle,
            coupon=coupon,
        )
        if intent is None:
            return json.dumps({
                "queued": False,
                "error": "no_rows",
                "message": "No quedaron rows tras sanitización.",
            }, ensure_ascii=False)
        total_options = intent["analytics"]["count"]
        _append_intent(ctx.session_key, intent)

        envelope: dict[str, Any] = {
            "queued": True,
            "kind": "variant_picker",
            "variant_type": variant_type,
            "count": total_options,
            "pages": 1,
            "summary": picker_sent_summary(variant_type, total_options),
        }
        if removed_invalid:
            envelope["removed_invalid_options"] = removed_invalid
            envelope["summary"] += (
                " ATENCIÓN: estas opciones que pasaste NO existen en el "
                "catálogo y NO se mostraron: "
                + ", ".join(removed_invalid)
                + ". No las ofrezcas ni las aceptes si el cliente las pide."
            )
        if added:
            envelope["added_options"] = added
            envelope["summary"] += (
                " El cliente ve TODAS las opciones del producto: se agregaron "
                "las que no pasaste (" + ", ".join(added) + ")."
            )
        if coupon is not None:
            envelope["summary"] += coupon["summary"]
        return json.dumps(envelope, ensure_ascii=False)

    def _coupon_block(
        self, session_key: str, handle: str | None, variant_type: str
    ) -> dict[str, Any] | None:
        """Bloque del cupón aplicado para este producto (ver
        `picker_coupon_block`). Sin metadata o ilegible: el picker de siempre."""
        if self._metadata_store is None or not handle:
            return None
        from src.plugins.chats.agent.sales.use_cases.coupon_quota import (
            picker_coupon_block,
        )
        from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
            get_active_episode,
        )

        try:
            episode = get_active_episode(self._metadata_store.read(session_key)) or {}
        except Exception as exc:  # noqa: BLE001 — sin metadata, el picker de siempre
            logger.warning(
                "🎨 [TOOL present_variant_picker] session={} metadata ilegible ({}): "
                "picker sin cupón",
                session_key, exc,
            )
            return None
        return picker_coupon_block(
            episode.get("applied_coupon"),
            episode.get("order_draft"),
            handle=handle,
            variant_type=variant_type,
        )



def build_variant_picker_intent(
    *,
    variant_type: str,
    labels: list[str],
    intro_text: str,
    handle: str | None,
    coupon: dict[str, Any] | None = None,
    closing_text: str | None = None,
) -> dict[str, Any] | None:
    """Intent `variant_picker` (texto curado con emojis) para `labels` ya
    validados. `None` si no queda ninguna row. Lo usan la tool
    `present_variant_picker` y la guarda de enumeración del workflow
    (run 9bd495be) — un solo formato para las variantes. `coupon`: el
    bloque del cupón con cupo (`picker_coupon_block`) que va arriba."""
    from src.platform.whatsapp.variant_emoji import (
        color_emoji,
        group_colors,
        group_scents,
        scent_emoji,
    )

    sections_payload: list[dict[str, Any]] = []
    if variant_type == "scent":
        grouped = group_scents(labels)
        for sec_title, sec_labels in grouped:
            rows = [
                {
                    # id semántico para que el LLM lo reciba como
                    # "[el cliente seleccionó: scent.lavanda]"
                    "id": f"scent.{_slug(lbl)}",
                    "title": f"{scent_emoji(lbl)} {lbl}"[
                        : wa_limits.MAX_LIST_ROW_TITLE
                    ],
                }
                for lbl in sec_labels
            ]
            if rows:
                sections_payload.append({
                    "title": sec_title[: wa_limits.MAX_LIST_SECTION_TITLE],
                    "rows": rows,
                })
    elif variant_type == "color":
        grouped = group_colors(labels)
        for sec_title, sec_labels in grouped:
            rows = [
                {
                    "id": f"color.{_slug(lbl)}",
                    "title": f"{color_emoji(lbl)} {lbl.capitalize()}"[
                        : wa_limits.MAX_LIST_ROW_TITLE
                    ],
                }
                for lbl in sec_labels
            ]
            if rows:
                sections_payload.append({
                    "title": sec_title[: wa_limits.MAX_LIST_SECTION_TITLE],
                    "rows": rows,
                })
    else:
        # 'size' u otras: una sola sección, sin emoji.
        sections_payload.append({
            "title": "Opciones"[: wa_limits.MAX_LIST_SECTION_TITLE],
            "rows": [
                {
                    "id": f"{variant_type}.{_slug(lbl)}",
                    "title": lbl[: wa_limits.MAX_LIST_ROW_TITLE],
                }
                for lbl in labels
            ],
        })

    # UN SOLO MENSAJE (bug run fe86d4e4): el render es texto plano
    # (cambiado en sesión adc6400c). Un mensaje de texto NO tiene el cap
    # de 10 rows de `interactive.list` — ese límite era de Meta para las
    # listas tappables, que ya no usamos. Por eso ya NO paginamos: TODAS
    # las opciones (aromas/colores) van en UN solo mensaje, evitando
    # partir las variantes en dos burbujas. El único límite real es el de
    # caracteres del body de WhatsApp (~4096), de sobra para las ~13
    # variantes de Hubara con sus secciones.
    if not sections_payload:
        return None

    total_options = sum(len(s["rows"]) for s in sections_payload)
    intent = {
        "kind": "variant_picker",
        "params": {
            "variant_type": variant_type,
            "intro_text": intro_text,
            "sections": sections_payload,
            "button_label": "Ver opciones",
            "handle": handle,
            "page": 1,
            "total_pages": 1,
            **(
                {"coupon": {k: v for k, v in coupon.items() if k != "summary"}}
                if coupon
                else {}
            ),
            **({"closing_text": closing_text} if closing_text else {}),
        },
        "analytics": {
            "component_id": f"variant_picker.{variant_type}",
            # Render actual: texto plano con emojis curados
            # (cambiado en sesión adc6400c, antes era interactive.list).
            "component_kind": "text.variant_picker",
            "handle": handle,
            "count": total_options,
            "page": 1,
            "total_pages": 1,
        },
    }
    return intent


def _slug(label: str) -> str:
    """Slug minimalista para IDs de row (snake-case ASCII)."""
    import unicodedata

    nfkd = unicodedata.normalize("NFD", label.lower())
    cleaned = "".join(c for c in nfkd if not unicodedata.combining(c))
    return "".join(c if c.isalnum() else "_" for c in cleaned).strip("_")


def _category_label(product: Any, slug: str) -> str:
    """slug → nombre real de la categoría; snapshots viejos → deslugify."""
    return (getattr(product, "category_labels", None) or {}).get(
        slug
    ) or deslugify(slug)
