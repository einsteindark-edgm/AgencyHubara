"""Serializa el SISTEMA a un grafo `{nodes, edges}` desde el registry — la **única
fuente** que consumen todos los frontends del explorer:

- el CLI (`graph --format=mermaid|json`),
- el visor estático React Flow (`viewer/index.html`),
- el backend http vivo (`viewer/server.py`, `GET /api/graph`).

Mismo patrón que el TestKit: una fuente, varios frontends. Es una **proyección**
read-only de los manifests + el catálogo (NO los muta): los manifests siguen
siendo la verdad (git + cert + TDD).

Nodos (`kind`):
- `tool`  — una tool agnóstica del catálogo (`tools/<id>/tool.yaml`).
- `agent` — un agente de catálogo (`*.agent.yaml`) o un task graph (`*.taskgraph.yaml`).
- `port`  — un connector del ConnectorKit (lo que un agente `consumes:`).

Aristas (`kind`):
- `uses`     — agente → tool   (binding `uses:`/`with:`, G-BIND).
- `agent`    — supervisor → agente (`uses: agent://<id>`, G-BIND-AGENT).
- `consumes` — agente → port   (`consumes:`, G-PORT).
"""
from __future__ import annotations

from pathlib import Path

from sdk.connectorkit.ports import PORTS
from sdk.manifest_model import AgentNode, load_manifest
from sdk.registry import discover_agents, discover_tools
from sdk.testkit.checks import level_of
from sdk.testkit.tool_checks import tool_level

CertLevel = str


def _safe_tool_cert(contract, ga_root: Path) -> CertLevel:
    try:
        return tool_level(contract, ga_root)
    except Exception:  # noqa: BLE001 — el visor nunca rompe por un manifest a medio hacer
        return "none"


def _safe_agent_cert(manifest, ga_root: Path) -> CertLevel:
    try:
        return level_of(manifest, ga_root)
    except Exception:  # noqa: BLE001
        return "none"


def _tool_node(contract, ga_root: Path) -> dict:
    return {
        "id": f"tool:{contract.id}",
        "kind": "tool",
        "label": contract.id,
        "version": contract.version,
        "side_effect": contract.side_effect,
        "idempotent": contract.idempotent,
        "approval_required": contract.approval_required,
        "credentials": list(contract.credentials),
        "tags": list(contract.tags),
        "certification": _safe_tool_cert(contract, ga_root),
        "description": contract.description,
    }


def _agent_node(m: AgentNode, ga_root: Path) -> dict:
    return {
        "id": f"agent:{m.name}",
        "kind": "agent",
        "label": m.name,
        "archetype": m.archetype,
        "strategy": m.strategy,
        "capability": m.capability,
        "certification": _safe_agent_cert(m, ga_root),
        "declared_cert": m.certification,
        "exposes_as_tool": m.exposes_as_tool,
        "publish": m.publish.as_ if m.publish else None,
        "consumes": list(m.consumes),
        "description": m.description,
    }


def _port_node(name: str) -> dict:
    return {
        "id": f"port:{name}",
        "kind": "port",
        "label": name,
        "contract": PORTS.get(name),
        "vendors": ["fixture"],          # determinista (golden); sin red
        "planned_vendors": ["live", "warehouse"],  # G2/G3
    }


def _edges_of(m: AgentNode) -> list[dict]:
    """Las aristas que salen de un nodo: tools (uses), sub-agentes (agent), ports."""
    src = f"agent:{m.name}"
    out: list[dict] = []
    for t in m.tools:
        if t.ref_id:  # tool del catálogo (las inline no son nodos del palette)
            out.append(
                {
                    "source": src,
                    "target": f"tool:{t.ref_id}",
                    "kind": "uses",
                    "binding": dict(t.binding),
                }
            )
    for a in m.agents:
        target = a.ref_agent_id or a.name
        out.append(
            {
                "source": src,
                "target": f"agent:{target}",
                "kind": "agent",
                "strategy": m.strategy,
            }
        )
    for p in m.consumes:
        out.append({"source": src, "target": f"port:{p}", "kind": "consumes"})
    return out


def build_graph(ga_root: Path | str) -> dict:
    """Proyecta todo el catálogo + las composiciones a `{nodes, edges}`."""
    ga_root = Path(ga_root)
    nodes: dict[str, dict] = {}  # id -> node (dedup)
    edges: list[dict] = []

    # tools del catálogo
    for contract in discover_tools(ga_root):
        n = _tool_node(contract, ga_root)
        nodes[n["id"]] = n

    # agentes de catálogo (*.agent.yaml) — la definición rica del nodo
    catalog_agents: list[AgentNode] = discover_agents(ga_root)
    for m in catalog_agents:
        n = _agent_node(m, ga_root)
        nodes[n["id"]] = n

    # task graphs (*.taskgraph.yaml) — supervisores/composiciones
    task_graphs: list[AgentNode] = [
        load_manifest(p) for p in sorted((ga_root / "manifests").glob("*.taskgraph.yaml"))
    ]
    for m in task_graphs:
        nid = f"agent:{m.name}"
        if nid not in nodes:  # un task graph root también es un nodo del sistema
            nodes[nid] = _agent_node(m, ga_root)

    # aristas: de cada agente de catálogo y cada task graph
    for m in [*catalog_agents, *task_graphs]:
        edges.extend(_edges_of(m))

    # ports: los del registry + cualquiera que un agente consuma
    port_names = set(PORTS) | {
        p for m in [*catalog_agents, *task_graphs] for p in m.consumes
    }
    for name in sorted(port_names):
        nid = f"port:{name}"
        if nid not in nodes:
            nodes[nid] = _port_node(name)

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
    }


# --------------------------------------------------------------------- mermaid

def _mid(node_id: str) -> str:
    """id de mermaid (sin `:` ni `-`)."""
    return node_id.replace(":", "_").replace("-", "_")


def _node_label(n: dict) -> str:
    if n["kind"] == "tool":
        return f"{n['label']}<br/>tool · {n['side_effect']} · {n['certification']}"
    if n["kind"] == "port":
        return f"{n['label']}<br/>port"
    sub = " · ".join(x for x in (n.get("archetype"), n.get("certification")) if x)
    return f"{n['label']}<br/>{sub}" if sub else n["label"]


def _node_shape(n: dict, label: str) -> str:
    mid = _mid(n["id"])
    if n["kind"] == "tool":
        return f'  {mid}(["{label}"])'          # rounded = tool
    if n["kind"] == "port":
        return f'  {mid}[("{label}")]'          # cilindro = connector
    return f'  {mid}["{label}"]'                # caja = agente


def to_mermaid(graph: dict) -> str:
    """Render `flowchart LR` — se ve directo en GitHub/Markdown (sin UI)."""
    lines = ["flowchart LR"]
    for n in graph["nodes"]:
        lines.append(_node_shape(n, _node_label(n)))
    for e in graph["edges"]:
        s, t = _mid(e["source"]), _mid(e["target"])
        if e["kind"] == "consumes":
            lines.append(f"  {s} -. consume .-> {t}")
        elif e["kind"] == "agent":
            lines.append(f"  {s} -->|{e.get('strategy') or 'agent'}| {t}")
        else:  # uses
            lines.append(f"  {s} -->|uses| {t}")
    return "\n".join(lines) + "\n"
