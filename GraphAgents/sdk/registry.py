"""El **palette / registry** — indexa el catálogo de **tools** (`tools/<id>/tool.yaml`)
y de **agentes** (`manifests/<id>.agent.yaml`) para que un task graph agarre del
catálogo en vez de redefinir inline. Frontends: la CLI (`list-tools`, `search`,
`list-agents`) y los checks de binding (G-BIND, G-BIND-AGENT).
"""
from __future__ import annotations

from pathlib import Path

from sdk.manifest_model import TaskGraphManifest, load_manifest
from sdk.tool_model import ToolContract, load_tool

# ---------------------------------------------------------------- tools

def discover_tools(ga_root: Path) -> list[ToolContract]:
    return [load_tool(p) for p in sorted((ga_root / "tools").glob("*/tool.yaml"))]


def discover_tool_ids(ga_root: Path) -> set[str]:
    return {t.id for t in discover_tools(ga_root)}


def index(ga_root: Path) -> list[dict]:
    return [
        {
            "id": t.id,
            "version": t.version,
            "side_effect": t.side_effect,
            "approval_required": t.approval_required,
            "tags": t.tags,
            "description": t.description,
        }
        for t in discover_tools(ga_root)
    ]


def search(ga_root: Path, term: str) -> list[dict]:
    term = term.lower()
    return [
        row
        for row in index(ga_root)
        if term in row["id"].lower()
        or term in row["description"].lower()
        or any(term in tag.lower() for tag in row["tags"])
    ]


# ---------------------------------------------------------------- agentes

def discover_agents(ga_root: Path) -> list[TaskGraphManifest]:
    """Los agentes de catálogo = `manifests/*.agent.yaml` (los `*.taskgraph.yaml`
    son composiciones, no entradas de catálogo)."""
    return [load_manifest(p) for p in sorted((ga_root / "manifests").glob("*.agent.yaml"))]


def discover_agent_ids(ga_root: Path) -> set[str]:
    return {a.name for a in discover_agents(ga_root) if a.name}


def load_agent_by_id(ga_root: Path, agent_id: str) -> TaskGraphManifest:
    p = ga_root / "manifests" / f"{agent_id}.agent.yaml"
    if not p.exists():
        raise FileNotFoundError(f"no existe el agente de catálogo '{agent_id}' ({p})")
    return load_manifest(p)


def agent_index(ga_root: Path) -> list[dict]:
    return [
        {
            "id": a.name,
            "archetype": a.archetype,
            "exposes_as_tool": a.exposes_as_tool,
            "publish": a.publish.as_ if a.publish else None,
            "description": a.description,
        }
        for a in discover_agents(ga_root)
    ]
