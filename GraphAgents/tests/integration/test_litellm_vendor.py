"""El vendor `LiteLLMProxy` construye el request OpenAI-compatible. Unit (SIN red): monkeypatcheamos
`urlopen` para capturar el `Request` y asertar su forma. Lo que se prueba es el header de
**Authorization** — lo que habilita la **opción D**: pegarle DIRECTO al proveedor autenticado
(DeepSeek, `api.deepseek.com`) sin un proxy LiteLLM intermedio. Sin key (proxy abierto local /
fixture) NO manda auth, así el loop local contra el `:4000` abierto sigue funcionando.
"""
from __future__ import annotations

import json

from sdk.connectorkit.ports import LiteLLMProxy


class _FakeResp:
    """Context-manager mínimo que imita la respuesta de `urlopen` (choices OpenAI-compatible)."""

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self) -> bytes:
        return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()


def _capture(monkeypatch) -> list:
    """Monkeypatchea urlopen para capturar el Request (sin red) y devolver una respuesta canned."""
    captured: list = []

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return captured


def test_api_key_from_env_sends_bearer(monkeypatch):
    """RED canónico (comportamiento, no TypeError): la key inyectada por env `GRAPHAGENTS_LLM_API_KEY`
    (lo que setea SSM en prod para D) → el vendor manda `Authorization: Bearer <key>`. Contra el
    código viejo no se manda ningún auth → la aserción falla."""
    monkeypatch.setenv("GRAPHAGENTS_LLM_API_KEY", "sk-env-key")
    captured = _capture(monkeypatch)
    out = LiteLLMProxy(base_url="https://api.deepseek.com").complete(system="s", user="u")
    assert out == "ok"
    assert captured[0].get_header("Authorization") == "Bearer sk-env-key"


def test_explicit_api_key_overrides_env(monkeypatch):
    """La key explícita del ctor gana sobre el env (inyección directa en tests/integration)."""
    monkeypatch.setenv("GRAPHAGENTS_LLM_API_KEY", "sk-env-key")
    captured = _capture(monkeypatch)
    LiteLLMProxy(base_url="https://api.deepseek.com", api_key="sk-explicit").complete(system="s", user="u")
    req = captured[0]
    assert req.get_header("Authorization") == "Bearer sk-explicit"
    # y arma el endpoint OpenAI-compatible del proveedor directo
    assert req.full_url == "https://api.deepseek.com/v1/chat/completions"


def test_no_auth_header_without_key(monkeypatch):
    """Sin key (proxy abierto local / fixture) → SIN Authorization. El header es opt-in: no rompe el
    dev local contra el litellm `:4000` abierto."""
    monkeypatch.delenv("GRAPHAGENTS_LLM_API_KEY", raising=False)
    captured = _capture(monkeypatch)
    LiteLLMProxy(base_url="http://localhost:4000").complete(system="s", user="u")
    assert captured[0].get_header("Authorization") is None
