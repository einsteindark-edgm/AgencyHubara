"""Lo que cuesta transcribir una nota de voz.

Antes se estimaba con una constante de US$0,000004 por segundo que suponía el
audio a US$0,10 / 1M: esa es la tarifa de TEXTO de gemini-2.5-flash-lite. Google
cobra el audio de ese modelo a US$0,30 / 1M (ai.google.dev/gemini-api/docs/pricing,
revisado 2026-10-05), así que cada nota costaba ~3x lo registrado. Ahora sale
igual que la visión: primero el costo que calcula el proxy; si no viene, tokens ×
la tabla de precios, con la entrada a la tarifa de audio del modelo.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.platform.audio import litellm_adapter
from src.platform.audio.dtos import TranscriptionRequest
from src.platform.audio.litellm_adapter import LiteLLMTranscriptionAdapter

PRICING = Path(__file__).resolve().parents[2] / "deploy" / "openlit" / "pricing.json"
_TEXT = "hola, quiero una vela de lavanda para regalar"


def _response(*, cost: float | None = None, usage: tuple[int, int] | None = None) -> Any:
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=_TEXT))])
    if cost is not None:
        response._hidden_params = {"response_cost": cost}
    if usage is not None:
        response.usage = SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1])
    return response


async def _transcribe(monkeypatch, response: Any):
    async def fetch(media_id: str) -> tuple[bytes, str]:
        return b"OggS nota de voz", "audio/ogg"

    async def completion(**kwargs: Any) -> Any:
        return response

    monkeypatch.setattr(litellm_adapter, "fetch_media_bytes", fetch)
    monkeypatch.setattr(litellm_adapter.litellm, "acompletion", completion)
    adapter = LiteLLMTranscriptionAdapter(
        model="litellm_proxy/gemini-multimodal", api_base="http://proxy", api_key="k"
    )
    return await adapter.transcribe(TranscriptionRequest(media_id="mid-1", mime_type="audio/ogg"))


@pytest.mark.asyncio
async def test_a_voice_note_is_charged_at_the_audio_rate(monkeypatch) -> None:
    """~25 s de audio: 800 tokens de entrada a US$0,30 / 1M + 40 de salida a US$0,40 / 1M."""
    monkeypatch.setenv("OPENLIT_PRICING_JSON", str(PRICING))

    result = await _transcribe(monkeypatch, _response(usage=(800, 40)))

    assert result.ok
    assert result.cost_usd_estimate == pytest.approx(800 * 0.30 / 1e6 + 40 * 0.40 / 1e6)


@pytest.mark.asyncio
async def test_a_voice_note_costs_what_the_proxy_says(monkeypatch) -> None:
    result = await _transcribe(monkeypatch, _response(cost=0.00031, usage=(800, 40)))

    assert result.cost_usd_estimate == pytest.approx(0.00031)


@pytest.mark.asyncio
async def test_a_voice_note_heard_by_the_successor_is_priced_as_the_successor(monkeypatch) -> None:
    """3.5 Flash-Lite cobra el audio igual que el texto: US$0,30 / 1M, salida US$2,50 / 1M."""
    monkeypatch.setenv("OPENLIT_PRICING_JSON", str(PRICING))
    response = _response(usage=(800, 40))
    response.model = "gemini-3.5-flash-lite"

    result = await _transcribe(monkeypatch, response)

    assert result.cost_usd_estimate == pytest.approx(800 * 0.30 / 1e6 + 40 * 2.50 / 1e6)
