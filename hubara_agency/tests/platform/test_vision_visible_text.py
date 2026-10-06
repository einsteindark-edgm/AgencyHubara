"""Visión: la foto del cliente llega con el texto que se ve en ella.

Caso 2026-09-28 (conversación del anuncio de Halloween): 3 de 5 fotos eran
capturas de NUESTRO catálogo de WhatsApp con el nombre, el precio y la URL en
grande. El prompt viejo (`TIPO:` / `DESCRIPCION:`) no pedía copiar el texto y
Gemini solo describió la forma: el bot negó productos que sí tenemos. Con un
prompt JSON por campo el MISMO modelo (gemini-2.5-flash-lite) copió nombre,
precio y URL en 15 de 15 capturas (investigación 2026-09-29).

Estos tests fijan el contrato del adaptador: pide JSON al proxy y devuelve el
texto visible por campo (`VisionResult.visible_text`), sin romper la lectura de
una respuesta en el formato viejo.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.platform.vision import litellm_adapter
from src.platform.vision.dtos import (
    VISION_KIND_OTHER,
    VISION_KIND_PAYMENT_RECEIPT,
    VISION_KIND_PRODUCT_PHOTO,
    VisionRequest,
)
from src.platform.vision.litellm_adapter import LiteLLMVisionAdapter

_CATALOG_SCREENSHOT = {
    "tipo": "foto_producto",
    "es_captura_de_pantalla": True,
    "pantalla": "catálogo de WhatsApp (Detalles)",
    "texto_visible": {
        "nombre_producto": "Sacrificio de Amor",
        "precio": "COP 24,000",
        "url": "hubara.com.co/products/sacrificio-de-amor",
        "sku": None,
        "otros": ["Añadir a la solicitud de pedido", "  Detalles  "],
    },
    "descripcion": "vela gris con una cruz dorada y un rostro en relieve",
}


def _answer(content: str, calls: list[dict[str, Any]]):
    async def fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    return fake_acompletion


async def _describe(monkeypatch: pytest.MonkeyPatch, content: str) -> tuple[Any, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(litellm_adapter.litellm, "acompletion", _answer(content, calls))
    adapter = LiteLLMVisionAdapter(model="litellm_proxy/gemini-multimodal", api_base="http://proxy", api_key="k")
    return await adapter.describe_image(b"\xff\xd8jpeg", "image/jpeg"), calls


@pytest.mark.asyncio
async def test_a_screenshot_of_our_catalog_comes_with_the_text_it_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    result, calls = await _describe(monkeypatch, json.dumps(_CATALOG_SCREENSHOT, ensure_ascii=False))

    assert result.ok and result.kind == VISION_KIND_PRODUCT_PHOTO
    assert result.description == "vela gris con una cruz dorada y un rostro en relieve"
    assert result.is_screenshot is True
    text = result.visible_text
    assert text is not None
    assert text.product_name == "Sacrificio de Amor"
    assert text.price == "COP 24,000"
    assert text.url == "hubara.com.co/products/sacrificio-de-amor"
    assert text.sku is None
    assert text.other == ("Añadir a la solicitud de pedido", "Detalles")
    # El proxy recibe el pedido de JSON (Gemini: responseMimeType JSON).
    assert calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_json_inside_a_code_fence_is_read_too(monkeypatch: pytest.MonkeyPatch) -> None:
    fenced = "```json\n" + json.dumps(_CATALOG_SCREENSHOT, ensure_ascii=False) + "\n```"

    result, _ = await _describe(monkeypatch, fenced)

    assert result.visible_text is not None and result.visible_text.product_name == "Sacrificio de Amor"


@pytest.mark.asyncio
async def test_empty_or_null_fields_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = {
        "tipo": "foto_producto",
        "es_captura_de_pantalla": False,
        "texto_visible": {"nombre_producto": "null", "precio": "", "url": None, "sku": " ", "otros": [None, ""]},
        "descripcion": "vela lila con pájaros en una rama",
    }

    result, _ = await _describe(monkeypatch, json.dumps(answer))

    text = result.visible_text
    assert text is not None
    assert (text.product_name, text.price, text.url, text.sku, text.other) == (None, None, None, None, ())
    assert result.is_screenshot is False


@pytest.mark.asyncio
async def test_a_payment_receipt_keeps_its_amount_in_the_description(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = {
        "tipo": "comprobante_pago",
        "es_captura_de_pantalla": True,
        "texto_visible": {"nombre_producto": None, "precio": "$34.000", "url": None, "sku": None, "otros": []},
        "descripcion": "transferencia Nequi por $34.000, referencia M1234",
    }

    result, _ = await _describe(monkeypatch, json.dumps(answer))

    assert result.kind == VISION_KIND_PAYMENT_RECEIPT and result.is_payment_receipt
    assert result.description == "transferencia Nequi por $34.000, referencia M1234"


@pytest.mark.asyncio
async def test_an_unknown_kind_is_other(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = {**_CATALOG_SCREENSHOT, "tipo": "meme"}

    result, _ = await _describe(monkeypatch, json.dumps(answer, ensure_ascii=False))

    assert result.kind == VISION_KIND_OTHER and not result.is_payment_receipt


@pytest.mark.asyncio
async def test_without_a_description_the_visible_name_describes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nunca el JSON crudo en la conversación: sin `descripcion`, lo que se lee."""
    answer = {**_CATALOG_SCREENSHOT, "descripcion": None}

    result, _ = await _describe(monkeypatch, json.dumps(answer, ensure_ascii=False))

    assert result.description == "imagen con el texto «Sacrificio de Amor»"


