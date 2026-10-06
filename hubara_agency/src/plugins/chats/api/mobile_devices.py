"""El registro de teléfonos para avisos push: `<vault>/_mobile/devices.json`.

Por operador (el actor verificado de Cognito), sus tokens de Firebase: solo el
token, la plataforma y la versión de la app — nada del teléfono ni del cliente.
El `_` deja el archivo fuera de todo lo que recorre sesiones (`wa_*`). Cada
cambio es read-modify-write bajo flock (dos teléfonos registrándose a la vez no
se pisan) con escritura atómica.

    {"operators": {"ana@equipo.test": [{token, platform, app_version, registered_at_ms, updated_at_ms}]}}
"""
from __future__ import annotations

import fcntl
import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from src.sdk.runtime import atomic_write_json

Operators = dict[str, list[dict[str, Any]]]


def registry_path(vault_dir: Path) -> Path:
    return vault_dir / "_mobile" / "devices.json"


def _read(vault_dir: Path) -> Operators:
    try:
        data = json.loads(registry_path(vault_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    operators = data.get("operators") if isinstance(data, dict) else None
    return operators if isinstance(operators, dict) else {}


def _update(vault_dir: Path, mutate: Callable[[Operators], None]) -> None:
    path = registry_path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / f"{path.name}.lock", "w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            operators = _read(vault_dir)
            mutate(operators)
            atomic_write_json(path, {"operators": operators})
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _tokens(devices: Any) -> list[str]:
    if not isinstance(devices, list):
        return []
    return [d["token"] for d in devices if isinstance(d, dict) and isinstance(d.get("token"), str) and d["token"]]


def register_token(
    vault_dir: Path, actor: str, token: str, *, app_version: str, now_ms: int, platform: str = "android"
) -> None:
    """Idempotente: el mismo token se actualiza, no se duplica. Un token queda
    del ÚLTIMO operador que lo registró (el teléfono cambió de manos)."""

    def mutate(operators: Operators) -> None:
        previous: dict[str, Any] | None = None
        for owner, devices in operators.items():
            for device in list(devices if isinstance(devices, list) else []):
                if isinstance(device, dict) and device.get("token") == token:
                    devices.remove(device)
                    previous = previous or (device if owner == actor else None)
        operators.setdefault(actor, []).append({
            "token": token,
            "platform": platform,
            "app_version": app_version,
            "registered_at_ms": int((previous or {}).get("registered_at_ms") or now_ms),
            "updated_at_ms": now_ms,
        })

    _update(vault_dir, mutate)


def unregister_token(vault_dir: Path, actor: str, token: str) -> None:
    """Borra el token del operador que llama (idempotente)."""

    def mutate(operators: Operators) -> None:
        devices = operators.get(actor) or []
        operators[actor] = [d for d in devices if not (isinstance(d, dict) and d.get("token") == token)]

    _update(vault_dir, mutate)


def registered_tokens(vault_dir: Path) -> list[str]:
    """Todos los tokens registrados, sin repetir (en el orden del archivo)."""
    return list(dict.fromkeys(t for devices in _read(vault_dir).values() for t in _tokens(devices)))


def operator_tokens(vault_dir: Path, actor: str) -> list[str]:
    return _tokens(_read(vault_dir).get(actor))


def forget_tokens(vault_dir: Path, tokens: Iterable[str]) -> None:
    """Borra tokens que Firebase ya no reconoce (de cualquier operador)."""
    dead = frozenset(tokens)
    if not dead:
        return

    def mutate(operators: Operators) -> None:
        for actor, devices in list(operators.items()):
            operators[actor] = [d for d in devices or [] if not (isinstance(d, dict) and d.get("token") in dead)]

    _update(vault_dir, mutate)
