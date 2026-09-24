"""Guard de arranque del worker `sales_lab` (plan §3.5, candado 3). PURO.

Stdlib solamente: se importa ANTES que la app, porque la composición guarda
rutas y clientes con `lru_cache` — las variables se fijan primero y después se
importa el resto.

`prepare_lab_env` fija las carpetas dentro de `LAB_ROOT` (si no vienen ya
adentro) y apaga la telemetría. `check_lab_env` devuelve los motivos para NO
arrancar (lista vacía = puede arrancar).
"""
from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping

LAB_TEMPORAL_URL = "temporal:7233"
LAB_NAMESPACE = "hubara-lab"

# Variables que nunca pueden tener valor en la caja del laboratorio.
_FORBIDDEN_PREFIXES = (
    "WHATSAPP_",
    "MEDUSA_",
    "META_",
    "COGNITO_",
    "PAYMENT_",
    "MBA_",
    "GRAPHAGENTS_",
)
_FORBIDDEN_KEYS = (
    "TEMPORAL_API_KEY",
    "TEMPORAL_ADDRESS",
    "TEMPORAL_TLS_CERT",
    "TEMPORAL_TLS_KEY",
    "HUBARA_SERVICE_TOKEN",
    "HUBARA_MBA_API_KEY",
    "APP_SECRET",
)

# Carpetas que el código de producción escribe: todas dentro de LAB_ROOT.
_FOLDERS: dict[str, str] = {
    "WORKSPACE_VAULT_DIR": "sandbox/vault",
    "EXOCLAW_STATE_DIR": "sandbox/vault/agent_state",
    "CATALOG_SNAPSHOT_DIR": "sandbox/vault/catalog",
    "EVAL_CANDIDATES_DIR": "sandbox/vault/_evals/candidates",
    "EVAL_HISTORY_DIR": "sandbox/vault/_evals/history",
    "MEDIA_STORE_DIR": "sandbox/media",
}


def _root(env: Mapping[str, str]) -> str:
    return os.path.normpath(env.get("LAB_ROOT") or "/lab")


def _inside(path: str, root: str) -> bool:
    normalized = os.path.normpath(path)
    return normalized == root or normalized.startswith(root.rstrip("/") + "/")


def prepare_lab_env(env: MutableMapping[str, str]) -> None:
    root = _root(env)
    env.setdefault("LAB_ROOT", root)
    for var, rel in _FOLDERS.items():
        current = env.get(var) or ""
        if not current or not _inside(current, root):
            env[var] = os.path.join(root, rel)
    env["OTEL_SDK_DISABLED"] = "true"


def check_lab_env(env: Mapping[str, str]) -> list[str]:
    problems: list[str] = []
    root = _root(env)
    for key, value in env.items():
        if not value:
            continue
        if key in _FORBIDDEN_KEYS or key.startswith(_FORBIDDEN_PREFIXES):
            problems.append(f"{key}: llave o dato de producción en la caja del laboratorio")
    if env.get("TEMPORAL_URL") != LAB_TEMPORAL_URL:
        problems.append(f"TEMPORAL_URL debe ser {LAB_TEMPORAL_URL} (el Temporal de la caja)")
    if env.get("TEMPORAL_NAMESPACE") != LAB_NAMESPACE:
        problems.append(f"TEMPORAL_NAMESPACE debe ser {LAB_NAMESPACE}")
    if env.get("HUBARA_ENV") != "lab":
        problems.append("HUBARA_ENV debe ser lab")
    for var in _FOLDERS:
        value = env.get(var) or ""
        if not value or not _inside(value, root):
            problems.append(f"{var}={value!r} está fuera de {root}")
    return problems
