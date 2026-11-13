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

#: Qué tanto respalda el comprobante un «coincide»: con cuenta nuestra y fecha
#: reciente a la vista, o solo por el monto (lo demás no se leía).
EVIDENCE_FULL = "completa"
EVIDENCE_PARTIAL = "parcial"
EVIDENCE_LEVELS = frozenset({EVIDENCE_FULL, EVIDENCE_PARTIAL})

#: Un pago de hace más días que esto no es el de este pedido.
MAX_RECEIPT_AGE_DAYS = 3
#: Un día adelante es diferencia de reloj; más, la fecha no se leyó bien.
_MAX_DAYS_AHEAD = 1
#: Con menos dígitos que estos no se sabe de qué cuenta habla el comprobante.
_MIN_ACCOUNT_DIGITS = 4

#: Un número corrido de este largo es un celular o una cuenta, no un valor.
_MIN_ID_DIGITS = 9
#: Desde acá un número del campo es una cifra de plata y no un «1 pago».
_MIN_FIGURE_COP = 1_000

_NUMBER_RE = re.compile(r"\d[\d.,]*")
_DECIMALS_RE = re.compile(r"[.,]\d{1,2}$")
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
    #: Solo en «coincide»: `EVIDENCE_FULL` o `EVIDENCE_PARTIAL`.
    evidence: str | None = None


def reference_fingerprint(reference: str) -> str:
    """Huella de la referencia del pago, para reconocer un comprobante repetido
    sin guardar el número."""
    normalized = _NOT_ALNUM_RE.sub("", reference.upper())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _amount_cop(text: str | None) -> int | None:
    """El valor en pesos, sin centavos («1 pago por $84.000», «84.000,00»,
    «84,000.00», «84000.0»). Un celular o una cuenta pegados al valor no son el
    valor; con dos cifras distintas (el valor y un saldo, lo pagado y lo que
    falta) no se sabe cuál es: ``None``."""
    values: set[int] = set()
    for raw in _NUMBER_RE.findall(text or ""):
        raw = raw.rstrip(".,")
        digits = _NOT_DIGIT_RE.sub("", _DECIMALS_RE.sub("", raw))
        if digits and not (raw.isdigit() and len(raw) >= _MIN_ID_DIGITS):
            values.add(int(digits))
    if len([v for v in values if v >= _MIN_FIGURE_COP]) > 1:
        return None
    return max(values, default=None)


def _is_our_account(account: str | None, our_accounts: Iterable[str]) -> bool | None:
    """``None`` = no se puede saber: el comprobante no muestra la cuenta, o no
    tenemos ninguna cuenta con números contra la cual comparar."""
    digits = _NOT_DIGIT_RE.sub("", account or "")
    ours = [o for o in (_NOT_DIGIT_RE.sub("", a) for a in our_accounts) if len(o) >= _MIN_ACCOUNT_DIGITS]
    if len(digits) < _MIN_ACCOUNT_DIGITS or not ours:
        return None
    return any(o.endswith(digits) for o in ours)


def _is_recent(day: str | None, today: date) -> bool | None:
    """``None`` = la fecha no se lee (o viene del futuro): ni condena ni respalda."""
    try:
        age = (today - date.fromisoformat((day or "").strip())).days
    except ValueError:
        return None
    if age < -_MAX_DAYS_AHEAD:
        return None
    return age <= MAX_RECEIPT_AGE_DAYS


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
    if order_total_cop is None or order_total_cop <= 0:
        return ReceiptVerdict(VERDICT_NO_ORDER)
    if amount != order_total_cop:
        return ReceiptVerdict(VERDICT_MISMATCH, REASON_AMOUNT)
    ours = _is_our_account(reading.account, our_accounts)
    if ours is False:
        return ReceiptVerdict(VERDICT_MISMATCH, REASON_ACCOUNT)
    recent = _is_recent(reading.date, today)
    if recent is False:
        return ReceiptVerdict(VERDICT_MISMATCH, REASON_OLD_DATE)
    return ReceiptVerdict(VERDICT_MATCH, evidence=EVIDENCE_FULL if ours and recent else EVIDENCE_PARTIAL)
