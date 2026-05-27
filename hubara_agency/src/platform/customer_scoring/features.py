"""Compute RFM features puros del cliente desde `metadata.json` + Medusa.

DEHA:
  * Función pura. NO I/O. El caller pasa el `metadata` dict ya leído del
    vault y el `medusa_order_totals_cop` map ya fetcheado.
  * Determinística — `now_ms` por DI para tests.
  * Sin side effects sobre `metadata` (no muta).

Lo que computa:
  * RFM clásico (Recency / Frequency / Monetary).
  * Lost ratio + fricción (msgs_avg).
  * Antigüedad (first_seen).
  * Last purchase (para el KV directo del UI).

Fallback legacy (sesiones sin `episodes[]`): usa `metadata.tag` +
`metadata.registered_order` como pseudo-episodio cerrado (espeja la lógica
del `classify_conversation_state.py` que ya tiene fallback para legacy).
"""
from __future__ import annotations

from typing import Any

from src.platform.customer_scoring.port import CustomerFeatures


# Tags que cuentan como cierre formal "ganado".
# Nota: aunque el LLM NO marque COMPRA_EXITOSA directamente (lo hace el
# humano desde el dashboard de orders tras verificar el pago), este set
# captura el estado final. Si una venta queda en CONFIRMADO_PAGO_PENDIENTE
# es porque el humano todavía no verificó el pago — ese caso entra como
# _PARTIAL_TAGS abajo (no infla monetary hasta confirmación).
_WON_TAGS: frozenset[str] = frozenset({"COMPRA_EXITOSA"})
# Tags que cuentan como rechazo formal (cliente NO compró).
_LOST_TAGS: frozenset[str] = frozenset({"RECHAZO"})
# Tags parciales (cliente confirmó pero queda paso operativo pendiente):
#   - CONFIRMADO_SIN_DATOS: confirmó la compra pero no completó shipping.
#   - CONFIRMADO_PAGO_PENDIENTE: orden registrada en Medusa, falta
#     verificación humana del pago (operativo hasta que haya pasarela).
# Estas NO inflan `monetary_cop` hasta que el humano confirme la venta
# (cambia la tag a COMPRA_EXITOSA desde el dashboard).
_PARTIAL_TAGS: frozenset[str] = frozenset(
    {"CONFIRMADO_SIN_DATOS", "CONFIRMADO_PAGO_PENDIENTE"}
)
# Tag sintético cuando el lifecycle cerró por timeout (cliente abandonó).
_TIMEOUT_TAG: str = "TIMEOUT"

_MS_PER_DAY: int = 24 * 60 * 60 * 1000


