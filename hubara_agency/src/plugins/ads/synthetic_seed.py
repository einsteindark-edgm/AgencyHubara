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
