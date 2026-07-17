"""Plan PURO de sesiones CTWA sintéticas (histórico de prueba, 2026-07-09).

El operador quiere ejercitar el embudo/análisis con conversaciones NO cerradas
atribuidas a una campaña real. Este módulo arma los specs (el IO — escribir el
vault — vive en `scripts/seed_test_ctwa_sessions.py`):

- Estados cubiertos: nuevo, activo, calificado, cotizado, perdido, no_reply.
  `ganado` NO se inventa acá — las ventas de prueba viven en Medusa (backfill).
- Toda sesión nace con `seeded_test: true` (limpiable por marker) y teléfono
  obviamente falso (wa_5730000009XX) — no colisiona con clientes reales y si
  algo intentara escribirles, WhatsApp rechaza el número.
- Los estados se garantizan contra el clasificador real (test_synthetic_seed).

Spec shape: {session_key, metadata, history_msgs, last_inbound_ms}
  - history_msgs > 0 → el script escribe un history JSONL con esas líneas
    (y ajusta el mtime a last_inbound_ms — así deriva no_reply/activo).
"""
from __future__ import annotations

from typing import Any

_DAY_MS = 24 * 60 * 60 * 1000
_HOUR_MS = 60 * 60 * 1000


def _episode(
    started_at_ms: int,
    *,
    closed_at_ms: int | None = None,
    closing_tag: str | None = None,
) -> dict[str, Any]:
    return {
        "episode_id": f"ep-seed-{started_at_ms}",
        "started_at_ms": started_at_ms,
        "started_inbound_message_id": f"wamid.seed.{started_at_ms}",
        "closed_at_ms": closed_at_ms,
        "closing_tag": closing_tag,
        "closing_motivo": "seed" if closing_tag else None,
        "order_id": None,
        "order_total_cop": None,
        "referral_snapshot": None,
        "msgs_count_at_start": 0,
        "msgs_count_at_close": None,
    }


def build_seed_sessions(ad_id: str, *, now_ms: int) -> list[dict[str, Any]]:
    """Specs de sesiones sintéticas atribuidas al `ad_id` (campaña real).

    Determinista respecto a `now_ms` (sin reloj propio — DI). Cada spec declara
    su historia (msgs + last inbound) para que el clasificador derive el estado
    prometido sin depender de heurísticas frágiles.
    """

    def _base(idx: int, started_ms: int, seen_ms: int) -> dict[str, Any]:
        return {
            "seeded_test": True,
            "origin": {
                "channel": "ad",
                "source_id": ad_id,
                "headline": "[seed] Chatea con nosotros",
                "first_seen_ms": started_ms,
            },
            "last_touch": {"channel": "ad", "source_id": ad_id, "seen_at_ms": seen_ms},
            "episodes": [],
        }

    specs: list[dict[str, Any]] = []

    def add(
        idx: int,
        *,
        days_ago: float,
        tag: str | None = None,
        closing_tag: str | None = None,
        closed: bool = False,
        history_msgs: int = 0,
        last_inbound_hours_ago: float | None = None,
    ) -> None:
        started = now_ms - int(days_ago * _DAY_MS)
        last_inbound = (
            now_ms - int(last_inbound_hours_ago * _HOUR_MS)
            if last_inbound_hours_ago is not None
            else None
        )
        meta = _base(idx, started, last_inbound or started)
        if tag:
            meta["tag"] = tag
        meta["episodes"].append(
            _episode(
                started,
                closed_at_ms=(started + 2 * _HOUR_MS) if closed else None,
                closing_tag=closing_tag,
            )
        )
        specs.append(
            {
                "session_key": f"wa_57300000090{idx}",
                "metadata": meta,
                "history_msgs": history_msgs,
                "last_inbound_ms": last_inbound,
            }
        )

    # nuevo ×2 — recién llegan, ≤2 mensajes, sin tag.
    add(0, days_ago=0.5, history_msgs=1, last_inbound_hours_ago=3)
    add(1, days_ago=1, history_msgs=2, last_inbound_hours_ago=8)
    # activo ×2 — conversación en curso (tag RETOMA_VENTA; NO usar HUMANO:
    # contaminaría la bandeja humana de Chats).
    add(2, days_ago=2, tag="RETOMA_VENTA", history_msgs=6, last_inbound_hours_ago=5)
    add(3, days_ago=3, tag="RETOMA_VENTA", history_msgs=9, last_inbound_hours_ago=12)
    # calificado — mostró interés real.
    add(4, days_ago=2.5, tag="INTERESADO", history_msgs=7, last_inbound_hours_ago=6)
    # cotizado — cerró sin datos de pago.
    add(5, days_ago=4, closing_tag="CONFIRMADO_SIN_DATOS", closed=True, history_msgs=10)
    # perdido — rechazo explícito.
    add(6, days_ago=5, closing_tag="RECHAZO", closed=True, history_msgs=8)
    # no_reply — conversó y desapareció hace >24h.
    add(7, days_ago=6, history_msgs=5, last_inbound_hours_ago=50)

    return specs


