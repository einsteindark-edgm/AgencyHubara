"""El agente como unidad de catálogo: referenciable por id, exponible como tool y
publicable. Incluye el negativo de G-BIND-AGENT."""
from __future__ import annotations

from pathlib import Path

from sdk.manifest_model import TaskGraphManifest, load_manifest
from sdk.registry import agent_index, discover_agent_ids
from sdk.testkit.checks import level_of, run_checks

GA = Path(__file__).resolve().parents[2]


def test_catalogo_de_agentes_no_vacio() -> None:
    assert {"meta-insights", "roas-cac"} <= discover_agent_ids(GA)


def test_supervisor_referencia_por_id_no_inline_y_certifica_C2() -> None:
    m = load_manifest(GA / "manifests" / "ads-supervisor.taskgraph.yaml")
    assert m.agents and all(a.is_reference for a in m.agents)  # refs, no inline
    assert level_of(m, GA) in {"C2", "C3"}, run_checks(m, GA)


def test_g_bind_agent_caza_referencia_inexistente() -> None:
    m = TaskGraphManifest(
        name="x", archetype="supervisor", strategy="router", agents=[{"uses": "agent://nope@1"}]
    )
    errs = run_checks(m, GA)["errors"]
    assert any("G-BIND-AGENT" in e for e in errs)


def test_agente_puede_exponerse_como_tool_y_publicar() -> None:
    rows = {r["id"]: r for r in agent_index(GA)}
    assert rows["roas-cac"]["exposes_as_tool"] is True
    assert rows["meta-insights"]["publish"] == "mcp"