def compute_customer_features(
    metadata: dict[str, Any],
    *,
    now_ms: int,
    medusa_order_totals_cop: dict[str, int] | None = None,
    medusa_order_created_at_ms: dict[str, int] | None = None,
) -> CustomerFeatures:
    """Compute `CustomerFeatures` a partir del metadata de la sesión.

    Args:
      metadata: el dict de `<vault>/<session>/metadata.json` (puede estar
        vacío o legacy sin `episodes[]`).
      now_ms: epoch ms actual (DI).
      medusa_order_totals_cop: map `order_id → total_cop` (major units).
        El caller (composition/endpoint) lo fetchea de Medusa para los
        order_ids referenciados en los episodios. Si None, monetary=0.
      medusa_order_created_at_ms: idem para recency precisa. Si None,
        fallback a `episode.closed_at_ms`.

    Returns:
      `CustomerFeatures` — siempre devuelve un objeto válido. Para metadata
      vacío, todos los counts son 0 y los timestamps None.
    """
    totals_map = medusa_order_totals_cop or {}
    created_map = medusa_order_created_at_ms or {}

    episodes = _normalize_episodes(metadata)

    episodes_total = len(episodes)
    episodes_won = 0
    episodes_lost = 0
    episodes_partial = 0
    episodes_timeout = 0
    episodes_active = 0
    monetary_cop = 0
    last_purchase_at_ms: int | None = None
    last_purchase_order_id: str | None = None
    msgs_diffs: list[int] = []
    first_started_at_ms: int | None = None

    for ep in episodes:
        # First seen: el started_at_ms más viejo de la lista.
        started = ep.get("started_at_ms")
        if isinstance(started, int) and started > 0:
            if first_started_at_ms is None or started < first_started_at_ms:
                first_started_at_ms = started

        # Active vs cerrado.
        closed_at_ms = ep.get("closed_at_ms")
        if closed_at_ms is None:
            episodes_active += 1
            continue

        # Buckets de cierre.
        closing_tag = ep.get("closing_tag")
        # CONFIRMADO_PAGO_PENDIENTE no cuenta como ganado para el scoring
        # RFM aunque tenga order_id — el pago todavía no fue verificado
        # por el humano. Cuenta como _PARTIAL_TAGS abajo. Si el humano
        # confirma el pago desde el dashboard de orders, ese flujo cambia
        # el closing_tag a COMPRA_EXITOSA y el siguiente compute_features
        # ya lo incluye en monetary.
        is_pending_payment = closing_tag == "CONFIRMADO_PAGO_PENDIENTE"
        if (closing_tag in _WON_TAGS or ep.get("order_id")) and not is_pending_payment:
            # `order_id` truthy también cuenta como ganado aunque el tag no
            # haya sido COMPRA_EXITOSA todavía (el agente puede haber
            # registered_order pero no haberse cerrado el episodio aún).
            episodes_won += 1
            order_id = ep.get("order_id")
            if isinstance(order_id, str) and order_id:
                # Monetary: del map de Medusa si está, sino 0 (legacy /
                # stub orders no contribuyen al LTV computado).
                amount = totals_map.get(order_id, 0)
                if isinstance(amount, int) and amount > 0:
                    monetary_cop += amount
                # Last purchase: usa Medusa created_at si está, sino el
                # episode.closed_at_ms como aproximación.
                purchase_at_ms = (
                    created_map.get(order_id)
                    if order_id in created_map
                    else closed_at_ms
                )
                if isinstance(purchase_at_ms, int):
                    if (
                        last_purchase_at_ms is None
                        or purchase_at_ms > last_purchase_at_ms
                    ):
                        last_purchase_at_ms = purchase_at_ms
                        last_purchase_order_id = order_id
        elif closing_tag in _LOST_TAGS:
            episodes_lost += 1
        elif closing_tag in _PARTIAL_TAGS:
            episodes_partial += 1
        elif closing_tag == _TIMEOUT_TAG:
            episodes_timeout += 1

        # Msgs_avg: diff msgs_count_at_close - at_start (FU3 feature).
        start_count = ep.get("msgs_count_at_start")
        close_count = ep.get("msgs_count_at_close")
        if isinstance(start_count, int) and isinstance(close_count, int):
            diff = close_count - start_count
            if diff >= 0:
                msgs_diffs.append(diff)

    # Recency: días desde last_purchase. None si nunca compró.
    if last_purchase_at_ms is not None:
        age_ms = max(0, now_ms - last_purchase_at_ms)
        recency_days: int | None = age_ms // _MS_PER_DAY
    else:
        recency_days = None

    # Lost ratio sobre cierres formales (ignora active + timeout).
    formal_closes = episodes_won + episodes_lost
    lost_ratio = (episodes_lost / formal_closes) if formal_closes > 0 else 0.0

    msgs_avg = (
        sum(msgs_diffs) / len(msgs_diffs) if msgs_diffs else None
    )

    first_seen_days_ago: int | None = None
    if first_started_at_ms is not None:
        first_seen_days_ago = max(
            0, (now_ms - first_started_at_ms) // _MS_PER_DAY
        )

    return CustomerFeatures(
        episodes_total=episodes_total,
        episodes_won=episodes_won,
        episodes_lost=episodes_lost,
        episodes_partial=episodes_partial,
        episodes_timeout=episodes_timeout,
        episodes_active=episodes_active,
        monetary_cop=monetary_cop,
        recency_days=recency_days,
        frequency_total=episodes_won,  # alias para legibilidad YAML
        lost_ratio=round(lost_ratio, 3),
        msgs_avg_to_close=round(msgs_avg, 1) if msgs_avg is not None else None,
        first_seen_days_ago=first_seen_days_ago,
        last_purchase_at_ms=last_purchase_at_ms,
        last_purchase_order_id=last_purchase_order_id,
    )


def _normalize_episodes(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Devuelve la lista de episodios — usa `metadata.episodes[]` si está
    poblado; sino reconstruye un pseudo-episodio del legacy.

    Espejo del fallback que tiene `classify_conversation_state.classify_state`:
    sesiones pre-feature sin `episodes[]` pero CON `registered_order` válido
    o `tag` de cierre cuentan como un episodio cerrado sintético.
    """
    episodes_raw = metadata.get("episodes")
    if isinstance(episodes_raw, list) and episodes_raw:
        # Path moderno. Filtramos entries que no son dicts (defensivo).
        return [ep for ep in episodes_raw if isinstance(ep, dict)]

    # Legacy fallback. Mirror de classify_state lógica.
    tag = metadata.get("tag")
    registered = metadata.get("registered_order")
    is_closing_tag = tag in (_WON_TAGS | _LOST_TAGS | _PARTIAL_TAGS)
    has_order = (
        isinstance(registered, dict) and registered.get("success") is True
    )

    if not is_closing_tag and not has_order:
        return []  # sesión legacy sin cierre — sin episodios

    # Sintetizamos UN episodio cerrado.
    pseudo: dict[str, Any] = {
        "episode_id": "ep_legacy",
        "started_at_ms": None,
        "closed_at_ms": 1,  # marcador "cerrado" (no usamos el value real)
        "closing_tag": tag if is_closing_tag else "COMPRA_EXITOSA",
        "closing_motivo": metadata.get("motivo"),
        "order_id": registered.get("order_id") if has_order else None,
    }
    return [pseudo]
