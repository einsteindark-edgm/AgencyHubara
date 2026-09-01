"""`fetch_media_bytes(max_bytes=...)` — cap de descarga de media inbound.

PM-02 del premortem de chat-pdf-attachments: WhatsApp permite documentos de
hasta 100 MB; sin cap, un cliente puede meter 100 MB a la RAM del API y al
EBS del vault por cada PDF. La política es NO descargar por encima del cap:
Meta ya declara `file_size` en el metadata (paso 1), así que el corte ocurre
ANTES de bajar los bytes; el chequeo post-download es defensa en profundidad
para un `file_size` ausente o mentiroso.

Sin `max_bytes` (audio/visión) el comportamiento es el histórico: sin límite.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import src.platform.audio.meta_media_fetcher as fetcher


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content
        self.text = ""


def _fake_async_client(response: _FakeResponse, calls: list):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            calls.append(url)
            return response

    return _Client


def _meta(file_size, url="https://lookaside.fbsbx.com/x"):
    meta = {"url": url, "mime_type": "application/pdf"}
    if file_size is not None:
        meta["file_size"] = file_size
    return meta


@pytest.mark.asyncio
async def test_declared_size_over_max_skips_download(monkeypatch):
    monkeypatch.setattr(fetcher, "WHATSAPP_ACCESS_TOKEN", "tok", raising=True)
    calls: list = []
    with (
        patch.object(
            fetcher,
            "fetch_media_metadata",
            new=AsyncMock(return_value=_meta(11 * 1024 * 1024)),
        ),
        patch.object(
            fetcher.httpx,
            "AsyncClient",
            _fake_async_client(_FakeResponse(200, b"x"), calls),
        ),
    ):
        result = await fetcher.fetch_media_bytes(
            "m-big", max_bytes=10 * 1024 * 1024
        )
    assert result is None
    # El corte fue ANTES de bajar los bytes.
    assert calls == []


@pytest.mark.asyncio
async def test_actual_bytes_over_max_are_dropped(monkeypatch):
    """Defensa en profundidad: `file_size` ausente pero el body real excede."""
    monkeypatch.setattr(fetcher, "WHATSAPP_ACCESS_TOKEN", "tok", raising=True)
    big = b"%PDF-" + b"x" * (10 * 1024 * 1024)
    with (
        patch.object(
            fetcher, "fetch_media_metadata", new=AsyncMock(return_value=_meta(None))
        ),
        patch.object(
            fetcher.httpx,
            "AsyncClient",
            _fake_async_client(_FakeResponse(200, big), []),
        ),
    ):
        result = await fetcher.fetch_media_bytes(
            "m-liar", max_bytes=10 * 1024 * 1024
        )
    assert result is None


@pytest.mark.asyncio
async def test_within_max_returns_bytes(monkeypatch):
    monkeypatch.setattr(fetcher, "WHATSAPP_ACCESS_TOKEN", "tok", raising=True)
    body = b"%PDF-1.4 recibo"
    with (
        patch.object(
            fetcher,
            "fetch_media_metadata",
            new=AsyncMock(return_value=_meta(len(body))),
        ),
        patch.object(
            fetcher.httpx,
            "AsyncClient",
            _fake_async_client(_FakeResponse(200, body), []),
        ),
    ):
        result = await fetcher.fetch_media_bytes(
            "m-ok", max_bytes=10 * 1024 * 1024
        )
    assert result == (body, "application/pdf")


@pytest.mark.asyncio
async def test_no_max_bytes_keeps_legacy_unlimited_behavior(monkeypatch):
    monkeypatch.setattr(fetcher, "WHATSAPP_ACCESS_TOKEN", "tok", raising=True)
    big = b"x" * (11 * 1024 * 1024)
    with (
        patch.object(
            fetcher,
            "fetch_media_metadata",
            new=AsyncMock(return_value=_meta(len(big))),
        ),
        patch.object(
            fetcher.httpx,
            "AsyncClient",
            _fake_async_client(_FakeResponse(200, big), []),
        ),
    ):
        result = await fetcher.fetch_media_bytes("m-audio")
    assert result is not None
