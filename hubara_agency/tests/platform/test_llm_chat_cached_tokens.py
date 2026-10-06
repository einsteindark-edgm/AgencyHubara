"""`llm_chat` conserva los tokens que el proveedor sirvió desde su caché.

Verificado en prod (2026-10-06, worker de ventas → proxy LiteLLM → DeepSeek): la
respuesta trae `prompt_tokens_details.cached_tokens` (0 en la primera llamada,
5.760 de 5.936 en la repetida), pero el proveedor de exoclaw copiaba solo
prompt/completion/total → el costo por episodio cobraba TODO el input a precio
sin caché (US$0,15/M en vez de US$0,003/M) y Ads mostraba ~3x el costo real.
"""
from __future__ import annotations

import litellm
import pytest

import exoclaw_provider_litellm.provider as provider_module
from exoclaw_temporal.activities.llm import llm_chat
from exoclaw_temporal.config import LLMChatInput, LLMConfig


def _response(**usage: object) -> litellm.ModelResponse:
    return litellm.ModelResponse(
        choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hola"}}],
        usage=litellm.Usage(**usage),
    )


async def _chat(monkeypatch: pytest.MonkeyPatch, response: litellm.ModelResponse) -> dict:
    async def fake_acompletion(**_kwargs: object) -> litellm.ModelResponse:
        return response

    monkeypatch.setattr(provider_module, "acompletion", fake_acompletion)
    out = await llm_chat(
        LLMChatInput(messages=[{"role": "user", "content": "hola"}], llm=LLMConfig(model="deepseek/deepseek-v4-flash"))
    )
    return out.usage or {}


@pytest.mark.asyncio
async def test_cached_prompt_tokens_survive_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = await _chat(
        monkeypatch,
        _response(prompt_tokens=5936, completion_tokens=1, total_tokens=5937, prompt_tokens_details={"cached_tokens": 5760}),
    )
    assert usage["prompt_tokens"] == 5936
    assert usage.get("cached_tokens") == 5760


@pytest.mark.asyncio
async def test_deepseek_native_hit_field_counts_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin `prompt_tokens_details` (otra versión de litellm), el campo nativo
    de DeepSeek."""
    usage = await _chat(
        monkeypatch,
        _response(prompt_tokens=5936, completion_tokens=1, total_tokens=5937, prompt_cache_hit_tokens=5760),
    )
    assert usage.get("cached_tokens") == 5760


@pytest.mark.asyncio
async def test_no_cache_reports_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = await _chat(monkeypatch, _response(prompt_tokens=100, completion_tokens=1, total_tokens=101))
    assert usage.get("cached_tokens", 0) == 0
