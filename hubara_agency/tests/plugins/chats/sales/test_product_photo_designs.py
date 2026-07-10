"""Selección de diseño por label en las tools de fotos (ui_intents).

Caso real (sesión wa_573125671604): el cliente pidió "un leo"; la foto
`leo-*.webp` existía en el producto pero la tool solo sabía mandar el
thumbnail. Con `design=` el LLM manda LA foto de ese diseño, y la galería
etiqueta cada foto para que "esta me gusta" (reply) sea resoluble.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.ui_intents import (
    PresentProductDetailTool,
    PresentProductGalleryTool,
)

_ARIES = "https://assets.hubara.com.co/1.%20aries-01KW2SQMAPCTQD74M3HK8DQ3AB.webp"
_ACUARIO = "https://assets.hubara.com.co/Acuario-01KW2SQN75E2KRYVDPA2Z42MHM.webp"
_CANCER = "https://assets.hubara.com.co/cancer-01KW2SQP0Q0VFRJK20Y0N89KJ3.webp"
_LEO = "https://assets.hubara.com.co/leo-01KW2SQSD4RP0KSM9HTJ38QPEF.webp"


class _FakeImage:
    def __init__(self, url: str, rank: int = 0) -> None:
        self.url = url
        self.rank = rank


class _FakeProduct:
    def __init__(self) -> None:
        self.id = "prod_duo"
        self.handle = "duo-zodiacal"
        self.title = "Duo Zodiacal"
        self.thumbnail = _ARIES
        self.images = [
            _FakeImage(_ARIES, 0),
            _FakeImage(_ACUARIO, 1),
            _FakeImage(_CANCER, 2),
            _FakeImage(_LEO, 3),
        ]
        self.variants = []
        self.categories = []
        self.tags = []


class _FakeCatalog:
    def __init__(self) -> None:
        self._p = _FakeProduct()

    async def get_by_handle(self, handle: str):
        if handle != self._p.handle:
            from src.platform.catalog import ProductNotFoundError

            raise ProductNotFoundError(handle)
        return self._p


@pytest.fixture
def ctx() -> ToolContext:
    session_key = "wa_test_designs"
    return ToolContext(
        session_key=session_key, channel="whatsapp", chat_id=session_key
    )


@pytest.fixture
def vault(tmp_path: Path, ctx: ToolContext) -> Path:
    # `_isolate_vault_dir` (autouse) redirige WORKSPACE_VAULT_DIR a
    # tmp_path/isolated_vault — sembramos metadata.json ahí.
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")
    return vault


def _pending_intents(vault: Path, session_key: str) -> list[dict]:
    data = json.loads(
        (vault / session_key / "metadata.json").read_text(encoding="utf-8")
    )
    return data.get("pending_ui_intents", [])


@pytest.mark.asyncio
async def test_detail_with_design_sends_that_photo(ctx, tmp_path, vault):
    """`design="leo"` (case-insensitive) manda LA foto leo, no el thumbnail,
    y el caption nombra el diseño."""
    tool = PresentProductDetailTool(workspace=str(tmp_path), catalog=_FakeCatalog())
    result = json.loads(await tool.execute_with_context(
        ctx, handle="duo-zodiacal", design="leo",
    ))
    assert result["queued"] is True
    assert result["design"] == "Leo"
    (intent,) = _pending_intents(vault, ctx.session_key)
    assert intent["params"]["image_url"] == _LEO
    assert intent["params"]["design"] == "Leo"
    assert "Leo" in intent["params"]["caption"]


@pytest.mark.asyncio
async def test_detail_with_unknown_design_returns_closed_list(ctx, tmp_path, vault):
    """Diseño inexistente → error accionable con la lista cerrada (el LLM
    se auto-corrige en vez de inventar)."""
    tool = PresentProductDetailTool(workspace=str(tmp_path), catalog=_FakeCatalog())
    result = json.loads(await tool.execute_with_context(
        ctx, handle="duo-zodiacal", design="Virgo",
    ))
    assert result["queued"] is False
    assert result["error"] == "design_not_found"
    assert result["available_designs"] == ["Aries", "Acuario", "Cancer", "Leo"]
    assert _pending_intents(vault, ctx.session_key) == []


@pytest.mark.asyncio
async def test_gallery_labels_each_photo_and_reports_them(ctx, tmp_path, vault):
    """La galería lleva el label de cada foto en el intent y se los cuenta
    al LLM en el tool result (para que sepa QUÉ mandó)."""
    tool = PresentProductGalleryTool(workspace=str(tmp_path), catalog=_FakeCatalog())
    result = json.loads(await tool.execute_with_context(
        ctx, handle="duo-zodiacal", max_images=3,
    ))
    assert result["queued"] is True
    # skip_first=True → Acuario, Cancer, Leo
    assert result["sent_designs"] == ["Acuario", "Cancer", "Leo"]
    assert "Acuario" in result["summary"]
    (intent,) = _pending_intents(vault, ctx.session_key)
    images = intent["params"]["images"]
    assert [i["url"] for i in images] == [_ACUARIO, _CANCER, _LEO]
    assert [i["label"] for i in images] == ["Acuario", "Cancer", "Leo"]
    # Compat: image_urls sigue presente para el dispatch viejo
    assert intent["params"]["image_urls"] == [_ACUARIO, _CANCER, _LEO]
