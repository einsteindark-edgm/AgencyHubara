"""Tests del loader FastAPI (``src.main``) post-auditoría 2026-05-16.

El loader (introducido en PR3) NO tenía tests automatizados. La verificación
era manual via `python -c "import src.main"`. Estos tests cierran ese hueco
cubriendo los caminos críticos:

- Descubrimiento de manifests reales del repo (smoke).
- Filtro ``ENABLED_PLUGINS`` (CSV, vacío = todos).
- Priorización ``legacy_routers`` > ``python_module`` cuando ambos.
- Fail-fast cuando un módulo del manifest no se puede importar.
- ``id`` del manifest debe matchear el nombre del directorio.

Diseño:

- ``_bootstrap_routers(target_app=...)`` admite una ``FastAPI`` fresca para que
  los tests NO muten el singleton ``src.main.app`` (eso rompía otros tests
  que dependían de las rutas reales del repo).
- ``ephemeral_module`` (conftest) crea módulos fake bajo ``tests/plugins/``
  con cleanup garantizado en teardown — evita ``_fake_*.py`` huérfanos si un
  test crashea.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(plugin_dir: Path, body: str) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(body, encoding="utf-8")


def _isolated_loader(monkeypatch: pytest.MonkeyPatch, manifest_dir: Path):
    """Devuelve ``(mod, fresh_app)`` listo para que el test invoque ``_bootstrap_routers(fresh_app)``.

    NO mutamos ``mod.app`` — usamos un app fresco para evitar leakage entre tests.
    """
    # validate_enabled (P-6) lee los manifests vía plugin_manifest — apuntarlo
    # al mismo dir temporal ANTES de importar src.main (el bootstrap corre en
    # module-load y valida el env contra ese universo).
    import src.platform.plugin_manifest as pm

    monkeypatch.setattr(pm, "_PLUGINS_MANIFEST_DIR", manifest_dir)
    if "src.main" in sys.modules:
        del sys.modules["src.main"]
    import src.main as mod

    monkeypatch.setattr(mod, "_PLUGINS_MANIFEST_DIR", manifest_dir)
    return mod, FastAPI()


# ---------------------------------------------------------------------------
# Discovery — smoke contra el repo real
# ---------------------------------------------------------------------------


def test_real_manifests_loaded_at_module_import() -> None:
    """Smoke: el repo en su estado actual carga al menos `chats`."""
    if "src.main" in sys.modules:
        del sys.modules["src.main"]
    import src.main as mod

    assert isinstance(mod.app, FastAPI)
    assert "chats" in mod._LOADED_PLUGINS, (
        f"Expected `chats` in loaded plugins, got {mod._LOADED_PLUGINS}"
    )


# ---------------------------------------------------------------------------
# ENABLED_PLUGINS filter
# ---------------------------------------------------------------------------


def test_enabled_plugins_filter_empty_loads_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ephemeral_module,
) -> None:
    """ENABLED_PLUGINS unset → carga todos los plugins con `api` declarado."""
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)

    alpha_dotted = ephemeral_module(
        "_fake_alpha_router",
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/alpha-ping')\n"
        "def ping(): return {'alpha': True}\n",
    )
    beta_dotted = ephemeral_module(
        "_fake_beta_router",
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/beta-ping')\n"
        "def ping(): return {'beta': True}\n",
    )

    _write_manifest(
        tmp_path / "alpha",
        f"id: alpha\nversion: 0.1.0\napi:\n  python_module: {alpha_dotted}\n",
    )
    _write_manifest(
        tmp_path / "beta",
        f"id: beta\nversion: 0.1.0\napi:\n  python_module: {beta_dotted}\n",
    )

    mod, fresh_app = _isolated_loader(monkeypatch, tmp_path)
    loaded = mod._bootstrap_routers(fresh_app)
    assert loaded == ["alpha", "beta"]


def test_enabled_plugins_filter_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ephemeral_module,
) -> None:
    """ENABLED_PLUGINS=alpha → solo alpha aunque beta también esté en disco."""
    monkeypatch.setenv("ENABLED_PLUGINS", "alpha")

    a_dotted = ephemeral_module(
        "_fake_subset_router",
        "from fastapi import APIRouter\nrouter = APIRouter()\n",
    )
    b_dotted = ephemeral_module(
        "_fake_subset_router_b",
        "from fastapi import APIRouter\nrouter = APIRouter()\n",
    )

    _write_manifest(
        tmp_path / "alpha",
        f"id: alpha\nversion: 0.1.0\napi:\n  python_module: {a_dotted}\n",
    )
    _write_manifest(
        tmp_path / "beta",
        f"id: beta\nversion: 0.1.0\napi:\n  python_module: {b_dotted}\n",
    )

    mod, fresh_app = _isolated_loader(monkeypatch, tmp_path)
    loaded = mod._bootstrap_routers(fresh_app)
    assert loaded == ["alpha"]


def test_enabled_plugins_unknown_id_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ENABLED_PLUGINS con id inexistente → error de boot (P-6, antes: silencio).

    Cambio de contrato F1 (PLUGIN_REFACTOR_PLAN_fable.md): un typo en
    ENABLED_PLUGINS apagaba el plugin sin ruido; ahora el boot muere con
    mensaje accionable.
    """
    monkeypatch.setenv("ENABLED_PLUGINS", "ghost")
    _write_manifest(tmp_path / "alpha", "id: alpha\nversion: 0.1.0\n")

    import src.platform.plugin_manifest as pm
    from src.platform.plugin_loader import PluginDependencyError, validate_enabled

    monkeypatch.setattr(pm, "_PLUGINS_MANIFEST_DIR", tmp_path)
    with pytest.raises(PluginDependencyError, match="ghost"):
        validate_enabled()


