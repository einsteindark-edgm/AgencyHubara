"""Entorno del proceso de UN caso (plan §3.5 candado 3, PR 11). PURO, stdlib.

Se llama ANTES de importar la app: la composición guarda rutas y clientes
con `lru_cache` y varios módulos leen `WORKSPACE_VAULT_DIR` al importarse.
Cada carpeta que el código de producción escribe apunta al sandbox del caso
(`/lab/runs/<corrida>/<brazo>/<rep>/<caso>/`), que se arma desde el banco y
se borra al terminar. El catálogo del banco se exportó horas antes: sin un
tope de edad alto, la tool de búsqueda le diría al LLM que está "viejo"
(`stale`), cosa que en producción no pasaba. Los centinelas del guard
(`SANDBOX_SENTINELS`) completan lo que el envío simulado pide.
"""
from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

from src.plugins.chats.agent.sales_lab.guard import SANDBOX_SENTINELS

CATALOG_NEVER_STALE_MINUTES = 60 * 24 * 365 * 10

_FOLDERS = {
    "WORKSPACE_VAULT_DIR": "vault",
    "EXOCLAW_STATE_DIR": "agent_state",
    "CATALOG_SNAPSHOT_DIR": "catalog",
    "MEDIA_STORE_DIR": "media",
    "EVAL_CANDIDATES_DIR": "vault/_evals/candidates",
    "EVAL_HISTORY_DIR": "vault/_evals/history",
}


def prepare_case_env(env: MutableMapping[str, str], sandbox_dir: Path) -> None:
    for var, rel in _FOLDERS.items():
        env[var] = os.path.join(str(sandbox_dir), rel)
    env["CATALOG_MAX_AGE_MINUTES"] = str(CATALOG_NEVER_STALE_MINUTES)
    env["OTEL_SDK_DISABLED"] = "true"
    env.update(SANDBOX_SENTINELS)
    # litellm baja su tabla de precios de GitHub al importarse: en la caja,
    # la local del paquete (el test de fugas vio la conexión).
    env["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
