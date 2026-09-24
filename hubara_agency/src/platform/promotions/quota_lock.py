"""Candado por código de cupón + la guarda de "la última unidad".

Registrar un pedido con cupo es "leer vendidas → repartir → crear el draft".
Dos clientes confirmando la última unidad a la vez venderían dos: por eso la
secuencia corre bajo un `flock` por código (API y workers comparten el disco
del vault) y, adentro, se relee lo vendido y se recalcula el reparto. Si ya
no es el que se le confirmó al cliente, NO se registra: `QuotaChanged` con el
reparto nuevo, y el bot pide confirmación otra vez.

El candado es async: espera con `LOCK_NB` + sleep (nunca bloquea el event
loop) y se rinde a los `timeout_s` (`QuotaLockTimeout`).
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Generic, TypeVar

A = TypeVar("A")
R = TypeVar("R")

_CODE = re.compile(r"[A-Z0-9]{1,32}")


@dataclass(frozen=True)
class QuotaChanged(Generic[A]):
    """El reparto cambió entre la confirmación y el registro."""

    allocation: A


class QuotaLockTimeout(TimeoutError):
    """Otro registro del mismo cupón tardó demasiado."""


class VaultQuotaLock:
    def __init__(self, vault_dir: Path, *, poll_s: float = 0.02) -> None:
        self._dir = Path(vault_dir) / "_promotions" / "locks"
        self._poll_s = poll_s

    @contextlib.asynccontextmanager
    async def hold(self, code: str, *, timeout_s: float = 15.0) -> AsyncIterator[None]:
        key = code.strip().upper()
        if not _CODE.fullmatch(key):
            raise ValueError(f"código inválido para el candado: {code!r}")
        self._dir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_s
        with open(self._dir / f"{key}.lock", "w", encoding="utf-8") as fh:
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


async def register_under_quota(
    lock: VaultQuotaLock,
    code: str,
    *,
    read_sold: Callable[[], Awaitable[dict[str, int]]],
    allocate: Callable[[dict[str, int]], A],
    confirmed: A,
    register: Callable[[], Awaitable[R]],
    timeout_s: float = 15.0,
) -> R | QuotaChanged[A]:
    """Registra SOLO si, releyendo lo vendido bajo el candado del código, el
    reparto sigue siendo el confirmado."""
    async with lock.hold(code, timeout_s=timeout_s):
        fresh = allocate(await read_sold())
        if fresh != confirmed:
            return QuotaChanged(fresh)
        return await register()


__all__ = ["QuotaChanged", "QuotaLockTimeout", "VaultQuotaLock", "register_under_quota"]
