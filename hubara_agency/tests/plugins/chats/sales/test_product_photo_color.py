"""El bot sabe de qué color es la vela de la foto que manda (caso 2026-10-09).

El bot mandó la foto de Encanto Silvestre (una ardilla café) y después ofreció
gris, amarillo y verde; el cliente citó la foto: «¿no viene en este color?».
Nadie sabía que la vela de la foto es café. `present_product_detail` pregunta
el color de la foto (lista cerrada = los colores del producto, una vez por
foto) y se lo dice al bot; el intent lo lleva para que la cita de esa foto lo
nombre después.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools import ui_intents
from src.plugins.chats.agent.sales.tools.ui_intents import PresentProductDetailTool

_PHOTO = "https://assets.hubara.com.co/ardilla-01M2GSP3ARRAN2TKRQSSQW2G6N.webp"


class _Product:
    id = "prod_ardilla"
    handle = "encanto-silvestre"
    title = "Encanto Silvestre"
    thumbnail = _PHOTO
    images: list = []
    variants: list = []
    options: dict = {}
    categories: list = []
    tags = ["Aroma: Caballero de la noche", "Color: gris", "Color: Amarillo", "Color: verde", "Color: Café"]


class _Catalog:
    async def get_by_handle(self, handle: str):
        return _Product()


class _Colors:
    def __init__(self, color: str | None = "Café", *, delay: float = 0, error: Exception | None = None) -> None:
        self.color, self.delay, self.error = color, delay, error
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    async def __call__(self, url: str, title: str, palette) -> str | None:
        self.calls.append((url, title, tuple(palette)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.color


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key="wa_test_color", channel="whatsapp", chat_id="wa_test_color")


@pytest.fixture
def vault(tmp_path: Path, ctx: ToolContext) -> Path:
    vault = tmp_path / "isolated_vault"
    (vault / ctx.session_key).mkdir(parents=True, exist_ok=True)
    (vault / ctx.session_key / "metadata.json").write_text("{}", encoding="utf-8")
    return vault


def _intent(vault: Path, ctx: ToolContext) -> dict:
    data = json.loads((vault / ctx.session_key / "metadata.json").read_text(encoding="utf-8"))
    [intent] = data["pending_ui_intents"]
    return intent


@pytest.mark.asyncio
async def test_the_bot_is_told_the_color_of_the_candle_in_the_photo(ctx, vault) -> None:
    colors = _Colors("Café")
    tool = PresentProductDetailTool(workspace=str(vault), catalog=_Catalog(), photo_colors=colors)

    env = json.loads(await tool.execute_with_context(ctx, handle="encanto-silvestre"))

    assert env["queued"] is True
    assert env["photo_color"] == "Café"
    assert "«Café»" in env["summary"]
    assert _intent(vault, ctx)["params"]["color"] == "Café"
    # Lo pregunta por ESA foto y con la paleta del producto.
    assert colors.calls == [(_PHOTO, "Encanto Silvestre", ("gris", "Amarillo", "verde", "Café"))]


@pytest.mark.asyncio
async def test_a_photo_that_does_not_tell_its_color_says_nothing(ctx, vault) -> None:
    tool = PresentProductDetailTool(workspace=str(vault), catalog=_Catalog(), photo_colors=_Colors(None))

    env = json.loads(await tool.execute_with_context(ctx, handle="encanto-silvestre"))

    assert "photo_color" not in env and "color" not in _intent(vault, ctx)["params"]


@pytest.mark.asyncio
async def test_a_slow_or_broken_reading_never_holds_the_photo(ctx, vault, monkeypatch) -> None:
    monkeypatch.setattr(ui_intents, "PHOTO_COLOR_TIMEOUT_S", 0.05)
    slow = PresentProductDetailTool(workspace=str(vault), catalog=_Catalog(), photo_colors=_Colors(delay=1))
    broken = PresentProductDetailTool(workspace=str(vault), catalog=_Catalog(),
                                      photo_colors=_Colors(error=RuntimeError("proxy caído")))

    first = json.loads(await slow.execute_with_context(ctx, handle="encanto-silvestre"))
    second = json.loads(await broken.execute_with_context(ctx, handle="encanto-silvestre"))

    assert first["queued"] is True and "photo_color" not in first
    assert second["queued"] is True and "photo_color" not in second


@pytest.mark.asyncio
async def test_without_a_color_reader_the_photo_goes_as_always(ctx, vault) -> None:
    env = json.loads(await PresentProductDetailTool(workspace=str(vault), catalog=_Catalog())
                     .execute_with_context(ctx, handle="encanto-silvestre"))

    assert env["queued"] is True and "photo_color" not in env


@pytest.mark.asyncio
async def test_the_gallery_tells_the_color_of_each_photo(ctx, vault) -> None:
    from src.plugins.chats.agent.sales.tools.ui_intents import PresentProductGalleryTool

    other = "https://assets.hubara.com.co/ardilla-blanca-01M2GSP3ARRAN2TKRQSSQW2G6X.webp"

    class _TwoPhotos(_Product):
        images = [type("I", (), {"url": _PHOTO, "rank": 0})(), type("I", (), {"url": other, "rank": 1})()]

    class _Cat:
        async def get_by_handle(self, handle: str):
            return _TwoPhotos()

    async def colors(url: str, title: str, palette) -> str | None:
        return {_PHOTO: "Café", other: None}[url]

    tool = PresentProductGalleryTool(workspace=str(vault), catalog=_Cat(), photo_colors=colors)

    env = json.loads(await tool.execute_with_context(ctx, handle="encanto-silvestre", skip_first=False))

    images = _intent(vault, ctx)["params"]["images"]
    assert [img.get("color") for img in images] == ["Café", None]
    assert "foto 1: «Café»" in env["summary"]