@pytest.mark.asyncio
async def test_an_answer_in_the_old_format_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = await _describe(monkeypatch, "TIPO: foto_producto\nDESCRIPCION: vela beige y plato lila")

    assert result.ok and result.kind == VISION_KIND_PRODUCT_PHOTO
    assert result.description == "vela beige y plato lila"
    assert result.visible_text is None


@pytest.mark.asyncio
async def test_an_answer_without_shape_is_kept_as_the_description(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = await _describe(monkeypatch, "una vela bonita")

    assert result.ok and result.kind == VISION_KIND_OTHER
    assert result.description == "una vela bonita" and result.visible_text is None


@pytest.mark.asyncio
async def test_describe_by_media_id_reads_the_same_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetched(media_id: str) -> tuple[bytes, str]:
        assert media_id == "m1"
        return b"\xff\xd8jpeg", "image/jpeg"

    monkeypatch.setattr(litellm_adapter, "fetch_media_bytes", fetched)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        litellm_adapter.litellm, "acompletion", _answer(json.dumps(_CATALOG_SCREENSHOT, ensure_ascii=False), calls)
    )
    adapter = LiteLLMVisionAdapter(model="litellm_proxy/gemini-multimodal", api_base="http://proxy", api_key="k")

    result = await adapter.describe(VisionRequest(media_id="m1"))

    assert result.visible_text is not None and result.visible_text.url == "hubara.com.co/products/sacrificio-de-amor"


@pytest.mark.asyncio
async def test_every_vision_adapter_describes_bytes_already_in_memory() -> None:
    """El laboratorio describe la foto de su banco (no hay media_id de Meta):
    todo adaptador del puerto sabe describir bytes."""
    from src.platform.vision.composition import _FakeVisionAdapter, _NullVisionAdapter

    null = await _NullVisionAdapter().describe_image(b"x", "image/jpeg")
    receipt = await _FakeVisionAdapter().describe_image(b"comprobante", "image/jpeg")
    photo = await _FakeVisionAdapter().describe_image(b"vela", "image/jpeg")

    assert not null.ok and null.error == "no_provider_configured"
    assert receipt.ok and receipt.is_payment_receipt
    assert photo.ok and photo.kind == VISION_KIND_PRODUCT_PHOTO
