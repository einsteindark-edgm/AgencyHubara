"""El veredicto de un comprobante de pago contra el pedido (sombra)."""
from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ReceiptReading:
    amount: str | None = None
    date: str | None = None
    account: str | None = None
    reference: str | None = None


@dataclass(frozen=True)
class ReceiptVerdict:
    verdict: str
    reason: str | None = None


def reference_fingerprint(reference: str) -> str:
    return reference


def check_receipt(
    reading: ReceiptReading | None,
    *,
    order_total_cop: int | None,
    our_accounts: Iterable[str],
    used_references: Collection[str],
    today: date,
) -> ReceiptVerdict:
    return ReceiptVerdict("pendiente")
