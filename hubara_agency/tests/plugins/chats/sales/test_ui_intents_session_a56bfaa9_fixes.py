"""Regression tests para los fixes de la sesión a56bfaa9.

Tres bugs detectados en producción:

1. LLM mostró catálogo en texto AND llamó `present_products` (duplicación).
   Fix: regla de prompt (anti-duplicación) + tests validan que el widget se emita.
2. Cliente pidió "más fotos" → LLM llamó `send_cta_url` apuntando a
   `/products/sacrificio-de-amor` → sacó al cliente fuera de WhatsApp.
   Fix: nueva tool `present_product_gallery` + whitelist bloqueada para
   `/products/*` + tests validan ambos paths.
3. En el saludo no aparecieron botones para guiar la elección.
   Fix: nueva tool `send_quick_replies` + Protocolo de saludo en TOOLS.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from exoclaw.agent.tools import ToolContext
from src.plugins.chats.agent.sales.tools.ui_intents import (
    PresentProductGalleryTool,
    SendCTAUrlTool,
    SendQuickRepliesTool,
)


# =============================================================================
# Fixtures / stubs
# =============================================================================


class _FakeCatalog:
    """CatalogPort minimal para tests — devuelve un producto sintético con
    múltiples imágenes."""

    def __init__(self, product) -> None:
        self._p = product

    async def get_by_handle(self, handle: str):
        if handle != self._p.handle:
            from src.platform.catalog import ProductNotFoundError

            raise ProductNotFoundError(handle)
        return self._p


class _FakeImage:
    def __init__(self, url: str, rank: int = 0) -> None:
        self.url = url
        self.rank = rank


class _FakeProduct:
    def __init__(self, handle: str, title: str, thumbnail: str, images: list) -> None:
        self.id = "prod_test"
        self.handle = handle
        self.title = title
        self.thumbnail = thumbnail
        self.images = images
        self.variants = []
        self.categories = []
        self.tags = []


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    """ToolContext minimal con un session_key apuntando a tmp_path/<session_id>.

    El _append_intent escribe en `WORKSPACE_VAULT_DIR/<session_key>/metadata.json`.
    """
    session_key = "wa_test_a56bfaa9"
    return ToolContext(
        session_key=session_key,
        channel="whatsapp",
        chat_id=session_key,
    )


@pytest.fixture
def fake_catalog():
    images = [
        _FakeImage("https://assets.hubara.com.co/img1.webp", rank=0),
        _FakeImage("https://assets.hubara.com.co/img2.webp", rank=1),
        _FakeImage("https://assets.hubara.com.co/img3.webp", rank=2),
        _FakeImage("https://assets.hubara.com.co/img4.webp", rank=3),
        _FakeImage("https://assets.hubara.com.co/img5.webp", rank=4),
    ]
    return _FakeCatalog(
        _FakeProduct(
            handle="sacrificio-de-amor",
            title="Sacrificio de Amor",
            thumbnail="https://assets.hubara.com.co/img1.webp",
            images=images,
        )
    )


# =============================================================================
# Bug #2: send_cta_url DEBE bloquear /products/*
# =============================================================================


def _decode(json_str: str) -> dict:
    import json

    return json.loads(json_str)


@pytest.mark.asyncio
async def test_send_cta_url_blocks_product_detail_url(ctx, tmp_path, monkeypatch):
    """Si el LLM intenta mandar al cliente a /products/<handle>, la tool
    debe rechazar con `url_blocked_pattern` (no `url_not_whitelisted`)
    para distinguir que es un bloqueo intencional y guiar al LLM a la tool
    correcta."""
    monkeypatch.setenv("WORKSPACE_VAULT_DIR", str(tmp_path))
    tool = SendCTAUrlTool(workspace=str(tmp_path))
    result = _decode(await tool.execute_with_context(
        ctx,
        url="https://hubara.com.co/products/sacrificio-de-amor",
        button_text="Ver fotos",
        body_text="Más fotos acá",
    ))
    assert result["queued"] is False
    assert result["error"] == "url_blocked_pattern"
    assert "present_product_gallery" in result["message"]


@pytest.mark.asyncio
async def test_send_cta_url_blocks_checkout(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_VAULT_DIR", str(tmp_path))
    tool = SendCTAUrlTool(workspace=str(tmp_path))
    result = _decode(await tool.execute_with_context(
        ctx,
        url="https://hubara.com.co/checkout/abc",
        button_text="Pagar",
        body_text="Completá el pago",
    ))
    assert result["queued"] is False
    assert result["error"] == "url_blocked_pattern"


@pytest.mark.asyncio
async def test_send_cta_url_allows_instagram(ctx, tmp_path, monkeypatch):
    """Whitelist sigue permitiendo Instagram (browsing intent)."""
    monkeypatch.setenv("WORKSPACE_VAULT_DIR", str(tmp_path))
    # _append_intent toca WORKSPACE_VAULT_DIR; redirect al tmp via config
    # `_isolate_vault_dir` (autouse en tests/conftest.py) ya redirigió
    # WORKSPACE_VAULT_DIR a `tmp_path/isolated_vault` en TODOS los módulos.
    # Solo necesitamos sembrar metadata.json bajo ese path.
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")
    tool = SendCTAUrlTool(workspace=str(tmp_path))
    result = _decode(await tool.execute_with_context(
        ctx,
        url="https://www.instagram.com/hubara.com.co",
        button_text="Ver Instagram",
        body_text="Mirá nuestro feed",
    ))
    assert result["queued"] is True


# =============================================================================
# Bug #2 fix: present_product_gallery
# =============================================================================


@pytest.mark.asyncio
async def test_gallery_skips_first_image_by_default(ctx, tmp_path, monkeypatch, fake_catalog):
    """`skip_first=True` (default) — la idea es que el cliente YA vio la
    portada vía present_product_detail."""
    # `_isolate_vault_dir` (autouse en tests/conftest.py) ya redirigió
    # WORKSPACE_VAULT_DIR a `tmp_path/isolated_vault` en TODOS los módulos.
    # Solo necesitamos sembrar metadata.json bajo ese path.
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")

    tool = PresentProductGalleryTool(workspace=str(tmp_path), catalog=fake_catalog)
    result = _decode(await tool.execute_with_context(
        ctx,
        handle="sacrificio-de-amor",
        max_images=3,
    ))
    assert result["queued"] is True
    assert result["count"] == 3
    # Verificar que el intent quedó en metadata
    import json

    data = json.loads(
        (tmp_path / "isolated_vault" / ctx.session_key / "metadata.json").read_text(encoding="utf-8")
    )
    intents = data["pending_ui_intents"]
    assert len(intents) == 1
    intent = intents[0]
    assert intent["kind"] == "product_gallery"
    # Default skip_first=True → primera imagen NO incluida
    urls = intent["params"]["image_urls"]
    assert "https://assets.hubara.com.co/img1.webp" not in urls
    assert len(urls) == 3  # img2, img3, img4


@pytest.mark.asyncio
async def test_gallery_caps_at_4_images(ctx, tmp_path, monkeypatch, fake_catalog):
    """max_images está cap a 4 — pedir más se trunca para no spamear."""
    # `_isolate_vault_dir` (autouse en tests/conftest.py) ya redirigió
    # WORKSPACE_VAULT_DIR a `tmp_path/isolated_vault` en TODOS los módulos.
    # Solo necesitamos sembrar metadata.json bajo ese path.
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")

    tool = PresentProductGalleryTool(workspace=str(tmp_path), catalog=fake_catalog)
    result = _decode(await tool.execute_with_context(
        ctx,
        handle="sacrificio-de-amor",
        max_images=99,  # extremo
    ))
    assert result["queued"] is True
    assert result["count"] <= 4


@pytest.mark.asyncio
async def test_gallery_no_additional_images_returns_error(ctx, tmp_path, monkeypatch):
    """Producto sin imágenes adicionales: la tool falla cleanly — el LLM
    debe continuar en texto, no asumir que mandó algo."""
    # `_isolate_vault_dir` (autouse en tests/conftest.py) ya redirigió
    # WORKSPACE_VAULT_DIR a `tmp_path/isolated_vault` en TODOS los módulos.
    # Solo necesitamos sembrar metadata.json bajo ese path.
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")

    one_image_product = _FakeProduct(
        handle="one-pic",
        title="Solo una foto",
        thumbnail="https://assets.hubara.com.co/only.webp",
        images=[_FakeImage("https://assets.hubara.com.co/only.webp")],
    )
    catalog = _FakeCatalog(one_image_product)
    tool = PresentProductGalleryTool(workspace=str(tmp_path), catalog=catalog)
    result = _decode(await tool.execute_with_context(
        ctx,
        handle="one-pic",
        skip_first=True,
    ))
    assert result["queued"] is False
    assert result["error"] == "no_additional_images"


# =============================================================================
# Bug #3 fix: send_quick_replies
# =============================================================================


@pytest.mark.asyncio
async def test_quick_replies_enqueues_intent(ctx, tmp_path, monkeypatch):
    # `_isolate_vault_dir` (autouse en tests/conftest.py) ya redirigió
    # WORKSPACE_VAULT_DIR a `tmp_path/isolated_vault` en TODOS los módulos.
    # Solo necesitamos sembrar metadata.json bajo ese path.
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")

    tool = SendQuickRepliesTool(workspace=str(tmp_path))
    result = _decode(await tool.execute_with_context(
        ctx,
        body="¿Por dónde te ayudo?",
        buttons=[
            {"id": "catalog.browse", "title": "Ver catálogo"},
            {"id": "catalog.by_scent", "title": "Por aroma 🌿"},
            {"id": "help.advice", "title": "Asesoría"},
        ],
    ))
    assert result["queued"] is True
    assert result["count"] == 3

    import json

    data = json.loads(
        (tmp_path / "isolated_vault" / ctx.session_key / "metadata.json").read_text(encoding="utf-8")
    )
    intents = data["pending_ui_intents"]
    assert len(intents) == 1
    assert intents[0]["kind"] == "quick_replies"
    assert len(intents[0]["params"]["buttons"]) == 3


@pytest.mark.asyncio
async def test_quick_replies_caps_at_3_buttons(ctx, tmp_path, monkeypatch):
    """Meta limit: 3 reply buttons max — extras se truncan."""
    # `_isolate_vault_dir` (autouse en tests/conftest.py) ya redirigió
    # WORKSPACE_VAULT_DIR a `tmp_path/isolated_vault` en TODOS los módulos.
    # Solo necesitamos sembrar metadata.json bajo ese path.
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")

    tool = SendQuickRepliesTool(workspace=str(tmp_path))
    result = _decode(await tool.execute_with_context(
        ctx,
        body="Elige uno",
        buttons=[
            {"id": "a", "title": "Uno"},
            {"id": "b", "title": "Dos"},
            {"id": "c", "title": "Tres"},
            {"id": "d", "title": "Cuatro"},  # se debería truncar
            {"id": "e", "title": "Cinco"},
        ],
    ))
    assert result["queued"] is True
    assert result["count"] == 3


@pytest.mark.asyncio
async def test_quick_replies_rejects_empty_buttons(ctx, tmp_path, monkeypatch):
    # `_isolate_vault_dir` (autouse en tests/conftest.py) ya redirigió
    # WORKSPACE_VAULT_DIR a `tmp_path/isolated_vault` en TODOS los módulos.
    # Solo necesitamos sembrar metadata.json bajo ese path.
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")

    tool = SendQuickRepliesTool(workspace=str(tmp_path))
    result = _decode(await tool.execute_with_context(
        ctx,
        body="?",
        buttons=[{"id": "", "title": ""}],  # vacíos
    ))
    assert result["queued"] is False
    assert result["error"] == "no_valid_buttons"


# Registration in worker: validado por tests/architecture/test_worker_*
# (gate global que asegura que toda tool definida en src/plugins/*/tools/
# está registrada vía register_tool_extension en el worker correspondiente).
