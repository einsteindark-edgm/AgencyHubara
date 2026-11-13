"""El veredicto de un comprobante de pago contra el pedido (sombra).

Tarjeta 33 del ciclo: casi la mitad de los relevos al equipo son para verificar
un pago. Antes de que el bot diga nada distinto, se mide si sabe leer un
comprobante: lo compara con el pedido que ya registró y anota qué habría dicho.
El equipo sigue entrando a la cuenta a ver si la plata llegó; una imagen nunca
prueba un pago (se falsifica), solo permite descartar lo que a simple vista no
cuadra.

Funciones puras, sin I/O. Quien las llama trae lo leído por la visión, el total
del pedido (``OrderFacts``, nunca la copia del episodio), las cuentas del
negocio y las huellas de las referencias ya vistas.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date

VERDICT_MATCH = "coincide"
VERDICT_MISMATCH = "no_coincide"
VERDICT_UNREADABLE = "ilegible"
VERDICT_NO_ORDER = "sin_pedido"
VERDICTS = frozenset({VERDICT_MATCH, VERDICT_MISMATCH, VERDICT_UNREADABLE, VERDICT_NO_ORDER})

REASON_AMOUNT = "monto"
REASON_OLD_DATE = "fecha_vieja"
REASON_ACCOUNT = "cuenta_destino"
REASON_REPEATED = "repetido"
REASONS = frozenset({REASON_AMOUNT, REASON_OLD_DATE, REASON_ACCOUNT, REASON_REPEATED})

#: Un pago de hace más días que esto no es el de este pedido.
MAX_RECEIPT_AGE_DAYS = 3
#: Con menos dígitos que estos no se sabe de qué cuenta habla el comprobante.
_MIN_ACCOUNT_DIGITS = 4

_AMOUNT_RE = re.compile(r"\d[\d.,]*")
_DECIMALS_RE = re.compile(r"[.,]\d{2}$")
_NOT_DIGIT_RE = re.compile(r"\D+")
_NOT_ALNUM_RE = re.compile(r"[^A-Z0-9]+")


@dataclass(frozen=True)
class ReceiptReading:
    """Lo que la visión leyó del comprobante, tal como estaba escrito.
    ``date`` en ``AAAA-MM-DD``; ``account``, la cuenta de destino (o sus
    últimos dígitos)."""

    amount: str | None = None
    date: str | None = None
    account: str | None = None
    reference: str | None = None


@dataclass(frozen=True)
class ReceiptVerdict:
    verdict: str
    reason: str | None = None


def reference_fingerprint(reference: str) -> str:
    """Huella de la referencia del pago, para reconocer un comprobante repetido
    sin guardar el número."""
    normalized = _NOT_ALNUM_RE.sub("", reference.upper())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _amount_cop(text: str | None) -> int | None:
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return None
    digits = _NOT_DIGIT_RE.sub("", _DECIMALS_RE.sub("", m.group(0)))
    return int(digits) if digits else None


def _is_our_account(account: str | None, our_accounts: Iterable[str]) -> bool | None:
    """``None`` = el comprobante no deja saber a qué cuenta fue."""
    digits = _NOT_DIGIT_RE.sub("", account or "")
    if len(digits) < _MIN_ACCOUNT_DIGITS:
        return None
    ours = [_NOT_DIGIT_RE.sub("", a) for a in our_accounts]
    return any(o and o.endswith(digits) for o in ours)


def _is_old(day: str | None, today: date) -> bool:
    try:
        paid = date.fromisoformat((day or "").strip())
    except ValueError:
        return False  # una fecha que no se lee no condena el comprobante
    return (today - paid).days > MAX_RECEIPT_AGE_DAYS


def check_receipt(
    reading: ReceiptReading | None,
    *,
    order_total_cop: int | None,
    our_accounts: Iterable[str],
    used_references: Collection[str],
    today: date,
) -> ReceiptVerdict:
    """Qué diría Colossus de este comprobante. ``used_references``: huellas
    (`reference_fingerprint`) de los comprobantes ya vistos en OTROS pedidos."""
    amount = _amount_cop(reading.amount) if reading else None
    if reading is None or amount is None:
        return ReceiptVerdict(VERDICT_UNREADABLE)
    if reading.reference and reference_fingerprint(reading.reference) in used_references:
        return ReceiptVerdict(VERDICT_MISMATCH, REASON_REPEATED)
    if order_total_cop is None:
        return ReceiptVerdict(VERDICT_NO_ORDER)
    if amount != order_total_cop:
        return ReceiptVerdict(VERDICT_MISMATCH, REASON_AMOUNT)
    if _is_our_account(reading.account, our_accounts) is False:
        return ReceiptVerdict(VERDICT_MISMATCH, REASON_ACCOUNT)
    if _is_old(reading.date, today):
        return ReceiptVerdict(VERDICT_MISMATCH, REASON_OLD_DATE)
    return ReceiptVerdict(VERDICT_MATCH)
