"""Test de ``add_traced_background_task`` — propagación de contexto OTel HTTP→task.

El valor del helper: el contexto OTel del request (con el span/baggage activos) se
preserva DENTRO del background task aunque el request ya haya terminado. Eso es lo
que liga el webhook con el workflow de Temporal en un solo trace distribuido.
"""

from __future__ import annotations

import asyncio

from opentelemetry import baggage, context

from src.platform.observability.tracing import add_traced_background_task


class _FakeBackgroundTasks:
    """Stub de fastapi.BackgroundTasks — solo guarda lo encolado."""

    def __init__(self) -> None:
        self.tasks: list = []

    def add_task(self, func, *args, **kwargs) -> None:
        self.tasks.append((func, args, kwargs))


def test_preserva_contexto_otel_en_task_async() -> None:
    seen: dict = {}

    async def work(x: int) -> None:
        seen["arg"] = x
        seen["bag"] = baggage.get_baggage("k")  # leído DENTRO del task

    # Simula el contexto del request: baggage activo (como el span del webhook).
    token = context.attach(baggage.set_baggage("k", "v"))
    bt = _FakeBackgroundTasks()
    try:
        add_traced_background_task(bt, work, 42)
    finally:
        context.detach(token)  # el request "termina" → contexto global se limpia

    # El task corre DESPUÉS, con el contexto global ya limpio...
    assert baggage.get_baggage("k") is None
    func, _args, _kwargs = bt.tasks[0]
    asyncio.run(func())  # Starlette correría esto

    assert seen["arg"] == 42
    assert seen["bag"] == "v"  # ← el contexto del request se re-attacheó en el task


def test_soporta_func_sync() -> None:
    seen: dict = {}

    def work(x: str) -> None:
        seen["arg"] = x

    bt = _FakeBackgroundTasks()
    add_traced_background_task(bt, work, "hello")
    func, _args, _kwargs = bt.tasks[0]
    asyncio.run(func())
    assert seen["arg"] == "hello"


def test_pasa_args_y_kwargs() -> None:
    seen: dict = {}

    async def work(a, b, *, c) -> None:
        seen.update(a=a, b=b, c=c)

    bt = _FakeBackgroundTasks()
    add_traced_background_task(bt, work, 1, 2, c=3)
    func, _args, _kwargs = bt.tasks[0]
    asyncio.run(func())
    assert seen == {"a": 1, "b": 2, "c": 3}
