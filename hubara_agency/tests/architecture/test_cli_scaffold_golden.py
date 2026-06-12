"""Test de oro del scaffolder — todo template del CLI NACE certificado (F-SDK-3).

El scaffolder está atado al TCK por construcción: si un template degenera
(archivo faltante, perfil violado, manifest inválido), este gate lo caza en
CI antes de que un usuario genere basura con confianza. Corre HERMÉTICO en
un skeleton tmp (CheckContext.repo_root inyectable) — jamás ensucia el repo.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.sdk.cli.scaffold import SCAFFOLDABLE, ScaffoldError, create_plugin
from src.sdk.testkit import build_context, compute_level, run_all_checks


def _mk_repo(tmp_path: Path) -> Path:
    (tmp_path / "frontend_dashboard" / "src" / "plugins").mkdir(parents=True)
    (tmp_path / "hubara_agency" / "src" / "plugins").mkdir(parents=True)
    (tmp_path / "hubara_agency" / "tests" / "conformance").mkdir(parents=True)
    return tmp_path


@pytest.mark.parametrize("archetype", SCAFFOLDABLE)
def test_scaffolded_plugin_is_born_c2(tmp_path: Path, archetype: str) -> None:
    repo = _mk_repo(tmp_path)
    created = create_plugin("golden_probe", archetype, repo_root=repo)
    assert created, "el scaffolder no creó archivos"
    checks = run_all_checks(build_context("golden_probe", repo_root=repo))
    fails = [c for c in checks if c.status == "fail"]
    assert compute_level(checks) == "C2", (
        f"el template {archetype!r} NO nace certificado — el scaffolder y el "
        f"TCK divergieron (INV-5). Fallas:\n"
        + "\n".join(f"  [{c.code}] {c.detail}" for c in fails)
    )


@pytest.mark.parametrize("archetype", SCAFFOLDABLE)
def test_scaffolded_plugin_has_zero_warnings(tmp_path: Path, archetype: str) -> None:
    """Los plugins VIEJOS pueden tener warnings de migración; los NUEVOS no."""
    repo = _mk_repo(tmp_path)
    create_plugin("golden_probe", archetype, repo_root=repo)
    checks = run_all_checks(build_context("golden_probe", repo_root=repo))
    warns = [c for c in checks if c.status == "warn"]
    assert not warns, (
        f"template {archetype!r} nace con warnings (un plugin nuevo nace "
        f"limpio): {[c.detail for c in warns]}"
    )


def test_scaffold_rejects_bad_id(tmp_path: Path) -> None:
    repo = _mk_repo(tmp_path)
    with pytest.raises(ScaffoldError, match="id inválido"):
        create_plugin("Bad-Id", "api_only", repo_root=repo)
    # nada escrito a disco:
    assert not list((repo / "frontend_dashboard" / "src" / "plugins").iterdir())


def test_scaffold_rejects_collision(tmp_path: Path) -> None:
    repo = _mk_repo(tmp_path)
    create_plugin("dup", "api_only", repo_root=repo)
    with pytest.raises(ScaffoldError, match="ya existe"):
        create_plugin("dup", "api_only", repo_root=repo)


def test_scaffold_worker_archetypes_fail_loud_not_half_built(tmp_path: Path) -> None:
    """agentic/notifier/sync: mejor un error claro que un worker a medias."""
    repo = _mk_repo(tmp_path)
    with pytest.raises(ScaffoldError, match="F-SDK-3b"):
        create_plugin("agent_x", "agentic", repo_root=repo)
    assert not list((repo / "frontend_dashboard" / "src" / "plugins").iterdir())
