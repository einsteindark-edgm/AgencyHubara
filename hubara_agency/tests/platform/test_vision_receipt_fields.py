"""Visión: un comprobante de pago llega con sus datos por campo.

Tarjeta 33 del ciclo: para comparar el comprobante con el pedido hace falta el
monto, la fecha, la cuenta de destino y la referencia sueltos, no dentro de una
frase. De la cuenta de destino solo viajan los últimos 4 dígitos. Datos
inventados.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.platform.vision import litellm_adapter
from src.platform.vision.litellm_adapter import LiteLLMVisionAdapter

_RECEIPT = {
    "tipo": "comprobante_pago",
    "es_captura_de_pantalla": True,
    "texto_visible": {"nombre_producto": None, "precio": None, "url": None, "sku": None, "otros": []},
    "comprobante": {
        "monto": "$ 84.000,00",
        "fecha": "2026-11-12",
        "cuenta_destino": "300 111 2233",
        "referencia": "M0001",
    },
    "descripcion": "transferencia por $84.000, referencia M0001",
}


async def _describe(monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any] | str) -> tuple[Any, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    content = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)

    async def fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr(litellm_adapter.litellm, "acompletion", fake_acompletion)
    adapter = LiteLLMVisionAdapter(model="litellm_proxy/gemini-multimodal", api_base="http://proxy", api_key="k")
    return await adapter.describe_image(b"\xff\xd8jpeg", "image/jpeg"), calls


@pytest.mark.asyncio
async def test_a_receipt_comes_with_amount_date_and_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = await _describe(monkeypatch, _RECEIPT)

    assert result.is_payment_receipt
    receipt = result.receipt
    assert receipt is not None
    assert (receipt.amount, receipt.date, receipt.reference) == ("$ 84.000,00", "2026-11-12", "M0001")


@pytest.mark.asyncio
async def test_only_the_last_four_digits_of_the_destination_account_travel(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = await _describe(monkeypatch, _RECEIPT)

    assert result.receipt is not None and result.receipt.account == "2233"


@pytest.mark.asyncio
async def test_fields_the_model_could_not_read_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = {**_RECEIPT, "comprobante": {"monto": "$84.000", "fecha": "null", "cuenta_destino": None}}

    result, _ = await _describe(monkeypatch, answer)

    receipt = result.receipt
    assert receipt is not None
    assert (receipt.amount, receipt.date, receipt.account, receipt.reference) == ("$84.000", None, None, None)


@pytest.mark.asyncio
async def test_a_receipt_answered_without_the_block_has_no_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = {k: v for k, v in _RECEIPT.items() if k != "comprobante"}

    result, _ = await _describe(monkeypatch, answer)

    assert result.is_payment_receipt and result.receipt is None


@pytest.mark.asyncio
async def test_a_product_photo_never_carries_receipt_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = {**_RECEIPT, "tipo": "foto_producto"}

    result, _ = await _describe(monkeypatch, answer)

    assert result.receipt is None


@pytest.mark.asyncio
async def test_the_old_answer_format_still_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = await _describe(monkeypatch, "TIPO: comprobante_pago\nDESCRIPCION: transferencia por $84.000")

    assert result.is_payment_receipt and result.receipt is None


@pytest.mark.asyncio
async def test_the_model_is_asked_for_the_receipt_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _, calls = await _describe(monkeypatch, _RECEIPT)

    prompt = json.dumps(calls[0]["messages"], ensure_ascii=False)
    for field in ("monto", "fecha", "cuenta_destino", "referencia", "AAAA-MM-DD"):
        assert field in prompt
