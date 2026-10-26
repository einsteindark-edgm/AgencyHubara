"""Interruptor del juez LLM del eval — sin deepeval, para que la API lo pueda leer.

Vive aparte de `judge.py` porque ese módulo importa deepeval a nivel módulo y la
API (`chats/api/scorecards.py`) solo necesita saber si el juez está prendido:
importarlo desde `judge.py` le cargaba deepeval (~1.500 módulos, +37 MB de RSS)
a un proceso que nunca juzga (memoria de la caja de app, 2026-10-14). Guard:
tests/plugins/chats/test_api_boot_without_deepeval.py.
"""
from __future__ import annotations

import os

# APAGADO por defecto desde 2026-09-28 (decisión del operador: evaluar con IA
# costaba demasiado). Gemini 3.1 Pro con razonamiento "high" hacía ~18 llamadas
# de scorecard + ~28 de métricas DeepEval por CADA episodio cerrado, más el
# barrido diario: ~USD 1 por episodio, ~200x lo que cuesta atenderlo. Todo
# camino que llama al juez pasa por acá; los checks de código siguen (gratis).
# Guard: tests/evals/test_llm_judge_switch.py.
LLM_JUDGE_DISABLED_MSG = "el juez con IA está apagado (EVAL_LLM_JUDGE_ENABLED)"


def llm_judge_enabled() -> bool:
    """Interruptor único del juez LLM del eval. Env `EVAL_LLM_JUDGE_ENABLED`, default apagado."""
    return os.getenv("EVAL_LLM_JUDGE_ENABLED", "false").strip().lower() in ("1", "true", "yes")
