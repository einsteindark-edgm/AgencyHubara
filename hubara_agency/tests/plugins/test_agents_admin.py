"""Tests del plugin ``agents_admin`` — descubrimiento de agentes + lectura de
workspaces reales para la sección *Agents* del dashboard.

Cubre:
- Integración: ``discover_agents()`` lee los agentes reales (sales, remarketing)
  y el contenido VIVO de sus 5 .md desde los manifests del repo.
- Unit: un bloque ``dashboard`` en un manifest fake + workspace temporal se
  descubre y sus .md se leen (aislado de los manifests reales). Un worker SIN
  ``dashboard`` NO es un agente visible.
- Seguridad: una ruta de workspace que escapa del repo root se rechaza.
- HTTP: ``GET /api/agents`` devuelve 200 con la estructura esperada.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.agents_admin import service
from src.plugins.agents_admin.api import router

_PROMPT_KEYS = {"agents", "identity", "soul", "tools", "users"}


# ── Integración: manifests + workspaces reales del repo ──────────────────


def test_discover_real_agents_reads_live_workspace_md() -> None:
    agents = {a.id: a for a in service.discover_agents()}
    # sales y remarketing están declarados con bloque dashboard en chats/plugin.yaml
    assert {"sales", "remarketing"} <= set(agents)

    for agent_id in ("sales", "remarketing"):
        agent = agents[agent_id]
        assert {p.key for p in agent.prompts} == _PROMPT_KEYS
        for p in agent.prompts:
            assert p.content.strip(), f"{agent_id}/{p.filename} llegó vacío"
            assert p.word_count > 0
        assert agent.model, f"{agent_id} sin modelo declarado"
        assert agent.capabilities, f"{agent_id} sin capacidades"


def test_real_sales_identity_carries_real_md_content() -> None:
    agents = {a.id: a for a in service.discover_agents()}
    identity = next(p for p in agents["sales"].prompts if p.key == "identity")
    # texto real del IDENTITY.md del agente de ventas
    assert "Asesor" in identity.content


# ── Unit: manifest + workspace temporales (aislado de los reales) ────────


def _write_workspace(ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "AGENTS.md").write_text("Coordinación con otros agentes", encoding="utf-8")
    (ws / "IDENTITY.md").write_text("Soy un agente de prueba", encoding="utf-8")
    (ws / "SOUL.md").write_text("Mi propósito es testear", encoding="utf-8")
    (ws / "TOOLS.md").write_text("Mis herramientas", encoding="utf-8")
    (ws / "USER.md").write_text("Mi audiencia objetivo", encoding="utf-8")


def test_discover_from_temp_manifest_skips_workers_without_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path
    ws_rel = "pkg/agent/foo/workspace"
    _write_workspace(repo_root / ws_rel)

    manifest_dir = repo_root / "frontend_dashboard" / "src" / "plugins"
    plugin_dir = manifest_dir / "demo"
    plugin_dir.mkdir(parents=True)
    manifest = {
        "id": "demo",
        "version": "0.1.0",
        # P-17/PM-3: discover_agents ahora honra el flag del schema — solo los
        # plugins `agentic: true` exponen agentes.
        "agentic": True,
        "agent": {
            "workers": [
                {
                    "name": "foo",
                    "module": "x",
                    "task_queue": "queue-foo",
                    "dashboard": {
                        "display_name": "Foo Bot",
                        "role": "rol de prueba",
                        "category": "Demo",
                        "icon": "bolt",
                        "color": "blue",
                        "model": "TestModel",
                        "workspace": ws_rel,
                        "capabilities": [{"label": "Hacer algo", "icon": "tag"}],
                    },
                },
                {
                    # worker SIN bloque dashboard → NO es un agente visible
                    "name": "bar",
                    "module": "y",
                    "task_queue": "queue-bar",
                },
            ]
        },
    }
    (plugin_dir / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    monkeypatch.setattr(service, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(service, "_PLUGINS_MANIFEST_DIR", manifest_dir)

    agents = service.discover_agents()
    assert len(agents) == 1
    foo = agents[0]
    assert foo.id == "foo"
    assert foo.name == "Foo Bot"
    assert foo.model == "TestModel"
    assert len(foo.capabilities) == 1 and foo.capabilities[0].label == "Hacer algo"
    assert {p.key for p in foo.prompts} == _PROMPT_KEYS
    identity = next(p for p in foo.prompts if p.key == "identity")
    assert identity.content == "Soy un agente de prueba"


def test_discover_respects_enabled_plugins_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PM-4/AP-8: un plugin fuera de ENABLED_PLUGINS no muestra sus agentes.

    Era la asimetría destapada por la extracción de eta: la sección FE
    desaparecía pero la card del agente seguía en Agents.
    """
    repo_root = tmp_path
    ws_rel = "pkg/agent/foo/workspace"
    _write_workspace(repo_root / ws_rel)
    manifest_dir = repo_root / "frontend_dashboard" / "src" / "plugins"
    plugin_dir = manifest_dir / "demo"
    plugin_dir.mkdir(parents=True)
    manifest = {
        "id": "demo",
        "version": "0.1.0",
        "agentic": True,
        "agent": {
            "workers": [
                {
                    "name": "foo",
                    "module": "x",
                    "task_queue": "queue-foo",
                    "dashboard": {"display_name": "Foo Bot", "workspace": ws_rel},
                }
            ]
        },
    }
    (plugin_dir / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setattr(service, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(service, "_PLUGINS_MANIFEST_DIR", manifest_dir)

    monkeypatch.setenv("ENABLED_PLUGINS", "demo")
    assert [a.id for a in service.discover_agents()] == ["foo"]

    monkeypatch.setenv("ENABLED_PLUGINS", "otro")
    assert service.discover_agents() == []


def test_discover_skips_non_agentic_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PM-3/AP-11: sin `agentic: true` el plugin no expone agentes aunque
    tenga bloques `dashboard:` (el schema ya no miente — P-17 mantiene la
    coherencia flag⟺dashboard en los manifests reales)."""
    repo_root = tmp_path
    ws_rel = "pkg/agent/foo/workspace"
    _write_workspace(repo_root / ws_rel)
    manifest_dir = repo_root / "frontend_dashboard" / "src" / "plugins"
    plugin_dir = manifest_dir / "demo"
    plugin_dir.mkdir(parents=True)
    manifest = {
        "id": "demo",
        "version": "0.1.0",
        # sin agentic → invisible para agents_admin
        "agent": {
            "workers": [
                {
                    "name": "foo",
                    "module": "x",
                    "task_queue": "queue-foo",
                    "dashboard": {"display_name": "Foo Bot", "workspace": ws_rel},
                }
            ]
        },
    }
    (plugin_dir / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setattr(service, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(service, "_PLUGINS_MANIFEST_DIR", manifest_dir)
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)

    assert service.discover_agents() == []


def test_workspace_escaping_repo_root_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # un workspace con archivos reales, pero FUERA del repo root
    _write_workspace(tmp_path / "secret")

    monkeypatch.setattr(service, "_REPO_ROOT", repo_root)
    # path traversal: no debe leer nada fuera del repo
    assert service._read_workspace_prompts("../secret") == ()


# ── HTTP: endpoint real ──────────────────────────────────────────────────


def test_endpoint_returns_200_with_real_agents() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/agents", tags=["Agents"])
    client = TestClient(app)

    resp = client.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    ids = {a["id"] for a in data["agents"]}
    assert {"sales", "remarketing"} <= ids
    sales = next(a for a in data["agents"] if a["id"] == "sales")
    assert {p["key"] for p in sales["prompts"]} == _PROMPT_KEYS
