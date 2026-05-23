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

import json
import time
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext
from loguru import logger

from src.platform.catalog import CatalogPort, ProductNotFoundError
from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp import limits as wa_limits


def _append_intent(session_key: str, intent: dict[str, Any]) -> None:
    """Persiste un UI intent en `metadata.json[pending_ui_intents]`.

    El workflow lo consume después de cada iteración LLM y dispara la
    activity correspondiente (ver `workflow_helpers.flush_pending_ui_intents`,
    a integrar en follow-up).

    Cada intent lleva:
      * id: uuid (para idempotencia en delivery + analytics correlation)
      * kind: discriminador
      * payload: serializable JSON con los args del send_*
      * queued_at_ms
      * analytics: metadata para emitir wa_outbound event tras send_*
    """
    store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    data = store.read(session_key)
    intents = list(data.get("pending_ui_intents") or [])
    intents.append({
        **intent,
        "queued_at_ms": int(time.time() * 1000),
    })
    data["pending_ui_intents"] = intents
    store.write(session_key, data)


def _first_price(product) -> tuple[str | None, str | None]:
    if not product.variants:
        return (None, None)
    v = product.variants[0]
    if not v.prices:
        return (None, None)
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
                    "Ej: 'aroma lavanda · 40 horas de duración'."
                ),
                "maxLength": 400,
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
    ) -> str:
        logger.info(
            "🖼️ [TOOL present_product_detail] session={} handle={!r}",
            ctx.session_key, handle,
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
            "summary": (
                f"Producto enviado al cliente: {product.title} ({price} {currency}). "
                f"NO repitas el precio en tu próximo mensaje (ya se mostró en la "
                f"imagen). Continúa con una pregunta natural sobre la compra."
            ),
        }, ensure_ascii=False)


# =============================================================================
# A.3/A.11 — Present multiple products
# =============================================================================


