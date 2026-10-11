"""Contrato del lector de color de una foto del catálogo (`PhotoColorPort`).

Caso del 2026-10-09: el bot mandó la foto de Encanto Silvestre (una ardilla
café) y le ofreció 3 colores que no eran el de la foto; el cliente citó la
foto («¿no viene en este color?») y nadie sabía de qué color era. El lector ve
la foto UNA vez y dice cuál de los colores del producto es el de la vela.

Invariantes (la MISMA suite contra el fake y el adaptador real, con la
respuesta del modelo grabada):
* el color, si viene, es uno de la paleta del producto, con su nombre del
  catálogo (no el que escribió el modelo);
* `None` con `ok=True` = la foto no permite decirlo (varias velas de colores
  distintos, un color que no está en la lista): se guarda y no se vuelve a
  preguntar;
* nunca lanza: error del proveedor o respuesta rara → `ok=False` (se vuelve a
  intentar otro día).
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from src.platform.vision import photo_color
from src.platform.vision.photo_color import (
    FakePhotoColorAdapter,
    LiteLLMPhotoColorAdapter,
    NullPhotoColorAdapter,
)


def _image(color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (60, 40), color).save(out, "PNG")
    return out.getvalue()


BROWN = _image((120, 80, 50))
PALETTE = ["gris", "Amarillo", "verde", "Café", "Blanco"]


def _answering(monkeypatch: pytest.MonkeyPatch, content: str | None = None, error: Exception | None = None) -> list:
    calls: list[dict[str, Any]] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if error is not None:
            raise error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr(photo_color.litellm, "acompletion", fake_acompletion)
    return calls


def _litellm(monkeypatch: pytest.MonkeyPatch, answer: dict, expected: str | None) -> LiteLLMPhotoColorAdapter:
    _answering(monkeypatch, json.dumps(answer))
    return LiteLLMPhotoColorAdapter(model="litellm_proxy/gemini-photo-match", api_base="http://proxy", api_key="k")


def _fake(monkeypatch: pytest.MonkeyPatch, answer: dict, expected: str | None) -> FakePhotoColorAdapter:
    return FakePhotoColorAdapter({BROWN: expected})


ADAPTERS = {"fake": _fake, "litellm": _litellm}


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_it_names_the_color_of_the_candle_with_the_catalog_name(name: str, monkeypatch) -> None:
    adapter = ADAPTERS[name](monkeypatch, {"color": "café"}, "Café")

    pick = await adapter.pick_color(BROWN, "image/png", title="Encanto Silvestre", palette=PALETTE)

    assert pick.ok and pick.color == "Café"


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_none_when_the_photo_does_not_tell(name: str, monkeypatch) -> None:
    adapter = ADAPTERS[name](monkeypatch, {"color": None}, None)

    pick = await adapter.pick_color(BROWN, "image/png", title="Trilogía del Terror", palette=PALETTE)

    assert pick.ok and pick.color is None


async def test_a_color_outside_the_palette_is_none_not_an_invented_color(monkeypatch) -> None:
    adapter = _litellm(monkeypatch, {"color": "fucsia"}, None)

    pick = await adapter.pick_color(BROWN, "image/png", title="Encanto Silvestre", palette=PALETTE)

    assert pick.ok and pick.color is None


async def test_the_model_sees_the_photo_the_product_and_the_closed_list(monkeypatch) -> None:
    calls = _answering(monkeypatch, json.dumps({"color": "Café"}))
    adapter = LiteLLMPhotoColorAdapter(model="litellm_proxy/gemini-photo-match", api_base="http://proxy", api_key="k")

    await adapter.pick_color(BROWN, "image/png", title="Encanto Silvestre", palette=PALETTE)

    [call] = calls
    text = call["messages"][0]["content"][0]["text"]
    assert "Encanto Silvestre" in text
    assert all(color in text for color in PALETTE)
    assert call["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert call["temperature"] == 0


async def test_provider_errors_and_odd_answers_never_raise(monkeypatch) -> None:
    _answering(monkeypatch, error=RuntimeError("timeout"))
    adapter = LiteLLMPhotoColorAdapter(api_base="http://proxy", api_key="k")
    failed = await adapter.pick_color(BROWN, "image/png", title="X", palette=PALETTE)

    _answering(monkeypatch, "no es json")
    odd = await adapter.pick_color(BROWN, "image/png", title="X", palette=PALETTE)

    assert failed.ok is False and failed.color is None
    assert odd.ok is False and odd.color is None


async def test_without_a_palette_there_is_nothing_to_ask(monkeypatch) -> None:
    calls = _answering(monkeypatch, json.dumps({"color": "Café"}))

    pick = await LiteLLMPhotoColorAdapter(api_base="http://proxy", api_key="k").pick_color(
        BROWN, "image/png", title="X", palette=[]
    )

    assert pick.ok and pick.color is None and calls == []


async def test_the_null_adapter_never_answers() -> None:
    pick = await NullPhotoColorAdapter().pick_color(BROWN, "image/png", title="X", palette=PALETTE)

    assert pick.ok is False
