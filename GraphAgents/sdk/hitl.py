"""Marcador HITL del SDK — agnóstico del runtime.

`@human_task` marca un nodo del grafo como punto de decisión humana. Mapea a la
convención de AgentSpan (atributos `_agentspan_human_task` / `_agentspan_human_prompt`
que su serializer de LangGraph lee): al compilar en AgentSpan, el nodo se vuelve una
**HUMAN task de Conductor** que PAUSA durable hasta que un humano responde (vía
`Result.respond({...})` / `approve()` / `reject()`), y se observa como `awaiting` en
el trace (ver `sdk/trace.py`).

OFFLINE (LocalRuntime / LangGraph+MemorySaver) el decorador es INERTE: solo setea
los atributos, y el body del nodo (con `interrupt()`) corre normal — esa es la
pausa del golden. AgentSpan es el único que NO corre el body (lo reemplaza por la
HUMAN task). Así el MISMO nodo sirve a ambos runtimes sin acoplar la forma offline
a agentspan.

Validado en vivo (`:6767`): sin este marcador, AgentSpan corre el nodo como SIMPLE
task y el `interrupt()` revienta con 'Called get_config outside of a runnable
context'. Con él, el nodo pausa como HUMAN task.
"""
from __future__ import annotations

from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable)


def human_task(func: F | None = None, *, prompt: str = ""):
    """Decorador: marca el nodo como HUMAN task (HITL). Con o sin argumentos."""

    def deco(f: F) -> F:
        f._agentspan_human_task = True  # lo lee el serializer de AgentSpan
        f._agentspan_human_prompt = prompt
        return f

    return deco(func) if func is not None else deco
