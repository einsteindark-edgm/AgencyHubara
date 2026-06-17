"""Self-test del TestKit — el gate que nunca falla es un gate roto.

Construye plugins SINTÉTICOS (rotos de maneras conocidas) en un repo-skeleton
temporal y verifica que cada check los CAZA y que ``compute_level`` degrada
como corresponde. Esto protege al TCK de la clase de bug más peligrosa de un
sistema de certificación: pasar en verde por accidente (path mal resuelto,
glob que no matchea nada, skip silencioso).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.sdk.testkit import build_context, compute_level, run_all_checks


def _mk_repo(tmp_path: Path) -> Path:
    """Skeleton mínimo del monorepo (manifests dir + backend dir + conformance)."""
    (tmp_path / "frontend_dashboard" / "src" / "plugins").mkdir(parents=True)
    (tmp_path / "hubara_agency" / "src" / "plugins").mkdir(parents=True)
    (tmp_path / "hubara_agency" / "tests" / "conformance").mkdir(parents=True)
    return tmp_path


def _write_plugin(
    repo: Path,
    manifest: dict,
    *,
    backend_files: dict[str, str] | None = None,
    frontend_index: bool = False,
    conformance: bool = True,
) -> str:
    pid = manifest["id"]
    mdir = repo / "frontend_dashboard" / "src" / "plugins" / pid
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    if frontend_index:
        (mdir / "frontend").mkdir(exist_ok=True)
        (mdir / "frontend" / "index.ts").write_text("export default {};\n")
    for rel, content in (backend_files or {}).items():
        path = repo / "hubara_agency" / "src" / "plugins" / pid / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if conformance:
        (repo / "hubara_agency" / "tests" / "conformance" / f"test_{pid}_conformance.py").write_text(
            f'from src.sdk.testkit import conformance_suite\n\nglobals().update(conformance_suite("{pid}"))\n'
        )
    return pid


def _results(repo: Path, pid: str):
    return run_all_checks(build_context(pid, repo_root=repo))


def test_manifest_invalido_es_level_none(tmp_path: Path) -> None:
    repo = _mk_repo(tmp_path)
    pid = _write_plugin(repo, {"id": "rotox", "version": "0.1.0", "campo_falso": 1})
    checks = _results(repo, pid)
    assert any(c.code == "C0-SCHEMA" and c.status == "fail" for c in checks)
    assert compute_level(checks) == "none"


def test_archetype_ausente_es_level_none(tmp_path: Path) -> None:
    repo = _mk_repo(tmp_path)
    pid = _write_plugin(
        repo,
        {"id": "sinarq", "version": "0.1.0", "api": {"python_module": "src.plugins.sinarq.api"}},
        backend_files={"api/__init__.py": "router = None\n"},
    )
    checks = _results(repo, pid)
    assert any(c.code == "P-29A" and c.status == "fail" for c in checks)
    assert compute_level(checks) == "none"


def test_modulo_declarado_inexistente_cae_a_c0(tmp_path: Path) -> None:
    repo = _mk_repo(tmp_path)
    pid = _write_plugin(
        repo,
        {
            "id": "fantasma",
            "version": "0.1.0",
            "archetype": "api_only",
            "api": {"python_module": "src.plugins.fantasma.api"},
        },
        backend_files=None,  # el módulo NO existe
    )
    checks = _results(repo, pid)
    assert any(c.code == "C1-API-MODULE" and c.status == "fail" for c in checks)
    assert compute_level(checks) == "C0"


def test_perfil_violado_cae_a_c1(tmp_path: Path) -> None:
    # api_only PROHÍBE frontend: — lo declaramos a propósito.
    repo = _mk_repo(tmp_path)
    pid = _write_plugin(
        repo,
        {
            "id": "violador",
            "version": "0.1.0",
            "archetype": "api_only",
            "frontend": {"entry": "./frontend"},
            "api": {"python_module": "src.plugins.violador.api"},
        },
        backend_files={"api/__init__.py": "router = None\n"},
        frontend_index=True,
    )
    checks = _results(repo, pid)
    p29 = [c for c in checks if c.code == "P-29" and c.status == "fail"]
    assert p29 and "PROHÍBE frontend" in p29[0].detail
    assert compute_level(checks) == "C1"


def test_notifier_con_owns_route_falla_perfil(tmp_path: Path) -> None:
    repo = _mk_repo(tmp_path)
    pid = _write_plugin(
        repo,
        {
            "id": "pushy",
            "version": "0.1.0",
            "archetype": "notifier",
            "agent": {
                "workers": [
                    {
                        "name": "w",
                        "module": "src.plugins.pushy.workers.w",
                        "task_queue": "queue-pushy",
                        "owns_route": "pushy",  # L-4: prohibido para notifier
                        "route_workflow_id_template": "pushy-{session_id}",
                    }
                ]
            },
        },
        backend_files={
            "workers/w.py": "async def main():\n    ensure_plugin_enabled('pushy')\n",
            "agent/w/activities/__init__.py": "",
        },
    )
    checks = _results(repo, pid)
    assert any(
        c.code == "P-29" and c.status == "fail" and "owns_route" in c.detail
        for c in checks
    )


def test_worker_sin_selfgate_falla_p21(tmp_path: Path) -> None:
    repo = _mk_repo(tmp_path)
    pid = _write_plugin(
        repo,
        {
            "id": "zombi",
            "version": "0.1.0",
            "archetype": "notifier",
            "agent": {
                "workers": [
                    {
                        "name": "w",
                        "module": "src.plugins.zombi.workers.w",
                        "task_queue": "queue-zombi",
                    }
                ]
            },
        },
        backend_files={
            "workers/w.py": "async def main():\n    print('arranco sin gate')\n",
            "agent/w/activities/__init__.py": "",
        },
    )
    checks = _results(repo, pid)
    assert any(c.code == "P-21" and c.status == "fail" for c in checks)


def test_plugin_sano_certifica_c2(tmp_path: Path) -> None:
    repo = _mk_repo(tmp_path)
    pid = _write_plugin(
        repo,
        {
            "id": "sano",
            "version": "0.1.0",
            "archetype": "api_only",
            "api": {"python_module": "src.plugins.sano.api"},
        },
        backend_files={"api/__init__.py": "router = None\n", "domain/__init__.py": ""},
    )
    checks = _results(repo, pid)
    assert compute_level(checks) == "C2", [c for c in checks if c.status == "fail"]


def test_queue_ajena_falla_p16(tmp_path: Path) -> None:
    repo = _mk_repo(tmp_path)
    pid = _write_plugin(
        repo,
        {
            "id": "ladron",
            "version": "0.1.0",
            "archetype": "api_only",
            "api": {"python_module": "src.plugins.ladron.api"},
        },
        backend_files={
            "api/__init__.py": (
                "from src.sdk import get_task_queue\n"
                "router = None\n"
                "QUEUE = get_task_queue('chats', 'sales')\n"
            )
        },
    )
    checks = _results(repo, pid)
    assert any(c.code == "P-16" and c.status == "fail" for c in checks)
