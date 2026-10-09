"""Acción del operador desde la app: correr una tool de UI del bot COMO EL HUMANO.

    POST /api/chats/session-actions/{session_key}/tools/{tool}
         body {"client_action_id": "<uuid>", "args": {...}}

Corre la MISMA tool que usa el bot (constructores = los del worker de Sales,
misma validación contra el catálogo) y manda SOLO los intents que encoló esta
llamada (flush acotado por id: nunca otros de la cola). Lo enviado queda en el
historial firmado por el humano (``sender: "human"`` + ``operator_tool``).
Serializa por sesión con el MISMO lock que ``/order``.

P-28: este módulo importa SOLO ``src.sdk`` + módulos de ``chats``.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    _render_payment_instructions_text,
    flush_pending_ui_intents,
)
from src.plugins.chats.agent.sales.config.shipping import shipping_rate_for_city
from src.plugins.chats.agent.sales.tools.coupons import ApplyCouponTool
from src.plugins.chats.agent.sales.tools.ui_intents import (
    PresentOrderConfirmationTool,
    PresentProductDetailTool,
    PresentProductGalleryTool,
    PresentProductsTool,
    PresentVariantPickerTool,
    RequestShippingDetailsTool,
    SendQuickRepliesTool,
    SendShippingRatesTool,
    collect_enqueued_intent_ids,
)
from src.plugins.chats.agent.sales.use_cases.quantity_capture import parse_leading_quantity
from src.plugins.chats.api.mobile import unit_price_cop
from src.plugins.chats.api.order_intake import _read_events
from src.plugins.chats.api.session_actions import (
    _ctx,
    _release_session_lock,
    _session_lock,
)
from src.plugins.chats.shared.draft_items import draft_items, find_product
from src.plugins.chats.shared.operator_order import operator_view, positive_quantity
from src.plugins.chats.shared.order_intake import normalize_payment_method
from src.sdk.identitykit import is_customer_session_id
from src.sdk.connectorkit import (
    ProductNotFoundError,
    get_catalog_client,
    get_coupon_sales_reader,
    get_promo_quota_store,
    get_promotions_port,
    parse_variant_tags,
    schedule_capi_flush,
)
from src.sdk.messagingkit import is_service_window_closed
from src.sdk.runtime import WORKSPACE_VAULT_DIR, FilesystemMetadataStore

router = APIRouter()

#: Mismo valor que ``src.platform.constants.ROUTE_HUMANO``.
ROUTE_HUMANO = "humano"

#: Lo que el operador puede correr desde la app: las tools de UI del bot +
#: `send_payment_methods` (el texto de pago de `register_order`, sin registrar).
ALLOWED_TOOLS: frozenset[str] = frozenset({
    "present_variant_picker",
    "present_products",
    "present_product_detail",
    "present_product_gallery",
    "request_shipping_details",
    "send_shipping_rates",
    "present_order_confirmation",
    "send_quick_replies",
    "apply_coupon",
    "send_payment_methods",
})

#: Tools que leen el catálogo (sin snapshot en este proceso → 503).
_NEEDS_CATALOG = frozenset({
    "present_variant_picker",
    "present_products",
    "present_product_detail",
    "present_product_gallery",
    "request_shipping_details",
    "present_order_confirmation",
})
#: Tools que cambian el pedido pero no le mandan nada al cliente.
_NO_SEND_TOOLS = frozenset({"apply_coupon"})

#: Ledger de idempotencia en el metadata de la sesión (últimas N acciones).
LEDGER_KEY = "operator_tool_actions"
LEDGER_CAP = 200


@dataclass
class OperatorToolsDeps:
    vault_dir: Path
    catalog: Any | None  # CatalogPort (snapshot)
    promotions: Any | None  # PromotionsPort (apply_coupon)
    quotas: Any | None  # cupo por unidad (mismos que el bot)
    sales: Any | None
    #: `flush_pending_ui_intents(session, *, only_ids, operator_tool) -> enviados`
    flush: Callable[..., Awaitable[int]]
    now_ms: Callable[[], int]


def _try(name: str, factory: Callable[[], Any]) -> Any | None:
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001 — sin config = endpoint degradado (503), no 500
        logger.warning("[chats.operator_tools] {} no disponible en este proceso: {}", name, exc)
        return None


def _now_ms() -> int:
    return int(time.time() * 1000)


@lru_cache(maxsize=1)
def get_operator_tools_deps() -> OperatorToolsDeps:
    return OperatorToolsDeps(
        vault_dir=WORKSPACE_VAULT_DIR,
        catalog=_try("catalog", get_catalog_client),
        promotions=_try("promotions", get_promotions_port),
        quotas=_try("promo_quota_store", get_promo_quota_store),
        sales=_try("coupon_sales_reader", get_coupon_sales_reader),
        flush=flush_pending_ui_intents,
        now_ms=_now_ms,
    )


OpDeps = Annotated[OperatorToolsDeps, Depends(get_operator_tools_deps)]


class ToolBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_action_id: str = Field(min_length=1, max_length=128)
    args: dict[str, Any] = Field(default_factory=dict)


def _bot_tools(deps: OperatorToolsDeps) -> dict[str, Callable[[], Any]]:
    """Las tools de UI del bot, con los MISMOS colaboradores que les da el
    worker de Sales (`workers/sales.py`)."""
    ws = str(deps.vault_dir)
    store = FilesystemMetadataStore(deps.vault_dir)
    return {
        "present_variant_picker": lambda: PresentVariantPickerTool(ws, catalog=deps.catalog, metadata_store=store),
        "present_products": lambda: PresentProductsTool(ws, deps.catalog),
        "present_product_detail": lambda: PresentProductDetailTool(ws, deps.catalog),
        "present_product_gallery": lambda: PresentProductGalleryTool(ws, deps.catalog),
        "request_shipping_details": lambda: RequestShippingDetailsTool(ws, catalog=deps.catalog),
        "send_shipping_rates": lambda: SendShippingRatesTool(ws),
        "present_order_confirmation": lambda: PresentOrderConfirmationTool(
            ws, deps.catalog, quotas=deps.quotas, sales=deps.sales
        ),
        "send_quick_replies": lambda: SendQuickRepliesTool(ws, catalog=deps.catalog),
        "apply_coupon": lambda: ApplyCouponTool(
            ws, promotions=deps.promotions, metadata_store=store, catalog=deps.catalog,
            quotas=deps.quotas, sales=deps.sales,
        ),
    }


def _arg_problems(instance: Any, args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """(args casteados, problemas) con el MISMO chequeo que aplica el registry
    de tools al LLM (`cast_params` + `validate_params`) + args desconocidos."""
    allowed = set((instance.parameters or {}).get("properties") or {})
    unknown = [f"argumento desconocido: {k}" for k in args if k not in allowed]
    cast = instance.cast_params(args)
    return cast, unknown + list(instance.validate_params(cast))


def _rejection(envelope: dict[str, Any]) -> tuple[str, str] | None:
    """(motivo, mensaje) si la tool rechazó la acción, o None si procedió.
    UI tools: `queued: false`. `apply_coupon`: `applied: false` (salvo quitar
    el cupón, que es un éxito)."""
    if envelope.get("queued") is False:
        return str(envelope.get("error") or "rejected"), str(envelope.get("message") or "")
    if envelope.get("applied") is False and envelope.get("reason") != "removed":
        return str(envelope.get("reason") or "rejected"), str(envelope.get("summary") or "")
    return None


# ── args del operador ────────────────────────────────────────────────────────
#
# Las burbujas (`GET /mobile/suggestions`) emiten args CORTOS que la app manda
# tal cual; acá se expanden a los args nativos de la tool desde el catálogo /
# el borrador. Si llegan los args nativos, pasan directo (y los valida la tool).


class _InvalidArgs(Exception):
    """422 `invalid_args`: `problems` (la lista) y `message`, la frase que la
    app le muestra al operador tal cual (sin ella decía «faltan datos para esta
    acción» y el operador no sabía qué faltaba)."""

    def __init__(self, problems: list[str], message: str | None = None) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems
        self.message = message or _sentence(problems)


def _sentence(problems: list[str]) -> str:
    text = "; ".join(p.strip().rstrip(".") for p in problems if p.strip())
    return f"{text[:1].upper()}{text[1:]}." if text else "Faltan datos para esta acción."


def _join(parts: list[str]) -> str:
    """«a, b y c»."""
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} y {parts[-1]}"


class _Rejected(Exception):
    """La tool (o la acción) no procede: 422 `tool_rejected`."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.message = message


