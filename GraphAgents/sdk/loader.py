"""Compila un `TaskGraphManifest` a algo EJECUTABLE. Dos caminos (mismo modelo):

- `build_runnable(node, ga_root, ports)` → un callable `(input)->output` para el
  **LocalRuntime** (puro, sin agentspan/langgraph): resuelve refs `agent://`,
  inyecta los ports (ConnectorKit) y las tools del catálogo (binding `uses:`).
  Es lo que corre y se testea hoy.
- `build_agent(node, ga_root)` → el `Agent` de **AgentSpan** (runtime real). La
  firma exacta del wrap (StateGraph, tool, agent-as-tool, publish) se cierra en
  G1+ al integrar.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable

from sdk.manifest_model import AgentNode, TaskGraphManifest
from sdk.registry import load_agent_by_id


def _resolve_capability(ref: str) -> Any:
    mod_name, fn_name = ref.split(":", 1)
    return getattr(importlib.import_module(mod_name), fn_name)


def _resolve_tools(node: AgentNode, ga_root: Path) -> dict[str, Callable]:
    """Resuelve las tools del catálogo que el agente bindea (`uses:`) a su callable
    puro (`impl`). Es la inyección que hace reusable a la tool."""
    from sdk.registry import discover_tools

    catalog = {t.id: t for t in discover_tools(ga_root)}
    out: dict[str, Callable] = {}
    for t in node.tools:
        if not t.ref_id:
            continue
        contract = catalog.get(t.ref_id)
        if contract is None:
            raise RuntimeError(f"tool '{t.ref_id}' no está en el catálogo (G-BIND)")
        mod_name, fn = contract.impl.split(":", 1)
        out[t.ref_id] = getattr(importlib.import_module(mod_name), fn)
    return out


def _resolve_binding(binding: dict, state: dict, label: str) -> dict:
    """Resuelve el binding `inputs:` de un agente (`{input_key: $state.<path> | literal}`)
    contra el ESTADO acumulador del task graph. Un `$state.X` ausente es un error de
    cableado/orden — falla LOUD, no adivina."""
    out: dict = {}
    for key, val in binding.items():
        if isinstance(val, str) and val.startswith("$state."):
            path = val[len("$state.") :]
            if path not in state:
                raise RuntimeError(
                    f"task graph: el agente '{label}' lee $state.{path} pero no está en el "
                    "estado todavía (¿orden de los agentes / wiring del binding?)."
                )
            out[key] = state[path]
        else:
            out[key] = val
    return out


def build_runnable(node: AgentNode, ga_root: Path, ports: dict | None = None) -> Callable[[Any], Any]:
    """Un callable ejecutable por el runtime port. No requiere agentspan/langgraph."""
    ports = ports or {}

    if node.is_reference:
        return build_runnable(load_agent_by_id(ga_root, node.ref_agent_id), ga_root, ports)

    if node.capability:
        mod_name = node.capability.split(":", 1)[0]
        mod = importlib.import_module(mod_name)
        run_fn = getattr(mod, "run", None)
        if run_fn is None:
            raise RuntimeError(f"la capability '{mod_name}' no expone `run` (entrypoint puro)")
        bound_ports = {p: ports[p] for p in node.consumes if p in ports}
        tools = _resolve_tools(node, ga_root)

        def runnable(input: Any) -> Any:
            return run_fn(input, ports=bound_ports, tools=tools)

        return runnable

    if node.is_supervisor:
        # router/manual: rutea a UN agente y le pasa el input crudo (sin wiring).
        if node.strategy in ("router", "manual", None):
            subs = {(a.ref_agent_id or a.name): a for a in node.agents}

            def runnable(input: Any) -> Any:
                key = input.get("route") if isinstance(input, dict) else None
                target = subs.get(key) or next(iter(subs.values()))
                return build_runnable(target, ga_root, ports)(input)

            return runnable

        # COMPOSICIÓN — el task graph threadea un ESTADO acumulador: cada agente lee su
        # input por su binding `inputs:` (G-WIRE lo exige) y su output se mergea al estado.
        # Así el DAG (varios extractores → un analyzer → qa → reporter) se cablea solo. Es
        # LA forma de orquestar en esta arquitectura — equivale al State de un StateGraph.
        if node.strategy in ("sequential", "parallel"):
            compiled = [(a, build_runnable(a, ga_root, ports)) for a in node.agents]

            def runnable(input: Any) -> Any:
                state: dict = dict(input) if isinstance(input, dict) else {"input": input}
                for ref, run in compiled:
                    label = ref.ref_agent_id or ref.name or "?"
                    agent_input = _resolve_binding(ref.inputs, state, label) if ref.inputs else state
                    out = run(agent_input)
                    if isinstance(out, dict):
                        state.update(out)  # mergea el patch (modelo reducer del StateGraph)
                    else:
                        state[label] = out
                return state

            return runnable

        raise NotImplementedError(
            f"strategy '{node.strategy}' (handoff/swarm/round_robin/random) = ruteo DINÁMICO; "
            "corre en AgentSpan (G1+). El LocalRuntime threadea sequential/parallel (determinista)."
        )

    def runnable(input: Any) -> Any:  # nodo inline sin capability (G2)
        return input

    return runnable


# --------------------------------------------------- AgentSpan (runtime real, G1+)

def build_agent(node: AgentNode, ga_root: Path | None = None) -> Any:
    """El artefacto que corre `AgentSpanRuntime`: para una capability, el
    `CompiledStateGraph` de LangGraph (`build()` con `compile(name=...)`). AgentSpan
    lo toma DIRECTO — lo autodetecta como langgraph, SIN wrapper `Agent` (ver L-8).
    Supervisor / tools nativos de AgentSpan: G2+."""
    if node.is_reference:
        if ga_root is None:
            raise RuntimeError("para resolver `uses: agent://...` pasá ga_root al loader")
        return build_agent(load_agent_by_id(ga_root, node.ref_agent_id), ga_root)

    if node.capability:
        return _resolve_capability(node.capability)()  # CompiledStateGraph

    raise NotImplementedError(
        "build_agent: G1 corre capabilities (StateGraph) en AgentSpan; supervisor/"
        "tools nativos son G2+ (para el LocalRuntime usá build_runnable)."
    )


def agent_as_tool(ga_root: Path, agent_id: str) -> Any:
    """Envuelve un agente de catálogo como tool de AgentSpan (agent-as-tool). G1+:
    el wrap por execution-id se cierra al integrar."""
    return build_agent(load_agent_by_id(ga_root, agent_id), ga_root)


def load(manifest: TaskGraphManifest, ga_root: Path | None = None) -> Any:
    return build_agent(manifest, ga_root)
