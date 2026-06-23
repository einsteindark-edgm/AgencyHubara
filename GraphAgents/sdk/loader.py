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
import operator
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

from sdk.manifest_model import AgentNode, TaskGraphManifest
from sdk.registry import load_agent_by_id


class _SupervisorState(TypedDict):
    # Un único canal acumulador `acc` SIN reducer custom (LastValue). AgentSpan server-side
    # solo mapea `operator.add` (L-14): un reducer de merge custom se ignora → last-write-wins.
    # Por eso cada nodo MERGEA EN CÓDIGO y devuelve el acc COMPLETO → last-write-wins es correcto
    # (secuencial). `acc: dict` (builtin) resuelve en get_type_hints aún con future-annotations (L-13).
    acc: dict


class _ParallelSupervisorState(TypedDict):
    # Para `parallel`: `acc` LastValue (el seed, READ-ONLY durante el fan-out) + `patches` con
    # reducer `operator.add` (el ÚNICO server-safe, L-14) → cada agente concurrente APPENDEA su
    # output `[out]` sin pelear por un canal; un nodo `join` foldea los patches al acc. Tipos a
    # nivel módulo para que get_type_hints resuelva `operator.add`/`Annotated` (L-13).
    acc: dict
    patches: Annotated[list, operator.add]


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

def build_agent(node: AgentNode, ga_root: Path | None = None, ports: dict | None = None) -> Any:
    """El artefacto que corre `AgentSpanRuntime`: para una capability, el
    `CompiledStateGraph` de LangGraph (`build()` con `compile(name=...)`). AgentSpan
    lo toma DIRECTO — lo autodetecta como langgraph, SIN wrapper `Agent` (ver L-8).
    `ports`: los vendors (ConnectorKit) que el durable inyecta a los miembros que `consumes:`
    (ej. el port `llm` del reporter → LiteLLMProxy/FixtureLLM). Supervisor / tools nativos: G2+."""
    if node.is_reference:
        if ga_root is None:
            raise RuntimeError("para resolver `uses: agent://...` pasá ga_root al loader")
        return build_agent(load_agent_by_id(ga_root, node.ref_agent_id), ga_root, ports)

    if node.capability:
        return _resolve_capability(node.capability)()  # CompiledStateGraph

    if node.is_supervisor:
        return build_supervisor_graph(node, ga_root, ports=ports)

    raise NotImplementedError(
        "build_agent: G1 corre capabilities (StateGraph); G2 compone supervisors "
        "(sequential/parallel) a un grafo nativo. Tools nativos de AgentSpan: G2.x."
    )


def build_supervisor_graph(node: AgentNode, ga_root: Path | None, *, checkpointer=None, ports: dict | None = None) -> Any:
    """G2 — compila un supervisor que COMPONE a UN `StateGraph` nativo. Cada agente es un NODO:
    resuelve su binding `inputs:` desde el acumulador → corre su capability (su `run` puro, vía
    `build_runnable`) → MERGEA EN CÓDIGO y devuelve el `acc` COMPLETO. Dos topologías hoy:
    - `sequential`: cadena START→a→b→…→END (cada nodo ve el acc del anterior).
    - `router`: UN nodo dispatcher que elige UN agente por `acc['route']` (default: el 1ro) y lo
      corre. Los agentes router no declaran `inputs:` (G-WIRE los exime) → reciben el acc crudo.
      (El conditional edge NATIVO de langgraph cuelga en Conductor — por eso un nodo, L-15.)

    Por qué merge-en-código y no un reducer (L-14): AgentSpan compila el grafo multi-nodo a
    tasks de Conductor POR-NODO y server-side SOLO mapea `operator.add` — un reducer de merge
    custom se ignora → last-write-wins → el acumulador se perdería. Devolviendo el acc COMPLETO
    en un canal LastValue, last-write-wins ES correcto (cadena secuencial / 1 agente ruteado).
    `acc` dinámico: no hay que declarar las claves; los outputs terminales sobreviven.

    `parallel`: fan-out de START a agentes INDEPENDIENTES + un `join`; cada uno appendea su
    output a `patches` (reducer `operator.add`, server-safe) y el join los foldea al acc.

    Con `checkpointer`, recovery por-nodo cuando LangGraph drive (Phase C/L-11). `handoff`/
    `swarm` (routing dinámico multi-vuelta) y nesting de los `build()` subgrafos = G2.x."""
    if ga_root is None:
        raise RuntimeError("para componer un supervisor pasá ga_root al loader")
    if node.strategy not in ("sequential", "router", "parallel"):
        raise NotImplementedError(
            f"build_supervisor_graph: strategy '{node.strategy}' no se compone como StateGraph "
            "determinista. `sequential`/`router`/`parallel` sí. `handoff`/`swarm` son NATIVOS de "
            "AgentSpan y LLM-driven (Agent(strategy='swarm', handoffs=[OnToolResult/OnTextMention])) "
            "→ van con el nodo LLM, no acá (L-16)."
        )
    from langgraph.graph import END, START, StateGraph

    # THREAD los ports a cada miembro (mismo que el LocalRuntime): un miembro que `consumes:` un
    # port (ej. el reporter → `llm`) recibe su vendor en el durable. `build_runnable` filtra por
    # `consumes` (bound_ports) → solo le llega lo que declaró.
    compiled = [(a, build_runnable(a, ga_root, ports or {})) for a in node.agents]

    if node.strategy == "parallel":
        return _build_parallel_graph(node, compiled, checkpointer)

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
    if node.strategy == "sequential":
        prev = START
        for i, (ref, run) in enumerate(compiled):
            name = f"{(ref.ref_agent_id or ref.name or 'step').replace('-', '_')}_{i}"  # langgraph node id
            g.add_node(name, _make_node(ref, run))
            g.add_edge(prev, name)
            prev = name
        g.add_edge(prev, END)
    else:  # router: UN nodo que despacha por `route` (server-safe). El conditional edge de
        # langgraph genera un task `__start__router` que CUELGA en Conductor (L-15); en su lugar
        # un solo nodo elige y corre el agente. Misma semántica que el router del LocalRuntime.
        by_id = {(ref.ref_agent_id or ref.name): (ref, run) for ref, run in compiled}

        def _router_node(state: _SupervisorState) -> dict:
            acc = dict(state["acc"])
            route = acc.get("route")
            if route is None:
                ref, run = compiled[0]  # sin `route` → default documentado: el 1er agente
            elif route in by_id:
                ref, run = by_id[route]
            else:  # `route` presente pero typo/desconocido → NO degradar en silencio al 1ro
                raise ValueError(
                    f"router: la ruta '{route}' no existe entre {sorted(by_id)}. Sin `route` "
                    "corre el default (el 1ro); una ruta presente debe ser una clave válida."
                )
            label = ref.ref_agent_id or ref.name or "?"
            agent_input = _resolve_binding(ref.inputs, acc, label) if ref.inputs else acc
            acc.update(run(agent_input))
            return {"acc": acc}

        g.add_node("router", _router_node)
        g.add_edge(START, "router")
        g.add_edge("router", END)

    return g.compile(name=node.name or "supervisor", checkpointer=checkpointer)


