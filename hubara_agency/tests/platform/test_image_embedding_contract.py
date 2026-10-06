"""Contrato del puerto de embeddings de imagen (`ImageEmbeddingPort`).

Regla del ConnectorKit (docs/_sdk/07-connectorkit.md): la MISMA suite corre
contra el fake y contra el adaptador real (acá, con la respuesta del proxy
simulada). El puerto convierte una foto en un vector para buscar las fotos del
catálogo más parecidas a la del cliente.

Invariantes:
* nunca lanza: un error del proveedor, una respuesta rara o bytes que no son
  imagen → None (quien llama sigue sin la búsqueda por imagen);
* la misma imagen da el mismo vector, del largo declarado.
"""
from __future__ import annotations

import base64
import io
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from src.platform.vision import embeddings
from src.platform.vision.embeddings import (
    FakeImageEmbeddingAdapter,
    LiteLLMImageEmbeddingAdapter,
    NullImageEmbeddingAdapter,
)


def _image(color: tuple[int, int, int], fmt: str = "WEBP") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(out, fmt)
    return out.getvalue()


_RECORDED = [0.1] * 768


def _proxy(monkeypatch: pytest.MonkeyPatch, vector: Any = _RECORDED, error: Exception | None = None) -> list[dict]:
    calls: list[dict] = []

    async def fake_aembedding(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if error is not None:
            raise error
        return SimpleNamespace(data=[{"embedding": vector, "index": 0, "object": "embedding"}])

    monkeypatch.setattr(embeddings.litellm, "aembedding", fake_aembedding)
    return calls


def _litellm(monkeypatch: pytest.MonkeyPatch) -> LiteLLMImageEmbeddingAdapter:
    _proxy(monkeypatch)
    return LiteLLMImageEmbeddingAdapter(model="litellm_proxy/gemini-embedding", api_base="http://proxy", api_key="k")


ADAPTERS = {
    "fake": lambda monkeypatch: FakeImageEmbeddingAdapter(dimensions=768),
    "litellm": _litellm,
}


# ── El contrato: lo que vale para todos los adaptadores ─────────────────────


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_the_same_photo_gives_the_same_vector_of_the_declared_length(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = ADAPTERS[name](monkeypatch)
    photo = _image((200, 30, 30))

    first = await adapter.embed(photo, "image/webp")
    again = await adapter.embed(photo, "image/webp")

    assert first is not None and len(first) == 768
    assert all(isinstance(x, float) for x in first)
    assert first == again


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_bytes_that_are_not_an_image_give_none(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert await ADAPTERS[name](monkeypatch).embed(b"hola", "image/jpeg") is None


async def test_the_null_adapter_never_answers() -> None:
    assert await NullImageEmbeddingAdapter().embed(_image((1, 2, 3)), "image/jpeg") is None


# ── El adaptador real: transporte al proxy y fallas ────────────────────────


async def test_the_photo_reaches_the_proxy_as_a_jpeg_with_the_declared_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gemini-embedding-2` solo acepta PNG o JPEG: una foto WEBP del
    catálogo viaja en JPEG. El alias es del proxy (`litellm_proxy/…`)."""
    calls = _proxy(monkeypatch)
    adapter = LiteLLMImageEmbeddingAdapter(model="litellm_proxy/gemini-embedding", api_base="http://proxy", api_key="k")

    await adapter.embed(_image((0, 90, 200)), "image/webp")

    [call] = calls
    assert call["model"] == "litellm_proxy/gemini-embedding" and call["api_base"] == "http://proxy"
    assert call["dimensions"] == 768
    [data_uri] = call["input"]
    assert data_uri.startswith("data:image/jpeg;base64,")
    sent = Image.open(io.BytesIO(base64.b64decode(data_uri.split(",", 1)[1])))
    assert sent.format == "JPEG"


@pytest.mark.parametrize(
    ("vector", "error"),
    [
        (None, RuntimeError("proxy caído")),
        ([0.1] * 10, None),  # otro largo
        (["x"] * 768, None),  # no son números
        (None, None),
    ],
)
async def test_a_provider_failure_or_an_odd_answer_gives_none(
    vector: Any, error: Exception | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _proxy(monkeypatch, vector=vector, error=error)
    adapter = LiteLLMImageEmbeddingAdapter(model="litellm_proxy/gemini-embedding", api_base="http://proxy", api_key="k")

    assert await adapter.embed(_image((5, 5, 5)), "image/jpeg") is None


def test_the_factory_follows_the_vision_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin red en tests y en desarrollo: el mismo interruptor de la visión."""
    from src.platform.vision.composition import get_image_embedding_port

    for value, kind in (("fake", FakeImageEmbeddingAdapter), ("off", NullImageEmbeddingAdapter)):
        get_image_embedding_port.cache_clear()
        monkeypatch.setenv("IMAGE_VISION_PROVIDER", value)
        assert isinstance(get_image_embedding_port(), kind)
    get_image_embedding_port.cache_clear()
    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "auto")
    assert isinstance(get_image_embedding_port(), LiteLLMImageEmbeddingAdapter)
    get_image_embedding_port.cache_clear()
