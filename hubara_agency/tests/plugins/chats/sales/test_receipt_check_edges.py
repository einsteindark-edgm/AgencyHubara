"""Bordes del veredicto del comprobante que encontró la revisión (tarjeta 33).

Un «coincide» de más es el error caro: acá van los casos donde el veredicto no
debe pasarse de confiado, y los montos que el banco escribe raro. Inventados.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.plugins.chats.agent.sales.use_cases.receipt_check import ReceiptReading, check_receipt

_TODAY = date(2026, 11, 12)
_OURS = ("3001112233",)


def _check(*, amount="$84.000", day="2026-11-12", account="2233", total=84_000, ours=_OURS):
    return check_receipt(
        ReceiptReading(amount=amount, date=day, account=account, reference=None),
        order_total_cop=total, our_accounts=ours, used_references=frozenset(), today=_TODAY,
    )


def test_amount_account_and_date_together_are_full_evidence() -> None:
    v = _check()
    assert (v.verdict, v.evidence) == ("coincide", "completa")


@pytest.mark.parametrize("missing", [{"day": None}, {"account": None}, {"day": "mañana"}, {"account": "33"}])
def test_a_match_on_the_amount_alone_is_marked_as_partial_evidence(missing: dict) -> None:
    v = _check(**missing)
    assert (v.verdict, v.evidence) == ("coincide", "parcial")


def test_a_date_in_the_future_is_not_evidence_of_a_recent_payment() -> None:
    v = _check(day="2027-05-01")
    assert (v.verdict, v.evidence) == ("coincide", "parcial")


def test_a_date_one_day_ahead_is_only_a_clock_difference() -> None:
    assert _check(day="2026-11-13").evidence == "completa"


def test_when_we_have_no_account_to_compare_the_account_decides_nothing() -> None:
    v = _check(account="9999", ours=("@llave-sin-digitos", ""))
    assert (v.verdict, v.evidence) == ("coincide", "parcial")


@pytest.mark.parametrize("total", [0, -1])
def test_an_order_without_a_real_total_is_not_an_order_to_compare(total: int) -> None:
    assert _check(amount="$0", total=total).verdict == "sin_pedido"


@pytest.mark.parametrize("amount", ["84000.0", "1 pago por $84.000", "Valor: $ 84.000,5", "84.000 pesos del 12"])
def test_the_amount_is_the_biggest_number_however_it_is_written(amount: str) -> None:
    assert _check(amount=amount).verdict == "coincide"


def test_a_mismatch_carries_no_evidence_level() -> None:
    assert _check(amount="$48.000").evidence is None
