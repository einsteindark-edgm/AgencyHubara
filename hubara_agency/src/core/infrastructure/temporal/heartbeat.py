"""Decorador `@with_heartbeat(every=N)` para activities de larga duracion (R-HEARTBEAT).

Se aplica DESPUES de `@activity.defn` y reemplaza los loops `asyncio.create_task(_heartbeat_loop)`
hand-rolled dentro del cuerpo. Garantiza:
  * cancelacion segura del task en `finally`,
  * supresion de `CancelledError` post-cancel,
  * cero leaks si el cuerpo lanza antes del primer `await`.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
from typing import Any, Awaitable, Callable, TypeVar

from temporalio import activity

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def with_heartbeat(every: int = 10) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async def _loop() -> None:
                while True:
                    activity.heartbeat()
                    await asyncio.sleep(every)

            heartbeat_task = asyncio.create_task(_loop())
            try:
                return await func(*args, **kwargs)
            finally:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task

        return wrapper  # type: ignore[return-value]

    return decorator
