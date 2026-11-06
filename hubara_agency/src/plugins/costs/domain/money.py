"""Money as integer micros (1 unit = 1_000_000 micros) — never floats.

Providers answer with decimal strings ("1.0232362140000000") or floats
(Meta's ``cost``); everything is turned into ``Decimal`` at the edge and
stored as integer micros, the unit the rest of the repo already uses for
sub-cent prices (WhatsApp, LLM tokens).
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

MICROS = Decimal(1_000_000)


def to_decimal(value: object) -> Decimal:
    """``Decimal`` from a provider value; floats go through ``str`` (no binary noise)."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"monto inválido: {value!r}") from exc


def to_micros(value: object) -> int:
    return int((to_decimal(value) * MICROS).to_integral_value(rounding=ROUND_HALF_EVEN))


def micros_to_decimal(micros: int) -> Decimal:
    return Decimal(micros) / MICROS


def fmt_usd(micros: int) -> str:
    """Human USD with 4 decimals (sub-cent amounts stay visible)."""
    return f"US${micros_to_decimal(micros):.4f}"