def plan_segment_spread(
    sessions: list[tuple[str, dict[str, Any]]],
    campaign_ad_ids: frozenset[str],
    target_ad_ids: list[str],
) -> list[dict[str, Any]]:
    """Plan PURO para repartir las sesiones SEEDED de una campaña entre sus
    segmentos (segmentación 2026-07-10).

    Solo entran sesiones con `seeded_test: true` cuyo `origin.source_id`
    pertenece a `campaign_ad_ids` — las conversaciones REALES y las de otras
    campañas quedan intactas (cero contaminación / cero falsificación de
    atribución real). Round-robin determinista por `session_key` sobre
    `target_ad_ids` (un ad representativo por segmento).

    Cada entry: `{session_key, new_source_id, metadata, order_ids}` con la
    metadata YA reescrita (origin + last_touch + referral_snapshot de cada
    episodio que apuntaba a la campaña) sin mutar la original. `order_ids`
    lista las ventas del vault afectadas — el IO las usa para re-estampar
    `meta_ad_id` en Medusa y mantener la coherencia orden↔segmento.
    """
    import copy

    eligible = sorted(
        (
            (key, meta)
            for key, meta in sessions
            if meta.get("seeded_test") is True
            and (meta.get("origin") or {}).get("source_id") in campaign_ad_ids
        ),
        key=lambda kv: kv[0],
    )

    plan: list[dict[str, Any]] = []
    for i, (key, meta) in enumerate(eligible):
        new_ad = target_ad_ids[i % len(target_ad_ids)]
        new_meta = copy.deepcopy(meta)
        new_meta["origin"]["source_id"] = new_ad
        touch = new_meta.get("last_touch")
        if touch and touch.get("source_id") in campaign_ad_ids:
            touch["source_id"] = new_ad
        order_ids: list[str] = []
        for ep in new_meta.get("episodes") or []:
            snap = ep.get("referral_snapshot")
            # Un episodio re-atribuido a OTRA campaña (FU2) queda intacto y su
            # orden NO se re-estampa — falsificaría la atribución ajena. Sin
            # snapshot, el episodio hereda el origin de la sesión (que SÍ es de
            # la campaña — filtro de elegibilidad de arriba).
            if snap is not None and snap.get("source_id") not in campaign_ad_ids:
                continue
            if snap is not None:
                snap["source_id"] = new_ad
            if ep.get("order_id"):
                order_ids.append(ep["order_id"])
        plan.append(
            {
                "session_key": key,
                "new_source_id": new_ad,
                "metadata": new_meta,
                "order_ids": order_ids,
            }
        )
    return plan


def build_won_sessions(
    orders: list[dict[str, Any]], ad_id: str
) -> list[dict[str, Any]]:
    """Espeja compras de Medusa como conversaciones GANADAS del vault.

    Pedido 2026-07-09: el tablero deriva `ganado`/`revenue`/`avg_ticket` de los
    episodios del vault — sin estas sesiones, las ventas de Medusa no suman.
    Cada spec lleva el `order_id` REAL y el `total_cop` real de la orden (el
    revenue del tablero coincide con Medusa peso por peso, y el join
    orden↔episodio queda coherente). Fechas del `created_at` de la orden —
    el filtro de fecha las respeta.

    Determinista: orden estable por id → mismas session keys en re-runs
    (idempotente: re-escribe, no duplica). Canceladas y sin total se saltean.
    """
    from datetime import datetime

    specs: list[dict[str, Any]] = []
    idx = 0
    for order in sorted(orders, key=lambda o: str(o.get("id"))):
        if order.get("status") == "canceled":
            continue
        total = (order.get("metadata") or {}).get("total_cop")
        created = order.get("created_at")
        if not total or not created:
            continue
        started_ms = int(
            datetime.fromisoformat(str(created).replace("Z", "+00:00")).timestamp() * 1000
        )
        episode = _episode(
            started_ms,
            closed_at_ms=started_ms + 2 * _HOUR_MS,
            closing_tag="COMPRA_EXITOSA",
        )
        episode["order_id"] = str(order["id"])
        episode["order_total_cop"] = total
        specs.append(
            {
                "session_key": f"wa_5730000009{10 + idx}",
                "metadata": {
                    "seeded_test": True,
                    "tag": "COMPRA_EXITOSA",
                    "origin": {
                        "channel": "ad",
                        "source_id": ad_id,
                        "headline": "[seed] Chatea con nosotros",
                        "first_seen_ms": started_ms,
                    },
                    "last_touch": {
                        "channel": "ad",
                        "source_id": ad_id,
                        "seen_at_ms": started_ms,
                    },
                    "episodes": [episode],
                },
                "history_msgs": 0,
                "last_inbound_ms": started_ms,
            }
        )
        idx += 1
    return specs
