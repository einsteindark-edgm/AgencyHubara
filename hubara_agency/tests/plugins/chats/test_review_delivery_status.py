"""Lo que pasó con la invitación a calificar y cuánto costó (premortem C7).

Reseñas tras la entrega (2026-10-13): la invitación sale casi siempre con el
último episodio CERRADO (el pedido ya se entregó). Antes:
  * el envío no quedaba en ningún episodio → su costo (una plantilla de
    MARKETING) no aparecía en el costo de la conversación;
  * el estado de Meta (entregado / leído / fallido) no se encontraba: cuatro
    búsquedas por todo el vault y al archivo de huérfanos. El plugin de
    reseñas necesita saber si falló (p. ej. 131047, ventana cerrada) para
    mandar la plantilla.

Contrato:
  * un envío con el último episodio cerrado queda en ESE episodio marcado
    `after_close` y su costo se materializa cuando llega el estado (lo que ya
    estaba al cerrar sigue congelado);
  * un estado cuyo wamid es de un `review_request_touches[]` se anota en el
    touch (`status`, y `error_code` si falló) y no es un huérfano.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.cost import OutboundLogEntry
from src.plugins.chats.agent.sales.use_cases.ingest_delivery_status import IngestDeliveryStatus
from tests.plugins.chats.test_campaign_delivery_status import _PRICING, _orphans, _rate_card

_SESSION = "wa_573001234567"
_NOW = 1_790_000_000_000
_WAMID = "wamid.REVIEW.TPL"


def _closed_episode() -> dict[str, Any]:
    return {"episode_id": "ep_003", "started_at_ms": _NOW - 9 * 86_400_000,
            "closed_at_ms": _NOW - 3 * 86_400_000, "closing_tag": "COMPRA_EXITOSA",
            "outbound_messages": [], "cost_summary": None}


def _seed(vault: Path, metadata: dict[str, Any]) -> FilesystemMetadataStore:
    store = FilesystemMetadataStore(vault)
    (vault / _SESSION).mkdir(parents=True, exist_ok=True)
    store.write(_SESSION, metadata)
    return store


def _use_case(vault: Path, sleeps: list[float]) -> IngestDeliveryStatus:
    async def _sleep(s: float) -> None:
        sleeps.append(s)

    return IngestDeliveryStatus(
        metadata_store=FilesystemMetadataStore(vault), rate_card=_rate_card(), vault_dir=vault,
        sleeper=_sleep, retry_delays=(0.5, 1.0, 2.0),
    )


def test_a_send_after_the_episode_closed_is_recorded_in_that_episode() -> None:
    from src.platform.whatsapp.activities import record_outbound_in_active_episode

    metadata = {"episodes": [_closed_episode()]}
    entry = OutboundLogEntry(sent_at_ms=_NOW, wa_message_id=_WAMID, kind="template",
                             template_name="order_review_marketing_v1", pricing=None,
                             cost_usd_micros=None, rate_card_version=None)

    record_outbound_in_active_episode(metadata, entry)

    [recorded] = metadata["episodes"][-1]["outbound_messages"]
    assert recorded["wa_message_id"] == _WAMID and recorded["after_close"] is True
    assert metadata["episodes"][-1]["cost_summary"]["messages_pending_count"] == 1


@pytest.mark.asyncio
async def test_the_cost_of_a_send_after_close_is_materialized(tmp_path: Path) -> None:
    from src.platform.whatsapp.activities import record_outbound_in_active_episode

    metadata = {"episodes": [_closed_episode()]}
    record_outbound_in_active_episode(metadata, OutboundLogEntry(
        sent_at_ms=_NOW, wa_message_id=_WAMID, kind="template", template_name="order_review_marketing_v1",
        pricing=None, cost_usd_micros=None, rate_card_version=None))
    store = _seed(tmp_path, metadata)

    await _use_case(tmp_path, []).execute(_WAMID, "delivered", _PRICING, timestamp_ms=_NOW + 1000)

    episode = store.read(_SESSION)["episodes"][-1]
    assert episode["cost_summary"]["total_usd_micros"] == 12500
    assert episode["outbound_messages"][0]["cost_usd_micros"] == 12500
    assert episode["outbound_messages"][0]["after_close"] is True
    assert _orphans(tmp_path) == []


@pytest.mark.parametrize(
    ("status", "error_code", "expected"),
    [
        ("delivered", None, {"status": "delivered"}),
        ("read", None, {"status": "read"}),
        ("failed", 131047, {"status": "failed", "error_code": "131047"}),
    ],
)
@pytest.mark.asyncio
async def test_the_status_of_a_review_invitation_lands_on_its_touch(
    tmp_path: Path, status: str, error_code: int | None, expected: dict[str, Any]
) -> None:
    touch = {"order_id": "order_01A", "kind": "flow", "at_ms": _NOW, "wa_message_id": _WAMID}
    store = _seed(tmp_path, {"episodes": [_closed_episode()], "review_request_touches": [touch]})
    sleeps: list[float] = []

    await _use_case(tmp_path, sleeps).execute(
        _WAMID, status, None if status == "failed" else _PRICING, timestamp_ms=_NOW + 1000, error_code=error_code
    )

    [saved] = store.read(_SESSION)["review_request_touches"]
    assert {k: saved.get(k) for k in expected} == expected
    assert sleeps == []  # encontrado a la primera, sin barrer el vault cuatro veces
    assert _orphans(tmp_path) == []


@pytest.mark.asyncio
async def test_a_late_status_does_not_undo_a_later_one(tmp_path: Path) -> None:
    """Meta puede entregar «leído» antes que «entregado»; un «fallido» queda."""
    touches = [
        {"order_id": "o1", "kind": "flow", "at_ms": _NOW, "wa_message_id": "wamid.A", "status": "read"},
        {"order_id": "o2", "kind": "template", "at_ms": _NOW, "wa_message_id": "wamid.B", "status": "failed",
         "error_code": "131049"},
    ]
    store = _seed(tmp_path, {"episodes": [_closed_episode()], "review_request_touches": touches})
    use_case = _use_case(tmp_path, [])

    await use_case.execute("wamid.A", "delivered", _PRICING)
    await use_case.execute("wamid.B", "sent", _PRICING)

    a, b = store.read(_SESSION)["review_request_touches"]
    assert a["status"] == "read"
    assert (b["status"], b["error_code"]) == ("failed", "131049")