class _CatalogUnavailable(Exception):
    pass


_PICKER_TYPES = {"aroma": "scent", "color": "color"}
_PICKER_INTROS = {"aroma": "Tenemos estos aromas:", "color": "Estos son los colores disponibles:"}


def _unknown(args: dict[str, Any], allowed: set[str]) -> list[str]:
    return [f"argumento desconocido: {k}" for k in sorted(set(args) - allowed)]


async def _catalog_product(deps: OperatorToolsDeps, handle: Any, problems: list[str]) -> Any | None:
    if not isinstance(handle, str) or not handle.strip():
        problems.append("elige el producto")
        return None
    if deps.catalog is None:
        raise _CatalogUnavailable()
    try:
        return await deps.catalog.get_by_handle(handle.strip())
    except ProductNotFoundError:
        problems.append(f"el producto «{handle}» no está en el catálogo")
        return None
    except Exception as exc:  # noqa: BLE001 — catálogo caído ≠ args malos
        raise _CatalogUnavailable() from exc


async def _picker_args(args: dict[str, Any], deps: OperatorToolsDeps) -> dict[str, Any]:
    """`{product, attribute}` (+ `options`/`intro_text` opcionales) → args de
    `present_variant_picker` con la lista CERRADA del producto."""
    problems = _unknown(args, {"product", "attribute", "options", "intro_text"})
    attribute = args.get("attribute")
    if attribute not in _PICKER_TYPES:
        problems.append("attribute debe ser 'aroma' o 'color'")
    product = await _catalog_product(deps, args.get("product"), problems)
    if problems or product is None:
        raise _InvalidArgs(problems)
    attrs = parse_variant_tags(product.tags)
    labels = args.get("options") or (attrs.aromas if attribute == "aroma" else attrs.colors)
    return {
        "variant_type": _PICKER_TYPES[attribute],
        "options": [{"label": str(label)} for label in labels],
        "intro_text": args.get("intro_text") or _PICKER_INTROS[attribute],
        "handle": product.handle,
    }


