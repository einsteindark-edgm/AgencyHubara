"""Tests funcionales del endpoint GET /api/agents_admin.

Cubre los 5 ACs del refinement (PM-001) + cases de robustez:
  AC-1 / AC-3 — solo plugins con agentic:true aparecen en la respuesta
  AC-2        — workspace content real (IDENTITY.md leído del FS, no stub)
  AC-5        — agentic:false excluye el plugin
  AC-2 skills — skills/*.md content aparece en la respuesta
  fallback    — workspace file faltante → campo vacío, no 500

Robustez:
  PM-002 — archivo con bytes inválidos UTF-8 → campo "" + no 500
  PM-003 — header X-Internal-Dashboard ausente → 403
  PM-007 — worker sin campo name → skipeado silenciosamente
  PM-012 — _extract_role ignora sub-headings ## / ###
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_HEADER = {"X-Internal-Dashboard": "1"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    from src.plugins.agents_admin.api import routes as routes_mod

    app = FastAPI()
    app.include_router(routes_mod.router, prefix="/api/agents_admin")
    return app


def _write_workspace(
    root: Path,
    plugin_id: str,
    worker_name: str,
    files: dict[str, str | bytes],
) -> Path:
    ws = root / plugin_id / "agent" / worker_name / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        filepath = ws / filename
        if isinstance(content, bytes):
            filepath.write_bytes(content)
        else:
            filepath.write_text(content, encoding="utf-8")
    return ws


# ---------------------------------------------------------------------------
# Fixture base
# ---------------------------------------------------------------------------


@pytest.fixture
def ws_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirige _PLUGINS_PYTHON_DIR al tmp_path del test."""
    monkeypatch.setattr("src.plugins.agents_admin.api.routes._PLUGINS_PYTHON_DIR", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# AC-1 / AC-3 — solo plugins agénticos
# ---------------------------------------------------------------------------


def test_list_agents_returns_agentic_only(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Endpoint devuelve solo workers de plugins con agentic:true."""
    _write_workspace(ws_root, "chats", "sales", {
        "IDENTITY.md": "# Sales Agent\n\nSoy el agente de ventas.",
    })

    manifests = {
        "chats": {"agentic": True, "agent": {"workers": [{"name": "sales"}]}},
        "catalog": {"agentic": False, "agent": {"workers": [{"name": "sync"}]}},
    }
    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.enumerate_manifest_workers",
        lambda: [
            ("chats", "sales", "src.plugins.chats.workers.sales"),
            ("catalog", "sync", "src.plugins.catalog.workers.sync"),
        ],
    )
    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.load_manifest",
        lambda plugin_id: manifests[plugin_id],
    )

    client = TestClient(_make_app())
    resp = client.get("/api/agents_admin", headers=_HEADER)
    assert resp.status_code == 200
    data = resp.json()
    plugin_ids = [a["plugin_id"] for a in data]
    assert "chats" in plugin_ids
    assert "catalog" not in plugin_ids


# ---------------------------------------------------------------------------
# AC-2 — workspace content real (CLAUDE.md gotcha #1: datos emitidos, no schema)
# ---------------------------------------------------------------------------


def test_list_agents_includes_workspace_content(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El campo workspace.identity contiene el texto real del IDENTITY.md."""
    identity_text = "# Sales Agent\n\nSoy el agente de ventas de Hubara."
    _write_workspace(ws_root, "chats", "sales", {"IDENTITY.md": identity_text})

    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.enumerate_manifest_workers",
        lambda: [("chats", "sales", "src.plugins.chats.workers.sales")],
    )
    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.load_manifest",
        lambda _: {"agentic": True, "agent": {"workers": [{"name": "sales"}]}},
    )

    client = TestClient(_make_app())
    resp = client.get("/api/agents_admin", headers=_HEADER)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    agent = data[0]
    assert agent["workspace"]["identity"] == identity_text
    assert agent["workspace"]["identity"] != ""


# ---------------------------------------------------------------------------
# AC-5 — agentic:false excluye el plugin
# ---------------------------------------------------------------------------


