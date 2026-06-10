"""Tests del meta-launcher (``src.run_workers``) post-auditoría 2026-05-16.

El meta-launcher tampoco tenía tests automatizados pre-auditoría. Estos
tests cubren:

- Descubrimiento desde manifests (workers explícitos y atajo worker_module).
- Filtro ENABLED_PLUGINS.
- Validación de shape de cada worker entry — error claro cuando falta `name`
  o `module`.
- Validación de que el módulo del worker exponga `async def main()`.

Usa la fixture ``ephemeral_module`` (conftest) para crear módulos fake con
cleanup garantizado.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _reload_run_workers(monkeypatch: pytest.MonkeyPatch, manifest_dir: Path) -> object:
    # validate_enabled (P-6) lee los manifests vía plugin_manifest — apuntarlo
    # al mismo dir temporal que el loader para que vea el mismo universo.
    import src.platform.plugin_manifest as pm

    monkeypatch.setattr(pm, "_PLUGINS_MANIFEST_DIR", manifest_dir)
    if "src.run_workers" in sys.modules:
        del sys.modules["src.run_workers"]
    import src.run_workers as mod

    monkeypatch.setattr(mod, "_PLUGINS_MANIFEST_DIR", manifest_dir)
    return mod


def _write_manifest(plugin_dir: Path, body: str) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_workers_real_repo() -> None:
    """Smoke: el repo real tiene chats/sales, chats/remarketing, catalog/sync."""
    if "src.run_workers" in sys.modules:
        del sys.modules["src.run_workers"]
    import src.run_workers as mod

    workers = mod._discover_workers()
    names = {(p, n) for p, n, _ in workers}
    assert ("chats", "sales") in names
    assert ("chats", "remarketing") in names
    assert ("catalog", "sync") in names


def test_discover_workers_filters_by_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENABLED_PLUGINS", "alpha")
    _write_manifest(
        tmp_path / "alpha",
        "id: alpha\nversion: 0.1.0\n"
        "agent:\n"
        "  workers:\n"
        "    - { name: w1, module: tests.plugins._fake_wm1 }\n",
    )
    _write_manifest(
        tmp_path / "beta",
        "id: beta\nversion: 0.1.0\n"
        "agent:\n"
        "  workers:\n"
        "    - { name: w2, module: tests.plugins._fake_wm2 }\n",
    )
    mod = _reload_run_workers(monkeypatch, tmp_path)
    workers = mod._discover_workers()
    assert workers == [("alpha", "w1", "tests.plugins._fake_wm1")]


def test_discover_workers_singular_worker_module_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``agent.worker_module: X`` se expande a [(plugin, 'default', X)]."""
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)
    _write_manifest(
        tmp_path / "alpha",
        "id: alpha\nversion: 0.1.0\n"
        "agent:\n"
        "  worker_module: tests.plugins._fake_solo_worker\n",
    )
    mod = _reload_run_workers(monkeypatch, tmp_path)
    workers = mod._discover_workers()
    assert workers == [("alpha", "default", "tests.plugins._fake_solo_worker")]


def test_discover_workers_no_agent_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plugin sin `agent` → no aporta workers."""
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)
    _write_manifest(tmp_path / "alpha", "id: alpha\nversion: 0.1.0\n")
    mod = _reload_run_workers(monkeypatch, tmp_path)
    assert mod._discover_workers() == []


# ---------------------------------------------------------------------------
# Validation — fail-fast cuando manifest mal formado
# ---------------------------------------------------------------------------


def test_workers_not_a_list_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)
    _write_manifest(
        tmp_path / "alpha",
        "id: alpha\nversion: 0.1.0\n"
        "agent:\n"
        "  workers: not-a-list\n",
    )
    mod = _reload_run_workers(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="must be a list"):
        mod._discover_workers()


def test_worker_entry_missing_name_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)
    _write_manifest(
        tmp_path / "alpha",
        "id: alpha\nversion: 0.1.0\n"
        "agent:\n"
        "  workers:\n"
        "    - { module: tests.plugins._fake }\n",
    )
    mod = _reload_run_workers(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="name"):
        mod._discover_workers()


def test_worker_entry_missing_module_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)
    _write_manifest(
        tmp_path / "alpha",
        "id: alpha\nversion: 0.1.0\n"
        "agent:\n"
        "  workers:\n"
        "    - { name: w1 }\n",
    )
    mod = _reload_run_workers(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="module"):
        mod._discover_workers()


def test_worker_entry_not_a_dict_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ENABLED_PLUGINS", raising=False)
    _write_manifest(
        tmp_path / "alpha",
        "id: alpha\nversion: 0.1.0\n"
        "agent:\n"
        "  workers:\n"
        "    - just_a_string\n",
    )
    mod = _reload_run_workers(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="no es dict"):
        mod._discover_workers()


# ---------------------------------------------------------------------------
# _run_worker — validación del módulo
# ---------------------------------------------------------------------------


async def test_run_worker_rejects_module_without_main(ephemeral_module) -> None:
    """``_run_worker`` da error claro si el módulo no expone ``async def main()``."""
    dotted = ephemeral_module(
        "_fake_no_main",
        "X = 1  # módulo sin `main`\n",
    )
    if "src.run_workers" in sys.modules:
        del sys.modules["src.run_workers"]
    import src.run_workers as mod

    with pytest.raises(RuntimeError, match="async def main"):
        await mod._run_worker("alpha", "w1", dotted)


async def test_run_worker_rejects_sync_main(ephemeral_module) -> None:
    """Un ``def main()`` (sync) NO es aceptado — solo async."""
    dotted = ephemeral_module(
        "_fake_sync_main",
        "def main():\n    return 1\n",
    )
    if "src.run_workers" in sys.modules:
        del sys.modules["src.run_workers"]
    import src.run_workers as mod

    with pytest.raises(RuntimeError, match="async def main"):
        await mod._run_worker("alpha", "w1", dotted)


# ---------------------------------------------------------------------------
# Shutdown timeout configuration (F4 premortem PR9)
# ---------------------------------------------------------------------------


def test_shutdown_timeout_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``RUN_WORKERS_SHUTDOWN_TIMEOUT_S`` override el default 15s.

    En producción con muchos workflows in-flight, 15s puede no alcanzar para
    que el Temporal Worker flushe. Permitir override sin recompilar.
    """
    monkeypatch.setenv("RUN_WORKERS_SHUTDOWN_TIMEOUT_S", "42.5")
    if "src.run_workers" in sys.modules:
        del sys.modules["src.run_workers"]
    import src.run_workers as mod

    assert mod._SHUTDOWN_TIMEOUT_S == 42.5


def test_shutdown_timeout_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin env var, default 15s (razonable para dev local)."""
    monkeypatch.delenv("RUN_WORKERS_SHUTDOWN_TIMEOUT_S", raising=False)
    if "src.run_workers" in sys.modules:
        del sys.modules["src.run_workers"]
    import src.run_workers as mod

    assert mod._SHUTDOWN_TIMEOUT_S == 15.0


def test_shutdown_timeout_invalid_value_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el valor no parsea como float, se usa el default y se loguea warning."""
    monkeypatch.setenv("RUN_WORKERS_SHUTDOWN_TIMEOUT_S", "not-a-number")
    if "src.run_workers" in sys.modules:
        del sys.modules["src.run_workers"]
    import src.run_workers as mod

    assert mod._SHUTDOWN_TIMEOUT_S == 15.0