#: Cuántos productos del snapshot se leen para cruzar el borrador.
_CATALOG_LIMIT = 200


async def _all_products(deps: OperatorToolsDeps) -> list[Any]:
    if deps.catalog is None:
        raise _CatalogUnavailable()
    try:
        result = await deps.catalog.search("", limit=_CATALOG_LIMIT)
    except Exception as exc:  # noqa: BLE001
        raise _CatalogUnavailable() from exc
    return [p for p in result.results if getattr(p, "status", "published") == "published"]


#: El borrador sin productos: lo que el operador tiene que hacer.
_NO_PRODUCTS = "el pedido todavía no tiene productos: elige el producto y la cantidad"


async def _draft_lines(view: dict[str, Any], deps: OperatorToolsDeps) -> list[tuple[dict[str, Any], Any, int]]:
    """(ítem del pedido, producto del catálogo, cantidad) del borrador del
    operador (`mobile.operator_view`)."""
    items = draft_items(view)
    if not items:
        raise _InvalidArgs([_NO_PRODUCTS])
    products = await _all_products(deps)
    lines: list[tuple[dict[str, Any], Any, int]] = []
    problems: list[str] = []
    for item in items:
        product = find_product(products, item.get("producto"))
        quantity = parse_leading_quantity(str(item.get("cantidad") or ""))
        if product is None:
            problems.append(f"«{item.get('producto')}» no está en el catálogo")
        elif not quantity:
            problems.append(f"falta la cantidad de {product.title}")
        else:
            lines.append((item, product, quantity))
    if problems:
        raise _InvalidArgs(problems)
    return lines