class PresentProductsTool(ToolBase):
    """Envía una lista de productos al cliente como list message o product_list.

    Cuando todos los handles están en Meta Catalog (Parte B activo),
    workflow renderiza `interactive.product_list` (A.11). Si no, fallback
    a `interactive.list` (A.3) con un row por producto.

    Hasta 30 productos. Si recibís más, recortá vos los más relevantes y
    el cliente puede pedir "ver más" para paginar.
    """

    name = "present_products"
    description = (
        "Envía una lista tappable de hasta 30 productos al cliente — el "
        "cliente la ve como un menú dentro del chat y puede tocar uno para "
        "elegirlo. Úsala cuando vayas a mostrar 4 o más productos. Para "
        "1-3 productos, mejor describílos en texto. Para UN producto con "
        "foto, usa present_product_detail. Los handles deben venir de "
        "search_products (closed-list strict)."
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
                    "querés que aparezcan. Solo handles vistos en "
                    "search_products."
                ),
            },
            "intro_text": {
                "type": "string",
                "maxLength": 400,
                "description": (
                    "Texto corto que acompaña la lista. Ej: 'Estas son "
                    "nuestras velas religiosas:'. Máx 1024 chars Meta."
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
        "required": ["handles", "intro_text"],
    }

    def __init__(self, workspace: str | Path, catalog: CatalogPort) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(
        self,
        ctx: ToolContext,
        handles: list[str],
        intro_text: str,
        group_by: str = "categories",
    ) -> str:
        logger.info(
            "📋 [TOOL present_products] session={} count={} group_by={}",
            ctx.session_key, len(handles), group_by,
        )

        # Resolver productos del snapshot, validar closed-list
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
                "message": "Ningún handle resolvió. Llamá search_products primero.",
            }, ensure_ascii=False)

        # Agrupar
        groups: dict[str, list[Any]] = {}
        if group_by == "categories":
            for p in products:
                key = (p.categories[0] if p.categories else "Otros")
                groups.setdefault(key, []).append(p)
        elif group_by == "tags":
            for p in products:
                key = (p.tags[0] if p.tags else "Otros")
                groups.setdefault(key, []).append(p)
        else:
            groups["Productos"] = list(products)

        # Sections para la list (cada section ≤10 rows, máx 10 sections)
        sections_payload = []
        for sec_title, prods in list(groups.items())[: wa_limits.MAX_LIST_SECTIONS]:
            rows = []
            for p in prods[: wa_limits.MAX_LIST_ROWS_PER_SECTION]:
                price, currency = _first_price(p)
                desc = None
                if price and currency:
                    try:
                        price_int = int(float(price))
                        desc = f"${price_int:,} {currency}".replace(",", ".")
                    except (ValueError, TypeError):
                        desc = f"{price} {currency}"
                rows.append({
                    "id": p.handle,  # closed-list: row id = handle
                    "title": p.title,
                    "description": desc,
                    "product_retailer_id": p.id,  # para A.11 product_list
                })
            sections_payload.append({"title": sec_title, "rows": rows})

        # Paginación (bug 10d8bd21): Meta limita TOTAL de rows a 10. Si
        # el catálogo crece a 11+ productos, dividimos en N mensajes.
        # Hubara hoy tiene 9 → 1 sola página. Futuro-proof.
        pages = wa_limits.paginate_list_rows(
            sections_payload, wa_limits.MAX_LIST_ROWS_TOTAL
        )

        total_pages = len(pages)
        for page_idx, page_sections in enumerate(pages):
            is_first = page_idx == 0
            body = (
                wa_limits.truncate(intro_text, wa_limits.MAX_LIST_BODY)
                if is_first
                else "Más productos del catálogo:"
            )
            intent = {
                "kind": "products_list",
                "params": {
                    "intro_text": body,
                    "sections": page_sections,
                    "button_label": "Ver opciones",
                    "page": page_idx + 1,
                    "total_pages": total_pages,
                },
                "analytics": {
                    "component_id": "catalog_browse",
                    "component_kind": "list",
                    "handles": [
                        r["id"] for s in page_sections for r in s["rows"]
                    ],
                    "page": page_idx + 1,
                    "total_pages": total_pages,
                },
                "fallback": {"prefer_native_product_list": True},
            }
            _append_intent(ctx.session_key, intent)

        paging_note = (
            f" (en {total_pages} mensajes)" if total_pages > 1 else ""
        )
        return json.dumps({
            "queued": True,
            "kind": "products_list",
            "count": len(products),
            "pages": total_pages,
            "missing": missing,
            "summary": (
                f"Lista de {len(products)} productos enviada al cliente "
                f"como menú tappable{paging_note}. Esperá su selección — "
                "cuando toque uno, recibirás '[el cliente seleccionó: <título>]'."
            ),
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


class RequestShippingDetailsTool(ToolBase):
    """Solicita los datos de envío al cliente.

    Mientras el WhatsApp Flow Meta no esté configurado (productivo
    pendiente), esta tool envía un MENSAJE DE TEXTO PLANO al cliente
    enumerando los campos que necesitamos (ciudad, barrio, dirección,
    teléfono, método de pago) — con emojis y formato bonito. El cliente
    responde por texto y vos parseás su respuesta turn-by-turn.

    Cuando el Flow esté configurado (cambiando `flow_id` a un id real),
    esta misma tool abre el formulario nativo y recibe los datos en
    `nfm_reply`. La tool es agnóstica al modo — el dispatcher decide.

    Solo se debe llamar UNA SOLA VEZ por sesión.
    """

    name = "request_shipping_details"
    description = (
        "Pide al cliente los datos de envío (ciudad, barrio, dirección, "
        "teléfono, método de pago). Llámala UNA SOLA VEZ por sesión, "
        "después de que el cliente confirmó qué quiere comprar. El "
        "sistema le manda un mensaje de texto enumerando los campos — "
        "el cliente responde libremente por chat y vos vas armando los "
        "datos hasta tenerlos completos. NO repitas la lista en tu propio "
        "texto: la tool ya manda el mensaje formateado."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "order_total_cop": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Total estimado del pedido en pesos colombianos. El "
                    "Flow lo usa para decidir si mostrar 'Contra entrega' "
                    "como método de pago (solo si total > 45000 COP)."
                ),
            },
            "items_summary": {
                "type": "string",
                "maxLength": 200,
                "description": (
                    "Resumen breve del pedido — aparece como header del "
                    "Flow. Ej: '2× Vela Cruz de Vida'."
                ),
            },
        },
        "required": ["order_total_cop", "items_summary"],
    }

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)

    async def execute_with_context(
        self,
        ctx: ToolContext,
        order_total_cop: int,
        items_summary: str,
    ) -> str:
        logger.info(
            "📦 [TOOL request_shipping_details] session={} total={} COP",
            ctx.session_key, order_total_cop,
        )
        flow_token = f"shipping_{ctx.session_key}_{int(time.time())}"

        # Opciones de pago dinámicas. El RadioButtonsGroup del Flow JSON
        # bindea `data-source: ${data.payment_options}`, así que con cambiar
        # esta lista se cambia lo que ve el cliente — sin republicar el Flow
        # en Meta. "Contra entrega" solo aparece si el total > $45.000 COP
        # (política Hubara — pedidos chicos van prepago para asegurar el
        # margen vs el costo del envío).
        payment_options: list[dict[str, str]] = [
            {"id": "card", "title": "💳 Tarjeta"},
            {"id": "transfer", "title": "🏦 Transferencia"},
        ]
        if order_total_cop > 45000:
            payment_options.append(
                {"id": "cash_on_delivery", "title": "💵 Contra entrega"}
            )

        intent = {
            "kind": "shipping_flow",
            "params": {
                # PLACEHOLDER — el dispatcher resuelve el flow_id real desde
                # la env var `META_FLOW_ID_SHIPPING`. Si esa env está vacía,
                # cae al fallback de texto plano (recolección conversacional
                # turn-by-turn). Operador setup: ver
                # docs/META_CATALOG_SETUP.md §Fase 13.
                "flow_id": "FLOW_ID_SHIPPING_PLACEHOLDER",
                "flow_token": flow_token,
                "flow_cta": "Completar datos",
                "flow_action": "navigate",
                "flow_action_screen": "SHIPPING_DETAILS",
                "flow_action_data": {
                    "order_total_cop": order_total_cop,
                    "items_summary": items_summary,
                    "show_cash_on_delivery": order_total_cop > 45000,
                    "payment_options": payment_options,
                },
                "body": (
                    f"Para enviarte *{items_summary}* necesito unos datos. "
                    "Tocá el botón para completar el formulario — "
                    "toma 30 segundos."
                ),
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
        _append_intent(ctx.session_key, intent)
        return json.dumps({
            "queued": True,
            "kind": "shipping_flow",
            "flow_token": flow_token,
            "summary": (
                "Mensaje pidiendo datos de envío enviado al cliente "
                "(ciudad, barrio, dirección, teléfono, método de pago). "
                "El cliente responde por texto — vos vas recolectando los "
                "campos en los próximos turnos. Cuando los tengas TODOS, "
                "continúa con verify_order_for_checkout. NO pidas los "
                "datos otra vez en tu próximo mensaje — la tool ya los "
                "pidió formateados."
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
        "precios, envío, total, dirección, y botón para confirmar/pagar. "
        "Llamala SOLO después de que `verify_order_for_checkout` retornó "
        "verified=True y discrepancy=False. Si hay discrepancia, primero "
        "informá al cliente honestamente y pedile confirmación con el "
        "precio nuevo, recién después usás esta tool."
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
                    },
                    "required": ["handle", "quantity", "unit_price_cop"],
                },
            },
            "shipping_cop": {
                "type": "integer",
                "minimum": 0,
                "description": "Costo de envío en COP. 0 si envío gratis.",
            },
            "tax_cop": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": "Impuestos en COP. Velas artesanales suelen ser 0.",
            },
            "shipping_address_summary": {
                "type": "string",
                "maxLength": 400,
                "description": "Resumen de dirección, ej: 'Cl 100 #15-20, Chapinero, Bogotá'.",
            },
            "payment_method": {
                "type": "string",
                "enum": ["card", "transfer", "cash_on_delivery"],
                "description": "Método de pago elegido por el cliente.",
            },
        },
        "required": ["items", "shipping_cop", "shipping_address_summary", "payment_method"],
    }

    def __init__(
        self,
        workspace: str | Path,
        catalog: CatalogPort,
    ) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(
        self,
        ctx: ToolContext,
        items: list[dict[str, Any]],
        shipping_cop: int,
        shipping_address_summary: str,
        payment_method: str,
        tax_cop: int = 0,
    ) -> str:
        # PREMORTEM #5: validar consistencia contra snapshot.
        # El LLM puede pasar `unit_price_cop` inventado. Acá comparamos
        # contra el snapshot — si el precio del snapshot es muy distinto
        # (>5% drift), levantamos un flag y NO encolamos el intent: el LLM
        # debe re-llamar `verify_order_for_checkout` antes de cerrar.
        # NO bloquea si el catalog falla (degradado), solo si el precio
        # snapshot existe y es claramente distinto del que pasó el LLM.
        price_drift_alerts: list[str] = []
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
                retailer_id = product.id
                # Resolver precio del snapshot para validación
                sp, _sc = _first_price(product)
                if sp is not None:
                    try:
                        snapshot_price_cop = int(float(sp))
                    except (ValueError, TypeError):
                        snapshot_price_cop = None
            except Exception:  # noqa: BLE001
                title = handle
                retailer_id = handle
            # Drift check: si el precio del LLM difiere del snapshot por más
            # del 5%, ese item necesita re-verify_order_for_checkout.
            if snapshot_price_cop is not None and snapshot_price_cop > 0:
                drift_pct = abs(unit_price - snapshot_price_cop) / snapshot_price_cop
                if drift_pct > 0.05:
                    price_drift_alerts.append(
                        f"{handle}: LLM=${unit_price:,} snapshot=${snapshot_price_cop:,} "
                        f"(drift {drift_pct*100:.1f}%)"
                    )
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

        total = subtotal + shipping_cop + tax_cop
        reference_id = f"HUB-hubara-{ctx.session_key}-{int(time.time())}"

        # PREMORTEM #5: si hay drift >5% en algún precio, RECHAZAR el intent
        # y forzar al LLM a re-verify_order_for_checkout antes de mostrar
        # confirmación.
        if price_drift_alerts:
            logger.warning(
                "🚨 [TOOL present_order_confirmation] price_drift_blocked: {}",
                price_drift_alerts,
            )
            return json.dumps({
                "queued": False,
                "error": "price_drift",
                "alerts": price_drift_alerts,
                "message": (
                    "Algunos precios que pasaste NO coinciden con el "
                    "snapshot del catálogo (drift >5%). NO se encoló la "
                    "confirmación. Volvé a llamar `verify_order_for_checkout` "
                    "con los items y usá los precios EXACTOS del envelope "
                    "que devuelve."
                ),
            }, ensure_ascii=False)

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

        return json.dumps({
            "queued": True,
            "kind": "order_confirmation",
            "reference_id": reference_id,
            "total_cop": total,
            "summary": (
                f"Confirmación enviada con total ${total:,} COP. "
                "Esperá la respuesta del cliente: si confirma con "
                "Order Details nativo, recibirás el evento "
                "order_status:captured. Si fallback a botones, "
                "recibirás '[el cliente tocó el botón: Confirmar]'."
            ).replace(",", "."),
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
        "Reacciona al último mensaje del cliente con un emoji. Usalo como "
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
        "espera que vos sigas atendiéndolo."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "maxLength": 200,
                "description": (
                    "Por qué compartís el contacto. Aparece como nota al "
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
                "Reasoná en tu próximo turno como continuación natural."
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
        "Envía un botón con URL al cliente. Usalo SOLO si el cliente lo "
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
                    "usá `present_product_gallery`. Si pide ver el "
                    "producto, usá `present_product_detail`. Si quiere "
                    "comprar, seguí el flow de checkout en WhatsApp."
                ),
            }, ensure_ascii=False)
        if not any(url.startswith(prefix) for prefix in self._WHITELIST):
            return json.dumps({
                "queued": False,
                "error": "url_not_whitelisted",
                "url": url,
                "message": (
                    "Esa URL no está en la whitelist Hubara. "
                    "Continuá la conversación en texto."
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
        "como una secuencia de imágenes dentro de WhatsApp. Usala cuando "
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
                    f"El handle '{handle}' no existe. Usá search_products "
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
                    f"No tengo más fotos de {product.title}. Continuá en "
                    "texto — preguntale al cliente por aroma/color."
                ),
            }, ensure_ascii=False)
        # Cap defensivo
        additional = additional[:max(1, min(max_images, 4))]

        # Encolamos UN intent product_gallery con todas las URLs. El
        # dispatch las manda en secuencia. Esto vs N intents separados:
        # menos overhead de I/O en metadata.json y mejor agrupación
        # analytics.
        intent = {
            "kind": "product_gallery",
            "params": {
                "handle": handle,
                "title": product.title,
                "image_urls": additional,
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
        return json.dumps({
            "queued": True,
            "kind": "product_gallery",
            "handle": handle,
            "count": len(additional),
            "summary": (
                f"{len(additional)} foto(s) adicionales de {product.title} "
                "enviadas en secuencia. NO le mandes el link a la web — el "
                "cliente ya las está viendo en el chat. Tu próximo "
                "mensaje: invitalo a elegir aroma/color o cerrar la compra."
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
    "¿continúas con el aroma lavanda o cambias?".

    Los IDs de botones deben ser semánticos (catalog.browse, order.cancel,
    etc.) — el LLM los verá de vuelta como "[el cliente tocó el botón:
    <título>]" cuando el cliente toque.
    """

    name = "send_quick_replies"
    description = (
        "Envía 1-3 botones de respuesta rápida al cliente. Úsala en el "
        "SALUDO inicial cuando la intención del cliente no está clara "
        "(ej: 'hola', 'buenas'), Y en decisiones binarias durante la "
        "conversación. Tu texto previo debe contextualizar la pregunta, "
        "y los botones presentan las opciones. NO repitas las opciones "
        "en texto — el cliente las ve como botones tappables."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "body": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1024,
                "description": (
                    "Texto que acompaña los botones. Breve y claro — "
                    "el cliente ya recibió tu mensaje principal en el "
                    "send_whatsapp_message_activity previo."
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

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)

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
                "Esperá la elección del cliente — recibirás "
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

    Closed-list strict: las opciones deben venir del envelope de
    `get_product_by_handle` (campo `tags` con prefijo `Aroma:` / `Color:`).
    NUNCA del conocimiento general del LLM.
    """

    name = "present_variant_picker"
    description = (
        "Envía un mensaje de texto bonito al cliente con las opciones de "
        "variante (aromas / colores / tamaños), con un emoji distintivo "
        "por opción agrupadas por categoría. El cliente lee y **responde "
        "por texto** la que prefiere (ej: 'lavanda', 'el morado'). Usala "
        "cuando vayas a presentar 4 o más opciones de aroma/color de un "
        "producto. El emoji por opción se asigna automáticamente desde el "
        "registry Hubara — **vos NO pasás emojis, solo el nombre literal "
        "del envelope**. Si el cliente ya eligió la variante, NO uses esta "
        "tool — continuá hacia el cierre."
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
                                "Nombre LITERAL del envelope (ej 'Lavanda', "
                                "'Verde menta', 'rosado'). Closed-list — "
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
                    "Texto breve que acompaña el picker. Ej: 'Tenemos "
                    "estos aromas:' o 'Estos son los colores disponibles:'. "
                    "Sin listar las opciones en el texto — el cliente las "
                    "ve en la lista tappable."
                ),
            },
            "handle": {
                "type": "string",
                "description": (
                    "Handle del producto al que pertenecen estas variantes "
                    "(opcional, para analytics y para que el LLM pueda "
                    "correlacionar la elección con el contexto)."
                ),
                "maxLength": 200,
            },
        },
        "required": ["variant_type", "options", "intro_text"],
    }

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)

    async def execute_with_context(
        self,
        ctx: ToolContext,
        variant_type: str,
        options: list[dict[str, Any]],
        intro_text: str,
        handle: str | None = None,
    ) -> str:
        from src.platform.whatsapp.variant_emoji import (
            color_emoji,
            group_colors,
            group_scents,
            scent_emoji,
        )

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

        # Construcción de sections con emoji desde closed-list
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

        # Paginación (bug 10d8bd21): Meta limita el TOTAL de rows a 10
        # sumadas todas las sections de UN mensaje. Hubara tiene 11
        # aromas, así que cuando se piden TODOS hay que dividir en 2
        # mensajes consecutivos. `paginate_list_rows` preserva el orden
        # de sections y trata de NO partir sections cuando se puede
        # (mejor UX: una categoría completa por mensaje).
        pages = wa_limits.paginate_list_rows(
            sections_payload, wa_limits.MAX_LIST_ROWS_TOTAL
        )

        if not pages:
            return json.dumps({
                "queued": False,
                "error": "no_rows",
                "message": "No quedaron rows tras sanitización.",
            }, ensure_ascii=False)

        # Body de continuación según variant_type (en español, profesional).
        continuation_body = {
            "scent": "Más aromas disponibles:",
            "color": "Más colores disponibles:",
            "size": "Más opciones:",
        }.get(variant_type, "Más opciones:")

        total_pages = len(pages)
        total_options = sum(len(s["rows"]) for p in pages for s in p)
        for page_idx, page_sections in enumerate(pages):
            is_first = page_idx == 0
            body = (
                wa_limits.truncate(intro_text, wa_limits.MAX_LIST_BODY)
                if is_first
                else continuation_body
            )
            intent = {
                "kind": "variant_picker",
                "params": {
                    "variant_type": variant_type,
                    "intro_text": body,
                    "sections": page_sections,
                    "button_label": "Ver opciones",
                    "handle": handle,
                    "page": page_idx + 1,
                    "total_pages": total_pages,
                },
                "analytics": {
                    "component_id": f"variant_picker.{variant_type}",
                    # Render actual: texto plano con emojis curados
                    # (cambiado en sesión adc6400c, antes era interactive.list).
                    "component_kind": "text.variant_picker",
                    "handle": handle,
                    "count": sum(len(s["rows"]) for s in page_sections),
                    "page": page_idx + 1,
                    "total_pages": total_pages,
                },
            }
            _append_intent(ctx.session_key, intent)

        paging_note = (
            f" (enviado en {total_pages} mensajes para mostrar todos)"
            if total_pages > 1
            else ""
        )
        return json.dumps({
            "queued": True,
            "kind": "variant_picker",
            "variant_type": variant_type,
            "count": total_options,
            "pages": total_pages,
            "summary": (
                f"Picker de {variant_type} con {total_options} opciones "
                f"enviado como texto con emojis curados{paging_note}. "
                "Esperá la respuesta libre del cliente (ej: 'lavanda', "
                "'el morado'). NO repitas la lista en tu próximo mensaje "
                "— el cliente acaba de verla."
            ),
        }, ensure_ascii=False)


def _slug(label: str) -> str:
    """Slug minimalista para IDs de row (snake-case ASCII)."""
    import unicodedata

    nfkd = unicodedata.normalize("NFD", label.lower())
    cleaned = "".join(c for c in nfkd if not unicodedata.combining(c))
    return "".join(c if c.isalnum() else "_" for c in cleaned).strip("_")