def test_list_agents_excludes_plugins_without_agentic_flag(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plugins con agentic:false no aparecen aunque declaren workers."""
    _write_workspace(ws_root, "orders", "backend", {
        "IDENTITY.md": "# Orders Backend",
    })

    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.enumerate_manifest_workers",
        lambda: [("orders", "backend", "src.plugins.orders.workers.backend")],
    )
    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.load_manifest",
        lambda _: {"agentic": False, "agent": {"workers": [{"name": "backend"}]}},
    )

    client = TestClient(_make_app())
    resp = client.get("/api/agents_admin", headers=_HEADER)
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# AC-2 skills — skills/*.md aparece en la respuesta
# ---------------------------------------------------------------------------


def test_list_agents_includes_skills_from_subdirectory(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los archivos skills/<name>/skill.md aparecen en workspace.skills."""
    ws = _write_workspace(ws_root, "chats", "sales", {
        "IDENTITY.md": "# Sales Agent\n\nRol descripción.",
    })
    skill_dir = ws / "skills" / "hubara_catalog"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text(
        "# Catálogo\n\nBusca productos del catálogo.", encoding="utf-8"
    )

    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.enumerate_manifest_workers",
        lambda: [("chats", "sales", "src.plugins.chats.workers.sales")],
    )
    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.load_manifest",
        lambda _: {"agentic": True, "agent": {"workers": [{"name": "sales"}]}},
    )

    client = TestClient(_make_app())
    resp = client.get("/api/agents_admin", headers=_HEADER)
    assert resp.status_code == 200
    data = resp.json()
    skills = data[0]["workspace"]["skills"]
    assert len(skills) == 1
    assert skills[0]["name"] == "hubara_catalog"
    assert "Catálogo" in skills[0]["content"]


# ---------------------------------------------------------------------------
# fallback — workspace file faltante → "" en lugar de 500
# ---------------------------------------------------------------------------


def test_list_agents_returns_empty_string_for_missing_workspace_file(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archivos de workspace ausentes devuelven cadena vacía, no 500."""
    # Solo IDENTITY.md — los demás no existen
    _write_workspace(ws_root, "chats", "sales", {
        "IDENTITY.md": "# Sales Agent\n\nRol descripción.",
    })

    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.enumerate_manifest_workers",
        lambda: [("chats", "sales", "src.plugins.chats.workers.sales")],
    )
    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.load_manifest",
        lambda _: {"agentic": True, "agent": {"workers": [{"name": "sales"}]}},
    )

    client = TestClient(_make_app())
    resp = client.get("/api/agents_admin", headers=_HEADER)
    assert resp.status_code == 200
    data = resp.json()
    ws = data[0]["workspace"]
    assert ws["soul"] == ""
    assert ws["tools"] == ""
    assert ws["agents"] == ""
    assert ws["users"] == ""
    assert ws["skills"] == []


# ---------------------------------------------------------------------------
# PM-002 — bytes inválidos UTF-8 → campo "" + no 500
# ---------------------------------------------------------------------------


def test_list_agents_returns_empty_string_for_non_utf8_file(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archivo con bytes inválidos UTF-8 devuelve campo vacío en lugar de HTTP 500."""
    _write_workspace(ws_root, "chats", "sales", {
        "IDENTITY.md": "# Sales Agent\n\nRol descripción.",
        "TOOLS.md": b"\xff\xfe invalid latin-1 bytes",  # type: ignore[dict-item]
    })

    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.enumerate_manifest_workers",
        lambda: [("chats", "sales", "src.plugins.chats.workers.sales")],
    )
    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.load_manifest",
        lambda _: {"agentic": True, "agent": {"workers": [{"name": "sales"}]}},
    )

    client = TestClient(_make_app())
    resp = client.get("/api/agents_admin", headers=_HEADER)
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["workspace"]["tools"] == ""


# ---------------------------------------------------------------------------
# PM-003 — header ausente → 403
# ---------------------------------------------------------------------------


def test_list_agents_requires_internal_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endpoint devuelve 403 cuando falta el header X-Internal-Dashboard."""
    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.enumerate_manifest_workers",
        lambda: [],
    )

    client = TestClient(_make_app())
    resp = client.get("/api/agents_admin")  # sin header
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PM-007 — worker sin campo name → skipeado
# ---------------------------------------------------------------------------


def test_list_agents_skips_worker_with_no_name_field(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workers con name ausente o vacío son omitidos sin causar error."""
    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.enumerate_manifest_workers",
        lambda: [("chats", "sales", "src.plugins.chats.workers.sales")],
    )
    monkeypatch.setattr(
        "src.plugins.agents_admin.api.routes.load_manifest",
        lambda _: {
            "agentic": True,
            "agent": {"workers": [{"module": "some.module"}]},  # name ausente
        },
    )

    client = TestClient(_make_app())
    resp = client.get("/api/agents_admin", headers=_HEADER)
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# PM-012 — _extract_role ignora sub-headings ##/###
# ---------------------------------------------------------------------------


def test_extract_role_skips_sub_headings() -> None:
    """_extract_role retorna el primer párrafo no-heading, ignorando ## y ###."""
    from src.plugins.agents_admin.api.routes import _extract_role

    identity = "# Nombre del agente\n## Sub-sección\nTexto del rol aquí."
    assert _extract_role(identity, "fallback") == "Texto del rol aquí."


def test_extract_role_uses_first_paragraph() -> None:
    """_extract_role retorna la primera línea de texto después del heading #."""
    from src.plugins.agents_admin.api.routes import _extract_role

    identity = "# Sales Agent\n\nSoy el agente de ventas de Hubara."
    assert _extract_role(identity, "fallback") == "Soy el agente de ventas de Hubara."
