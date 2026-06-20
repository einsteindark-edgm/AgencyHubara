"""Los checks G-* de los **manifests** — única fuente de verdad. Cada check toma
el nodo raíz (+ opcionalmente la raíz del proyecto, para resolver el catálogo) y
devuelve errores (rompen la cert) o warnings (permitidos en C2). `level_of`
computa C0–C3.

Reglas (manifest):
- C1            — refs de `capability` importan.
- G-RUN-SIG     — la capability expone `run(input, *, ports, tools)` (firma uniforme
                  que inyecta el loader; ver L-2).
- supervisor    — coherente (agents + strategy).
- G-WIRE        — un supervisor que COMPONE declara `inputs:` en cada agente (el task
                  graph se cablea y corre por su manifest; ver L-10).
- G-BIND        — toda tool `uses: <id>` resuelve a una tool del catálogo, y su `with:`
                  nombra SOLO inputs del contrato de esa tool (binding↔contrato).
- G-BIND-AGENT  — toda referencia `uses: agent://<id>` resuelve a un agente del catálogo.
- G-DUR (warn)  — tool INLINE cuyo nombre sugiere acción outward.

Los checks de las TOOLS (T-IMPL/T-DUR/G-AGNOSTIC) viven en `tool_checks.py`.
"""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path

from sdk.manifest_model import AgentNode, iter_nodes
from sdk.registry import discover_agent_ids, discover_tool_ids

_OUTWARD = ("recommend", "apply", "update", "create", "set", "pause", "spend", "budget", "delete")

# Estrategias que NO componen: rutean a UN agente y le pasan el input crudo (sin wiring).
_NO_WIRE_STRATEGIES = ("router", "manual")


def _accepts_injection(fn) -> bool:
    """True si `fn` acepta los kwargs `ports` y `tools` (o **kwargs). El loader los
    inyecta SIEMPRE a una capability `run` (L-2)."""
    params = inspect.signature(fn).parameters
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return True
    return {"ports", "tools"} <= set(params)


def check_capability_refs(root: AgentNode) -> list[str]:
    """C1: cada `capability: modulo:funcion` debe importar y existir."""
    errs: list[str] = []
    for n in iter_nodes(root):
        ref = n.capability
        if not ref:
            continue
        if ":" not in ref:
            errs.append(f"[C1-CAP] capability '{ref}' (agente {n.name}) debe ser 'modulo:funcion'")
            continue
        mod_name, fn_name = ref.split(":", 1)
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:  # noqa: BLE001
            errs.append(f"[C1-CAP] capability '{ref}' (agente {n.name}) no importa: {e}")
            continue
        if not hasattr(mod, fn_name):
            errs.append(f"[C1-CAP] capability '{ref}' (agente {n.name}): {mod_name} no tiene '{fn_name}'")
    return errs


def check_capability_run_signature(root: AgentNode) -> list[str]:
    """G-RUN-SIG: la capability expone `run` con la firma uniforme que inyecta el
    loader (`input, *, ports, tools`). Sin esto, correrla por el runtime truena
    con TypeError (L-2)."""
    errs: list[str] = []
    for n in iter_nodes(root):
        if not n.capability:
            continue
        mod_name = n.capability.split(":", 1)[0]
        try:
            mod = importlib.import_module(mod_name)
        except Exception:  # noqa: BLE001
            continue  # ya lo reporta check_capability_refs
        run = getattr(mod, "run", None)
        if run is None:
            errs.append(f"[G-RUN-SIG] capability '{n.name}' no expone `run` (entrypoint puro del runtime)")
            continue
        if not _accepts_injection(run):
            errs.append(
                f"[G-RUN-SIG] `run` de '{n.name}' debe aceptar (input, *, ports, tools) — "
                "el loader inyecta ambos"
            )
    return errs


def check_supervisor_coherent(root: AgentNode) -> list[str]:
    errs: list[str] = []
    for n in iter_nodes(root):
        if n.archetype != "supervisor":
            continue
        if not n.agents:
            errs.append(f"[SUP] supervisor '{n.name}' no declara `agents`")
        if not n.strategy:
            errs.append(f"[SUP] supervisor '{n.name}' no declara `strategy`")
    return errs


