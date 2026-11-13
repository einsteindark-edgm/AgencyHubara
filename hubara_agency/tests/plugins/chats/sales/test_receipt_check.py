"""El veredicto de un comprobante de pago contra el pedido (sombra).

Colossus lee el comprobante y lo compara con el pedido que ya registró. En este
escalón solo anota qué habría dicho: el equipo sigue verificando el pago en la
cuenta. Comprobantes inventados: montos, fechas, cuentas y referencias de mentira.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.plugins.chats.agent.sales.use_cases.receipt_check import (
    ReceiptReading,
    check_receipt,
    reference_fingerprint,
)

_TODAY = date(2026, 11, 12)
_OUR_ACCOUNTS = ("3001112233", "00012345678")


def _reading(amount="$84.000", day="2026-11-12", account="2233", reference="M0001") -> ReceiptReading:
    return ReceiptReading(amount=amount, date=day, account=account, reference=reference)


def _check(reading, *, total=84_000, used=frozenset()):
    v = check_receipt(
        reading, order_total_cop=total, our_accounts=_OUR_ACCOUNTS, used_references=used, today=_TODAY
    )
    return (v.verdict, v.reason)


def test_same_amount_recent_date_and_our_account_matches() -> None:
    assert _check(_reading()) == ("coincide", None)


@pytest.mark.parametrize("amount", ["84000", "$ 84.000", "$84.000,00", "84,000.00", "COP 84.000"])
def test_the_amount_matches_however_the_bank_writes_it(amount: str) -> None:
    assert _check(_reading(amount=amount)) == ("coincide", None)


def test_a_different_amount_does_not_match() -> None:
    assert _check(_reading(amount="$48.000")) == ("no_coincide", "monto")


def test_a_receipt_from_ten_days_ago_does_not_match() -> None:
    assert _check(_reading(day="2026-11-02")) == ("no_coincide", "fecha_vieja")


def test_a_receipt_from_yesterday_still_matches() -> None:
    assert _check(_reading(day="2026-11-11")) == ("coincide", None)


def test_a_transfer_to_someone_elses_account_does_not_match() -> None:
    assert _check(_reading(account="9999")) == ("no_coincide", "cuenta_destino")


def test_the_full_account_number_also_identifies_our_account() -> None:
    assert _check(_reading(account="300 111 2233")) == ("coincide", None)


def test_a_reference_already_used_for_another_order_does_not_match() -> None:
    used = frozenset({reference_fingerprint("M0001")})
    assert _check(_reading(), used=used) == ("no_coincide", "repetido")


def test_a_repeated_receipt_wins_over_any_other_difference() -> None:
    used = frozenset({reference_fingerprint("m-0001")})
    assert _check(_reading(amount="$1.000", account="9999"), used=used) == ("no_coincide", "repetido")


def test_the_fingerprint_never_carries_the_reference_itself() -> None:
    assert "M0001" not in reference_fingerprint("M0001")
    assert reference_fingerprint(" m-0001 ") == reference_fingerprint("M0001")


def test_without_a_date_or_an_account_only_the_amount_decides() -> None:
    assert _check(_reading(day=None, account=None, reference=None)) == ("coincide", None)


def test_a_date_that_cannot_be_read_does_not_condemn_the_receipt() -> None:
    assert _check(_reading(day="ayer en la tarde")) == ("coincide", None)


@pytest.mark.parametrize("reading", [None, _reading(amount=None), _reading(amount="no se ve")])
def test_a_receipt_whose_amount_cannot_be_read_is_unreadable(reading) -> None:
    assert _check(reading) == ("ilegible", None)


def test_a_receipt_without_a_registered_order_has_nothing_to_compare() -> None:
    assert _check(_reading(), total=None) == ("sin_pedido", None)
