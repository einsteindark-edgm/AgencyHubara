"""Anotar un envío que YA SALIÓ no puede hacer que salga otra vez (PR #393,
octava revisión).

`send_whatsapp_message_activity` manda el texto y DESPUÉS lo anota en
`metadata.json` (outbound + huella de idempotencia). Si esa anotación lanzaba
(un EIO), la activity fallaba, Temporal la reintentaba sin la huella y el
cliente recibía el mensaje dos veces. Misma regla que `_safe_update` del
flush: el envío ya ocurrió, la anotación es de mejor esfuerzo.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from temporalio.testing import ActivityEnvironment

import src.platform.whatsapp.activities as activities
from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.dtos import OutboundResult

SID = "wa_573001234567"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(activities, "WORKSPACE_VAULT_DIR", tmp_path, raising=True)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "phone-test")
    FilesystemMetadataStore(tmp_path).write(
        SID,
        {"phone_number_id": "pnid-1", "active_route": "ventas",
         "episodes": [{"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}]},
    )
    return tmp_path


async def _send_like_temporal(send_text: AsyncMock) -> list[str]:
    """Corre la activity como el workflow: hasta 2 intentos."""
    attempts: list[str] = []
    with (
        patch.object(activities.whatsapp_client, "send_text", new=send_text),
        patch.object(activities.asyncio, "sleep", new=AsyncMock()),
    ):
        for _ in range(2):
            try:
                await ActivityEnvironment().run(
                    activities.send_whatsapp_message_activity, SID, "Hola, te cuento el precio."
                )
                attempts.append("ok")
                break
            except Exception as exc:  # noqa: BLE001 — el intento fallido es el dato
                attempts.append(type(exc).__name__)
    return attempts


def _outbound(vault: Path) -> list[dict[str, Any]]:
    data = json.loads((vault / SID / "metadata.json").read_text(encoding="utf-8"))
    return data["episodes"][-1].get("outbound_messages") or []


@pytest.mark.asyncio
async def test_a_record_that_keeps_failing_after_the_send_does_not_send_twice(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    send_text = AsyncMock(return_value=OutboundResult(wa_message_id="wamid.out1", ok=True))

    def disk_error(self: FilesystemMetadataStore, session_id: str, mutator: Any) -> Any:
        raise OSError(errno.EIO, os.strerror(errno.EIO))

    monkeypatch.setattr(FilesystemMetadataStore, "update", disk_error)

    with structlog.testing.capture_logs() as logs:
        attempts = await _send_like_temporal(send_text)

    assert attempts == ["ok"], "la anotación posterior hizo fallar la activity (Temporal la reintenta)"
    assert send_text.await_count == 1, "el mensaje salió dos veces"
    assert any(e.get("log_level") == "error" for e in logs), "no quedó rastro de la anotación perdida"


@pytest.mark.asyncio
async def test_a_transient_error_recording_the_send_is_retried_and_recorded(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un EIO de un instante en la relectura de la anotación: el store lo
    reintenta y el envío queda anotado (una vez)."""
    send_text = AsyncMock(return_value=OutboundResult(wa_message_id="wamid.out1", ok=True))
    real_read_text = Path.read_text
    reads = {"n": 0}

    def eio_on_the_record(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == "metadata.json":
            reads["n"] += 1
            if reads["n"] == 2:  # la 1.ª es la lectura previa al envío
                raise OSError(errno.EIO, os.strerror(errno.EIO))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", eio_on_the_record)

    attempts = await _send_like_temporal(send_text)

    monkeypatch.setattr(Path, "read_text", real_read_text)
    assert attempts == ["ok"]
    assert send_text.await_count == 1
    assert [m["wa_message_id"] for m in _outbound(vault)] == ["wamid.out1"]
