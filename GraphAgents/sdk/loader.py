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
from typing import Any, Callable, TypedDict

from sdk.manifest_model import AgentNode, TaskGraphManifest
from sdk.registry import load_agent_by_id


class _SupervisorState(TypedDict):
    # Un único canal acumulador `acc` SIN reducer custom (LastValue). AgentSpan server-side
    # solo mapea `operator.add` (L-14): un reducer de merge custom se ignora → last-write-wins.
    # Por eso cada nodo MERGEA EN CÓDIGO y devuelve el acc COMPLETO → last-write-wins es correcto
    # (secuencial). `acc: dict` (builtin) resuelve en get_type_hints aún con future-annotations (L-13).
    acc: dict


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
        # NOTA: el LocalRuntime corre `parallel` SECUENCIALMENTE (alias pendiente de canales
        # por-clave); en AgentSpan `parallel` aún no es server-safe (raise en build_supervisor_graph,
        # L-14). Hoy inocuo: ningún manifest usa `parallel`. Divergencia local↔server a cerrar en G2.x.
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

    if node.is_supervisor:
        return build_supervisor_graph(node, ga_root)

    raise NotImplementedError(
        "build_agent: G1 corre capabilities (StateGraph); G2 compone supervisors "
        "(sequential/parallel) a un grafo nativo. Tools nativos de AgentSpan: G2.x."
    )


def build_supervisor_graph(node: AgentNode, ga_root: Path | None, *, checkpointer=None) -> Any:
    """G2 — compila un supervisor SECUENCIAL que COMPONE a UN `StateGraph` nativo. Cada agente
    es un NODO: resuelve su binding `inputs:` desde el acumulador → corre su capability (su
    `run` puro, vía `build_runnable`) → MERGEA EN CÓDIGO y devuelve el `acc` COMPLETO.

    Por qué merge-en-código y no un reducer (L-14): AgentSpan compila el grafo multi-nodo a
    tasks de Conductor POR-NODO (un worker por nodo) y server-side SOLO mapea `operator.add`
    como reducer — un reducer de merge custom se ignora → last-write-wins → el acumulador se
    perdería (cada nodo vería solo el output del anterior). Devolviendo el acc COMPLETO en un
    canal LastValue, last-write-wins ES correcto para una cadena secuencial. `acc` dinámico:
    no hay que declarar las claves; los outputs terminales (markdown/verdict) sobreviven.

    Con `checkpointer`, recovery por-nodo cuando LangGraph drive (Phase C/L-11). `parallel`,
    routing dinámico (router/handoff/swarm) y nesting de los `build()` subgrafos = G2.x."""
    if ga_root is None:
        raise RuntimeError("para componer un supervisor pasá ga_root al loader")
    if node.strategy != "sequential":
        raise NotImplementedError(
            f"build_supervisor_graph: strategy '{node.strategy}' = G2.x. Hoy se compone "
            "`sequential` (single-acc + merge-en-código + last-write-wins, server-safe). "
            "`parallel` necesita canales por-clave / operator.add server-side (L-14)."
        )
    from langgraph.graph import END, START, StateGraph

    compiled = [(a, build_runnable(a, ga_root)) for a in node.agents]

    def _make_node(ref: AgentNode, run: Callable):
        label = ref.ref_agent_id or ref.name or "?"

        def _node(state: _SupervisorState) -> dict:
            acc = dict(state["acc"])  # copia del acumulador completo
            agent_input = _resolve_binding(ref.inputs, acc, label) if ref.inputs else acc
            out = run(agent_input)
            acc.update(out if isinstance(out, dict) else {label: out})  # merge EN CÓDIGO
            return {"acc": acc}  # devolver el acc COMPLETO (LastValue server-safe)

        return _node

    g = StateGraph(_SupervisorState)
    prev = START
    for i, (ref, run) in enumerate(compiled):
        name = f"{(ref.ref_agent_id or ref.name or 'step').replace('-', '_')}_{i}"  # langgraph node id
        g.add_node(name, _make_node(ref, run))
        g.add_edge(prev, name)
        prev = name
    g.add_edge(prev, END)

    return g.compile(name=node.name or "supervisor", checkpointer=checkpointer)


def agent_as_tool(ga_root: Path, agent_id: str) -> Any:
    """Envuelve un agente de catálogo como tool de AgentSpan (agent-as-tool). G1+:
    el wrap por execution-id se cierra al integrar."""
    return build_agent(load_agent_by_id(ga_root, agent_id), ga_root)


def load(manifest: TaskGraphManifest, ga_root: Path | None = None) -> Any:
    return build_agent(manifest, ga_root)
