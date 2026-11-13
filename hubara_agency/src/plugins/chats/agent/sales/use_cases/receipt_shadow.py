"""La sombra del comprobante de pago: anotar el veredicto y nada más.

Tarjeta 33 del ciclo. Cuando llega un comprobante, el cliente recibe el mismo
mensaje y la conversación pasa al equipo igual que siempre. Aparte, acá se
anota qué habría dicho Colossus (`receipt_check.check_receipt`) para medirlo
contra lo que el equipo haga con el pedido. Quien llama lo hace DESPUÉS del
relevo y dentro de un ``try``: una falla acá nunca frena nada.

El total sale de OrderFacts (gotcha 13), nunca de la copia del episodio. La
fila guarda solo códigos; del comprobante no se escribe nada.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from src.plugins.chats.agent.sales.config.payments import get_payment_accounts
from src.plugins.chats.agent.sales.use_cases.after_purchase import CLOSED_WITH_ORDER, POSTSALE_MAX_DAYS
from src.plugins.chats.agent.sales.use_cases.receipt_check import (
    VERDICT_NO_ORDER,
    ReceiptReading,
    check_receipt,
    reference_fingerprint,
)
from src.plugins.chats.shared.receipt_checks import (
    append_check,
    references_of_other_orders,
    remember_reference,
)

logger = structlog.get_logger(__name__)

SOURCE_IMAGE = "image"
SOURCE_PDF = "pdf"

#: Colombia no cambia de hora: la fecha del pago se compara con el día de Bogotá.
_BOGOTA_OFFSET = timedelta(hours=-5)
_DAY_MS = 86_400_000
#: Un pedido que ya está pagado, entregado o cancelado no espera un comprobante.
_ORDER_ENDED = frozenset({"delivered", "cancelled"})
_PAID = "paid"
#: `sin_pedido` porque había pedido pero no se pudieron leer sus datos (Medusa
#: caído o lento): se distingue de «todavía no hay pedido».
REASON_ORDER_UNREAD = "pedido_no_leido"
#: Una referencia más corta que esto se repite entre clientes por casualidad.
_MIN_REFERENCE_CHARS = 6


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _reference(value: Any) -> str | None:
    text = _text(value)
    return text if text and sum(c.isalnum() for c in text) >= _MIN_REFERENCE_CHARS else None


def _reading(receipt: Any) -> ReceiptReading | None:
    """Lo que la visión leyó (`VisionResult.receipt`), o None si no trajo nada."""
    if receipt is None:
        return None
    return ReceiptReading(
        amount=_text(getattr(receipt, "amount", None)),
        date=_text(getattr(receipt, "date", None)),
        account=_text(getattr(receipt, "account", None)),
        reference=_reference(getattr(receipt, "reference", None)),
    )


def _episodes(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    episodes = metadata.get("episodes")
    return [e for e in episodes if isinstance(e, dict)] if isinstance(episodes, list) else []


def _episode_of_the_order(metadata: dict[str, Any], now_ms: int) -> dict[str, Any] | None:
    """El episodio del pedido que este comprobante vendría a pagar, o None.

    Al registrar el pedido el episodio se cierra, y el comprobante llega
    después: con el bot atendiendo abre un episodio NUEVO (sin pedido), y con
    el chat en manos del equipo los episodios no rotan. Por eso se mira el
    último episodio y, si está abierto y sin pedido, el anterior. Solo cuenta
    un cierre con pedido de los últimos ``POSTSALE_MAX_DAYS`` días: una compra
    de hace meses no es la de este comprobante.
    """
    episodes = _episodes(metadata)
    if not episodes:
        return None
    last = episodes[-1]
    if not last.get("order_id") and last.get("closed_at_ms") is None and len(episodes) > 1:
        last = episodes[-2]
    if not last.get("order_id") or last.get("closing_tag") not in CLOSED_WITH_ORDER:
        return None
    closed = last.get("closed_at_ms")
    if not isinstance(closed, (int, float)) or now_ms - closed > POSTSALE_MAX_DAYS * _DAY_MS:
        return None
    return last


async def _order_facts_of(order_facts: Any, order_id: str, session_id: str) -> Any:
    if order_facts is None:
        return None
    try:
        return await order_facts(order_id)
    except Exception as exc:  # noqa: BLE001 — sin datos del pedido no hay con qué comparar
        logger.warning("receipt_shadow.order_facts_failed", session_id=session_id, error=type(exc).__name__)
        return None


def _total_awaiting_payment(facts: Any) -> int | None:
    """El total del pedido si todavía espera el pago; si ya está pagado,
    entregado o cancelado, este comprobante no es el suyo."""
    if getattr(facts, "pay_status", None) == _PAID or getattr(facts, "stage", None) in _ORDER_ENDED:
        return None
    total = getattr(facts, "total_cop", None)
    return total if isinstance(total, int) and not isinstance(total, bool) else None


def _order_key(order_id: str) -> str:
    return hashlib.sha256(order_id.encode()).hexdigest()[:16]


async def record_receipt_check(
    *,
    vault_dir: Path,
    session_id: str,
    metadata: dict[str, Any],
    receipt: Any,
    source: str,
    order_facts: Any,
    now_ms: int,
) -> dict[str, Any]:
    """Anota el veredicto del comprobante y devuelve la fila escrita."""
    episode = _episode_of_the_order(metadata, now_ms)
    order_id = str(episode["order_id"]) if episode else None
    facts = await _order_facts_of(order_facts, order_id, session_id) if order_id else None
    total = _total_awaiting_payment(facts) if facts is not None else None
    if order_id and facts is not None and total is None:
        episode, order_id = None, None  # el pedido ya no espera un comprobante
    reading = _reading(receipt)
    order_key = _order_key(order_id) if order_id else None

    verdict = check_receipt(
        reading,
        order_total_cop=total,
        our_accounts=get_payment_accounts(),
        # Sin pedido no hay «otro pedido»: no se compara ni se recuerda.
        used_references=references_of_other_orders(vault_dir, order=order_key) if order_key else frozenset(),
        today=(datetime.fromtimestamp(now_ms / 1000, tz=UTC) + _BOGOTA_OFFSET).date(),
    )
    reason = verdict.reason
    if verdict.verdict == VERDICT_NO_ORDER and order_id and facts is None:
        reason = REASON_ORDER_UNREAD
    current = (_episodes(metadata) or [{}])[-1]
    row = {
        "at_ms": now_ms,
        "episode_id": (episode or current).get("episode_id"),
        "order_id": order_id,
        "verdict": verdict.verdict,
        "reason": reason,
        "evidence": verdict.evidence,
        "source": source,
    }
    append_check(vault_dir, session_id, row)
    if order_key and reading is not None and reading.reference:
        remember_reference(vault_dir, reference=reference_fingerprint(reading.reference), order=order_key)
    return row
