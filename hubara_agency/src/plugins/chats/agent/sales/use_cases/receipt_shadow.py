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
from src.plugins.chats.agent.sales.use_cases.receipt_check import (
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
_NO_ORDER = "sin_pedido"


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _reading(receipt: Any) -> ReceiptReading | None:
    """Lo que la visión leyó (`VisionResult.receipt`), o None si no trajo nada."""
    if receipt is None:
        return None
    return ReceiptReading(
        amount=_text(getattr(receipt, "amount", None)),
        date=_text(getattr(receipt, "date", None)),
        account=_text(getattr(receipt, "account", None)),
        reference=_text(getattr(receipt, "reference", None)),
    )


def _episode_of_the_receipt(metadata: dict[str, Any]) -> dict[str, Any]:
    """El último episodio: el abierto, o el que cerró con el pedido (al
    registrar el pedido el episodio se cierra y el comprobante llega después).
    Un episodio abierto sin pedido NO hereda el pedido de una compra anterior."""
    episodes = metadata.get("episodes")
    last = episodes[-1] if isinstance(episodes, list) and episodes else None
    return last if isinstance(last, dict) else {}


async def _order_total(order_facts: Any, order_id: str | None, session_id: str) -> int | None:
    if order_facts is None or not order_id:
        return None
    try:
        facts = await order_facts(order_id)
    except Exception as exc:  # noqa: BLE001 — sin datos del pedido no hay con qué comparar
        logger.warning("receipt_shadow.order_facts_failed", session_id=session_id, error=type(exc).__name__)
        return None
    total = getattr(facts, "total_cop", None)
    return total if isinstance(total, int) and not isinstance(total, bool) else None


def _order_key(order_id: str | None) -> str:
    return hashlib.sha256((order_id or _NO_ORDER).encode()).hexdigest()[:16]


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
    episode = _episode_of_the_receipt(metadata)
    order_id = str(episode["order_id"]) if episode.get("order_id") else None
    reading = _reading(receipt)
    order_key = _order_key(order_id)

    verdict = check_receipt(
        reading,
        order_total_cop=await _order_total(order_facts, order_id, session_id),
        our_accounts=get_payment_accounts(),
        used_references=references_of_other_orders(vault_dir, order=order_key),
        today=(datetime.fromtimestamp(now_ms / 1000, tz=UTC) + _BOGOTA_OFFSET).date(),
    )
    row = {
        "at_ms": now_ms,
        "episode_id": episode.get("episode_id"),
        "order_id": order_id,
        "verdict": verdict.verdict,
        "reason": verdict.reason,
        "source": source,
    }
    append_check(vault_dir, session_id, row)
    if reading is not None and reading.reference:
        remember_reference(vault_dir, reference=reference_fingerprint(reading.reference), order=order_key)
    return row
