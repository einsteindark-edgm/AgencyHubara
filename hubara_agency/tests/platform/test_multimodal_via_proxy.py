"""Guard del pipeline multimodal (audio + visión) contra el proxy litellm.

Bug de producción (2026-07): los adapters defaulteaban a
``model="gemini/gemini-2.5-flash-lite"`` con ``api_base`` apuntando al PROXY
litellm. Con el prefijo ``gemini/`` el SDK rutea por el provider nativo de
Google AI Studio y postea ``{api_base}/models/...:generateContent`` — una ruta
que el proxy no sirve (404) — y además ese modelo no estaba registrado en el
``model_list`` del proxy. Resultado: TODA transcripción de audio y descripción
de imagen fallaba con ``provider_error`` y el cliente recibía el mensaje de
"no pude entenderte"; el modelo multimodal nunca se consultaba. Los tests
existentes usan ``PROVIDER=fake`` y no lo cazaron (gotcha #1: verificar
comportamiento, no schema).

Este guard ejercita los adapters REALES (sin fake) contra un servidor HTTP
local que actúa de proxy y asierta el contrato de transporte:

  1. la request va en formato OpenAI (``POST .../chat/completions``) — la
     única superficie que el proxy litellm sirve;
  2. el ``model`` del payload es un alias registrado en el ``model_list``
     de ``exoclaw-temporal/litellm_config.yaml`` (el MISMO archivo que el
     deploy monta en el proxy de prod y local);
  3. funciona SIN api key en el env del proceso (el container hubara-api
     local no recibe GEMINI_API_KEY — la key vive en el proxy).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

_LITELLM_CONFIG = (
    Path(__file__).resolve().parents[3] / "exoclaw-temporal" / "litellm_config.yaml"
)


def _proxy_model_aliases() -> set[str]:
    config = yaml.safe_load(_LITELLM_CONFIG.read_text(encoding="utf-8"))
    return {entry["model_name"] for entry in config["model_list"]}


class _FakeProxy:
    """Servidor HTTP local que emula la superficie OpenAI del proxy litellm.

    Registra cada request (path + payload). Responde un chat completion
    válido SOLO en ``/chat/completions`` — cualquier otra ruta devuelve 404,
    igual que el proxy real ante una ruta nativa de Gemini.
    """

    def __init__(self, content: str) -> None:
        self.requests: list[tuple[str, dict]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — API de BaseHTTPRequestHandler
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    payload = {}
                outer.requests.append((self.path, payload))
                if not self.path.endswith("/chat/completions"):
                    body = b'{"error":{"message":"route not found"}}'
                    self.send_response(404)
                else:
                    body = json.dumps(
                        {
                            "id": "chatcmpl-fake",
                            "object": "chat.completion",
                            "created": 1,
                            "model": payload.get("model", ""),
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {
                                        "role": "assistant",
                                        "content": content,
                                    },
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 300,
                                "completion_tokens": 20,
                                "total_tokens": 320,
                            },
                        }
                    ).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:  # silencio en pytest
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def _no_ambient_keys(monkeypatch):
    """El container hubara-api local NO tiene keys de LLM — el adapter debe
    poder hablarle al proxy sin ninguna."""
    for var in (
        "GEMINI_API_KEY",
        "LITELLM_API_KEY",
        "AUDIO_TRANSCRIPTION_API_KEY",
        "IMAGE_VISION_API_KEY",
        "AUDIO_TRANSCRIPTION_MODEL",
        "IMAGE_VISION_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


async def test_vision_adapter_talks_openai_format_to_registered_proxy_alias(
    monkeypatch, _no_ambient_keys
):
    from src.platform.vision import litellm_adapter as vision_adapter_mod
    from src.platform.vision.dtos import VisionRequest

    proxy = _FakeProxy(content="TIPO: otro\nDESCRIPCION: una vela rosada")
    monkeypatch.setenv("IMAGE_VISION_API_BASE", proxy.base_url)

    async def _fake_fetch(media_id: str):
        return (b"\xff\xd8\xff fake jpeg", "image/jpeg")

    monkeypatch.setattr(vision_adapter_mod, "fetch_media_bytes", _fake_fetch)

    try:
        adapter = vision_adapter_mod.LiteLLMVisionAdapter.from_env()
        result = await adapter.describe(
            VisionRequest(media_id="mid-1", mime_type="image/jpeg")
        )
    finally:
        proxy.shutdown()

    assert result.ok, f"la visión debe resolver via proxy, falló: {result.error}"
    assert result.description == "una vela rosada"
    assert proxy.requests, "el adapter nunca llegó al proxy"
    path, payload = proxy.requests[-1]
    assert path.endswith("/chat/completions"), (
        f"el adapter debe hablarle al proxy en formato OpenAI, posteó a {path}"
    )
    assert payload.get("model") in _proxy_model_aliases(), (
        f"el modelo '{payload.get('model')}' no está registrado en el "
        f"model_list del proxy ({_LITELLM_CONFIG})"
    )


async def test_audio_adapter_talks_openai_format_to_registered_proxy_alias(
    monkeypatch, _no_ambient_keys
):
    from src.platform.audio import litellm_adapter as audio_adapter_mod
    from src.platform.audio.dtos import TranscriptionRequest

    proxy = _FakeProxy(content="hola, quiero dos velas de lavanda")
    monkeypatch.setenv("AUDIO_TRANSCRIPTION_API_BASE", proxy.base_url)

    async def _fake_fetch(media_id: str):
        return (b"OggS fake opus", "audio/ogg")

    monkeypatch.setattr(audio_adapter_mod, "fetch_media_bytes", _fake_fetch)

    try:
        adapter = audio_adapter_mod.LiteLLMTranscriptionAdapter.from_env()
        result = await adapter.transcribe(
            TranscriptionRequest(media_id="mid-2", mime_type="audio/ogg")
        )
    finally:
        proxy.shutdown()

    assert result.ok, f"la transcripción debe resolver via proxy, falló: {result.error}"
    assert result.text == "hola, quiero dos velas de lavanda"
    assert proxy.requests, "el adapter nunca llegó al proxy"
    path, payload = proxy.requests[-1]
    assert path.endswith("/chat/completions"), (
        f"el adapter debe hablarle al proxy en formato OpenAI, posteó a {path}"
    )
    assert payload.get("model") in _proxy_model_aliases(), (
        f"el modelo '{payload.get('model')}' no está registrado en el "
        f"model_list del proxy ({_LITELLM_CONFIG})"
    )
