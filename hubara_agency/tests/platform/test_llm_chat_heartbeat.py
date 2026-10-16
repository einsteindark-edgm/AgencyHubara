"""`llm_chat` late mientras espera al modelo (auditoría de Temporal, 2026-10-15).

Una llamada al LLM tarda de segundos a minutos (`start_to_close` de 5 min). Sin
heartbeat, si el worker muere a mitad de la llamada (un deploy, la caja sin
memoria), Temporal se entera recién cuando vence el `start_to_close`: el cliente
espera 5 minutos la respuesta. Con heartbeat y `heartbeat_timeout`, el reintento
sale en segundos en otro worker, y la cancelación del apagado ordenado le llega.
"""
from __future__ import annotations

import asyncio

import litellm
import pytest
from temporalio.testing import ActivityEnvironment

import exoclaw_provider_litellm.provider as provider_module
import exoclaw_temporal.activities.llm as llm_module
from exoclaw_temporal.activities.llm import llm_chat
from exoclaw_temporal.config import LLMChatInput, LLMConfig


def _response() -> litellm.ModelResponse:
    return litellm.ModelResponse(
        choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hola"}}],
        usage=litellm.Usage(prompt_tokens=10, completion_tokens=1, total_tokens=11),
    )


@pytest.mark.asyncio
async def test_llm_chat_heartbeats_while_the_model_thinks(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow_acompletion(**_kwargs: object) -> litellm.ModelResponse:
        await asyncio.sleep(0.2)
        return _response()

    monkeypatch.setattr(provider_module, "acompletion", slow_acompletion)
    monkeypatch.setattr(llm_module, "_HEARTBEAT_EVERY_S", 0.02, raising=False)
    beats: list[tuple] = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *details: beats.append(details)

    out = await env.run(
        llm_chat,
        LLMChatInput(messages=[{"role": "user", "content": "hola"}], llm=LLMConfig(model="deepseek/deepseek-v4-flash")),
    )

    assert out.content == "hola"
    assert len(beats) >= 3, f"llm_chat no late mientras el modelo piensa: {len(beats)} heartbeats"
