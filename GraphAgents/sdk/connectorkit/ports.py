"""Ports del ConnectorKit + sus vendors. El port es el contrato; el vendor la
implementación intercambiable. Para los golden-replay se usa el vendor `fixture`
(determinista, sin red) — así G-DET se sostiene.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class InsightsPort(Protocol):
    """Insights de campañas (gasto, impresiones, clicks, conversiones, …)."""

    def fetch(self, *, account_id: str, since: str, until: str) -> list[dict]: ...


class FixtureMetaInsights:
    """Vendor de test/golden: devuelve filas precargadas en vez de pegarle a Meta."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetch(self, *, account_id: str, since: str, until: str) -> list[dict]:
        return list(self._rows)


# --------------------------------------------------------------------------- LLM
# El LLM es un PORT como cualquier otro: el contrato es `complete(...)`, el vendor la
# implementación intercambiable. Para el golden-replay se inyecta `FixtureLLM` (determinista,
# sin red) → G-DET se sostiene aunque el nodo narrativo use un LLM. El vendor real le pega al
# proxy LiteLLM del proyecto central (:4000, deepseek-v4-flash con failover a gemini-flash-lite);
# el proxy tiene las keys (DEEPSEEK_API_KEY/GEMINI_API_KEY) — GraphAgents nunca las toca.

@runtime_checkable
class LLMPort(Protocol):
    """Completion de un LLM. `temperature=0` por default (el narrador cita números, no crea)."""

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str: ...


class FixtureLLM:
    """Vendor de test/golden: devuelve una respuesta fija (o derivada del prompt vía callable),
    sin red. Hace replayeable el nodo LLM."""

    def __init__(self, reply: str | Callable[[str], str]) -> None:
        self._reply = reply

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        return self._reply(user) if callable(self._reply) else self._reply


class LiteLLMProxy:
    """Vendor real: POST OpenAI-compatible al proxy LiteLLM del central (sin auth — el proxy es
    abierto en local). Reusa su key management + el failover deepseek→gemini. Config por env:
    `LITELLM_PROXY_URL` (default http://localhost:4000) · `GRAPHAGENTS_LLM_MODEL` (default
    deepseek-v4-flash). No es G-DET: va SOLO en el nodo marcado, nunca en el esqueleto."""

    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: float = 45) -> None:
        self._base = (base_url or os.environ.get("LITELLM_PROXY_URL", "http://localhost:4000")).rstrip("/")
        self._model = model or os.environ.get("GRAPHAGENTS_LLM_MODEL", "deepseek-v4-flash")
        self._timeout = timeout

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        body = json.dumps({
            "model": self._model, "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return data["choices"][0]["message"]["content"]


# TODO (G2):
#   LiveMetaInsights      — Meta Marketing API (timeout dimensionado por la cadena
#                           real de Meta, no por el hop local; retries con backoff).
#   WarehouseMetaInsights — lee de un warehouse ya ingestado.

# Registry declarativo: nombre de port → contrato. Lo lee el `consumes:` del manifest.
PORTS: dict[str, str] = {
    "meta_marketing_api": "InsightsPort",
    "llm": "LLMPort",  # el nodo narrativo del reporter (deepseek-v4-flash vía el proxy del central)
    # "ctwa_vault": "...",  # G3
}
