"""Adapter Medusa v2 del `PromotionsPort` + mapper puro del shape Admin API.

`GET /admin/promotions` con `application_method` (+ `target_rules`), `rules`
y `campaign` (+ `budget`). Cache corto (TTL) por proceso: los cupones cambian
poco y el bot los consulta en cada turno que los menciona.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Mapping

from src.platform.promotions.port import PromotionDTO, PromotionsUnavailableError

log = logging.getLogger(__name__)

_PRODUCT_ATTRS = {"items.product.id", "items.product_id", "product.id", "product_id"}
_VARIANT_ATTRS = {"items.variant.id", "items.variant_id", "variant.id", "variant_id"}
_COLLECTION_ATTRS = {
    "items.product.collection_id",
    "items.product.collection.id",
    "product.collection_id",
}
_SUBTOTAL_ATTRS = {"item_total", "subtotal", "items.subtotal", "item_subtotal", "total"}
#: Condición por etiquetas de producto: la regla trae ids ("ptag_…"); el
#: catálogo conoce el nombre ("Color: Rosado"). Run 28a8e407 (AMOR26).
_TAG_ID_ATTRS = {"items.product.tags.id", "items.product.tags", "product.tags.id"}
_TAG_VALUE_ATTRS = {"items.product.tags.value", "product.tags.value"}


def _values(rule: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for v in rule.get("values") or []:
        if isinstance(v, dict):
            v = v.get("value")
        if v is not None and str(v).strip():
            out.append(str(v).strip())
    return out


def _iso_to_ms(raw: Any) -> int | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _to_int(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return None


def tag_ids_in(raw: dict[str, Any]) -> set[str]:
    """Ids de etiqueta que usan las reglas de la promoción (para traducirlos
    a nombre antes de mapearla)."""
    method = raw.get("application_method") if isinstance(raw, dict) else None
    if not isinstance(method, dict):
        return set()
    return {
        value
        for rule in method.get("target_rules") or []
        if isinstance(rule, dict) and str(rule.get("attribute") or "") in _TAG_ID_ATTRS
        for value in _values(rule)
    }


def promotion_from_medusa(
    raw: dict[str, Any], *, tag_values: Mapping[str, str] | None = None
) -> PromotionDTO | None:
    """Promoción Admin API v2 → snapshot. None si no es un cupón usable
    (sin código, sin método de aplicación).

    `tag_values` traduce los ids de etiqueta de las reglas a su nombre. Sin
    él (o con un id que no trae) la regla por etiquetas no se puede evaluar
    y la promo queda con alcance desconocido (falla cerrada)."""
    if not isinstance(raw, dict):
        return None
    code = raw.get("code")
    method = raw.get("application_method")
    if not isinstance(code, str) or not code.strip() or not isinstance(method, dict):
        return None
    product_ids: list[str] = []
    variant_ids: list[str] = []
    collection_ids: list[str] = []
    tag_names: list[str] = []
    # Falla CERRADA: una regla que no sabemos leer (sin `values` o con un
    # atributo que no entendemos) NO se ignora — ignorarla convertía "solo
    # estos productos" en "todo el catálogo" (incidente AMOR26).
    scope_unresolved = False
    for rule in method.get("target_rules") or []:
        if not isinstance(rule, dict):
            continue
        attr = str(rule.get("attribute") or "")
        values = _values(rule)
        if not values:
            scope_unresolved = True
            continue
        if attr in _PRODUCT_ATTRS:
            product_ids.extend(values)
        elif attr in _VARIANT_ATTRS:
            variant_ids.extend(values)
        elif attr in _COLLECTION_ATTRS:
            collection_ids.extend(values)
        elif attr in _TAG_VALUE_ATTRS:
            tag_names.extend(values)
        elif attr in _TAG_ID_ATTRS:
            names = [(tag_values or {}).get(v) for v in values]
            if tag_values is None or not all(names):
                scope_unresolved = True
            else:
                tag_names.extend(n for n in names if n)
        else:
            scope_unresolved = True
    min_subtotal: int | None = None
    for rule in raw.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        attr = str(rule.get("attribute") or "")
        if attr in _SUBTOTAL_ATTRS and str(rule.get("operator") or "") in ("gte", "gt"):
            values = [_to_int(v) for v in _values(rule)]
            values = [v for v in values if v is not None]
            if values:
                min_subtotal = max(values)
            else:
                scope_unresolved = True
    campaign = raw.get("campaign") if isinstance(raw.get("campaign"), dict) else {}
    budget = campaign.get("budget") if isinstance(campaign.get("budget"), dict) else {}
    promo_type = str(raw.get("type") or "standard")
    discount_type = (
        "buyget" if promo_type == "buyget" else str(method.get("type") or "percentage")
    )
    return PromotionDTO(
        id=str(raw.get("id") or code),
        code=code.strip().upper(),
        discount_type=discount_type,
        value=_to_int(method.get("value")) or 0,
        currency_code=(method.get("currency_code") or None),
        target_type=str(method.get("target_type") or "items"),
        allocation=str(method.get("allocation") or "across"),
        max_quantity=_to_int(method.get("max_quantity")),
        product_ids=tuple(product_ids),
        variant_ids=tuple(variant_ids),
        collection_ids=tuple(collection_ids),
        min_subtotal_cop=min_subtotal,
        is_automatic=bool(raw.get("is_automatic")),
        status=str(raw.get("status") or "active"),
        starts_at_ms=_iso_to_ms(campaign.get("starts_at")),
        ends_at_ms=_iso_to_ms(campaign.get("ends_at")),
        budget_type=(budget.get("type") or None),
        budget_limit=_to_int(budget.get("limit")),
        budget_used=_to_int(budget.get("used")),
        description=(campaign.get("name") or None),
        scope_unresolved=scope_unresolved,
        tag_values=tuple(tag_names),
    )


class MedusaPromotionsPort:
    """Lee las promociones de Medusa con un cache corto por proceso."""

    def __init__(self, client: Any, *, ttl_s: float = 60.0) -> None:
        self._client = client
        self._ttl_s = ttl_s
        self._cache: list[PromotionDTO] | None = None
        self._cached_at = 0.0
        #: Sube con cada `invalidate()`: una lectura que salió ANTES (y vuelve
        #: después) no guarda lo viejo en el cache.
        self._generation = 0

    async def _all(self) -> list[PromotionDTO]:
        now = time.monotonic()
        if self._cache is not None and now - self._cached_at < self._ttl_s:
            return self._cache
        generation = self._generation
        try:
            raw_list = await self._client.list_promotions()
        except Exception as exc:  # noqa: BLE001 — el vendor no cruza el port
            if self._cache is not None:
                log.warning("promotions: Medusa falló, uso cache: %s", exc)
                return self._cache
            raise PromotionsUnavailableError(str(exc)) from exc
        tag_values = await self._tag_values(raw_list)
        promotions = [
            p
            for p in (promotion_from_medusa(r, tag_values=tag_values) for r in raw_list)
            if p
        ]
        if generation == self._generation:
            self._cache = promotions
            self._cached_at = now
        return promotions

    async def _tag_values(self, raw_list: list[dict[str, Any]]) -> dict[str, str] | None:
        """Nombre de cada etiqueta que usan las reglas. None si Medusa no
        responde: esas promos quedan con alcance desconocido, el resto no."""
        ids = sorted(set().union(*(tag_ids_in(r) for r in raw_list)) if raw_list else set())
        if not ids:
            return {}
        try:
            tags = await self._client.list_product_tags(ids)
        except Exception as exc:  # noqa: BLE001 — el vendor no cruza el port
            log.warning("promotions: no pude leer las etiquetas de las reglas: %s", exc)
            return None
        return {
            str(t["id"]): str(t["value"])
            for t in tags
            if isinstance(t, dict) and t.get("id") and t.get("value")
        }

    def invalidate(self) -> None:
        """Olvida el cache: la central acaba de escribir en Medusa y este
        proceso tiene que ver el cambio ya (los demás, en ≤ TTL). Una lectura
        en vuelo de antes no lo vuelve a llenar (generación nueva)."""
        self._generation += 1
        self._cache = None

    async def list_active(self) -> list[PromotionDTO]:
        return [p for p in await self._all() if p.status == "active" and not p.is_automatic]

    async def get_by_code(self, code: str) -> PromotionDTO | None:
        wanted = code.strip().upper()
        return next((p for p in await self._all() if p.code == wanted), None)


__all__ = ["MedusaPromotionsPort", "promotion_from_medusa", "tag_ids_in"]
