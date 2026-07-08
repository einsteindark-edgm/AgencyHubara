"""El KIT de tracing por-tool — la observabilidad de QUÉ hizo cada tool adentro de un nodo,
sin tocar el código de la capability ni la ejecución durable.

El seam es el mapping `tools` que el loader inyecta a `run(input, *, ports, tools)` (el
protocolo `Capability`): si lo envolvemos, registramos cada llamada. Como el esqueleto de la
capability es PURO y DETERMINISTA (G-DET), no hace falta PERSISTIR el trace en el runtime
durable: lo RECONSTRUIMOS replayeando el `run()` del nodo con tools trazadas sobre su input
registrado. Es fiel porque el supervisor durable corre el MISMO `run()` (vía `build_runnable`,
ver loader) y un standalone `build()` lo garantiza la paridad run≡build (cert).

Tres piezas:
- `ToolLedger`   — el registro ordenado `[{seq, tool, input, output}]` (seq ordinal, sin time).
- `traced(tools, ledger)` — envuelve el mapping para registrar; devuelve el output REAL intacto.
- `replay_with_trace(node, ga_root, input)` — reconstruye el I/O por-tool de un nodo capability.
"""
from __future__ import annotations

import importlib
from typing import Any, Callable, Mapping


class ToolLedger:
    """El registro ordenado de las invocaciones de tools de UNA corrida de capability. Cada entry
    es `{seq, tool, input, output}` con `seq` ordinal (0,1,2…) — DETERMINISTA, sin timestamps,
    para que un golden del trace sea reproducible."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(self, tool: str, input: dict, output: Any) -> None:
        self.entries.append({"seq": len(self.entries), "tool": tool, "input": input, "output": output})


def traced(tools: Mapping[str, Callable], ledger: ToolLedger) -> dict[str, Callable]:
    """Envuelve cada callable del mapping `tools` para que su invocación quede registrada en el
    `ledger` (orden + input + output), devolviendo el output REAL sin alterarlo. La impl es
    AGNÓSTICA — no sabe que la trazan. Las tools del kit son keyword-only (`run(*, payload)`,
    T-IMPL) → el `input` registrado son los kwargs (el `payload`)."""

    def _wrap(tool_id: str, fn: Callable) -> Callable:
        def _traced(*args, **kwargs):
            out = fn(*args, **kwargs)
            inp = dict(kwargs)
            if args:  # el contrato es keyword-only, pero registramos posicionales por si acaso
                inp["_args"] = list(args)
            ledger.record(tool_id, inp, out)
            return out

        return _traced

    return {tool_id: _wrap(tool_id, fn) for tool_id, fn in tools.items()}


def replay_with_trace(node, ga_root, input, ports: dict | None = None) -> dict:
    """Reconstruye el I/O por-tool de UNA capability: corre su `run()` con un mapping de tools
    TRAZADO sobre `input`, y devuelve `{output, tool_trace: [{seq, tool, input, output}]}`. Reusa
    la MISMA resolución de tools del loader (`_resolve_tools`) → el orden y el I/O son exactamente
    los que corren en producción. Un nodo REFERENCIA se resuelve al agente real; un nodo que NO es
    capability hoja (un supervisor) no tiene trace propio → `tool_trace=[]`."""
    from sdk.loader import _resolve_tools  # local: el loader importa registry/manifest → evita ciclo
    from sdk.registry import load_agent_by_id

    if getattr(node, "is_reference", False):
        node = load_agent_by_id(ga_root, node.ref_agent_id)
    if not node.capability:
        return {"output": None, "tool_trace": []}
    run_fn = getattr(importlib.import_module(node.capability.split(":", 1)[0]), "run", None)
    if run_fn is None:
        return {"output": None, "tool_trace": []}
    ledger = ToolLedger()
    bound_ports = {p: (ports or {})[p] for p in node.consumes if p in (ports or {})}
    output = run_fn(input, ports=bound_ports, tools=traced(_resolve_tools(node, ga_root), ledger))
    return {"output": output, "tool_trace": ledger.entries}


def replay_flow_with_trace(node, ga_root, input, ports: dict | None = None) -> dict:
    """Reconstruye el I/O por-tool de TODO un pod desde su seed. Threadea el acumulador EXACTAMENTE
    como `build_runnable` (sequential): cada agente lee su input por su binding `inputs:`, corre con
    tools TRAZADAS, y su output se mergea al acc. Devuelve `{output: acc_final, node_traces: {agente:
    [{seq,tool,input,output}]}}`. Determinista (G-DET) → reconstruye lo que corrió en el durable con
    el MISMO seed, sin tocar la ejecución ni persistir nada. Hoy: leaf + supervisor `sequential` (lo
    que el viewer muestra); router/parallel devuelven node_traces vacío (fuera de alcance del trace)."""
    from sdk.loader import _resolve_binding  # local: el loader importa registry → evita ciclo
    from sdk.registry import load_agent_by_id

    if getattr(node, "is_reference", False):
        node = load_agent_by_id(ga_root, node.ref_agent_id)

    if node.capability:  # capability hoja: un solo nodo
        r = replay_with_trace(node, ga_root, input, ports)
        return {"output": r["output"], "node_traces": {node.name: r["tool_trace"]},
                "node_accs": {node.name: r["output"]}}

    if node.is_supervisor and node.strategy in ("sequential", None):
        state: dict = dict(input) if isinstance(input, dict) else {"input": input}
        node_traces: dict[str, list] = {}
        node_accs: dict[str, dict] = {}  # snapshot del acc DESPUÉS de cada nodo (el «salió» local)
        for a in node.agents:
            label = a.ref_agent_id or a.name or "?"
            agent_input = _resolve_binding(a.inputs, state, label) if a.inputs else state
            r = replay_with_trace(a, ga_root, agent_input, ports)
            node_traces[label] = r["tool_trace"]
            out = r["output"]
            state.update(out if isinstance(out, dict) else {label: out})  # mismo merge que build_runnable
            node_accs[label] = dict(state)
        return {"output": state, "node_traces": node_traces, "node_accs": node_accs}

    # router/parallel: el trace por-tool no aplica hoy
    return {"output": None, "node_traces": {}, "node_accs": {}}
