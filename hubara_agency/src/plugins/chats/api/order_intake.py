"""Contrato HTTP ``order-intake@v1`` — el formulario pre-llenado del botón
"Crear pedido" del chat intervenido.

Por qué existe (caso 2026-09-17): el cliente no completó el Flow de
datos de envío, el bot escaló por ``ORDER_PENDING_SHIPPING_DETAILS`` y el
humano le sacó los datos a mano por chat. Ahí la conversación quedaba sin
salida: el bot ya no responde (ruta humano) y el humano no tenía ningún botón
para registrar el pedido. Los datos estaban **en la conversación** y nadie los
podía convertir en una orden.

Este router hace UNA cosa::

    POST /api/chats/order-intake/{session_key}/suggest

lee la conversación (la entera, marcando dónde entró el humano), se la pasa a
DeepSeek y devuelve el formulario **pre-llenado** + la lista cerrada del
catálogo para corregirlo. **NO registra nada**: el humano revisa, edita y
manda el formulario a ``session-actions@v1`` (``POST
/api/chats/session-actions/{session_key}/order``), que es el único camino que
crea la orden en Medusa (con su chequeo SEC-07 de montos, su idempotencia por
contenido y su cierre "pago pendiente").

Decisiones:

* **El LLM nunca pone precios ni inventa productos.** Devuelve handles de una
  lista cerrada (el catálogo va en el prompt); el endpoint descarta lo que no
  existe y precia contra el snapshot (``price_order_items``). Es la misma
  regla que ya aplica ``/order`` para Meta Business Agent.
* **La conversación viaja como DATO** (delimitada, con instrucción explícita
  de no obedecerla): el texto lo escribe un cliente, es input no confiable.
* **Degradación honesta**: si DeepSeek no responde, el endpoint igual devuelve
  200 con lo que el bot ya había anotado (``order_draft``) y ``degraded:true``.
  El humano nunca se queda sin formulario por un LLM caído.
* **Read-only**: no toca el vault. Ningún efecto de la sugerencia sobrevive a
  que el humano cierre el modal sin enviar.
* P-28: este módulo importa SOLO ``src.sdk`` + módulos de ``chats``.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam
from loguru import logger

from src.plugins.chats.agent.sales.config.shipping import shipping_rate_for_city
from src.plugins.chats.agent.sales.use_cases.coupon_quota import resolve_item_variants
from src.plugins.chats.agent.sales.use_cases.coupons import coupon_discount_for_items
from src.plugins.chats.agent.sales.use_cases.order_pricing import price_order_items
from src.plugins.chats.shared.order_intake import (
    build_prompt,
    catalog_digest,
    draft_slots_of,
    handoff_started_ms,
    llm_items,
    merge_shipping,
    missing_fields,
    normalize_payment_method,
    parse_extraction,
    phone_from_session,
    registered_order_id,
    render_conversation,
)
from src.sdk.connectorkit import (
    get_catalog_client,
    get_coupon_sales_reader,
    get_promo_quota_store,
    parse_variant_tags,
)
from src.sdk.runtime import WORKSPACE_VAULT_DIR, FilesystemMetadataStore

router = APIRouter()

#: Mismo guard que ``session_actions``: el segmento llega al filesystem del vault.
_SESSION_RE = re.compile(r"^wa_[0-9]{8,15}$")

#: Modelo de extracción. DeepSeek vía el proxy litellm (`litellm_proxy/` le dice
#: al SDK que resuelva el alias EN el proxy — la API key del vendor vive ahí,
#: no en el container de la API).
_DEFAULT_MODEL = "litellm_proxy/deepseek-v4-flash"
_TIMEOUT_S = 45.0
#: Cuántos productos del catálogo entran al prompt y al selector del formulario.
_CATALOG_LIMIT = 200


def extraction_model() -> str:
    return os.environ.get("ORDER_EXTRACTION_MODEL", _DEFAULT_MODEL)


class ExtractionLLM(Protocol):
    """Port del extractor (R-DIP): el endpoint no conoce a litellm."""

    model: str

    async def extract(self, prompt: str) -> str: ...


class LiteLLMExtractor:
    """Adapter litellm → DeepSeek. `temperature=0`: esto es extracción, no
    redacción; dos clicks sobre la misma conversación deben dar lo mismo."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or extraction_model()

    async def extract(self, prompt: str) -> str:
        import litellm

        response = await litellm.acompletion(
            model=self.model,
            api_base=os.environ.get("API_BASE_LLMLITE", "http://localhost:4000"),
            api_key=os.environ.get("LITELLM_API_KEY", "sk-litellm-proxy-local"),
            temperature=0,
            timeout=_TIMEOUT_S,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""


@dataclass
class OrderIntakeDeps:
    vault_dir: Path
    catalog: Any | None  # CatalogPort — None si este proceso no lo tiene
    llm: ExtractionLLM
    # Cupo por unidad: el sugerido muestra qué línea lleva descuento.
    quotas: Any | None = None
    sales: Any | None = None


def _try(name: str, factory: Any) -> Any | None:
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001 — sin config = endpoint degradado, no 500
        logger.warning("[chats.order_intake] {} no disponible en este proceso: {}", name, exc)
        return None


@lru_cache(maxsize=1)
def get_order_intake_deps() -> OrderIntakeDeps:
    return OrderIntakeDeps(
        vault_dir=WORKSPACE_VAULT_DIR,
        catalog=_try("catalog", get_catalog_client),
        llm=LiteLLMExtractor(),
        quotas=_try("promo_quota_store", get_promo_quota_store),
        sales=_try("coupon_sales_reader", get_coupon_sales_reader),
    )


Deps = Annotated[OrderIntakeDeps, Depends(get_order_intake_deps)]
SessionKey = Annotated[str, PathParam(min_length=1, max_length=64)]


def _session(session_key: str) -> str:
    if not _SESSION_RE.fullmatch(session_key):
        raise HTTPException(status_code=422, detail="session_key inválida (esperado wa_<dígitos>)")
    return session_key


def _read_events(vault_dir: Path, session_key: str) -> list[dict[str, Any]]:
    """JSONL de la sesión, tolerante línea a línea (la última puede estar a
    medio escribir si el proceso que escribía murió)."""
    path = vault_dir / session_key / "sessions" / f"{session_key}.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    events.append(parsed)
    except OSError:
        return []
    return events


async def _load_catalog(deps: OrderIntakeDeps, warnings: list[str]) -> list[Any]:
    if deps.catalog is None:
        warnings.append(
            "catalog no disponible en este proceso: elegí los productos a mano "
            "(los datos de envío sí se extrajeron)."
        )
        return []
    try:
        result = await deps.catalog.search("", limit=_CATALOG_LIMIT)
    except Exception as exc:  # noqa: BLE001 — el texto del vendor va al log
        logger.warning("[chats.order_intake] catálogo no disponible: {}", exc)
        warnings.append("catalog no disponible: elegí los productos a mano.")
        return []
    return list(getattr(result, "results", None) or [])


class _DictCatalog:
    """Adapter mínimo: los productos ya cargados como `CatalogPort.get_by_handle`."""

    def __init__(self, products_by_handle: dict[str, Any]) -> None:
        self._products = products_by_handle

    async def get_by_handle(self, handle: str) -> Any:
        try:
            return self._products[handle]
        except KeyError as exc:
            raise LookupError(handle) from exc


def _resolve_items(
    raw_items: list[dict[str, Any]], products_by_handle: dict[str, Any], warnings: list[str]
) -> list[dict[str, Any]]:
    """Ítems del LLM → ítems con precio del CATÁLOGO.

    Por ítem (no todo-o-nada como ``/order``): un handle alucinado se descarta
    con aviso y el resto del formulario sigue siendo útil.
    """
    resolved: list[dict[str, Any]] = []
    for raw in raw_items:
        handle = raw["handle"]
        product = products_by_handle.get(handle)
        if product is None:
            warnings.append(f"El modelo propuso un producto que no está en el catálogo: {handle}")
            continue
        priced = price_order_items({handle: product}, [raw])
        if priced.problems or not priced.items:
            warnings.append(f"No se pudo precisar el ítem {handle}: {'; '.join(priced.problems)}")
            continue
        item = priced.items[0]
        resolved.append(
            {
                "handle": item["handle"],
                "title": item["title"],
                "variant_label": item["variant_label"],
                "quantity": item["quantity"],
                "unit_price_cop": item["unit_price_cop"],
                "line_total_cop": item["unit_price_cop"] * item["quantity"],
                "variant_resolved": item["variant_resolved"],
                "evidence": raw.get("evidence"),
            }
        )
    return resolved


@router.post("/order-intake/{session_key}/suggest")
async def suggest(session_key: SessionKey, deps: Deps) -> dict[str, Any]:
    """Lee la conversación y devuelve el pedido SUGERIDO (no lo registra)."""
    session = _session(session_key)
    warnings: list[str] = []

    metadata = FilesystemMetadataStore(deps.vault_dir).read(session)
    events = _read_events(deps.vault_dir, session)
    handoff_ms = handoff_started_ms(metadata)
    conversation, considered = render_conversation(events, handoff_ms=handoff_ms)
    draft_slots = draft_slots_of(metadata)

    products = await _load_catalog(deps, warnings)
    catalog = catalog_digest(products)
    products_by_handle = {str(getattr(p, "handle", "")): p for p in products}

    extracted: dict[str, Any] = {}
    degraded = False
    error_detail: str | None = None
    if conversation:
        prompt = build_prompt(conversation=conversation, catalog=catalog, draft_slots=draft_slots)
        try:
            extracted = parse_extraction(await deps.llm.extract(prompt))
        except Exception as exc:  # noqa: BLE001 — degradación honesta, no 500
            degraded = True
            error_detail = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning(
                "[chats.order_intake] extracción degradada session={} err={}", session, error_detail
            )
    else:
        degraded = True
        error_detail = "la sesión no tiene mensajes para leer"

    raw_shipping = extracted.get("shipping")
    shipping, field_sources = merge_shipping(
        raw_shipping if isinstance(raw_shipping, dict) else {},
        draft_slots,
        session_phone=phone_from_session(session),
    )
    items = _resolve_items(llm_items(extracted), products_by_handle, warnings)

    payment_method = normalize_payment_method(extracted.get("payment_method"))
    payment_source: str | None = "conversation" if payment_method else None
    if payment_method is None:
        payment_method = normalize_payment_method(draft_slots.get("metodo_pago"))
        payment_source = "draft" if payment_method else None
    field_sources["payment_method"] = payment_source

    subtotal_cop = sum(it["line_total_cop"] for it in items)
    shipping_cop = shipping_rate_for_city(shipping.get("city"))
    # Cupón aplicado en el chat: el formulario muestra el mismo descuento
    # que va a exigir el registro (SEC-07).
    dict_catalog = _DictCatalog(products_by_handle)
    # Listas de color/aroma de cada producto del selector (para las líneas que
    # el operador agrega a mano). Se agregan DESPUÉS del prompt: el modelo no
    # las necesita.
    for entry in catalog:
        lists = parse_variant_tags(
            list(getattr(products_by_handle.get(entry["handle"]), "tags", None) or [])
        )
        entry.update(colors=list(lists.colors), aromas=list(lists.aromas))
    # Color y aroma de cada ítem (del borrador estructurado del chat) + las
    # listas del producto para los selectores del formulario.
    variants, _invalid = await resolve_item_variants(dict_catalog, items, metadata)
    for item, variant in zip(items, variants):
        attrs = parse_variant_tags(list(getattr(products_by_handle.get(item["handle"]), "tags", None) or []))
        item.update(color=variant.color, aroma=variant.aroma, colors=list(attrs.colors), aromas=list(attrs.aromas))
    discount = await coupon_discount_for_items(
        metadata, dict_catalog, items, shipping_cop=shipping_cop,
        quotas=deps.quotas, sales=deps.sales, variants=variants,
    )
    discount_cop = discount.discount_cop if discount else 0
    # Qué unidades de cada línea llevan el descuento (el formulario lo muestra).
    for index, item in enumerate(items):
        lines = [d for d in (discount.line_discounts if discount else ()) if d.index == index]
        item["coupon_units"] = sum(d.units for d in lines)
        item["coupon_discount_cop"] = sum(d.units * d.discount_unit_cop for d in lines)
    notes = extracted.get("notes")

    logger.info(
        "[chats.order_intake] suggest session={} items={} faltan={} degraded={} model={}",
        session,
        len(items),
        len(missing_fields(shipping, items, payment_method)),
        degraded,
        deps.llm.model,
    )
    return {
        "session_key": session,
        "phone_number": phone_from_session(session),
        "handoff_at_ms": handoff_ms,
        "messages_considered": considered,
        "items": items,
        "shipping": shipping,
        "field_sources": field_sources,
        "payment_method": payment_method,
        "subtotal_cop": subtotal_cop,
        "shipping_cop": shipping_cop,
        "discount_cop": discount_cop,
        "coupon_code": discount.code if discount and discount_cop > 0 else None,
        "total_cop": subtotal_cop + shipping_cop - discount_cop,
        "missing": missing_fields(shipping, items, payment_method),
        "warnings": warnings,
        "notes": str(notes)[:500] if isinstance(notes, str) and notes.strip() else None,
        "catalog": catalog,
        "already_registered_order_id": registered_order_id(metadata),
        "model": deps.llm.model,
        "degraded": degraded,
        "error_detail": error_detail,
    }
