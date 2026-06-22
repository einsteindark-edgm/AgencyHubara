"""El protocolo `Capability` del kit — el contrato FORMAL de un nodo determinista de UN agente.
Es la superficie que el loader compila y la cert verifica: amarra que TODAS las capabilities se
implementen igual, y por eso son uniformemente trazables y reconstruibles.

El contrato (dos métodos, uno opcional):

- `run(input, *, ports, tools) -> output`  **PURA, obligatoria** (G-DET, G-RUN-SIG). El esqueleto
  es determinista; el IO sale por `ports` (ConnectorKit) y las tools se llaman por el mapping
  `tools` INYECTADO — NUNCA por import directo. Ese mapping es el SEAM que hace a la capability
  trazable (`sdk.tooltrace`) sin tocar su código: el loader la corre por el LocalRuntime y, dentro
  de un supervisor, también en el durable (`build_supervisor_graph` → `build_runnable` → `run`).

- `build(*, checkpointer=None) -> CompiledStateGraph`  **OPCIONAL** (G1+). El MISMO comportamiento
  como StateGraph para correr standalone en AgentSpan. DEBE ser equivalente a `run()` en su output
  (paridad run≡build, conformance) — esa equivalencia es la que hace que el trace reconstruido por
  `run()` sea fiel también al camino durable que usa `build()`.

`assert_capability(module)` es la enforcement ESTRUCTURAL que consume la cert (firma de `run`,
firma de `build`). La parte BEHAVIORAL (declared-tools == real, run≡build) la prueba el
conformance con fixtures. Juntas: el protocolo es parte de la certificación.
"""
from __future__ import annotations

import inspect
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Capability(Protocol):
    """El contrato estructural de una capability (ver el módulo). `runtime_checkable` permite
    `isinstance(modulo, Capability)` como gate liviano de 'tiene run'; la firma la valida
    `assert_capability` (runtime_checkable solo chequea presencia, no firmas)."""

    def run(self, input: dict, *, ports: Any, tools: Any) -> Any: ...


def _accepts_injection(fn) -> bool:
    """True si `fn` acepta los kwargs `ports` y `tools` (o **kwargs) — lo que el loader inyecta
    SIEMPRE a `run`, y el seam por donde se trazan las tools (L-2)."""
    params = inspect.signature(fn).parameters
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return True
    return {"ports", "tools"} <= set(params)


def _build_is_keyword_only(fn) -> bool:
    """`build(*, checkpointer=None)`: el loader lo llama sin posicionales (`build()` o
    `build(checkpointer=...)`) → ningún parámetro POSICIONAL obligatorio."""
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty:
            return False
    return True


def assert_capability(module) -> list[str]:
    """Los errores (lista vacía = conforme) si `module` no satisface el protocolo `Capability`.
    Estructural — no corre la capability:
    - `run` existe, es callable, y acepta `(input, *, ports, tools)` (el seam de inyección/tracing).
    - si hay `build`, su firma es keyword-only `(*, checkpointer)` — el loader lo llama así.
    """
    name = getattr(module, "__name__", "?")
    run = getattr(module, "run", None)
    if not callable(run):
        return [f"[G-PROTO] la capability '{name}' no expone `run` (el entrypoint puro del runtime)"]
    errs: list[str] = []
    if not _accepts_injection(run):
        errs.append(
            f"[G-PROTO] `run` de '{name}' debe aceptar (input, *, ports, tools) — el loader inyecta "
            "ambos y el mapping `tools` es el seam que hace trazable a la capability"
        )
    build = getattr(module, "build", None)
    if callable(build) and not _build_is_keyword_only(build):
        errs.append(
            f"[G-PROTO] `build` de '{name}' debe ser keyword-only (*, checkpointer) — el loader lo "
            "llama sin argumentos posicionales"
        )
    return errs
