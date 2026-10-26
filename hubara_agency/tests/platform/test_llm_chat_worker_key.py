"""`llm_chat` llama al proxy con la llave del worker: el payload no la trae.

Lo que cruza el borde workflow↔activity queda en la historia de Temporal Cloud
(guard: ``tests/plugins/test_llm_key_not_in_temporal_history.py``), así que el
``LLMConfig`` de hubara no lleva la llave: la lee ``llm_chat`` del entorno del
worker al llamar, para el modelo principal y para el respaldo.

Se prueba contra el proxy falso por HTTP de verdad: lo que se asierta es el
``Authorization`` que sale por el cable.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from structlog.testing import capture_logs

from exoclaw_temporal.activities.llm import llm_chat
from exoclaw_temporal.config import LLMChatInput, LLMConfig
from src.platform.registries import build_default_llm_config
from tests.platform.test_llm_chat_deadline_fallback import _FakeProxy, _no_proxy_env  # noqa: F401 — fixture

#: La llave del worker en estas pruebas. Nunca es una llave real.
_WORKER_KEY = "sk-worker-not-a-real-key-41d2"


def _turn(llm: LLMConfig) -> LLMChatInput:
    return LLMChatInput(messages=[{"role": "user", "content": "hola, ¿tienen velas?"}], llm=llm)


def _calls_without(proxy: _FakeProxy, key: str) -> int:
    return sum(1 for header in proxy.authorizations if header != f"Bearer {key}")


@pytest.mark.usefixtures("_no_proxy_env")
async def test_llm_chat_calls_the_proxy_with_the_worker_key_also_for_the_backup(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", _WORKER_KEY)
    proxy = _FakeProxy({"deepseek-v4-flash": "error", "gemini-backup": "ok"})
    llm = replace(build_default_llm_config(), api_base=proxy.base_url, call_deadline_s=1.0, fallback_deadline_s=1.0)
    try:
        out = await asyncio.wait_for(llm_chat(_turn(llm)), timeout=15)
    finally:
        proxy.shutdown()

    assert out.content == "respuesta de gemini-backup"
    assert proxy.models() == ["deepseek-v4-flash", "gemini-backup"]
    assert _calls_without(proxy, _WORKER_KEY) == 0, (
        f"{_calls_without(proxy, _WORKER_KEY)} de {len(proxy.authorizations)} llamadas sin la llave del worker"
    )


@pytest.mark.usefixtures("_no_proxy_env")
async def test_an_input_scheduled_before_the_deploy_still_calls_with_its_own_key(monkeypatch) -> None:
    """Un reintento de un `llm_chat` agendado antes del deploy trae la llave en
    el payload y no conoce `api_key_env`: llama con esa llave."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", _WORKER_KEY)
    proxy = _FakeProxy({"deepseek-v4-flash": "ok"})
    llm = LLMConfig(model="deepseek/deepseek-v4-flash", api_key="sk-payload-not-a-real-key", api_base=proxy.base_url)
    try:
        await asyncio.wait_for(llm_chat(_turn(llm)), timeout=15)
    finally:
        proxy.shutdown()

    assert proxy.authorizations == ["Bearer sk-payload-not-a-real-key"]


@pytest.mark.usefixtures("_no_proxy_env")
async def test_a_worker_without_the_named_variable_says_so(monkeypatch) -> None:
    """Sin la variable el principal sale sin llave (el proxy no la valida), pero
    el respaldo (`litellm_proxy/`) falla en el cliente si el proceso tampoco
    tiene `OPENAI_API_KEY`: el worker mal configurado lo avisa con el nombre de
    la variable."""
    monkeypatch.delenv("HUBARA_TEST_LLM_KEY", raising=False)
    proxy = _FakeProxy({"deepseek-v4-flash": "ok"})
    llm = LLMConfig(model="deepseek/deepseek-v4-flash", api_key_env="HUBARA_TEST_LLM_KEY", api_base=proxy.base_url)
    try:
        with capture_logs() as logs:
            await asyncio.wait_for(llm_chat(_turn(llm)), timeout=15)
    finally:
        proxy.shutdown()

    missing = [e for e in logs if e.get("event") == "llm_api_key_missing"]
    assert [(e["log_level"], e["env"]) for e in missing] == [("warning", "HUBARA_TEST_LLM_KEY")]