async def _shipping_request_args(
    args: dict[str, Any], view: dict[str, Any], deps: OperatorToolsDeps
) -> dict[str, Any]:
    """`{product, quantity}` (lo que el operador elige en la app; la cantidad
    por defecto es 1) o `{}` → `items` {handle, quantity} del pedido (el total
    lo pone la tool desde el catálogo, igual que con el bot)."""
    problems = _unknown(args, {"product", "quantity"})
    if "product" not in args:
        if problems or "quantity" in args:
            raise _InvalidArgs(problems or ["elige el producto"])
        lines = await _draft_lines(view, deps)
        return {"items": [{"handle": p.handle, "quantity": q} for _item, p, q in lines]}
    quantity = positive_quantity(args.get("quantity", 1))
    if quantity is None:
        problems.append("la cantidad tiene que ser un número de 1 a 999")
    product = await _catalog_product(deps, args.get("product"), problems)
    if problems or product is None:
        raise _InvalidArgs(problems)
    return {"items": [{"handle": product.handle, "quantity": quantity}]}


#: Tope de productos de la lista tappable de la tool (Meta: 30).
_MAX_LIST_PRODUCTS = 30
_DEFAULT_PRODUCTS_INTRO = "Estos son nuestros productos:"


async def _products_args(args: dict[str, Any], deps: OperatorToolsDeps) -> dict[str, Any]:
    """Sin `handles` → el catálogo visible (hasta 30), con un intro neutro."""
    problems = _unknown(args, {"intro_text", "group_by"})
    if problems:
        raise _InvalidArgs(problems)
    products = await _all_products(deps)
    if not products:
        raise _Rejected("catalog_empty", "El catálogo no tiene productos visibles.")
    return {
        **args,
        "handles": [p.handle for p in products[:_MAX_LIST_PRODUCTS]],
        "intro_text": args.get("intro_text") or _DEFAULT_PRODUCTS_INTRO,
    }


async def _confirmation_args(
    args: dict[str, Any], view: dict[str, Any], deps: OperatorToolsDeps
) -> dict[str, Any]:
    """`{}` → args de `present_order_confirmation` desde el borrador del
    operador: precio del CATÁLOGO por ítem (la tool lo vuelve a validar), la
    tarifa mínima publicada de la ciudad (la misma regla que `/order`),
    dirección y medio de pago. Lo que falte se dice — no se inventa."""
    if args:
        raise _InvalidArgs(_unknown(args, set()))
    slots = view.get("slots") if isinstance(view.get("slots"), dict) else {}
    city = str(slots.get("ciudad") or "").strip()
    address = str(slots.get("direccion") or "").strip()
    method = normalize_payment_method(slots.get("metodo_pago"))
    missing = [
        part for part, absent in (
            ("el producto", not draft_items(view)),
            ("la ciudad", not city),
            ("la dirección", not address),
            ("el medio de pago", method is None),
        ) if absent
    ]
    if missing:
        hint = (
            "Elige el producto en «Pedir datos de envío» y espera a que el cliente llene el formulario."
            if "el producto" in missing
            else "Espera a que el cliente llene el formulario o pídeselo con «Pedir datos de envío»."
        )
        raise _InvalidArgs(
            [f"falta {part}" for part in missing],
            message=f"Para el resumen falta {_join(missing)}. {hint}",
        )
    problems: list[str] = []
    lines = await _draft_lines(view, deps)
    items: list[dict[str, Any]] = []
    for item, product, quantity in lines:
        price = unit_price_cop(product)
        if price is None:
            problems.append(f"{product.title} no tiene precio en el catálogo")
            continue
        items.append({
            "handle": product.handle,
            "quantity": quantity,
            "unit_price_cop": price,
            **{k: str(item[k]).strip() for k in ("color", "aroma") if item.get(k)},
        })
    if problems:
        raise _InvalidArgs(problems)
    neighborhood = str(slots.get("barrio") or "").strip()
    return {
        "items": items,
        "shipping_cop": shipping_rate_for_city(city),
        "shipping_address_summary": ", ".join(p for p in (address, neighborhood, city) if p),
        "payment_method": method,
    }