def check_taskgraph_wireable(root: AgentNode) -> list[str]:
    """G-WIRE: en un supervisor que COMPONE (no router/manual), CADA agente referenciado
    debe declarar `inputs:` (el binding state→input del task graph), para que el loader
    pueda CABLEAR el grafo y correrlo por su manifest. La orquestación en esta arquitectura
    ES el task graph: un supervisor que compone sin wiring no orquesta — correría UN agente
    en silencio (el caso 'panel verde, feature roto', L-10). Por eso es parte de la cert."""
    errs: list[str] = []
    for n in iter_nodes(root):
        if n.archetype != "supervisor" or not n.agents:
            continue
        if n.strategy in _NO_WIRE_STRATEGIES:
            continue  # router/manual: rutea a UN agente y le pasa el input — sin wiring
        for a in n.agents:
            if not a.inputs:
                errs.append(
                    f"[G-WIRE] supervisor '{n.name}' (strategy={n.strategy}): el agente "
                    f"'{a.ref_agent_id or a.name}' no declara `inputs:` — el task graph no se "
                    "puede cablear ni correr por su manifest (la orquestación ES el task graph)."
                )
    return errs


def check_tool_refs(root: AgentNode, ga_root: Path | None) -> list[str]:
    """G-BIND: toda tool `uses: <id>` resuelve a una tool del catálogo."""
    if ga_root is None:
        return []
    available = discover_tool_ids(ga_root)
    errs: list[str] = []
    for n in iter_nodes(root):
        for t in n.tools:
            rid = t.ref_id
            if rid and rid not in available:
                errs.append(
                    f"[G-BIND] agente '{n.name}' usa tool '{t.uses}' que no está en el catálogo (tools/)"
                )
    return errs


def check_tool_bindings_match_contract(root: AgentNode, ga_root: Path | None) -> list[str]:
    """G-BIND (binding↔contrato): cada clave del `with:` de una tool `uses:` debe ser un
    input DECLARADO en el contrato de esa tool (`tool.yaml` `inputs`). Un `with:` que nombra
    claves que el contrato no tiene es documentación que MIENTE: inerte (el loader inyecta la
    impl, no aplica el binding) pero engañosa para un lector. Regla de oro del plugin-protocol:
    ningún campo del manifest sin su check — `with:` (modelado `ToolSpec.binding`) ahora tiene el suyo."""
    if ga_root is None:
        return []
    from sdk.registry import discover_tools  # local: evita que ruff borre el import entre edits

    catalog = {t.id: t for t in discover_tools(ga_root)}
    errs: list[str] = []
    for n in iter_nodes(root):
        for t in n.tools:
            rid = t.ref_id
            if not rid or not t.binding:
                continue
            contract = catalog.get(rid)
            if contract is None:
                continue  # tool ausente del catálogo: ya lo reporta check_tool_refs
            declared = set(contract.inputs)
            unknown = sorted(k for k in t.binding if k not in declared)
            if unknown:
                errs.append(
                    f"[G-BIND] agente '{n.name}': el `with:` de la tool '{rid}' nombra {unknown} "
                    f"que no son inputs del contrato (tool.yaml inputs: {sorted(declared)})"
                )
    return errs


def check_agent_refs(root: AgentNode, ga_root: Path | None) -> list[str]:
    """G-BIND-AGENT: toda referencia `uses: agent://<id>` resuelve al catálogo."""
    if ga_root is None:
        return []
    available = discover_agent_ids(ga_root)
    errs: list[str] = []
    for n in iter_nodes(root):
        if not n.is_reference:
            continue
        rid = n.ref_agent_id
        if rid and rid not in available:
            errs.append(
                f"[G-BIND-AGENT] referencia 'agent://{rid}' no está en el catálogo (manifests/*.agent.yaml)"
            )
    return errs


def check_outward_tools_need_approval(root: AgentNode) -> list[str]:
    """G-DUR (warning): una tool INLINE que parece outward debe pedir aprobación."""
    warns: list[str] = []
    for n in iter_nodes(root):
        for t in n.tools:
            if t.name and any(k in t.name for k in _OUTWARD) and not t.approval_required:
                warns.append(
                    f"[G-DUR] tool inline '{t.name}' (agente {n.name}) parece outward y "
                    "no declara approval_required"
                )
    return warns


def run_checks(root: AgentNode, ga_root: Path | None = None) -> dict:
    """{'errors': [...], 'warnings': [...]} — el reporte que consumen los 3 frontends."""
    errors = (
        check_capability_refs(root)
        + check_capability_run_signature(root)
        + check_supervisor_coherent(root)
        + check_taskgraph_wireable(root)
        + check_tool_refs(root, ga_root)
        + check_tool_bindings_match_contract(root, ga_root)
        + check_agent_refs(root, ga_root)
    )
    warnings = check_outward_tools_need_approval(root)
    return {"errors": errors, "warnings": warnings}


def level_of(root: AgentNode, ga_root: Path | None = None) -> str:
    """C0 (algo declarado no existe) · C1 (carga, hay warnings) · C2 (TCK verde)."""
    res = run_checks(root, ga_root)
    if res["errors"]:
        return "C0"
    return "C2" if not res["warnings"] else "C1"
