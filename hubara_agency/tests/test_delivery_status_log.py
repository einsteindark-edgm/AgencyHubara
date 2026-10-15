"""Doble check de WhatsApp en el chat: lo que Meta informó de cada mensaje.

El estado sale SOLO de los webhooks `statuses[]` de Meta (nunca del bot). Llegan
desordenados (visto en prod el 2026-10-15: delivered → sent → read), así que el
plegado no retrocede: read > delivered > failed > sent. Un `failed` se guarda con
su código de Meta.
"""
from __future__ import annotations

from pathlib import Path

from src.plugins.chats.shared.delivery_status import (
    DeliveryStatusLog,
    fold_delivery_statuses,
    message_delivery,
)


def _rec(wamid: str, status: str, at_ms: int, **extra: object) -> dict[str, object]:
    return {"wamid": wamid, "status": status, "at_ms": at_ms, **extra}


def test_fold_out_of_order_statuses_keeps_the_highest_level() -> None:
    folded = fold_delivery_statuses(
        [
            _rec("wamid.A", "delivered", 2_000),
            _rec("wamid.A", "read", 3_000),
            _rec("wamid.A", "sent", 1_000),
        ]
    )

    assert folded["wamid.A"].status == "read"
    assert folded["wamid.A"].at_ms == 3_000


def test_fold_failed_after_sent_keeps_meta_error_code() -> None:
    folded = fold_delivery_statuses(
        [
            _rec("wamid.B", "sent", 1_000),
            _rec("wamid.B", "failed", 2_000, error_code=131047, error_title="Re-engagement message"),
        ]
    )

    assert folded["wamid.B"].status == "failed"
    assert folded["wamid.B"].error_code == 131047
    assert folded["wamid.B"].error_title == "Re-engagement message"


def test_fold_skips_records_that_are_not_a_whatsapp_status() -> None:
    folded = fold_delivery_statuses(
        [
            {"wamid": "wamid.C", "status": "deleted", "at_ms": 1},
            {"status": "read", "at_ms": 2},
            "basura",
            _rec("wamid.C", "delivered", 3),
        ]
    )

    assert set(folded) == {"wamid.C"}
    assert folded["wamid.C"].status == "delivered"


def test_message_split_in_bubbles_shows_its_weakest_bubble() -> None:
    """Un texto del bot sale como N burbujas: el chat dice «leído» solo cuando
    Meta confirmó TODAS; mientras tanto, lo que confirmó la más atrasada."""
    folded = fold_delivery_statuses(
        [
            _rec("wamid.1", "read", 3_000),
            _rec("wamid.2", "delivered", 2_500),
            _rec("wamid.3", "read", 3_100),
        ]
    )

    assert message_delivery(["wamid.1", "wamid.2", "wamid.3"], folded) == {
        "status": "delivered",
        "at_ms": 2_500,
        "error_code": None,
        "error_title": None,
    }


def test_message_without_any_whatsapp_status_is_unconfirmed() -> None:
    """Sin estado de Meta (o sin wamid, como los textos de antes del cambio) el
    chat dice «sin confirmación»: nunca un check inventado."""
    folded = fold_delivery_statuses([_rec("wamid.1", "read", 3_000)])

    assert message_delivery([], folded)["status"] == "unconfirmed"
    assert message_delivery(["wamid.1", "wamid.sin-estado"], folded)["status"] == "unconfirmed"


def test_a_failed_bubble_marks_the_whole_message_failed_with_its_code() -> None:
    """Una burbuja que no llegó es lo primero que el operador tiene que ver,
    aunque otra todavía no tenga estado."""
    folded = fold_delivery_statuses(
        [
            _rec("wamid.1", "read", 3_000),
            _rec("wamid.2", "failed", 2_000, error_code=131026),
        ]
    )

    delivery = message_delivery(["wamid.1", "wamid.2", "wamid.3"], folded)

    assert delivery["status"] == "failed"
    assert delivery["error_code"] == 131026
    assert delivery["at_ms"] == 2_000


#: Sintético (forge: nunca un teléfono real en el repo).
_SESSION = "wa_573001234567"


def test_log_appends_per_conversation_and_reads_back_folded(tmp_path: Path) -> None:
    log = DeliveryStatusLog(tmp_path)

    log.append(_SESSION, wamid="wamid.X", status="delivered", at_ms=2_000)
    log.append(_SESSION, wamid="wamid.X", status="sent", at_ms=1_000)
    log.append(_SESSION, wamid="wamid.Y", status="failed", at_ms=1_500, error_code=131049)

    folded = log.read(_SESSION)
    assert folded["wamid.X"].status == "delivered"
    assert folded["wamid.Y"].error_code == 131049
    assert (tmp_path / _SESSION / "delivery_statuses.jsonl").exists()
    assert log.read("wa_573009999999") == {}


def test_log_never_writes_outside_a_customer_conversation(tmp_path: Path) -> None:
    log = DeliveryStatusLog(tmp_path / "vault")

    assert log.append("../fuera", wamid="wamid.X", status="sent", at_ms=1) is False
    assert not (tmp_path / "fuera").exists()
