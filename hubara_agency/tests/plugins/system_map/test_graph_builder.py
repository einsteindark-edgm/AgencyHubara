"""Tests del builder + orphan_detector.

Estrategia: usar tmp_path con manifests sintéticos. NO depender del estado
de `frontend_dashboard/src/plugins/` real (eso es smoke test, no unit).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.plugins.system_map.domain.builder import build_system_graph


def _write_manifest(plugins_dir: Path, plugin_id: str, manifest: dict) -> None:
    p = plugins_dir / plugin_id
    p.mkdir(parents=True, exist_ok=True)
    (p / "plugin.yaml").write_text(yaml.dump(manifest), encoding="utf-8")


def test_empty_dir_returns_empty_graph(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    g = build_system_graph(plugins_dir)

    assert g.nodes == []
    assert g.edges == []
    assert g.plugins == []
    assert g.stats.total_nodes == 0
    assert g.warnings == []


def test_dir_missing_emits_warning(tmp_path: Path) -> None:
    g = build_system_graph(tmp_path / "does-not-exist")
    assert any("not found" in w for w in g.warnings)
    assert g.nodes == []


def test_minimal_plugin_produces_one_container_node(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "minimal", {"id": "minimal", "version": "0.1.0"})

    g = build_system_graph(tmp_path)

    assert len(g.plugins) == 1
    assert g.plugins[0].id == "minimal"
    # 1 plugin container + 0 contributions
    assert g.stats.total_nodes == 1
    # Plugin sin frontend/api/agent → empty_plugin orphan
    assert g.nodes[0].is_orphan is True
    assert g.nodes[0].orphan_reason == "empty_plugin"


def test_plugin_with_frontend_section_and_sidebar_not_orphan(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        "chats",
        {
            "id": "chats",
            "version": "0.1.0",
            "display_name": "Chats",
            "frontend": {
                "entry": "./frontend",
                "contributes": {
                    "sections": [{"key": "chat", "label": "Chats", "order": 1}],
                    "sidebar": [{"route": "/chat", "label": "Chats"}],
                },
            },
        },
    )

    g = build_system_graph(tmp_path)

    # plugin + section + sidebar = 3 nodos
    assert g.stats.total_nodes == 3
    # plugin container NO es orphan (tiene frontend)
    plugin_node = next(n for n in g.nodes if n.kind == "plugin")
    assert plugin_node.is_orphan is False
    # section + sidebar match → ninguno orphan
    section = next(n for n in g.nodes if n.kind == "section")
    sidebar = next(n for n in g.nodes if n.kind == "sidebar")
    assert section.is_orphan is False
    assert sidebar.is_orphan is False


def test_section_without_sidebar_flagged_orphan(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        "orphan_section",
        {
            "id": "orphan_section",
            "version": "0.1.0",
            "frontend": {
                "contributes": {
                    "sections": [{"key": "lonely", "label": "Lonely"}],
                    "sidebar": [],  # no matching sidebar entry
                },
            },
        },
    )

    g = build_system_graph(tmp_path)

    section = next(n for n in g.nodes if n.kind == "section")
    assert section.is_orphan is True
    assert section.orphan_reason == "section_without_sidebar"


def test_api_legacy_routers_creates_one_node_per_router(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        "multi_api",
        {
            "id": "multi_api",
            "version": "0.1.0",
            "api": {
                "python_module": "ignored",  # legacy_routers GANA
                "prefix": "/api/multi",
                "tags": ["X"],
                "legacy_routers": [
                    {"module": "a.b.c.sales", "prefix": "/api", "tags": ["Sales"]},
                    {"module": "a.b.c.handoff", "prefix": "/api/dashboard", "tags": ["Handoff"]},
                ],
            },
        },
    )

    g = build_system_graph(tmp_path)

    api_routers = [n for n in g.nodes if n.kind == "api_router"]
    assert len(api_routers) == 2
    assert {n.data["prefix"] for n in api_routers} == {"/api", "/api/dashboard"}


def test_api_python_module_creates_single_router_node(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        "simple_api",
        {
            "id": "simple_api",
            "version": "0.1.0",
            "api": {
                "python_module": "x.y.z",
                "prefix": "/api/simple",
                "tags": ["Simple"],
            },
        },
    )

    g = build_system_graph(tmp_path)

    api_routers = [n for n in g.nodes if n.kind == "api_router"]
    assert len(api_routers) == 1
    assert api_routers[0].data["source"] == "python_module"
    assert api_routers[0].data["module"] == "x.y.z"


def test_workers_emit_worker_and_task_queue_nodes(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        "agentic",
        {
            "id": "agentic",
            "version": "0.1.0",
            "agent": {
                "python_module": "src.plugins.agentic.agent",
                "workers": [
                    {
                        "name": "sales",
                        "module": "src.plugins.agentic.workers.sales",
                        "task_queue": "queue-sales-agent",
                        "deployment": {"replicas": 3},
                    },
                    {
                        "name": "remarketing",
                        "module": "src.plugins.agentic.workers.remarketing",
                        "task_queue": "queue-remarketing-agent",
                        "deployment": {"replicas": 1},
                    },
                ],
            },
        },
    )

    g = build_system_graph(tmp_path)

    workers = [n for n in g.nodes if n.kind == "worker"]
    queues = [n for n in g.nodes if n.kind == "task_queue"]
    assert len(workers) == 2
    assert len(queues) == 2  # 2 task_queues únicas

    # consumes_queue edges
    consumes_edges = [e for e in g.edges if e.kind == "consumes_queue"]
    assert len(consumes_edges) == 2


def test_worker_without_task_queue_orphan(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        "broken_worker",
        {
            "id": "broken_worker",
            "version": "0.1.0",
            "agent": {"workers": [{"name": "broken", "module": "x"}]},
        },
    )

    g = build_system_graph(tmp_path)

    workers = [n for n in g.nodes if n.kind == "worker"]
    assert len(workers) == 1
    assert workers[0].is_orphan is True
    assert workers[0].orphan_reason == "worker_no_task_queue"


def test_depends_on_missing_emits_warning_and_orphan(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        "child",
        {
            "id": "child",
            "version": "0.1.0",
            "depends_on": ["nonexistent_parent"],
            "frontend": {"contributes": {"sections": [{"key": "x"}], "sidebar": [{"route": "/x"}]}},
        },
    )

    g = build_system_graph(tmp_path)

    assert any("nonexistent_parent" in w for w in g.warnings)
    plugin_node = next(n for n in g.nodes if n.kind == "plugin")
    assert plugin_node.is_orphan is True
    assert plugin_node.orphan_reason == "depends_on_missing"


def test_depends_on_existing_creates_edge(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "parent", {"id": "parent", "version": "0.1.0", "api": {"python_module": "x", "prefix": "/p"}})
    _write_manifest(
        tmp_path,
        "child",
        {
            "id": "child",
            "version": "0.1.0",
            "depends_on": ["parent"],
            "api": {"python_module": "y", "prefix": "/c"},
        },
    )

    g = build_system_graph(tmp_path)

    depends_edges = [e for e in g.edges if e.kind == "depends_on"]
    assert len(depends_edges) == 1
    assert depends_edges[0].source == "plugin:child"
    assert depends_edges[0].target == "plugin:parent"


def test_invalid_yaml_emits_warning_and_skips(tmp_path: Path) -> None:
    p = tmp_path / "broken"
    p.mkdir()
    (p / "plugin.yaml").write_text("this is: : not valid yaml: : :", encoding="utf-8")

    g = build_system_graph(tmp_path)

    assert any("broken" in w and "invalid YAML" in w for w in g.warnings)
    # No plugin contributed (skipped)
    assert g.stats.total_plugins == 0


def test_id_mismatch_emits_warning_but_includes_plugin(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        "actual_dir_name",
        {"id": "different_id_in_manifest", "version": "0.1.0"},
    )

    g = build_system_graph(tmp_path)

    assert any("manifest id" in w for w in g.warnings)
    # Still included (best-effort UI rendering)
    assert g.stats.total_plugins == 1


def test_real_repo_manifests_load_clean(tmp_path: Path) -> None:
    """Smoke test contra los manifests reales del repo.

    Asegura que el builder NO se rompe con los 5 plugins existentes.
    No verifica counts específicos (frágil) — solo que produce algo.
    """
    g = build_system_graph()  # usa default _PLUGINS_MANIFEST_DIR

    # Sanity: hay al menos 1 plugin (agents_admin, catalog, chats, eta, orders)
    assert g.stats.total_plugins >= 1
    assert g.stats.total_nodes >= 1
    assert isinstance(g.generated_at, str)
    # No warnings críticos (parse errors) en el repo real
    yaml_errors = [w for w in g.warnings if "invalid YAML" in w]
    assert yaml_errors == [], f"Expected no YAML errors, got: {yaml_errors}"