async def _operator_args(
    tool: str, args: dict[str, Any], session: str, metadata: dict[str, Any], deps: OperatorToolsDeps
) -> dict[str, Any]:
    if tool == "present_variant_picker" and "variant_type" not in args:
        return await _picker_args(args, deps)
    if tool == "present_products" and "handles" not in args:
        return await _products_args(args, deps)
    if tool == "request_shipping_details" and "items" not in args:
        return await _shipping_request_args(args, _view(session, metadata, deps), deps)
    if tool == "present_order_confirmation" and "items" not in args:
        return await _confirmation_args(args, _view(session, metadata, deps), deps)
    return args


def _view(session: str, metadata: dict[str, Any], deps: OperatorToolsDeps) -> dict[str, Any]:
    """El borrador del pedido con lo que pasó con el humano al mando: el bot
    no corre y nadie más lo llena (caso 2026-10-09)."""
    return operator_view(metadata, _read_events(deps.vault_dir, session))


def _prior_action(metadata: dict[str, Any], client_action_id: str) -> dict[str, Any] | None:
    ledger = metadata.get(LEDGER_KEY)
    if not isinstance(ledger, list):
        return None
    return next((e for e in ledger if isinstance(e, dict) and e.get("id") == client_action_id), None)


def _record_action(
    store: FilesystemMetadataStore,
    session: str,
    *,
    client_action_id: str,
    tool: str,
    sent: bool,
    now_ms: int,
    args: dict[str, Any] | None = None,
) -> None:
    """Anota la acción en el ledger (idempotencia + la jugada que leen las
    burbujas). ``args``: con qué args la mandó el operador — «Enviar aromas» y
    «Enviar colores» son la misma tool con otro ``attribute``. Las entradas
    viejas no los tienen (las burbujas las cuentan por nombre)."""
    entry: dict[str, Any] = {"id": client_action_id, "tool": tool, "sent": sent, "at_ms": now_ms}
    if args is not None:
        entry["args"] = args

    def _mutate(data: dict[str, Any]) -> dict[str, Any] | None:
        if not data:
            return None  # lectura fresca vacía: no se revive una sesión a medias
        ledger = [e for e in data.get(LEDGER_KEY) or [] if isinstance(e, dict)]
        ledger.append(entry)
        data[LEDGER_KEY] = ledger[-LEDGER_CAP:]
        return data

    store.update(session, _mutate)


def _error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, **extra})


def _ok(tool: str, client_action_id: str, *, sent: bool, deduplicated: bool) -> dict[str, Any]:
    return {"sent": sent, "tool": tool, "client_action_id": client_action_id, "deduplicated": deduplicated}


@router.post("/session-actions/{session_key}/tools/{tool}")
async def run_tool(session_key: str, tool: str, body: ToolBody, deps: OpDeps) -> Any:
    # El formato de `session_actions` (`wa_<teléfono>` o `wa_<id de Meta>`: el
    # segmento llega al vault) con la forma de error de esta ruta: una llave que
    # no puede nombrar una sesión es una sesión que no existe — antes de tocar nada.
    if not is_customer_session_id(session_key):
        return _error(404, "session_not_found")
    session = session_key
    if tool not in ALLOWED_TOOLS:
        return _error(404, "unknown_tool")
    try:
        async with _session_lock(session):
            return await _run(session, tool, body, deps)
    finally:
        _release_session_lock(session)  # fuera del `async with`: ya no está tomado


