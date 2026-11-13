"""La sombra del comprobante: Colossus anota su veredicto y nada más.

Tarjeta 33 del ciclo. La fila lleva solo códigos (nunca el monto, la cuenta ni
la referencia) y el total del pedido sale de OrderFacts. Datos inventados.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.plugins.chats.agent.sales.use_cases.receipt_shadow import record_receipt_check
from src.plugins.chats.shared.receipt_checks import read_checks

_NOW_MS = 1_794_500_000_000  # 2026-11-12 (Bogotá)
_SESSION = "wa_570000000001"


def _receipt(amount="$84.000", day="2026-11-12", account="2233", reference="M000123") -> Any:
    return SimpleNamespace(amount=amount, date=day, account=account, reference=reference)


def _episode_with_order(order_id: str = "ord_1", **extra: Any) -> dict[str, Any]:
    return {"episode_id": "ep_002", "order_id": order_id, "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
            "closed_at_ms": _NOW_MS - 60_000, **extra}


def _closed_with_order(order_id: str = "ord_1", **extra: Any) -> dict[str, Any]:
    return {"episodes": [_episode_with_order(order_id, **extra)]}


def _facts(total: int = 84_000, *, stage: str = "new", pay_status: str = "pending"):
    calls: list[str] = []

    async def reader(order_id: str) -> Any:
        calls.append(order_id)
        return SimpleNamespace(order_id=order_id, total_cop=total, stage=stage, pay_status=pay_status)

    reader.calls = calls  # type: ignore[attr-defined]
    return reader


async def _record(vault: Path, *, session: str = _SESSION, metadata=None, receipt=None, source="image", facts=None):
    return await record_receipt_check(
        vault_dir=vault,
        session_id=session,
        metadata=_closed_with_order() if metadata is None else metadata,
        receipt=receipt,
        source=source,
        order_facts=_facts() if facts is None else facts,
        now_ms=_NOW_MS,
    )


@pytest.fixture(autouse=True)
def _our_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYMENT_NEQUI_NUMBER", "3001112233")
    monkeypatch.delenv("PAYMENT_TRANSFER_ACCOUNT_NUMBER", raising=False)


async def test_a_matching_receipt_is_noted_with_codes_only(tmp_path: Path) -> None:
    row = await _record(tmp_path, receipt=_receipt())

    assert row == {
        "at_ms": _NOW_MS,
        "episode_id": "ep_002",
        "order_id": "ord_1",
        "verdict": "coincide",
        "reason": None,
        "evidence": "completa",
        "source": "image",
    }
    assert read_checks(tmp_path, _SESSION) == [row]


async def test_nothing_read_from_the_receipt_is_written_to_disk(tmp_path: Path) -> None:
    await _record(tmp_path, receipt=_receipt(amount="$84.000", account="2233", reference="M000123"))

    written = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*") if p.is_file())
    assert written
    for secret in ("84.000", "84000", "2233", "M000123"):
        assert secret not in written


async def test_the_total_comes_from_order_facts_not_from_the_episode_copy(tmp_path: Path) -> None:
    metadata = _closed_with_order(order_total_cop=50_000)

    row = await _record(tmp_path, metadata=metadata, receipt=_receipt(amount="$84.000"), facts=_facts(84_000))

    assert row["verdict"] == "coincide"


async def test_a_different_amount_is_noted_with_its_reason(tmp_path: Path) -> None:
    row = await _record(tmp_path, receipt=_receipt(amount="$48.000"))

    assert (row["verdict"], row["reason"]) == ("no_coincide", "monto")


async def test_a_transfer_to_an_account_that_is_not_ours_is_noted(tmp_path: Path) -> None:
    row = await _record(tmp_path, receipt=_receipt(account="9999"))

    assert (row["verdict"], row["reason"]) == ("no_coincide", "cuenta_destino")


async def test_the_bank_account_of_the_business_also_counts_as_ours(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PAYMENT_TRANSFER_ACCOUNT_NUMBER", "000-123456-78")

    row = await _record(tmp_path, receipt=_receipt(account="5678"))

    assert row["verdict"] == "coincide"


async def test_a_receipt_before_any_order_has_nothing_to_compare(tmp_path: Path) -> None:
    facts = _facts()
    metadata = {"episodes": [{"episode_id": "ep_001", "order_id": "ord_old", "closed_at_ms": 1},
                             {"episode_id": "ep_002"}]}

    row = await _record(tmp_path, metadata=metadata, receipt=_receipt(), facts=facts)

    assert (row["verdict"], row["episode_id"], row["order_id"]) == ("sin_pedido", "ep_002", None)
    assert facts.calls == []


async def test_an_order_that_cannot_be_read_never_breaks_the_note(tmp_path: Path) -> None:
    async def broken(order_id: str) -> Any:
        raise TimeoutError("orders down")

    row = await _record(tmp_path, receipt=_receipt(), facts=broken)

    assert (row["verdict"], row["reason"]) == ("sin_pedido", "pedido_no_leido")


async def test_without_an_order_reader_there_is_nothing_to_compare(tmp_path: Path) -> None:
    row = await record_receipt_check(
        vault_dir=tmp_path, session_id=_SESSION, metadata=_closed_with_order(), receipt=_receipt(),
        source="image", order_facts=None, now_ms=_NOW_MS,
    )

    assert row["verdict"] == "sin_pedido"


async def test_the_same_reference_on_another_order_is_a_repeated_receipt(tmp_path: Path) -> None:
    first = await _record(tmp_path, receipt=_receipt(reference="M000123"))
    other = await _record(
        tmp_path, session="wa_570000000002", metadata=_closed_with_order("ord_2"), receipt=_receipt(reference="m-000123")
    )

    assert first["verdict"] == "coincide"
    assert (other["verdict"], other["reason"]) == ("no_coincide", "repetido")


async def test_sending_the_same_receipt_again_for_the_same_order_is_not_repeated(tmp_path: Path) -> None:
    await _record(tmp_path, receipt=_receipt())
    again = await _record(tmp_path, receipt=_receipt())

    assert again["verdict"] == "coincide"
    assert len(read_checks(tmp_path, _SESSION)) == 2


async def test_a_pdf_is_noted_as_unreadable_for_now(tmp_path: Path) -> None:
    row = await _record(tmp_path, receipt=None, source="pdf")

    assert (row["verdict"], row["source"]) == ("ilegible", "pdf")


async def test_an_old_receipt_is_judged_with_the_bogota_date(tmp_path: Path) -> None:
    row = await _record(tmp_path, receipt=_receipt(day="2026-11-01"))

    assert (row["verdict"], row["reason"]) == ("no_coincide", "fecha_vieja")


async def test_the_receipt_that_arrives_in_a_new_episode_is_compared_with_the_order_just_placed(tmp_path: Path) -> None:
    """El camino real: el pedido cerró su episodio y el comprobante abre otro."""
    metadata = {"episodes": [_episode_with_order("ord_1", episode_id="ep_001"), {"episode_id": "ep_002"}]}

    row = await _record(tmp_path, metadata=metadata, receipt=_receipt())

    assert (row["verdict"], row["episode_id"], row["order_id"]) == ("coincide", "ep_001", "ord_1")


async def test_an_order_closed_long_ago_is_not_the_order_of_this_receipt(tmp_path: Path) -> None:
    """Con el chat en manos del equipo los episodios no rotan: el último puede
    ser de una compra de hace meses."""
    old = _closed_with_order(closed_at_ms=_NOW_MS - 45 * 86_400_000)

    row = await _record(tmp_path, metadata=old, receipt=_receipt())

    assert (row["verdict"], row["order_id"]) == ("sin_pedido", None)


@pytest.mark.parametrize("facts", [{"pay_status": "paid"}, {"stage": "cancelled"}, {"stage": "delivered"}])
async def test_an_order_already_paid_or_finished_is_not_waiting_for_a_receipt(tmp_path: Path, facts: dict) -> None:
    row = await _record(tmp_path, receipt=_receipt(amount="$48.000"), facts=_facts(**facts))

    assert (row["verdict"], row["reason"]) == ("sin_pedido", None)


async def test_resending_a_receipt_first_seen_without_an_order_is_not_repeated(tmp_path: Path) -> None:
    before = await _record(tmp_path, metadata={"episodes": [{"episode_id": "ep_001"}]}, receipt=_receipt())
    after = await _record(tmp_path, receipt=_receipt())

    assert (before["verdict"], after["verdict"]) == ("sin_pedido", "coincide")


async def test_a_short_reference_is_not_enough_to_call_a_receipt_repeated(tmp_path: Path) -> None:
    await _record(tmp_path, receipt=_receipt(reference="1234"))
    other = await _record(
        tmp_path, session="wa_570000000002", metadata=_closed_with_order("ord_2"), receipt=_receipt(reference="1234")
    )

    assert other["verdict"] == "coincide"


async def test_a_match_on_the_amount_alone_is_noted_as_partial_evidence(tmp_path: Path) -> None:
    row = await _record(tmp_path, receipt=_receipt(day=None, account=None))

    assert (row["verdict"], row["evidence"]) == ("coincide", "parcial")
