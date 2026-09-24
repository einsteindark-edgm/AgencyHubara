"""Candado por código de cupón + la guarda de "la última unidad".

Registrar un pedido con cupo es "leer vendidas → repartir → crear el draft".
Dos clientes confirmando la última unidad a la vez venderían dos: por eso
`register_order` corre esa secuencia bajo un `flock` por código (API y
workers comparten el disco del vault) y, adentro, relee lo vendido y
recalcula el reparto; si ya no es el que se le confirmó al cliente, NO
registra (`quota_changed`).

El candado es async: espera con `LOCK_NB` + sleep (nunca bloquea el event
loop) y se rinde a los `timeout_s` (`QuotaLockTimeout`).
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import time
from pathlib import Path
from typing import AsyncIterator, TypeVar

A = TypeVar("A")
R = TypeVar("R")


class QuotaLockTimeout(TimeoutError):
    """Otro registro del mismo cupón tardó demasiado."""


class VaultQuotaLock:
    def __init__(self, vault_dir: Path, *, poll_s: float = 0.02) -> None:
        self._dir = Path(vault_dir) / "_promotions" / "locks"
        self._poll_s = poll_s

    @contextlib.asynccontextmanager
    async def hold(self, code: str, *, timeout_s: float = 15.0) -> AsyncIterator[None]:
        key = code.strip().upper()
        if not key:
            raise ValueError("código vacío para el candado")
        # Nombre del archivo = hash del código: vale para cualquier forma de
        # código que exista en Medusa (con `_`, `-`…) y no arma rutas.
        name = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        self._dir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_s
        with open(self._dir / f"{name}.lock", "w", encoding="utf-8") as fh:
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise QuotaLockTimeout(f"el cupón {key} está ocupado") from None
                    await asyncio.sleep(self._poll_s)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


__all__ = ["QuotaLockTimeout", "VaultQuotaLock"]
