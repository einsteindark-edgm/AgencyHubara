"""Composition root del harness de evals (R-STATELESS).

Las activities NO cachean a nivel módulo; el estado compartido (el juez, que es
caro de construir y se reusa entre métricas y entre conversaciones del mismo
worker) vive acá con `@lru_cache(maxsize=1)`. Mismo patrón que el resto del
backend DEHA.

`deepeval` se importa lazy dentro de los factories (vía `judge.build_judge`).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def get_judge() -> Any:
    """Modelo juez compartido (un alias del proxy litellm). Construido 1 vez."""
    from src.plugins.chats.agent.sales_eval.evals.judge import build_judge

    return build_judge()


@lru_cache(maxsize=1)
def get_vault_dir() -> Path:
    """Directorio del vault (donde viven `wa_*/sessions/*.jsonl`)."""
    from src.platform.config import WORKSPACE_VAULT_DIR

    return WORKSPACE_VAULT_DIR


@lru_cache(maxsize=1)
def get_candidates_dir() -> Path:
    """Directorio donde se escriben los candidatos a golden (auto-curación).

    * Local/dev: default = `tests/evals/goldens/sales/_candidates/` (en el repo)
      para que el curador humano los revise, redacte y promueva a `curated.json`.
    * Prod (worker en container): setear `EVAL_CANDIDATES_DIR` a un path montado
      durable (ej. un subdir del vault) — escribir en `tests/` del container es
      efímero y no llega a un humano. El operador baja los candidatos de ahí.
    """
    import os

    override = os.getenv("EVAL_CANDIDATES_DIR", "").strip()
    if override:
        return Path(override)
    # hubara_agency/ = parents[6] desde
    # src/plugins/chats/agent/sales/evals/composition.py
    repo_backend = Path(__file__).resolve().parents[6]
    return repo_backend / "tests" / "evals" / "goldens" / "sales" / "_candidates"
