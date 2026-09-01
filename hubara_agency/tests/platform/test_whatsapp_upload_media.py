"""Tests de `upload_media` — subida de bytes a WhatsApp Cloud API (`/media`).

Cierra el gap del envío de fotos DEL OPERADOR: `send_image` acepta `link`
HTTPS público o `media_id`, pero nunca bytes crudos. Como el vault del
dashboard está detrás de auth (Meta no podría fetchear un link), el operador
sube la foto → Meta la hostea → obtenemos un `media_id` → `send_image` la manda.

A diferencia del `_post_json` genérico (que hace swallow del error de
transporte devolviendo `OutboundResult(ok=False)`), `upload_media` PROPAGA el
fallo — el endpoint necesita distinguir "subí bien" de "Meta rechazó" para
devolver 502 y que el frontend reintente SOLO la subida (no el send).
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from src.platform.whatsapp import client as wa_client


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = text

    def json(self) -> dict:
        return self._json


def _capturing_async_client(captured: dict, response: _FakeResponse):
    """Devuelve un context manager async que captura los kwargs del `.post`."""

    class _Client:
        def __init__(self, *args, **kwargs):
            captured["init_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return response

    return _Client


@pytest.mark.asyncio
async def test_upload_media_posts_multipart_and_returns_media_id(monkeypatch):
    monkeypatch.setattr(wa_client, "WHATSAPP_ACCESS_TOKEN", "tok-123", raising=True)
    captured: dict = {}
    fake = _FakeResponse(200, {"id": "9988776655"})

    with patch.object(
        wa_client.httpx, "AsyncClient", _capturing_async_client(captured, fake)
    ):
        media_id = await wa_client.upload_media(
            "phone_42", b"\xff\xd8\xff-jpeg-bytes", "image/jpeg"
        )

    assert media_id == "9988776655"
    # URL apunta al endpoint /media del phone id (no /messages).
    assert captured["url"].endswith("/phone_42/media")
    # Es multipart: los bytes van en `files`, no en `json`.
    post_kwargs = captured["post_kwargs"]
    assert "files" in post_kwargs
    assert "json" not in post_kwargs
    # El form declara messaging_product=whatsapp + type del mime.
    data = post_kwargs.get("data", {})
    assert data.get("messaging_product") == "whatsapp"
    assert data.get("type") == "image/jpeg"
    # Auth header presente.
    headers = captured["init_kwargs"].get("headers") or post_kwargs.get("headers") or {}
    assert "Bearer tok-123" in headers.get("Authorization", "")


@pytest.mark.asyncio
async def test_upload_media_raises_on_meta_error(monkeypatch):
    """Meta 4xx/5xx → excepción (NO swallow), para que el endpoint 502."""
    monkeypatch.setattr(wa_client, "WHATSAPP_ACCESS_TOKEN", "tok", raising=True)
    captured: dict = {}
    fake = _FakeResponse(400, {"error": {"message": "bad file"}}, text='{"error":...}')

    with patch.object(
        wa_client.httpx, "AsyncClient", _capturing_async_client(captured, fake)
    ):
        with pytest.raises(wa_client.MediaUploadError):
            await wa_client.upload_media("phone_1", b"x", "image/png")


@pytest.mark.asyncio
async def test_upload_media_raises_on_transport_error(monkeypatch):
    monkeypatch.setattr(wa_client, "WHATSAPP_ACCESS_TOKEN", "tok", raising=True)

    class _BoomClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, *a, **k):
            raise httpx.ConnectError("dns")

    with patch.object(wa_client.httpx, "AsyncClient", _BoomClient):
        with pytest.raises(wa_client.MediaUploadError):
            await wa_client.upload_media("p", b"x", "image/jpeg")


@pytest.mark.asyncio
async def test_upload_media_fake_when_no_token(monkeypatch):
    """Sin token (dev/tests) → media_id sintético, sin tocar la red."""
    monkeypatch.setattr(wa_client, "WHATSAPP_ACCESS_TOKEN", "", raising=True)
    media_id = await wa_client.upload_media("p", b"x", "image/jpeg")
    assert media_id.startswith("fake-media-")


@pytest.mark.asyncio
async def test_upload_media_part_filename_carries_extension(monkeypatch):
    """PM-11: el part multipart iba como `upload` sin extensión — para
    documentos, Meta puede apoyarse en el nombre; lo derivamos del mime."""
    monkeypatch.setattr(wa_client, "WHATSAPP_ACCESS_TOKEN", "tok-123", raising=True)
    captured: dict = {}
    fake = _FakeResponse(200, {"id": "m-1"})

    with patch.object(
        wa_client.httpx, "AsyncClient", _capturing_async_client(captured, fake)
    ):
        await wa_client.upload_media("phone_42", b"%PDF-doc", "application/pdf")

    part = captured["post_kwargs"]["files"]["file"]
    assert part[0] == "upload.pdf"
    assert part[2] == "application/pdf"

    with patch.object(
        wa_client.httpx, "AsyncClient", _capturing_async_client(captured, fake)
    ):
        await wa_client.upload_media("phone_42", b"\xff\xd8\xff", "image/jpeg")
    assert captured["post_kwargs"]["files"]["file"][0] == "upload.jpg"
