"""Contrato del verificador de fotos (`PhotoMatchPort`).

Recibe la foto del cliente y los candidatos del catálogo (los más parecidos por
embedding; cada uno con hasta dos fotos del MISMO producto) y dice cuál es el
mismo diseño, o ninguno. Regla del ConnectorKit: la MISMA suite corre contra el
fake y contra el adaptador real (acá, con la respuesta del modelo grabada).

Invariantes:
* nunca lanza: error del proveedor, respuesta rara o un número fuera de la
  lista → `ok=False` y sin número (quien llama sigue sin identificar la foto);
* el número, si viene, es de la lista (1..N); `None` = ninguno es el mismo.
"""
from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from src.platform.vision import photo_match
from src.platform.vision.photo_match import (
    FakePhotoMatchAdapter,
    LiteLLMPhotoMatchAdapter,
    NullPhotoMatchAdapter,
)


def _image(color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (60, 40), color).save(out, "PNG")
    return out.getvalue()


RED, GREEN, BLUE, GRAY = _image((220, 0, 0)), _image((0, 200, 0)), _image((0, 0, 220)), _image((90, 90, 90))
CANDIDATES = [[BLUE], [RED, GREEN], [GRAY]]


def _answering(monkeypatch: pytest.MonkeyPatch, content: str | None = None, error: Exception | None = None) -> list:
    calls: list[dict[str, Any]] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if error is not None:
            raise error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr(photo_match.litellm, "acompletion", fake_acompletion)
    return calls


def _litellm(monkeypatch: pytest.MonkeyPatch, answer: dict) -> LiteLLMPhotoMatchAdapter:
    _answering(monkeypatch, json.dumps(answer))
    return LiteLLMPhotoMatchAdapter(model="litellm_proxy/gemini-photo-match", api_base="http://proxy", api_key="k")


ADAPTERS = {
    "fake": lambda monkeypatch, answer: FakePhotoMatchAdapter(),
    "litellm": _litellm,
}


# ── El contrato ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_it_names_the_candidate_with_the_same_design(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = ADAPTERS[name](monkeypatch, {"numero": 2, "motivo": "misma figura"})

    pick = await adapter.pick_same_design(RED, "image/png", CANDIDATES)

    assert pick.ok and pick.number == 2


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_none_when_no_candidate_is_the_same_design(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = ADAPTERS[name](monkeypatch, {"numero": None, "motivo": "ninguno"})

    pick = await adapter.pick_same_design(_image((250, 250, 0)), "image/png", CANDIDATES)

    assert pick.ok and pick.number is None


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_without_candidates_there_is_nothing_to_ask(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _answering(monkeypatch, json.dumps({"numero": 1}))
    adapter = ADAPTERS[name](monkeypatch, {"numero": 1}) if name == "fake" else LiteLLMPhotoMatchAdapter(
        model="litellm_proxy/gemini-photo-match", api_base="http://proxy", api_key="k"
    )

    pick = await adapter.pick_same_design(RED, "image/png", [])

    assert pick.ok and pick.number is None and calls == []


async def test_the_null_adapter_never_identifies() -> None:
    pick = await NullPhotoMatchAdapter().pick_same_design(RED, "image/png", CANDIDATES)

    assert not pick.ok and pick.number is None and pick.error == "no_provider_configured"


# ── El adaptador real ──────────────────────────────────────────────────────


async def test_the_model_sees_the_photo_and_one_sheet_with_the_numbered_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _answering(monkeypatch, json.dumps({"numero": 2, "motivo": "x"}))
    adapter = LiteLLMPhotoMatchAdapter(model="litellm_proxy/gemini-photo-match", api_base="http://proxy", api_key="k")

    await adapter.pick_same_design(RED, "image/png", CANDIDATES)

    [call] = calls
    assert call["model"] == "litellm_proxy/gemini-photo-match"
    assert call["response_format"] == {"type": "json_object"} and call["temperature"] == 0
    [message] = call["messages"]
    parts = message["content"]
    prompt = [p["text"] for p in parts if p["type"] == "text"][0]
    assert "3 productos candidatos" in prompt and "1 a 3" in prompt
    images = [p["image_url"]["url"] for p in parts if p["type"] == "image_url"]
    assert len(images) == 2
    sheet = Image.open(io.BytesIO(base64.b64decode(images[1].split(",", 1)[1])))
    assert sheet.format == "JPEG" and sheet.height == 3 * 270


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (json.dumps({"numero": 7}), None),  # no está en la lista
        (json.dumps({"numero": "dos"}), None),
        ("no sé", None),
        (None, RuntimeError("proxy caído")),
    ],
)
async def test_an_answer_that_cannot_be_trusted_is_not_a_match(
    content: str | None, error: Exception | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answering(monkeypatch, content, error)
    adapter = LiteLLMPhotoMatchAdapter(model="litellm_proxy/gemini-photo-match", api_base="http://proxy", api_key="k")

    pick = await adapter.pick_same_design(RED, "image/png", CANDIDATES)

    assert not pick.ok and pick.number is None and pick.error


def test_the_factory_follows_the_vision_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.platform.vision.composition import get_photo_match_port

    for value, kind in (("fake", FakePhotoMatchAdapter), ("off", NullPhotoMatchAdapter), ("auto", LiteLLMPhotoMatchAdapter)):
        get_photo_match_port.cache_clear()
        monkeypatch.setenv("IMAGE_VISION_PROVIDER", value)
        assert isinstance(get_photo_match_port(), kind)
    get_photo_match_port.cache_clear()
