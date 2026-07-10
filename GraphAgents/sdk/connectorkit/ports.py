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
# sin red) → G-DET se sostiene aunque el nodo narrativo use un LLM. El vendor real (`LiteLLMProxy`)
# tiene DOS modos (ver su docstring): un proxy LiteLLM ABIERTO (dev local :4000, las keys las tiene el
# proxy, con failover deepseek→gemini) o DIRECTO al proveedor autenticado (opción D, prod: DeepSeek con
# la key como Bearer — acá GraphAgents SÍ porta la DEEPSEEK_API_KEY, vía GRAPHAGENTS_LLM_API_KEY).

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
    """Vendor real: POST OpenAI-compatible a un endpoint LLM. Dos modos, según haya `api_key`:
      • SIN key  → un proxy LiteLLM ABIERTO (dev local `:4000`): reusa su key-management + el
                   failover deepseek→gemini. Es lo que corre el loop local / `test_llm_narrative`.
      • CON key  → el proveedor OpenAI-compatible DIRECTO y autenticado (opción D, prod AWS:
                   DeepSeek `https://api.deepseek.com`, model `deepseek-v4-flash`, Bearer = la
                   DEEPSEEK_API_KEY). Sin proxy, sin contenedor — el modelo es pura config. NO hay
                   failover (un solo proveedor); ok porque la narrativa degrada sola si falla (L-26).
    Config por env: `LITELLM_PROXY_URL` (base OpenAI-compatible; default http://localhost:4000) ·
    `GRAPHAGENTS_LLM_MODEL` (default deepseek-v4-flash — sirve igual como id real de DeepSeek) ·
    `GRAPHAGENTS_LLM_API_KEY` (Bearer; vacío = sin auth). No es G-DET: va SOLO en el nodo marcado.
    Cambiar de modelo/proveedor = un env/SSM, sin tocar código (api.deepseek.com↔api.stepfun.ai↔proxy)."""

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, timeout: float = 45) -> None:
        self._base = (base_url or os.environ.get("LITELLM_PROXY_URL", "http://localhost:4000")).rstrip("/")
        self._model = model or os.environ.get("GRAPHAGENTS_LLM_MODEL", "deepseek-v4-flash")
        self._api_key = api_key or os.environ.get("GRAPHAGENTS_LLM_API_KEY")
        self._timeout = timeout

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        body = json.dumps({
            "model": self._model, "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:  # opción D: Bearer → proveedor directo autenticado (DeepSeek). Sin key → proxy abierto.
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(
            f"{self._base}/v1/chat/completions", data=body, headers=headers, method="POST")
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

# Cómo armar el VENDOR FIXTURE de cada port a partir de la data de un caso (la clase ES el
# builder: su ctor toma la data). Es la contraparte de replay de PORTS — un caso que inyecta un
# port lo construye determinista (filas para Meta, la respuesta fija para el LLM). Sin red.
# Regla de oro: ningún port replayeable desde un caso sin su entrada acá (lo chequea CASE-PORT).
FIXTURE_VENDORS: dict[str, Callable] = {
    "meta_marketing_api": FixtureMetaInsights,  # FixtureMetaInsights(rows)
    "llm": FixtureLLM,                          # FixtureLLM(reply)
}


def durable_vendors() -> dict:
    """Los vendors REALES que el runtime DURABLE inyecta a quien `consumes:` un port.
    Fuente ÚNICA para el CLI (`start`, el buzón SSM) y el viewer (`_durable_ports`) —
    dos copias divergen y el classify degrada en prod con el proxy bien configurado.
    Hoy: `llm` → LiteLLMProxy (lazy: solo pega cuando un nodo lo invoca; config por
    LITELLM_PROXY_URL/GRAPHAGENTS_LLM_MODEL/GRAPHAGENTS_LLM_API_KEY — L-26: en
    multi-caja la URL default localhost:4000 NO alcanza el proxy). Meta sigue sin
    vendor real (G2) — un port consumido ausente acá se rechaza LOUD antes de
    submitear."""
    return {"llm": LiteLLMProxy()}
