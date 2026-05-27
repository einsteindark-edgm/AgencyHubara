"""Cost computation puro para outbound messages de WhatsApp Cloud API.

Meta NO devuelve el costo monetario en el webhook `message_status`. Lo que
devuelve es el objeto `pricing` con `pricing_type` (regular / free_customer_service /
free_entry_point) y `category` (marketing / utility / authentication / service).

Este módulo:
  1. Define los DTOs frozen (R-JSON) del pricing snapshot, rate card, log
     entry y episode summary.
  2. Carga rate cards versionados desde YAML (`rate_cards/<version>.yaml`).
  3. Cruza `pricing` × `rate_card` → `cost_cents_usd` (int).
  4. Mantiene el agregado `EpisodeCostSummary` precomputado.

Mantenido 100% puro — sin Temporal, sin I/O al filesystem fuera del loader.
El caller (use case `IngestDeliveryStatus`) toma el RateCard del composition
root y pasa los DTOs por argumento.

Ver HU-WA24H-001 §3.4 para el contrato completo.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml


log = logging.getLogger(__name__)


# =============================================================================
# DTOs (R-JSON, frozen)
# =============================================================================


@dataclass(frozen=True)
class PricingSnapshot:
    """Snapshot del objeto `pricing` que Meta envía en `message_status`.

    pricing_type:
      * "regular"               — cobra según category contra el rate card
      * "free_customer_service" — dentro de ventana 24h, gratis
      * "free_entry_point"      — dentro de 72h CTWA, gratis
    """

    billable: bool
    pricing_type: str
    category: str  # "marketing" | "utility" | "authentication" | "service"


@dataclass(frozen=True)
class RateCardEntry:
    """Un entry del rate card por category. `cents_per_message=None` indica
    que la category NO tiene precio publicado en este mercado (ej.
    authentication_international en Colombia al Q2 2026)."""

    cents_per_message: int | None


@dataclass(frozen=True)
class RateCard:
    """Rate card local versionado, snapshotead per-mensaje en el log.

    `version` es el ID estable (ej. "co_2026q2_v1") que se persiste en cada
    OutboundLogEntry. Si Meta sube precios, se crea un archivo nuevo con
    versión incrementada — el viejo queda inmutable.
    """

    version: str
    effective_from_ms: int
    country: str
    currency: str
    rates: dict[str, RateCardEntry]


@dataclass(frozen=True)
class OutboundLogEntry:
    """Entry de `metadata.episodes[*].outbound_messages[]`.

    `cost_cents_usd` y `rate_card_version` son None mientras el webhook
    `message_status` con pricing no ha llegado. Cuando llega, se reemplaza
    el log entry vía `dataclasses.replace`.
    """

    sent_at_ms: int
    wa_message_id: str
    kind: str  # "text" | "template" | "image" | "interactive_buttons" | ...
    template_name: str | None
    pricing: PricingSnapshot | None
    cost_cents_usd: int | None
    rate_card_version: str | None


@dataclass(frozen=True)
class CategoryCostBreakdown:
    """Sub-agregado del cost_summary, por category o por pricing_type."""

    count: int
    cents_usd: int


@dataclass(frozen=True)
class EpisodeCostSummary:
    """Agregado precomputado de costos del episodio.

    Recomputable desde `episodes[*].outbound_messages[]` pero materializado
    aquí para que el dashboard pueda hacer queries O(1) sin parsear el log.

    Invariantes:
      * messages_count == messages_billable_count + messages_free_count
                          + messages_pending_count
      * total_cents_usd == sum(by_category[*].cents_usd)
                        == sum(by_pricing_type[*].cents_usd)
    """

    total_cents_usd: int = 0
    messages_count: int = 0
    messages_billable_count: int = 0
    messages_free_count: int = 0
    messages_pending_count: int = 0
    by_category: dict[str, CategoryCostBreakdown] = field(default_factory=dict)
    by_pricing_type: dict[str, CategoryCostBreakdown] = field(default_factory=dict)


# =============================================================================
# Cost computation
# =============================================================================


#: Pricing types donde el mensaje es gratis aunque sea billable=true.
FREE_PRICING_TYPES: frozenset[str] = frozenset(
    {"free_customer_service", "free_entry_point"}
)


def compute_message_cost_cents(
    pricing: PricingSnapshot,
    rate_card: RateCard,
) -> int:
    """Devuelve costo en cents USD (entero) para UN mensaje outbound.

    Reglas (en orden de precedencia):
      1. ``pricing.billable == False`` → 0
      2. ``pricing.pricing_type in FREE_PRICING_TYPES`` → 0
         (gratis dentro de ventana 24h o 72h CTWA)
      3. ``pricing.pricing_type == "regular"`` →
         ``rate_card.rates[pricing.category].cents_per_message``
      4. Si la category no está en el rate card o ``cents_per_message=None``
         (ej. authentication_international en Colombia) → 0 + warning log.
    """
    if not pricing.billable:
        return 0
    if pricing.pricing_type in FREE_PRICING_TYPES:
        return 0

    entry = rate_card.rates.get(pricing.category)
    if entry is None:
        log.warning(
            "rate_card_missing_category",
            extra={
                "rate_card_version": rate_card.version,
                "category": pricing.category,
            },
        )
        return 0
    if entry.cents_per_message is None:
        log.warning(
            "rate_card_category_unpriced",
            extra={
                "rate_card_version": rate_card.version,
                "country": rate_card.country,
                "category": pricing.category,
            },
        )
        return 0
    return entry.cents_per_message


# =============================================================================
# EpisodeCostSummary aggregation
# =============================================================================


def empty_episode_cost_summary() -> EpisodeCostSummary:
    """Construye un summary vacío. Útil para inicializar el campo en metadata
    cuando se crea un episodio nuevo."""
    return EpisodeCostSummary()


def add_outbound_to_summary(
    summary: EpisodeCostSummary,
    log_entry: OutboundLogEntry,
) -> EpisodeCostSummary:
    """Acumula un outbound NUEVO al summary. Retorna un nuevo summary (frozen).

    Distingue 3 estados según `log_entry.cost_cents_usd`:
      * ``None``  → messages_pending_count += 1 (esperando webhook pricing)
      * ``0``     → messages_free_count += 1 (gratis dentro window/CTWA)
      * ``> 0``   → messages_billable_count += 1, total y breakdowns += cost

    Si el log entry no tiene `pricing` (cost pending), NO se acumula a
    `by_category` ni `by_pricing_type` (no sabemos qué bucket todavía).

    NOTE: este helper asume que el log_entry está siendo agregado por
    primera vez. Para resolver un pending (cuando llega el webhook con
    pricing), usar `materialize_pending_in_summary`.
    """
    total = summary.total_cents_usd
    messages_count = summary.messages_count + 1
    billable_count = summary.messages_billable_count
    free_count = summary.messages_free_count
    pending_count = summary.messages_pending_count
    by_category = dict(summary.by_category)
    by_pricing_type = dict(summary.by_pricing_type)

    if log_entry.cost_cents_usd is None:
        pending_count += 1
    elif log_entry.cost_cents_usd == 0:
        free_count += 1
        if log_entry.pricing is not None:
            _bump(by_category, log_entry.pricing.category, 0)
            _bump(by_pricing_type, log_entry.pricing.pricing_type, 0)
    else:
        billable_count += 1
        total += log_entry.cost_cents_usd
        if log_entry.pricing is not None:
            _bump(by_category, log_entry.pricing.category, log_entry.cost_cents_usd)
            _bump(
                by_pricing_type,
                log_entry.pricing.pricing_type,
                log_entry.cost_cents_usd,
            )

    return EpisodeCostSummary(
        total_cents_usd=total,
        messages_count=messages_count,
        messages_billable_count=billable_count,
        messages_free_count=free_count,
        messages_pending_count=pending_count,
        by_category=by_category,
        by_pricing_type=by_pricing_type,
    )


def materialize_pending_in_summary(
    summary: EpisodeCostSummary,
    log_entry: OutboundLogEntry,
) -> EpisodeCostSummary:
    """Aplica el delta cuando un pending se materializa (llegó el webhook).

    Pasa pending_count -1 → billable/free según el cost resuelto. El total
    y los breakdowns se acumulan con el nuevo cost.

    ASUME que `log_entry.cost_cents_usd` ya NO es None y que `log_entry.pricing`
    está presente. Defensivo: si pending_count ya es 0, no decrementa
    (idempotente — protege contra duplicate webhook delivery).
    """
    if log_entry.cost_cents_usd is None or log_entry.pricing is None:
        # Defensa: caller no debería pasar un log_entry sin resolver. Devolver
        # summary sin cambios para no corromper invariantes.
        return summary

    pending_count = max(0, summary.messages_pending_count - 1)
    billable_count = summary.messages_billable_count
    free_count = summary.messages_free_count
    total = summary.total_cents_usd
    by_category = dict(summary.by_category)
    by_pricing_type = dict(summary.by_pricing_type)

    if log_entry.cost_cents_usd == 0:
        free_count += 1
        _bump(by_category, log_entry.pricing.category, 0)
        _bump(by_pricing_type, log_entry.pricing.pricing_type, 0)
    else:
        billable_count += 1
        total += log_entry.cost_cents_usd
        _bump(by_category, log_entry.pricing.category, log_entry.cost_cents_usd)
        _bump(by_pricing_type, log_entry.pricing.pricing_type, log_entry.cost_cents_usd)

    return replace(
        summary,
        total_cents_usd=total,
        messages_billable_count=billable_count,
        messages_free_count=free_count,
        messages_pending_count=pending_count,
        by_category=by_category,
        by_pricing_type=by_pricing_type,
    )


def _bump(
    breakdown: dict[str, CategoryCostBreakdown], key: str, cents_delta: int
) -> None:
    """Incrementa el count y cents_usd del breakdown[key]. Mutates."""
    existing = breakdown.get(key)
    if existing is None:
        breakdown[key] = CategoryCostBreakdown(count=1, cents_usd=cents_delta)
    else:
        breakdown[key] = CategoryCostBreakdown(
            count=existing.count + 1,
            cents_usd=existing.cents_usd + cents_delta,
        )


# =============================================================================
# Rate card loading (YAML)
# =============================================================================


#: Directorio donde viven los rate cards versionados.
RATE_CARDS_DIR: Path = Path(__file__).parent / "rate_cards"


def load_rate_card_from_yaml(version: str) -> RateCard:
    """Carga un rate card por versión.

    Levanta:
      * FileNotFoundError si no existe `rate_cards/<version>.yaml`.
      * ValueError si el YAML está malformed o le faltan campos requeridos.
    """
    path = RATE_CARDS_DIR / f"{version}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Rate card YAML not found: {path}. "
            f"Existing: {[p.stem for p in RATE_CARDS_DIR.glob('*.yaml')]}"
        )

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _build_rate_card_from_dict(raw)


def _build_rate_card_from_dict(raw: dict[str, Any]) -> RateCard:
    """Constructor defensivo desde dict — valida tipos y presencia."""
    required = ("version", "effective_from_ms", "country", "currency", "rates")
    missing = [k for k in required if k not in raw]
    if missing:
        raise ValueError(f"Rate card YAML missing required fields: {missing}")

    rates_raw = raw["rates"]
    if not isinstance(rates_raw, dict):
        raise ValueError(f"Rate card 'rates' must be a dict, got {type(rates_raw)}")

    rates: dict[str, RateCardEntry] = {}
    for category, entry_raw in rates_raw.items():
        if not isinstance(entry_raw, dict) or "cents_per_message" not in entry_raw:
            raise ValueError(
                f"Rate card entry '{category}' must be {{cents_per_message: int|null}}"
            )
        cents = entry_raw["cents_per_message"]
        if cents is not None and not isinstance(cents, int):
            raise ValueError(
                f"Rate card '{category}.cents_per_message' must be int|null, "
                f"got {type(cents).__name__}"
            )
        rates[category] = RateCardEntry(cents_per_message=cents)

    return RateCard(
        version=raw["version"],
        effective_from_ms=raw["effective_from_ms"],
        country=raw["country"],
        currency=raw["currency"],
        rates=rates,
    )