def test_id_mismatch_directory_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manifest con `id: x` en dir `y` → RuntimeError (N-9, antes: skip silencioso)."""
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)
    _write_manifest(tmp_path / "alpha", "id: BOGUS\nversion: 0.1.0\n")
    mod, fresh_app = _isolated_loader(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="deben coincidir"):
        mod._bootstrap_routers(fresh_app)


# ---------------------------------------------------------------------------
# legacy_routers vs python_module priorization
# ---------------------------------------------------------------------------


def test_legacy_routers_wins_over_python_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ephemeral_module,
) -> None:
    """Si ambos están, ``legacy_routers`` se registra y ``python_module`` se ignora.

    Caso real: plugin chats declara ``python_module: src.plugins.chats.api``
    (decorativo, sin router agregado) y ``legacy_routers`` con 3 sub-routers
    reales. Si el loader registrara ambos, tendríamos rutas duplicadas.
    """
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)

    deco_dotted = ephemeral_module(
        "_fake_decorative_router",
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/decorative-only')\n"
        "def x(): return {}\n",
    )
    legacy_dotted = ephemeral_module(
        "_fake_legacy_router_only",
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/legacy-only')\n"
        "def x(): return {}\n",
    )

    _write_manifest(
        tmp_path / "alpha",
        "id: alpha\n"
        "version: 0.1.0\n"
        "api:\n"
        f"  python_module: {deco_dotted}\n"
        "  prefix: /api/alpha\n"
        "  legacy_routers:\n"
        f"    - {{ module: {legacy_dotted}, prefix: /api/alpha, tags: [Alpha] }}\n",
    )

    mod, fresh_app = _isolated_loader(monkeypatch, tmp_path)
    loaded = mod._bootstrap_routers(fresh_app)
    paths = {r.path for r in fresh_app.routes if hasattr(r, "path")}
    # /api/alpha/legacy-only debe estar; /api/alpha/decorative-only NO.
    assert "/api/alpha/legacy-only" in paths
    assert "/api/alpha/decorative-only" not in paths
    assert loaded == ["alpha"]


def test_python_module_no_router_attr_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ephemeral_module,
) -> None:
    """python_module sin `router` y sin legacy_routers → no aporta routers, sin crash."""
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)

    dotted = ephemeral_module(
        "_fake_no_router",
        "X = 1  # módulo sin `router` — el loader debe saltarlo silenciosamente.\n",
    )

    _write_manifest(
        tmp_path / "alpha",
        f"id: alpha\nversion: 0.1.0\napi:\n  python_module: {dotted}\n",
    )

    mod, fresh_app = _isolated_loader(monkeypatch, tmp_path)
    assert mod._bootstrap_routers(fresh_app) == []


# ---------------------------------------------------------------------------
# Error handling — fail-fast
# ---------------------------------------------------------------------------


def test_missing_module_raises_at_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si un manifest apunta a un módulo inexistente, el boot falla fuerte."""
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)
    _write_manifest(
        tmp_path / "alpha",
        "id: alpha\n"
        "version: 0.1.0\n"
        "api:\n"
        "  legacy_routers:\n"
        "    - { module: tests.plugins._does_not_exist, prefix: /api, tags: [X] }\n",
    )
    mod, fresh_app = _isolated_loader(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="cannot import"):
        mod._bootstrap_routers(fresh_app)


def test_module_with_non_apirouter_attribute_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ephemeral_module,
) -> None:
    """Si el ``router`` no es un APIRouter, fail-fast con mensaje útil."""
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)
    dotted = ephemeral_module(
        "_fake_wrong_type",
        "router = 'not_an_apirouter'  # bug típico — config se confundió con router.\n",
    )
    _write_manifest(
        tmp_path / "alpha",
        "id: alpha\n"
        "version: 0.1.0\n"
        "api:\n"
        "  legacy_routers:\n"
        f"    - {{ module: {dotted}, prefix: /api, tags: [X] }}\n",
    )
    mod, fresh_app = _isolated_loader(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="no es APIRouter"):
        mod._bootstrap_routers(fresh_app)
