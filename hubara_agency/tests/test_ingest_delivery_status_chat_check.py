"""El estado de WhatsApp llega al chat (doble check), no solo al costo.

Incidente 2026-10-15 (caída de DeepSeek, el bot respondió un rato con Gemini):
en tres conversaciones con respuesta del bot los estados sent/delivered/read de
Meta SÍ llegaron, pero el episodio no tenía `outbound_messages` y terminaron en
`_orphan_delivery_statuses.jsonl`. El operador no podía saber si sus mensajes
llegaron. Ahora cada estado se anota en la conversación del destinatario,
unido al mensaje por su wamid, sin tocar el costo por episodio.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.cost import RateCard, RateCardEntry
from src.plugins.chats.agent.sales.use_cases.ingest_delivery_status import (
    IngestDeliveryStatus,
)
from src.plugins.chats.shared.delivery_status import DeliveryStatusLog

#: Sintéticos (forge: nunca un teléfono real en el repo).
_PHONE = "573001234567"
_SESSION = f"wa_{_PHONE}"


async def _no_sleep(_seconds: float) -> None:
    return None


def _rate_card() -> RateCard:
    return RateCard(
        version="co_test",
        effective_from_ms=0,
        country="CO",
        currency="USD",
        rates={"service": RateCardEntry(usd_micros_per_message=0)},
    )


def _use_case(vault: Path, **kwargs: object) -> IngestDeliveryStatus:
    return IngestDeliveryStatus(
        metadata_store=FilesystemMetadataStore(vault),
        rate_card=_rate_card(),
        vault_dir=vault,
        sleeper=_no_sleep,
        retry_delays=(0.0,),
        **kwargs,  # type: ignore[arg-type]
    )


def _seed_conversation_without_outbound_log(vault: Path) -> None:
    """La forma del incidente: conversación viva, episodio sin outbound."""
    FilesystemMetadataStore(vault).write(
        _SESSION,
        {"episodes": [{"episode_id": "ep_1", "started_at_ms": 1, "closed_at_ms": None}]},
    )


@pytest.mark.asyncio
async def test_status_of_a_conversation_without_outbound_log_reaches_its_chat(tmp_path: Path) -> None:
    _seed_conversation_without_outbound_log(tmp_path)
    use_case = _use_case(tmp_path)

    await use_case.execute(
        "wamid.BOT1", "delivered", None, timestamp_ms=2_000, recipient_id=_PHONE
    )
    await use_case.execute("wamid.BOT1", "sent", None, timestamp_ms=1_000, recipient_id=_PHONE)

    folded = DeliveryStatusLog(tmp_path).read(_SESSION)
    assert folded["wamid.BOT1"].status == "delivered"
    assert folded["wamid.BOT1"].at_ms == 2_000


@pytest.mark.asyncio
async def test_status_without_recipient_lands_where_the_outbound_was_logged(tmp_path: Path) -> None:
    FilesystemMetadataStore(tmp_path).write(
        _SESSION,
        {
            "episodes": [
                {
                    "episode_id": "ep_1",
                    "started_at_ms": 1,
                    "closed_at_ms": None,
                    "outbound_messages": [
                        {"sent_at_ms": 1, "wa_message_id": "wamid.T1", "kind": "template"}
                    ],
                }
            ]
        },
    )

    await _use_case(tmp_path).execute("wamid.T1", "read", None, timestamp_ms=5_000)

    assert DeliveryStatusLog(tmp_path).read(_SESSION)["wamid.T1"].status == "read"


@pytest.mark.asyncio
async def test_status_of_a_customer_without_phone_reaches_the_bsuid_conversation(tmp_path: Path) -> None:
    """Clientes con nombre de usuario: Meta manda `recipient_user_id` (BSUID)."""
    session = "wa_CO9990000000000002"
    FilesystemMetadataStore(tmp_path).write(session, {"episodes": []})

    await _use_case(tmp_path).execute(
        "wamid.U1", "sent", None, timestamp_ms=1_000, recipient_user_id="CO.9990000000000002"
    )

    assert DeliveryStatusLog(tmp_path).read(session)["wamid.U1"].status == "sent"


@pytest.mark.asyncio
async def test_each_recorded_status_notifies_the_live_channel(tmp_path: Path) -> None:
    _seed_conversation_without_outbound_log(tmp_path)
    notified: list[str] = []

    await _use_case(tmp_path, on_delivery_recorded=notified.append).execute(
        "wamid.BOT1", "read", None, timestamp_ms=3_000, recipient_id=_PHONE
    )

    assert notified == [_SESSION]


@pytest.mark.asyncio
async def test_failed_status_keeps_the_meta_error_in_the_chat(tmp_path: Path) -> None:
    _seed_conversation_without_outbound_log(tmp_path)

    await _use_case(tmp_path).execute(
        "wamid.BOT2",
        "failed",
        None,
        timestamp_ms=4_000,
        error_code=131047,
        error_title="Re-engagement message",
        recipient_id=_PHONE,
    )

    check = DeliveryStatusLog(tmp_path).read(_SESSION)["wamid.BOT2"]
    assert (check.status, check.error_code, check.error_title) == ("failed", 131047, "Re-engagement message")


@pytest.mark.asyncio
async def test_status_for_an_unknown_recipient_writes_no_conversation(tmp_path: Path) -> None:
    """Nunca crea una conversación (ni una ruta) a partir del cuerpo del webhook."""
    vault = tmp_path / "vault"
    vault.mkdir()

    await _use_case(vault).execute("wamid.Z", "sent", None, timestamp_ms=1, recipient_id="../../etc")

    assert sorted(p.name for p in vault.iterdir()) == ["_orphan_delivery_statuses.jsonl"]
    assert not list(tmp_path.rglob("delivery_statuses.jsonl"))


def test_the_parser_reads_who_the_status_is_for_and_metas_error_title() -> None:
    from src.plugins.chats.agent.sales.parsers import parse_whatsapp_statuses

    body = {"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wamid.1", "status": "read", "timestamp": "1789992000", "recipient_id": _PHONE},
        {"id": "wamid.2", "status": "failed", "timestamp": "1789992060",
         "recipient_user_id": "CO.9990000000000002",
         "errors": [{"code": 131047, "title": "Re-engagement message"}]},
    ]}}]}]}

    read, failed = parse_whatsapp_statuses(body)

    assert (read.recipient_id, read.recipient_user_id, read.error_title) == (_PHONE, None, None)
    assert (failed.recipient_id, failed.recipient_user_id, failed.error_title) == (
        None,
        "CO.9990000000000002",
        "Re-engagement message",
    )
