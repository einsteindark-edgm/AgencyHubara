"""``send_ready_photo_activity`` — sube la foto del pedido a Meta y manda la
plantilla ``order_ready_photo_utility_v1`` con ella en el encabezado.

Contrato del vault (lo escribe la API de Órdenes al subir la foto)::

    metadata.order_photos[<order_id backend>] = {
        "filename": "out-order-....jpg",   # en <vault>/<session>/media/
        "media_ref": "/api/dashboard/media/<session>/<filename>",
        "mime": "image/jpeg",
        "uploaded_at_ms": 1779...,
    }

Se guardan los BYTES, no un media_id de Meta (vence a los ~30 días y un pedido
puede quedarse días en preparación): el media_id se obtiene al enviar y se
cachea para que un reintento de la activity no suba ni envíe dos veces.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.eta.agent.eta.activities import (
    claim_eta_notification_activity,
    send_ready_photo_activity,
)
from src.platform.whatsapp.dtos import OutboundResult

SID = "wa_573001234567"
ORDER = "order_01PHOTO"
PHOTO_BYTES = b"\xff\xd8\xff\xe0-foto-del-pedido"
MOD = "src.plugins.eta.agent.eta.activities.ready_photo"


def _meta(vault: Path) -> dict:
    return json.loads((vault / SID / "metadata.json").read_text(encoding="utf-8"))


def _seed(vault: Path, *, with_photo: bool = True, extra: dict | None = None) -> None:
    folder = vault / SID
    (folder / "media").mkdir(parents=True, exist_ok=True)
    data: dict = {"phone_number_id": "PHONE_TEST", **(extra or {})}
    if with_photo:
        (folder / "media" / "out-order-abc.jpg").write_bytes(PHOTO_BYTES)
        data["order_photos"] = {
            ORDER: {
                "filename": "out-order-abc.jpg",
                "media_ref": f"/api/dashboard/media/{SID}/out-order-abc.jpg",
                "mime": "image/jpeg",
                "uploaded_at_ms": 1_000,
            }
        }
    (folder / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


class _Fact:
    display_id = "#31"


class _Snapshot:
    facts = {ORDER: _Fact()}


class _FactsPort:
    async def get_facts(self, ids):
        return _Snapshot()


@pytest.fixture
def meta_fakes(monkeypatch):
    upload = AsyncMock(return_value="MEDIA_123")
    send = AsyncMock(return_value=OutboundResult(wa_message_id="wamid.P", ok=True))
    monkeypatch.setattr(f"{MOD}.upload_media", upload)
    monkeypatch.setattr(f"{MOD}.send_template_to_session", send)
    monkeypatch.setattr(f"{MOD}.get_order_facts_port", lambda: _FactsPort())
    return upload, send


async def test_sends_the_order_photo_template_with_the_uploaded_photo(
    _isolate_vault_dir: Path, meta_fakes
):
    upload, send = meta_fakes
    _seed(_isolate_vault_dir)

    result = await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)

    assert result["sent"] is True
    upload.assert_awaited_once_with("PHONE_TEST", PHOTO_BYTES, "image/jpeg")
    send.assert_awaited_once_with(
        SID,
        "order_ready_photo_utility_v1",
        {"order_reference": "#31"},
        header_media_id="MEDIA_123",
        header_image_url=f"/api/dashboard/media/{SID}/out-order-abc.jpg",
    )


async def test_records_the_send_in_the_eta_timeline_and_marks_ready_notified(
    _isolate_vault_dir: Path, meta_fakes
):
    _seed(_isolate_vault_dir)

    await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)

    meta = _meta(_isolate_vault_dir)
    entry = meta["eta_tracking"]["orders"][ORDER]
    assert "ready" in entry["notified_stages"]
    assert entry["current_stage"] == "ready"
    assert "foto" in entry["events"][-1]["agent_msg"].lower()
    assert meta["order_photos"][ORDER]["sent_at_ms"] > 0


async def test_manual_send_after_shipping_does_not_move_the_tracking_backwards(
    _isolate_vault_dir: Path, meta_fakes
):
    _seed(
        _isolate_vault_dir,
        extra={"eta_tracking": {"orders": {ORDER: {
            "order_id": ORDER, "current_stage": "shipping",
            "notified_stages": ["preparing", "ready", "shipping"], "events": [],
        }}}},
    )

    await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)

    assert _meta(_isolate_vault_dir)["eta_tracking"]["orders"][ORDER]["current_stage"] == "shipping"


async def test_without_photo_reports_not_sent_and_touches_nothing(
    _isolate_vault_dir: Path, meta_fakes
):
    upload, send = meta_fakes
    _seed(_isolate_vault_dir, with_photo=False)

    result = await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)

    assert result == {"sent": False, "reason": "no_photo"}
    upload.assert_not_awaited()
    send.assert_not_awaited()


async def test_a_retry_reuses_the_meta_media_id_instead_of_uploading_again(
    _isolate_vault_dir: Path, meta_fakes, monkeypatch
):
    """Si el envío falla DESPUÉS de subir la foto, el reintento reusa el
    media_id: mismo fingerprint → la idempotencia de plantillas lo cubre."""
    upload, send = meta_fakes
    send.side_effect = [RuntimeError("red caída"), OutboundResult(wa_message_id="wamid.P", ok=True)]
    _seed(_isolate_vault_dir)

    with pytest.raises(RuntimeError):
        await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)
    await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)

    assert upload.await_count == 1
    assert [c.kwargs["header_media_id"] for c in send.await_args_list] == ["MEDIA_123", "MEDIA_123"]


async def test_a_new_photo_is_uploaded_again(_isolate_vault_dir: Path, meta_fakes):
    upload, _ = meta_fakes
    _seed(_isolate_vault_dir)
    await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)

    # El operador reemplaza la foto: otro archivo, otro uploaded_at.
    meta = _meta(_isolate_vault_dir)
    (_isolate_vault_dir / SID / "media" / "out-order-new.jpg").write_bytes(b"\xff\xd8\xff-nueva")
    meta["order_photos"][ORDER].update(filename="out-order-new.jpg", uploaded_at_ms=2_000)
    (_isolate_vault_dir / SID / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)

    assert upload.await_count == 2


async def test_claim_tells_the_workflow_whether_the_order_has_a_photo(
    _isolate_vault_dir: Path, monkeypatch
):
    monkeypatch.setattr(
        "src.platform.orders.composition.get_order_query_port",
        lambda: type("P", (), {"get": AsyncMock(return_value=None)})(),
    )
    _seed(_isolate_vault_dir)
    with_photo = await ActivityEnvironment().run(
        claim_eta_notification_activity, SID, ORDER, "ready"
    )
    other = await ActivityEnvironment().run(
        claim_eta_notification_activity, SID, "order_sin_foto", "ready"
    )

    assert with_photo["has_ready_photo"] is True
    assert other["has_ready_photo"] is False


async def test_a_failed_send_is_left_on_the_photo_for_the_operator(
    _isolate_vault_dir: Path, meta_fakes
):
    """Si Meta rechaza (p.ej. plantilla aún no aprobada: 132001), el panel de
    Órdenes tiene que mostrarlo — el envío corre en el ETA, lejos del clic."""
    from temporalio.exceptions import ApplicationError

    _, send = meta_fakes
    send.side_effect = ApplicationError(
        'WhatsApp template send failed (non-retryable, code=132001): {"code":132001}',
        non_retryable=True,
    )
    _seed(_isolate_vault_dir)

    with pytest.raises(ApplicationError):
        await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)

    photo = _meta(_isolate_vault_dir)["order_photos"][ORDER]
    assert "aprobada" in photo["last_send_error"]
    assert "sent_at_ms" not in photo


async def test_a_later_success_clears_the_error(_isolate_vault_dir: Path, meta_fakes):
    _, send = meta_fakes
    send.side_effect = [RuntimeError("red caída"), OutboundResult(wa_message_id="wamid.P", ok=True)]
    _seed(_isolate_vault_dir)

    with pytest.raises(RuntimeError):
        await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)
    await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)

    assert "last_send_error" not in _meta(_isolate_vault_dir)["order_photos"][ORDER]


async def test_a_deduplicated_resend_is_not_recorded_as_a_new_send(
    _isolate_vault_dir: Path, meta_fakes
):
    """Reenviar la misma foto a los segundos: la idempotencia de plantillas
    devuelve el MISMO wamid sin mandar nada. No es un envío nuevo: ni otra
    entrada en el timeline ni otro sent_at."""
    _seed(_isolate_vault_dir)
    await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)
    first_sent_at = _meta(_isolate_vault_dir)["order_photos"][ORDER]["sent_at_ms"]

    result = await ActivityEnvironment().run(send_ready_photo_activity, SID, ORDER)

    meta = _meta(_isolate_vault_dir)
    assert result == {"sent": True, "wa_message_id": "wamid.P", "deduped": True}
    assert len(meta["eta_tracking"]["orders"][ORDER]["events"]) == 1
    assert meta["order_photos"][ORDER]["sent_at_ms"] == first_sent_at
