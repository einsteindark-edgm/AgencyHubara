"""Cálculo de costo LLM desde la tabla de pricing de OpenLIT.

Reusa el MISMO archivo de tarifas que OpenLIT (``OPENLIT_PRICING_JSON``, formato
USD por 1000 tokens, keyed por modelo) → **una sola fuente del precio**. Se usa
para persistir el costo USD por episodio en ``metadata.json`` (dato de negocio,
mostrado en el frontend), independiente del path de observabilidad (SigNoz).

Funciones puras (sin estado) salvo la lectura del archivo en ``load_pricing_table``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load_pricing_table(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Carga la sección ``chat`` de la tabla de pricing (USD por 1000 tokens).

    Sin ``path`` lee de ``OPENLIT_PRICING_JSON`` (misma fuente que OpenLIT).
    Devuelve ``{}`` ante archivo ausente/corrupto/sin env → costo 0 (degrada, no
    rompe el turno).
    """
    if path is None:
        raw = os.getenv("OPENLIT_PRICING_JSON", "").strip()
        path = Path(raw) if raw else None
    if path is None or not path.exists():
        return {}
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    chat = data.get("chat")
    return chat if isinstance(chat, dict) else {}


def _price_entry(model: str, pricing_table: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """El modelo exacto y, si no, la parte tras el primer ``/`` (igual que
    OpenLIT: ``deepseek/deepseek-v4-flash`` → ``deepseek-v4-flash``)."""
    pricing = pricing_table.get(model)
    if pricing is None and "/" in model:
        pricing = pricing_table.get(model.split("/", 1)[1])
    return pricing if isinstance(pricing, dict) else None


def compute_llm_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    pricing_table: dict[str, dict[str, Any]],
    *,
    audio_input: bool = False,
) -> float:
    """``(prompt/1000)*promptPrice + (completion/1000)*completionPrice`` en USD.

    Con ``audio_input`` la entrada va a ``audioPromptPrice`` cuando el modelo lo
    tiene (gemini-2.5-flash-lite cobra el audio 3x el texto); si no, a
    ``promptPrice``. Devuelve ``0.0`` si el modelo no está en la tabla.
    """
    pricing = _price_entry(model, pricing_table)
    if pricing is None:
        return 0.0
    prompt_price = float(pricing.get("promptPrice", 0.0) or 0.0)
    if audio_input:
        prompt_price = float(pricing.get("audioPromptPrice", prompt_price) or 0.0)
    completion_price = float(pricing.get("completionPrice", 0.0) or 0.0)
    cost = (prompt_tokens / 1000.0) * prompt_price + (
        completion_tokens / 1000.0
    ) * completion_price
    return round(cost, 8)


def image_price_usd(model: str, pricing_table: dict[str, dict[str, Any]]) -> float | None:
    """USD por imagen (``imagePrice``) de un modelo que cobra por imagen, como
    los embeddings de Gemini. ``None`` = el modelo no tiene ese precio."""
    pricing = _price_entry(model, pricing_table)
    price = pricing.get("imagePrice") if pricing is not None else None
    return float(price) if isinstance(price, (int, float)) and price > 0 else None


def proxy_reported_cost_usd(response: Any) -> float | None:
    """El costo que calculó el proxy (cabecera ``x-litellm-response-cost`` →
    ``_hidden_params``), si lo mandó y es positivo."""
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        cost = hidden.get("response_cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
            return float(cost)
    return None


def response_cost_usd(response: Any, model: str, *, audio_input: bool = False) -> float | None:
    """Lo que costó UNA respuesta de litellm: primero el costo que calculó el
    proxy; si no viene, ``usage`` × la tabla de precios. ``None`` = no se sabe
    (respuesta sin costo ni tokens, o modelo sin precio). Nunca lanza.

    La tabla se busca por el modelo que CONTESTÓ (``response.model``) si tiene
    precio, y si no por el que se pidió: cuando el failover del proxy atiende
    con el sucesor, el costo es el del sucesor y no el del alias."""
    reported = proxy_reported_cost_usd(response)
    if reported is not None:
        return reported
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", None) if usage is not None else None
    completion = getattr(usage, "completion_tokens", None) if usage is not None else None
    if not isinstance(prompt, int) and not isinstance(completion, int):
        return None
    table = load_pricing_table()
    served = getattr(response, "model", None)
    priced = served if isinstance(served, str) and _price_entry(served, table) is not None else model
    cost = compute_llm_cost_usd(
        priced, prompt if isinstance(prompt, int) else 0, completion if isinstance(completion, int) else 0,
        table, audio_input=audio_input,
    )
    return cost if cost > 0 else None
