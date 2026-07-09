"""PUBLICAR producción — el despliegue real del wiring bendecido: commit (+push +PR).

Semántica:
- Solo se publica lo BENDECIDO: exige snapshot al día (`save` previo = checks verdes).
  Publicar con drift se rehúsa — primero guardás, después desplegás.
- El commit es QUIRÚRGICO: solo `manifests/` + `production.yaml` (el wiring), nunca
  arrastra el resto del working tree del operador.
- En la rama default (main/master) crea una rama `graphagents/production-<fp8>`;
  en una rama de trabajo, commitea ahí (el PR sale de esa rama).
- push/PR son capas separadas (`push=`/`pr=`): acá se testea el commit REAL en un
  repo git temporal; la capa `gh` degrada con error claro si no está.
- Fuera de un repo git (p. ej. el container del viewer) degrada honesto, no crashea.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from sdk.production import plan_publication, production_status, publish_production, save_production
from viewer.server import api_route

GA_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.fixture()
def ga_repo(tmp_path: Path) -> Path:
    """Una copia del catálogo dentro de un repo git REAL (rama main, commit inicial)."""
    shutil.copytree(GA_ROOT / "manifests", tmp_path / "manifests")
    shutil.copytree(GA_ROOT / "tools", tmp_path / "tools")
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "explorer@test")
    _git(tmp_path, "config", "user.name", "explorer")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def test_publish_requiere_snapshot_al_dia(ga_repo):
    res = publish_production(ga_repo, push=False, pr=False)  # sin save previo
    assert res["ok"] is False
    assert any("guard" in e for e in res["errors"]), res


def test_publish_commitea_el_wiring_en_rama_nueva(ga_repo):
    save = save_production(ga_repo)
    assert save["ok"], save
    fp8 = save["snapshot"]["fingerprint"][:8]
    res = publish_production(ga_repo, push=False, pr=False)
    assert res["ok"] is True, res
    assert res["branch"] == f"graphagents/production-{fp8}"  # estaba en main → rama nueva
    assert res["commit"]
    assert _git(ga_repo, "rev-parse", "--abbrev-ref", "HEAD") == res["branch"]
    files = _git(ga_repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert "production.yaml" in files
    assert all(f == "production.yaml" or f.startswith("manifests/") for f in files), files
    assert fp8 in _git(ga_repo, "log", "-1", "--format=%s")


def test_publish_es_quirurgico_no_arrastra_otros_cambios(ga_repo):
    (ga_repo / "otro-archivo.txt").write_text("cambio del operador ajeno al wiring", encoding="utf-8")
    assert save_production(ga_repo)["ok"]
    res = publish_production(ga_repo, push=False, pr=False)
    assert res["ok"], res
    files = _git(ga_repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert "otro-archivo.txt" not in files
    assert "otro-archivo.txt" in _git(ga_repo, "status", "--porcelain")  # sigue sin commitear


def test_publish_sin_cambios_es_ok_e_informa(ga_repo):
    assert save_production(ga_repo)["ok"]
    assert publish_production(ga_repo, push=False, pr=False)["ok"]
    res = publish_production(ga_repo, push=False, pr=False)  # nada nuevo
    assert res["ok"] is True
    commit_step = next(s for s in res["steps"] if s["step"] == "commit")
    assert "sin cambios" in commit_step["detail"]


def test_publish_fuera_de_git_degrada_honesto(tmp_path):
    shutil.copytree(GA_ROOT / "manifests", tmp_path / "manifests")
    shutil.copytree(GA_ROOT / "tools", tmp_path / "tools")
    assert save_production(tmp_path)["ok"]
    res = publish_production(tmp_path, push=False, pr=False)
    assert res["ok"] is False
    assert any("git" in e for e in res["errors"]), res


# ----------------------------------------- plan_publication (F15: ejecución nativa VS Code)

def test_plan_publication_da_el_plan_sin_mutar(ga_repo):
    """El PLAN de publicación no crea rama ni commitea — solo describe qué hacer, para que
    la extensión lo ejecute con las APIs nativas de VS Code (git + GitHub, sin gh)."""
    assert save_production(ga_repo)["ok"]
    fp8 = production_status(ga_repo)["snapshot"]["fingerprint"][:8]
    head_before = _git(ga_repo, "rev-parse", "--abbrev-ref", "HEAD")
    plan = plan_publication(ga_repo)
    assert plan["ok"] is True, plan
    assert plan["on_default"] is True  # estamos en main
    assert plan["branch"] == f"graphagents/production-{fp8}"
    assert plan["base"] == "main"
    assert set(plan["paths"]) == {"manifests", "production.yaml"}  # ga_root == repo root en el fixture
    assert fp8 in plan["title"]
    assert plan["has_changes"] is True  # production.yaml recién bendecido, sin commitear
    # NO mutó: sigue en main, sin rama nueva, production.yaml sin commitear
    assert _git(ga_repo, "rev-parse", "--abbrev-ref", "HEAD") == head_before == "main"
    assert "production.yaml" in _git(ga_repo, "status", "--porcelain")


def test_plan_publication_requiere_snapshot_al_dia(ga_repo):
    plan = plan_publication(ga_repo)  # sin save previo
    assert plan["ok"] is False
    assert any("guard" in e for e in plan["errors"]), plan


def test_endpoint_publish_plan(ga_repo):
    # sin save previo → 422 (el snapshot no está al día)
    status, _ = api_route("GET", "/api/publish-plan", {}, None, ga_root=ga_repo)
    assert status == 422
    assert save_production(ga_repo)["ok"]
    status, payload = api_route("GET", "/api/publish-plan", {}, None, ga_root=ga_repo)
    assert status == 200, payload
    assert payload["ok"] is True and payload["branch"] and payload["paths"]


def test_endpoint_publish(ga_repo):
    assert save_production(ga_repo)["ok"]
    status, payload = api_route("POST", "/api/publish", {}, {"push": False, "pr": False}, ga_root=ga_repo)
    assert status == 200, payload
    assert payload["ok"] is True and payload["commit"]
    status, payload = api_route("POST", "/api/publish", {}, {"push": False, "pr": False},
                                ga_root=ga_repo)  # sigue ok (sin cambios)
    assert status == 200