async def _run(session: str, tool: str, body: ToolBody, deps: OperatorToolsDeps) -> Any:
    store = FilesystemMetadataStore(deps.vault_dir)
    metadata = store.read(session)
    if not metadata:
        return _error(404, "session_not_found")
    # Idempotencia ANTES de las guardas: el reintento de algo ya enviado
    # responde replay aunque la ventana haya cerrado entre medio.
    prior = _prior_action(metadata, body.client_action_id)
    if prior is not None:
        recorded = str(prior.get("tool") or tool)
        return _ok(recorded, body.client_action_id, sent=bool(prior.get("sent", True)), deduplicated=True)
    if metadata.get("active_route") != ROUTE_HUMANO:
        return _error(409, "not_in_control")
    if is_service_window_closed(deps.now_ms(), metadata):
        return _error(409, "window_closed")
    if tool in _NEEDS_CATALOG and deps.catalog is None:
        return _error(503, "catalog_unavailable")
    if tool == "apply_coupon" and deps.promotions is None:
        return _error(503, "promotions_unavailable")
    try:
        if tool == "send_payment_methods":
            produced = _queue_payment_methods(store, session, dict(body.args))
        else:
            produced = await _run_bot_tool(store, session, tool, dict(body.args), deps)
    except _InvalidArgs as exc:
        return _error(422, "invalid_args", problems=exc.problems, message=exc.message)
    except _Rejected as exc:
        if exc.reason == "catalog_unavailable":  # la tool no pudo leer el snapshot
            return _error(503, "catalog_unavailable")
        return _error(422, "tool_rejected", reason=exc.reason, message=exc.message)
    except _CatalogUnavailable:
        return _error(503, "catalog_unavailable")
    if tool in _NO_SEND_TOOLS:
        # `apply_coupon`: queda en el pedido (lo usa el próximo resumen); al
        # cliente no le sale nada.
        _record_action(store, session, client_action_id=body.client_action_id, tool=tool, sent=False,
                       now_ms=deps.now_ms(), args=dict(body.args))
        return _ok(tool, body.client_action_id, sent=False, deduplicated=False)
    sent = await deps.flush(session, only_ids=produced, operator_tool=tool)
    if not sent:
        return _error(502, "send_failed")
    schedule_capi_flush(session)  # señal de embudo de lo que el cliente vio (ViewContent…)
    _record_action(store, session, client_action_id=body.client_action_id, tool=tool, sent=True,
                   now_ms=deps.now_ms(), args=dict(body.args))
    return _ok(tool, body.client_action_id, sent=True, deduplicated=False)


async def _run_bot_tool(
    store: FilesystemMetadataStore, session: str, tool: str, raw: dict[str, Any], deps: OperatorToolsDeps
) -> list[str]:
    """Corre la tool del bot; devuelve los ids de los intents que encoló ESTA llamada.

    Los informa la propia tool (`collect_enqueued_intent_ids`). Comparar la cola antes y después atribuía
    al operador un intent que un turno del bot encolara en medio desde el worker de Ventas: el candado de
    este endpoint es por proceso, así que salía firmado como humano."""
    instance = _bot_tools(deps)[tool]()
    args, problems = _arg_problems(instance, await _operator_args(tool, raw, session, store.read(session), deps))
    if problems:
        raise _InvalidArgs(problems)
    with collect_enqueued_intent_ids() as produced:
        envelope = json.loads(await instance.execute_with_context(_ctx(session), **args))
    rejected = _rejection(envelope)
    if rejected is not None:
        raise _Rejected(*rejected)
    return list(produced)


def _queue_payment_methods(store: FilesystemMetadataStore, session: str, args: dict[str, Any]) -> list[str]:
    """Encola el MISMO intent `payment_instructions` que `register_order`
    (el flush renderiza la plantilla fija desde env), sin monto ni referencia
    porque no hay pedido — y sin registrar nada."""
    if args:
        raise _InvalidArgs(_unknown(args, set()))
    if _render_payment_instructions_text({"method": "transfer"}) is None:
        raise _Rejected(
            "payment_methods_unavailable",
            "No hay datos de pago configurados (llave Nequi ni cuenta bancaria).",
        )
    intent_id = f"ui_{uuid.uuid4().hex}"

    def _enqueue(data: dict[str, Any]) -> dict[str, Any] | None:
        if not data:
            return None
        data.setdefault("pending_ui_intents", []).append({
            "id": intent_id,
            "kind": "payment_instructions",
            "params": {"method": "transfer"},
            "analytics": {"component_id": "payment_instructions", "component_kind": "text"},
            # Mismo reloj que `_append_intent`: el TTL del flush lo compara con él.
            "queued_at_ms": int(time.time() * 1000),
        })
        return data

    store.update(session, _enqueue)
    return [intent_id]
