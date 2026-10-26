"""`llm_chat` corta una llamada colgada y la repite con el respaldo, dentro del intento.

Segunda capa del incidente del 2026-10-15 01:30 UTC (DeepSeek saturado colgó las
llamadas; 26 de 28 intentos murieron a los 5 min). La primera capa es el
``timeout: 30`` del proxy (``test_llm_hang_falls_over.py``): alcanza porque
DeepSeek saturado manda una línea vacía cada 60 s y el timeout de litellm es por
lectura. Ese intervalo DeepSeek NO lo documenta: si lo baja por debajo de 30 s,
el proxy no corta nunca. Esta capa no depende de eso: un tope TOTAL en la
activity (``LLMConfig.call_deadline_s``) que, al vencer o si la llamada falla,
cierra la conexión y repite la MISMA petición con ``LLMConfig.fallback_model``
por el proxy (con su propio tope, ``fallback_deadline_s``).

Se prueba contra un proxy falso por HTTP de verdad (no un monkeypatch de
``acompletion``): lo que se asierta es lo que sale por el cable — el body del
respaldo y que la conexión colgada se cierra.
"""
from __future__ import annotations

import asyncio
import json
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from structlog.testing import capture_logs
from temporalio.testing import ActivityEnvironment

from exoclaw_temporal.activities.llm import llm_chat
from exoclaw_temporal.config import LLMChatInput, LLMConfig

_PRIMARY = "deepseek/deepseek-v4-flash"
_BACKUP = "litellm_proxy/gemini-backup"
#: Topes escalados: la prueba dura segundos.
_DEADLINE_S = 1.0
_BACKUP_DEADLINE_S = 1.0
_MESSAGES = [
    {"role": "system", "content": "Sos el asistente de la tienda."},
    {"role": "user", "content": "hola, ¿tienen velas?"},
]
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Busca en el catálogo.",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    }
]
_BAGGAGE = {"session.id": "wa_test", "whatsapp.number": "test", "episode.id": "ep-1"}


def _completion(model: str) -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 1,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": f"respuesta de {model}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
    ).encode()


