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


def _response(
    *,
    cost: float | None = None,
    usage: tuple[int, int] | None = None,
    audio_tokens: int | None = None,
    text: str = _TEXT,
) -> Any:
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
    if cost is not None:
        response._hidden_params = {"response_cost": cost}
    if usage is not None:
        details = SimpleNamespace(audio_tokens=audio_tokens) if audio_tokens is not None else None
        response.usage = SimpleNamespace(
            prompt_tokens=usage[0], completion_tokens=usage[1], prompt_tokens_details=details
        )
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


# ── duración: Gemini cuenta el audio a 32 tokens por segundo ──────────────────
#
# "32 tokens per second of audio (1 minute = 1,920 tokens)"
# (ai.google.dev/gemini-api/docs/audio, revisado 2026-10-05). Antes se dividía
# por 25: una nota de ~47 s daba "más de 60 s" y el bot le contestaba al cliente
# que el audio era muy largo, sin leerlo.

_PROMPT_TEXT_TOKENS = 70  # la instrucción en español que acompaña al audio


@pytest.mark.asyncio
async def test_a_47_second_voice_note_is_not_rejected_as_too_long(monkeypatch) -> None:
    audio = 47 * 32
    result = await _transcribe(
        monkeypatch, _response(usage=(audio + _PROMPT_TEXT_TOKENS, 120), audio_tokens=audio)
    )

    assert result.ok, result.error
    assert result.duration_seconds == pytest.approx(47.0)


@pytest.mark.asyncio
async def test_without_the_audio_breakdown_the_duration_discounts_the_prompt(monkeypatch) -> None:
    """Si el proxy no separa los tokens de audio, se descuenta la instrucción."""
    result = await _transcribe(monkeypatch, _response(usage=(47 * 32 + _PROMPT_TEXT_TOKENS, 120)))

    assert result.ok, result.error
    assert result.duration_seconds == pytest.approx(47.0, abs=1.0)


@pytest.mark.asyncio
async def test_a_voice_note_longer_than_a_minute_is_still_rejected(monkeypatch) -> None:
    audio = 61 * 32
    result = await _transcribe(
        monkeypatch, _response(usage=(audio + _PROMPT_TEXT_TOKENS, 300), audio_tokens=audio)
    )

    assert (result.ok, result.error) == (False, "too_long")


# ── lo que Google cobró aunque no salga texto útil ─────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("text, error", [("", "empty"), ("[INAUDIBLE]", "inaudible"), ("a", "too_short")])
async def test_a_voice_note_without_usable_text_still_reports_what_it_cost(monkeypatch, text, error) -> None:
    """Google cobra la llamada aunque devuelva vacío o "inaudible": el costo
    tiene que viajar en el resultado para cargarse a la conversación."""
    monkeypatch.setenv("OPENLIT_PRICING_JSON", str(PRICING))

    result = await _transcribe(monkeypatch, _response(usage=(800, 2), text=text))

    assert (result.ok, result.error) == (False, error)
    assert result.cost_usd_estimate == pytest.approx(800 * 0.30 / 1e6 + 2 * 0.40 / 1e6)


# ── el costo va a la conversación (como LLM, WhatsApp, Jev e imágenes) ────────


def test_the_cost_of_a_voice_note_goes_to_the_conversation(tmp_path: Path) -> None:
    import json

    from src.platform.audio.costs import record_audio_cost

    sid = "wa_100000000001"
    path = tmp_path / sid / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"episodes": [{"episode_id": "ep_001", "closed_at_ms": None}]}), encoding="utf-8")

    assert record_audio_cost(sid, 0.000256, calls=1, vault_dir=tmp_path) is True
    assert record_audio_cost(sid, 0.0001, calls=1, vault_dir=tmp_path) is True

    episode = json.loads(path.read_text(encoding="utf-8"))["episodes"][0]
    assert episode["audio_usage"] == {"calls": 2, "cost_usd_micros": 356}
