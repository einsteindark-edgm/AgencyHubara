"""Los checks G-* de los **manifests** — única fuente de verdad. Cada check toma
el nodo raíz (+ opcionalmente la raíz del proyecto, para resolver el catálogo) y
devuelve errores (rompen la cert) o warnings (permitidos en C2). `level_of`
computa C0–C3.

Reglas (manifest):
- C1            — refs de `capability` importan.
- G-RUN-SIG     — la capability expone `run(input, *, ports, tools)` (firma uniforme
                  que inyecta el loader; ver L-2).
- supervisor    — coherente (agents + strategy).
- G-BIND        — toda tool `uses: <id>` resuelve a una tool del catálogo.
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
        + check_tool_refs(root, ga_root)
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
