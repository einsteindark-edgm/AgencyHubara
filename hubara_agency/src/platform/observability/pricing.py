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


def compute_llm_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    pricing_table: dict[str, dict[str, Any]],
) -> float:
    """``(prompt/1000)*promptPrice + (completion/1000)*completionPrice`` en USD.

    Busca el modelo exacto y, si no, la parte tras el primer ``/`` (igual que
    OpenLIT: ``deepseek/deepseek-v4-flash`` → ``deepseek-v4-flash``). Devuelve
    ``0.0`` si el modelo no está en la tabla.
    """
    pricing = pricing_table.get(model)
    if pricing is None and "/" in model:
        pricing = pricing_table.get(model.split("/", 1)[1])
    if not isinstance(pricing, dict):
        return 0.0
    prompt_price = float(pricing.get("promptPrice", 0.0) or 0.0)
    completion_price = float(pricing.get("completionPrice", 0.0) or 0.0)
    cost = (prompt_tokens / 1000.0) * prompt_price + (
        completion_tokens / 1000.0
    ) * completion_price
    return round(cost, 8)


def response_cost_usd(response: Any, model: str) -> float | None:
    """Lo que costó UNA respuesta de litellm: primero el costo que calculó el
    proxy (cabecera ``x-litellm-response-cost`` → ``_hidden_params``); si no
    viene, ``usage`` × la tabla de precios. ``None`` = no se sabe (respuesta
    sin costo ni tokens, o modelo sin precio). Nunca lanza."""
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        cost = hidden.get("response_cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
            return float(cost)
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", None) if usage is not None else None
    completion = getattr(usage, "completion_tokens", None) if usage is not None else None
    if not isinstance(prompt, int) and not isinstance(completion, int):
        return None
    cost = compute_llm_cost_usd(
        model, prompt if isinstance(prompt, int) else 0, completion if isinstance(completion, int) else 0,
        load_pricing_table(),
    )
    return cost if cost > 0 else None
