"""Inspección de un nodo/arista del grafo para el explorer — SIN mostrar código:
- **files**: qué archivos respaldan ese nodo o esa relación (para ir a buscarlos al IDE).
- **checks**: qué reglas G-*/cert GARANTIZAN ese nodo o esa relación (para confiar a solo
  visual). Los checks son los MISMOS del TestKit (`sdk.testkit`): una arista `uses` la
  garantiza G-BIND (binding↔contrato), una `agent` la garantizan G-BIND-AGENT + G-WIRE, etc.

Todo es proyección read-only y determinista (paths + correr los checks puros). El frontend
arma los links `vscode://file/<abspath>` para abrir cada archivo en el IDE (sin pasar por acá).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sdk.manifest_model import load_manifest
from sdk.registry import discover_agent_ids, discover_tool_ids, discover_tools
from sdk.testkit import checks as C
from sdk.testkit import tool_checks as TC


def _files(ga_root: Path, specs: list[tuple[str, str]]) -> list[dict]:
    """Filtra a los que EXISTEN; cada uno con path relativo, rol y abspath (para vscode://)."""
    out: list[dict] = []
    seen: set[str] = set()
    for rel, role in specs:
        if rel in seen:
            continue
        p = (ga_root / rel)
        if p.exists():
            seen.add(rel)
            out.append({"path": rel, "role": role, "abspath": str(p.resolve())})
    return out


def _manifest_path(ga_root: Path, agent_id: str) -> Path | None:
    for ext in (".agent.yaml", ".taskgraph.yaml"):
        p = ga_root / "manifests" / f"{agent_id}{ext}"
        if p.exists():
            return p
    return None


# --------------------------------------------------------------------- files

def node_files(node: dict, ga_root: Path) -> list[dict]:
    """Los archivos que respaldan un nodo del grafo (tool/agent/supervisor/port)."""
    kind, label = node["kind"], node["label"]
    if kind == "tool":
        us = label.replace("-", "_")
        specs = [
            (f"tools/{us}/tool.yaml", "contrato"),
            (f"tools/{us}/impl.py", "impl (pura)"),
            (f"tools/{us}/adapters/agentspan.py", "adapter · agentspan"),
            (f"tools/{us}/adapters/langgraph.py", "adapter · langgraph"),
            (f"tests/tools/test_{us}.py", "test · TCK"),
        ]
        return _files(ga_root, specs)
    if kind == "agent":
        specs = [
            (f"manifests/{label}.agent.yaml", "manifest"),
            (f"manifests/{label}.taskgraph.yaml", "manifest · taskgraph"),
        ]
        cap = node.get("capability")
        if cap and ":" in cap:
            mod = cap.split(":", 1)[0]                 # graphs.ctwa_insights
            specs.append((mod.replace(".", "/") + ".py", "capability · StateGraph"))
            base = mod.split(".")[-1]                  # ctwa_insights
            specs += [
                (f"tests/graphs/test_{base}_golden.py", "test · golden"),
                (f"tests/graphs/test_{base}_build.py", "test · build()"),
                (f"tests/conformance/test_{base}_conformance.py", "test · conformance"),
            ]
        files = _files(ga_root, specs)
        # tests de integración POR NOMBRE del nodo (los supervisores tienen cap=None y sus
        # tests viven en tests/integration/; sin esto el operador no los encuentra en el IDE).
        us = label.replace("-", "_")
        for p in sorted((ga_root / "tests" / "integration").glob(f"test_{us}*.py")):
            files.append({"path": f"tests/integration/{p.name}", "role": "test · integración",
                          "abspath": str(p.resolve())})
        return files
    if kind == "port":
        return _files(ga_root, [("sdk/connectorkit/ports.py", "port · connectorkit")])
    return []


def edge_files(edge: dict, ga_root: Path) -> list[dict]:
    """Los archivos que DEFINEN una relación entre nodos (dónde está escrita esa línea)."""
    src = edge["source"].split(":", 1)[-1]
    tgt = edge["target"].split(":", 1)[-1]
    kind = edge["kind"]
    if kind == "uses":            # agente → tool: el binding vive en el manifest del agente
        us = tgt.replace("-", "_")
        return _files(ga_root, [
            (f"manifests/{src}.agent.yaml", "binding · manifest del agente"),
            (f"manifests/{src}.taskgraph.yaml", "binding · manifest del agente"),
            (f"tools/{us}/tool.yaml", "contrato de la tool"),
        ])
    if kind == "agent":           # supervisor → agente: la composición vive en el taskgraph
        return _files(ga_root, [
            (f"manifests/{src}.taskgraph.yaml", "composición · taskgraph del supervisor"),
            (f"manifests/{src}.agent.yaml", "composición · manifest del supervisor"),
            (f"manifests/{tgt}.agent.yaml", "agente referenciado"),
            (f"manifests/{tgt}.taskgraph.yaml", "agente referenciado"),
        ])
    if kind == "consumes":        # agente → port
        return _files(ga_root, [
            (f"manifests/{src}.agent.yaml", "consumes · manifest del agente"),
            ("sdk/connectorkit/ports.py", "definición del port"),
        ])
    return []


# --------------------------------------------------------------------- checks

def _rule(name: str, errs: list[str]) -> dict:
    return {"rule": name, "ok": not errs, "detail": (errs[0] if errs else "")}


def node_checks(node: dict, ga_root: Path) -> dict:
    """Las reglas G-*/cert que garantizan un nodo, con su veredicto (ok/detalle) + el nivel."""
    kind, label = node["kind"], node["label"]
    if kind == "tool":
        contract = next((t for t in discover_tools(ga_root) if t.id == label), None)
        if contract is None:
            return {"level": "none", "rules": []}
        rules = [
            _rule("T-IMPL", TC.check_impl_ref(contract)),
            _rule("T-DUR", TC.check_outward_needs_approval(contract)),
            _rule("G-AGNOSTIC", TC.check_impl_is_agnostic(contract, ga_root)),
        ]
        return {"level": TC.tool_level(contract, ga_root), "rules": rules}
    if kind == "agent":
        path = _manifest_path(ga_root, label)
        if path is None:
            return {"level": "none", "rules": []}
        m = load_manifest(path)
        rules = [
            _rule("C1-CAP", C.check_capability_refs(m)),
            _rule("G-RUN-SIG", C.check_capability_run_signature(m)),
            _rule("G-BIND", C.check_tool_refs(m, ga_root) + C.check_tool_bindings_match_contract(m, ga_root)),
        ]
        if m.archetype == "supervisor":
            rules += [
                _rule("SUP", C.check_supervisor_coherent(m)),
                _rule("G-WIRE", C.check_taskgraph_wireable(m)),
                _rule("G-BIND-AGENT", C.check_agent_refs(m, ga_root)),
            ]
        return {"level": C.level_of(m, ga_root), "rules": rules}
    if kind == "port":
        from sdk.connectorkit.ports import PORTS
        ok = label in PORTS
        return {"level": "none", "rules": [_rule("port resuelve en el registry",
                [] if ok else [f"'{label}' no está en el ConnectorKit (sdk/connectorkit/ports.py)"])]}
    return {"level": "none", "rules": []}


def edge_checks(edge: dict, ga_root: Path) -> dict:
    """Las reglas que garantizan una RELACIÓN — la línea entre dos nodos. Es lo que deja
    confiar en la arista a solo visual: si está verde, el check lo prueba determinísticamente."""
    src = edge["source"].split(":", 1)[-1]
    tgt = edge["target"].split(":", 1)[-1]
    kind = edge["kind"]
    rules: list[dict] = []
    if kind == "uses":  # agente → tool: G-BIND (la tool resuelve + el with: matchea el contrato)
        resolves = tgt in discover_tool_ids(ga_root)
        rules.append(_rule("G-BIND · tool resuelve",
                           [] if resolves else [f"la tool '{tgt}' no está en el catálogo"]))
        path = _manifest_path(ga_root, src)
        if path is not None:
            m = load_manifest(path)
            catalog = {t.id: t for t in discover_tools(ga_root)}
            contract = catalog.get(tgt)
            spec = next((t for t in m.tools if t.ref_id == tgt), None)
            if contract is not None and spec is not None and spec.binding:
                unknown = sorted(k for k in spec.binding if k not in set(contract.inputs))
                rules.append(_rule("G-BIND · binding↔contrato",
                    [] if not unknown else [f"el with: nombra {unknown}, ausentes del contrato"]))
    elif kind == "agent":  # supervisor → agente: G-BIND-AGENT (resuelve) + G-WIRE (declara inputs)
        resolves = tgt in discover_agent_ids(ga_root)
        rules.append(_rule("G-BIND-AGENT · agente resuelve",
                           [] if resolves else [f"agent://{tgt} no está en el catálogo"]))
        path = _manifest_path(ga_root, src)
        if path is not None:
            m = load_manifest(path)
            if m.strategy not in ("router", "manual"):
                ref = next((a for a in m.agents if (a.ref_agent_id or a.name) == tgt), None)
                rules.append(_rule("G-WIRE · inputs declarados",
                    [] if (ref and ref.inputs) else [f"el agente '{tgt}' no declara inputs: en el task graph"]))
    elif kind == "consumes":  # agente → port: el port debe RESOLVER en el registry del ConnectorKit
        from sdk.connectorkit.ports import PORTS
        rules.append(_rule("port resuelve en el registry",
            [] if tgt in PORTS else [f"el port '{tgt}' no está en el ConnectorKit (sdk/connectorkit/ports.py)"]))
    return {"rules": rules}


# ------------------------------------------------------ entrypoints del viewer

def inspect_node(node: dict, ga_root: Path | str) -> dict:
    ga_root = Path(ga_root)
    return {"files": node_files(node, ga_root), "checks": node_checks(node, ga_root)}


def inspect_edge(edge: dict, ga_root: Path | str) -> dict:
    ga_root = Path(ga_root)
    return {"files": edge_files(edge, ga_root), "checks": edge_checks(edge, ga_root)}


def system_checks(graph: dict, ga_root: Path | str) -> dict:
    """Corre los checks de TODO el grafo → veredicto por nodo y por arista, para pintar el
    overlay de certificación (verde = garantizado, rojo = roto). Es 'correr las certs en el viewer'."""
    ga_root = Path(ga_root)
    nodes: dict[str, Any] = {}
    for n in graph["nodes"]:
        c = node_checks(n, ga_root)
        nodes[n["id"]] = {"level": c["level"], "ok": all(r["ok"] for r in c["rules"]),
                          "broken": [r["rule"] for r in c["rules"] if not r["ok"]]}
    edges: dict[str, Any] = {}
    for e in graph["edges"]:
        c = edge_checks(e, ga_root)
        key = f"{e['source']}|{e['target']}|{e['kind']}"
        edges[key] = {"ok": all(r["ok"] for r in c["rules"]),
                      "broken": [r["rule"] for r in c["rules"] if not r["ok"]]}
    return {"nodes": nodes, "edges": edges}
