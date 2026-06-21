"""El **replay** de un caso — corre el triple input fijo (seed + port-fixtures + llm-reply) por
su target y devuelve el output determinista. NO inventa motor: reusa lo que ya existe.

- `tool:<id>`   → la impl PURA del catálogo, `impl(**seed)` (keyword-only, T-IMPL).
- `agent:<id>`  → `build_runnable(<id>.agent.yaml)` con los ports armados desde el caso
                  (FixtureMetaInsights/FixtureLLM) — lo que `/api/run` rechaza hoy.
- `flow:<id>`   → `build_runnable(<id>.taskgraph.yaml)` threadea el estado por los agentes.

`build_runnable` ya inyecta los ports que el nodo `consumes` y auto-resuelve las tools del
catálogo (G-BIND); acá solo construimos el `ports` fijo del caso. Sin red, sin runtime durable:
es el golden-replay, el mismo determinismo que sostiene G-DET.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable

from sdk.case_model import Case, resolve
from sdk.connectorkit.ports import FIXTURE_VENDORS
from sdk.loader import build_runnable
from sdk.manifest_model import load_manifest
from sdk.registry import discover_tools, load_agent_by_id


def _tool_impl(tool_id: str, ga_root: Path) -> Callable:
    catalog = {t.id: t for t in discover_tools(ga_root)}
    contract = catalog.get(tool_id)
    if contract is None:
        raise RuntimeError(f"tool '{tool_id}' no está en el catálogo (tools/)")
    mod_name, fn = contract.impl.split(":", 1)
    return getattr(importlib.import_module(mod_name), fn)


def _target_node(case: Case, ga_root: Path):
    if case.kind == "agent":
        return load_agent_by_id(ga_root, case.target_id)  # <id>.agent.yaml
    p = ga_root / "manifests" / f"{case.target_id}.taskgraph.yaml"  # flow
    if not p.exists():
        raise RuntimeError(f"flow '{case.target_id}' no existe ({p})")
    return load_manifest(p)


def build_ports(case: Case, ga_root: Path) -> dict[str, Any]:
    """El `ports` fijo del caso: cada port_key → su vendor fixture construido con la data del
    caso (`{$ref}` resuelto). Para `llm`, la data es la respuesta fija (FixtureLLM)."""
    out: dict[str, Any] = {}
    for pk, val in case.ports.items():
        builder = FIXTURE_VENDORS.get(pk)
        if builder is None:
            raise RuntimeError(
                f"el caso '{case.id}' inyecta el port '{pk}' sin fixture vendor "
                f"(conocidos: {sorted(FIXTURE_VENDORS)})"
            )
        out[pk] = builder(resolve(val, ga_root))
    return out


def replay_case(case: Case, ga_root: Path) -> Any:
    """Corre el caso → su output determinista. El motor lo eligen el kind y el loader."""
    seed = resolve(case.seed, ga_root)
    if case.kind == "tool":
        return _tool_impl(case.target_id, ga_root)(**seed)
    # agent | flow: el loader resuelve refs, inyecta los ports del caso y las tools del catálogo.
    node = _target_node(case, ga_root)
    return build_runnable(node, ga_root, build_ports(case, ga_root))(seed)