class _FakeProxy:
    """Proxy LiteLLM falso. Cada alias se porta según ``modes[alias]``.

    ``ok`` contesta; ``error`` da 500; ``silencio`` no manda nada;
    ``lineas-vacias`` manda 200 y una línea vacía cada 0,1 s, sin JSON nunca.
    Registra cada body, su ``Authorization`` y si el cliente cerró la conexión
    colgada.
    """

    def __init__(self, modes: dict[str, str]) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.authorizations: list[str | None] = []
        self.hung_connection_closed = threading.Event()
        self._release = threading.Event()
        outer = self

        def _client_gone(sock: socket.socket) -> bool:
            readable, _, _ = select.select([sock], [], [], 0)
            if not readable:
                return False
            try:
                return sock.recv(1, socket.MSG_PEEK) == b""
            except OSError:
                return True

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 — API de BaseHTTPRequestHandler
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                outer.bodies.append(body)
                outer.authorizations.append(self.headers.get("Authorization"))
                mode = modes.get(body.get("model"), "error")
                if mode == "ok":
                    payload = _completion(body["model"])
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if mode == "error":
                    payload = json.dumps({"error": {"message": "upstream caído (test)"}}).encode()
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if mode == "lineas-vacias":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Connection", "close")
                    self.end_headers()
                while not outer._release.wait(0.1):
                    if _client_gone(self.connection):
                        outer.hung_connection_closed.set()
                        return
                    if mode == "lineas-vacias":
                        try:
                            self.wfile.write(b"\n")
                            self.wfile.flush()
                        except OSError:
                            outer.hung_connection_closed.set()
                            return

            def log_message(self, *args: Any) -> None:  # silencio en pytest
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def models(self) -> list[str]:
        return [b.get("model") for b in self.bodies]

    def shutdown(self) -> None:
        self._release.set()
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
async def _no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback sin proxy HTTP, y los dos clientes de litellm ya armados.

    La PRIMERA llamada de un proceso a cada proveedor arma su cliente (el SDK de
    OpenAI para ``litellm_proxy/``, el contexto SSL): medido, 2,45 s el respaldo
    y 0,65 s el principal, con el loop bloqueado; después, 0,03 s. En prod eso
    se paga una vez por worker contra un tope de 45 s; acá los topes son de 1 s,
    así que se calienta antes para medir el tope y no el arranque.
    """
    # Con HTTP(S)_PROXY en el env el cliente mandaría el loopback por el proxy.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    warm = _FakeProxy({"deepseek-v4-flash": "ok", "gemini-backup": "ok"})
    try:
        for model in (_PRIMARY, _BACKUP):
            await llm_chat(
                _input(warm, model=model, call_deadline_s=None, fallback_model=None, fallback_deadline_s=None)
            )
    finally:
        warm.shutdown()


def _input(proxy: _FakeProxy, **llm: Any) -> LLMChatInput:
    defaults: dict[str, Any] = {
        "model": _PRIMARY,
        "api_key": "sk-fake",
        "api_base": proxy.base_url,
        "reasoning_effort": "high",
        "call_deadline_s": _DEADLINE_S,
        "fallback_model": _BACKUP,
        "fallback_deadline_s": _BACKUP_DEADLINE_S,
    }
    defaults.update(llm)
    return LLMChatInput(
        messages=[dict(m) for m in _MESSAGES],
        llm=LLMConfig(**defaults),
        tool_definitions_json=json.dumps(_TOOLS),
        baggage=dict(_BAGGAGE),
    )


def _fallback_warnings(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if e.get("event") == "llm_fallback"]


@pytest.mark.usefixtures("_no_proxy_env")
@pytest.mark.parametrize("hang", ["silencio", "lineas-vacias"])
async def test_a_hung_primary_is_cut_and_answered_by_the_backup(hang: str) -> None:
    proxy = _FakeProxy({"deepseek-v4-flash": hang, "gemini-backup": "ok"})
    try:
        started = time.monotonic()
        with capture_logs() as logs:
            out = await asyncio.wait_for(llm_chat(_input(proxy)), timeout=15)
        elapsed = time.monotonic() - started
        closed = await asyncio.to_thread(proxy.hung_connection_closed.wait, 3)
    finally:
        proxy.shutdown()

    assert out.content == "respuesta de gemini-backup"
    assert proxy.models() == ["deepseek-v4-flash", "gemini-backup"]
    assert elapsed < _DEADLINE_S + 2, f"el respaldo contestó a los {elapsed:.1f} s"
    assert closed, (
        "al vencer el tope la conexión colgada tiene que cerrarse: si queda viva, "
        "la llamada a DeepSeek sigue ocupando el proxy por detrás"
    )
    warnings = _fallback_warnings(logs)
    assert len(warnings) == 1, f"un WARNING por respaldo; logs: {logs!r}"
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["cause"] == "timeout"
    assert warnings[0]["session_id"] == "wa_test"
    assert warnings[0]["episode_id"] == "ep-1"
    assert warnings[0]["primary_model"] == _PRIMARY
    assert warnings[0]["fallback_model"] == _BACKUP


@pytest.mark.usefixtures("_no_proxy_env")
async def test_the_backup_request_is_the_same_turn_without_deepseek_reasoning() -> None:
    """Por el proxy, al alias del respaldo, mismos mensajes y tools, sin thinking."""
    proxy = _FakeProxy({"deepseek-v4-flash": "silencio", "gemini-backup": "ok"})
    try:
        await asyncio.wait_for(llm_chat(_input(proxy)), timeout=15)
    finally:
        proxy.shutdown()

    backup = proxy.bodies[-1]
    assert backup["model"] == "gemini-backup"
    assert backup["messages"] == _MESSAGES
    assert [t["function"]["name"] for t in backup["tools"]] == ["search_products"]
    assert "reasoning_effort" not in backup, f"body del respaldo: {backup!r}"
    assert "thinking" not in backup and "thinking" not in (backup.get("extra_body") or {})


@pytest.mark.usefixtures("_no_proxy_env")
async def test_a_failing_primary_is_answered_by_the_backup_and_says_why() -> None:
    proxy = _FakeProxy({"deepseek-v4-flash": "error", "gemini-backup": "ok"})
    try:
        with capture_logs() as logs:
            out = await asyncio.wait_for(llm_chat(_input(proxy)), timeout=15)
    finally:
        proxy.shutdown()

    assert out.content == "respuesta de gemini-backup"
    warnings = _fallback_warnings(logs)
    assert [w["cause"] for w in warnings] == ["error"]


@pytest.mark.usefixtures("_no_proxy_env")
async def test_a_healthy_primary_never_calls_the_backup() -> None:
    proxy = _FakeProxy({"deepseek-v4-flash": "ok", "gemini-backup": "ok"})
    try:
        with capture_logs() as logs:
            out = await asyncio.wait_for(llm_chat(_input(proxy)), timeout=15)
    finally:
        proxy.shutdown()

    assert out.content == "respuesta de deepseek-v4-flash"
    assert proxy.models() == ["deepseek-v4-flash"]
    assert _fallback_warnings(logs) == []


@pytest.mark.usefixtures("_no_proxy_env")
@pytest.mark.parametrize("backup", ["silencio", "error"])
async def test_when_the_backup_also_fails_the_attempt_fails_in_time(backup: str) -> None:
    """Sin respuesta de nadie, la activity FALLA (Temporal reintenta), y a tiempo."""
    proxy = _FakeProxy({"deepseek-v4-flash": "silencio", "gemini-backup": backup})
    started = time.monotonic()
    try:
        with pytest.raises(Exception):  # noqa: B017 — cualquier error: Temporal reintenta
            await asyncio.wait_for(llm_chat(_input(proxy)), timeout=15)
        elapsed = time.monotonic() - started
    finally:
        proxy.shutdown()

    assert elapsed < _DEADLINE_S + _BACKUP_DEADLINE_S + 2, (
        f"falló a los {elapsed:.1f} s: el tope del respaldo no cortó, y un Gemini "
        "colgado repetiría el problema"
    )
    # El SDK de OpenAI reintenta un 500 por su cuenta: lo acota el tope del respaldo.
    models = proxy.models()
    assert models[0] == "deepseek-v4-flash" and set(models[1:]) == {"gemini-backup"}


@pytest.mark.usefixtures("_no_proxy_env")
async def test_without_a_backup_configured_nothing_changes() -> None:
    """``fallback_model=None`` (el default, y los inputs en vuelo): el error sube igual."""
    proxy = _FakeProxy({"deepseek-v4-flash": "error", "gemini-backup": "ok"})
    try:
        with pytest.raises(Exception):
            await asyncio.wait_for(
                llm_chat(_input(proxy, call_deadline_s=None, fallback_model=None, fallback_deadline_s=None)),
                timeout=15,
            )
    finally:
        proxy.shutdown()

    assert proxy.models() == ["deepseek-v4-flash"]


def test_an_input_from_before_the_change_still_deserializes() -> None:
    """Workflows en vuelo traen un ``LLMConfig`` sin los campos nuevos: sin respaldo."""
    from temporalio.converter import DataConverter

    old = {"model": _PRIMARY, "temperature": 0.1, "max_tokens": 32768, "max_iterations": 10,
           "reasoning_effort": None, "api_key": "k", "api_base": "http://proxy",
           "extra_headers": {}, "memory_window": 100}
    payload = DataConverter.default.payload_converter.to_payloads([old])
    [config] = DataConverter.default.payload_converter.from_payloads(payload, [LLMConfig])
    assert config.model == _PRIMARY
    assert (config.call_deadline_s, config.fallback_model, config.fallback_deadline_s) == (None, None, None)


@pytest.mark.usefixtures("_no_proxy_env")
async def test_a_cancelled_attempt_does_not_fall_back() -> None:
    """Si Temporal cancela el intento, se cancela: no hay respaldo a escondidas."""
    proxy = _FakeProxy({"deepseek-v4-flash": "silencio", "gemini-backup": "ok"})
    env = ActivityEnvironment()
    try:
        task = asyncio.ensure_future(env.run(llm_chat, _input(proxy, call_deadline_s=10.0)))
        await asyncio.sleep(0.5)
        env.cancel()
        with pytest.raises(BaseException) as raised:
            await asyncio.wait_for(task, timeout=10)
    finally:
        proxy.shutdown()

    assert isinstance(raised.value, asyncio.CancelledError) or "Cancel" in type(raised.value).__name__
    assert proxy.models() == ["deepseek-v4-flash"]