def _build_parallel_graph(node: AgentNode, compiled: list, checkpointer) -> Any:
    """G2.x `parallel` — fan-out de START a agentes INDEPENDIENTES + un `join`. Cada agente lee
    el seed (`acc`, LastValue read-only) por su binding, corre, y APPENDEA su output a `patches`
    (reducer `operator.add` — el único server-safe, L-14): así dos agentes concurrentes no pelean
    por un canal. El `join` foldea los patches al acc. SOLO para agentes que escriben claves
    DISJUNTAS; si dependen entre sí, su binding falla LOUD (faltaría la clave en el seed)."""
    from langgraph.graph import END, START, StateGraph

    def _make_par_node(ref: AgentNode, run: Callable):
        label = ref.ref_agent_id or ref.name or "?"

        def _node(state: _ParallelSupervisorState) -> dict:
            acc = state["acc"]  # el seed, read-only durante el fan-out
            # copia al pasar el acc entero (consistente con el nodo secuencial; evita aliasing
            # del seed compartido si una capability mutara su input):
            agent_input = _resolve_binding(ref.inputs, acc, label) if ref.inputs else dict(acc)
            out = run(agent_input)
            return {"patches": [out if isinstance(out, dict) else {label: out}]}  # operator.add

        return _node

    def _join(state: _ParallelSupervisorState) -> dict:
        acc = dict(state["acc"])
        written: set = set()
        for patch in state["patches"]:  # foldea los outputs concurrentes
            dup = written & set(patch)
            if dup:  # dos agentes paralelos escriben la misma clave → last-write-wins perdería
                raise ValueError(  # datos en SILENCIO → fallá LOUD (parallel exige claves disjuntas)
                    f"parallel: la(s) clave(s) {sorted(dup)} las escribe más de un agente "
                    "concurrente — el merge perdería un output en silencio. `parallel` exige "
                    "outputs DISJUNTOS; si los agentes dependen entre sí o comparten salida, usá "
                    "`sequential`."
                )
            written |= set(patch)
            acc.update(patch)
        return {"acc": acc}

    g = StateGraph(_ParallelSupervisorState)
    g.add_node("join", _join)
    for i, (ref, run) in enumerate(compiled):
        name = f"{(ref.ref_agent_id or ref.name or 'step').replace('-', '_')}_{i}"
        g.add_node(name, _make_par_node(ref, run))
        g.add_edge(START, name)  # fan-out
        g.add_edge(name, "join")  # fan-in
    g.add_edge("join", END)
    return g.compile(name=node.name or "supervisor", checkpointer=checkpointer)


def agent_as_tool(ga_root: Path, agent_id: str) -> Any:
    """Envuelve un agente de catálogo como tool de AgentSpan (agent-as-tool). G1+:
    el wrap por execution-id se cierra al integrar."""
    return build_agent(load_agent_by_id(ga_root, agent_id), ga_root)


def load(manifest: TaskGraphManifest, ga_root: Path | None = None) -> Any:
    return build_agent(manifest, ga_root)
