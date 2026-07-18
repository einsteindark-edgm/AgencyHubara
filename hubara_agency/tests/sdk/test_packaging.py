"""Comportamiento del empaquetador de plugins (acktospkg/1 — F1).

El contrato que estos tests exigen:

- ``plan_export`` resuelve la CLAUSURA por ``depends_on`` y deriva los
  requirements del manifest (env vars ``${...}`` del compose, env_secrets,
  wiring_intents) — sin tocar k8s.
- ``build_package`` produce un tar.gz con ``package.yaml`` + ``units/`` +
  ``checksums.sha256`` y ``read_package`` verifica integridad.
- ``plan_install`` clasifica cada unidad contra el repo destino
  (new / overwrite) y detecta dependencias faltantes.
- ``install_package`` escribe SOLO paths single-owner del plugin (INV-1),
  regenera el TCK instanciado, y el overwrite REEMPLAZA el dir completo
  (los archivos borrados en origen desaparecen en destino).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.sdk.cli.scaffold import create_plugin
from src.sdk.packaging import (
    build_package,
    install_package,
    plan_export,
    plan_install,
    read_package,
)

REAL_REPO_ROOT = Path(__file__).resolve().parents[3]


def _mini_repo(tmp_path: Path) -> Path:
    """Repo origen sintético: alpha (api_only) + beta (full_stack → alpha).

    beta lleva además un worker Temporal con compose env interpolada y un
    secret — el material del que ``plan_export`` deriva requirements.
    """
    root = tmp_path / "origen"
    root.mkdir()
    create_plugin("alpha", "api_only", repo_root=root)
    create_plugin("beta", "full_stack", repo_root=root)

    manifest_path = root / "frontend_dashboard" / "src" / "plugins" / "beta" / "plugin.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["depends_on"] = ["alpha"]
    manifest["agent"] = {
        "python_module": "src.plugins.beta.agent",
        "workers": [
            {
                "name": "campaigns",
                "module": "src.plugins.beta.workers.campaigns",
                "task_queue": "queue-beta-campaigns",
                "workflow_classes": ["BetaSendWorkflow"],
                "deployment": {
                    "replicas": 1,
                    "env_secrets": [
                        {
                            "var": "WHATSAPP_ACCESS_TOKEN",
                            "secret": "hubara-whatsapp-secret",
                            "key": "WHATSAPP_ACCESS_TOKEN",
                        }
                    ],
                },
                "compose": {
                    "env": {
                        "TEMPORAL_URL": "temporal:7233",
                        "WHATSAPP_PHONE_NUMBER_ID": "${WHATSAPP_PHONE_NUMBER_ID}",
                    },
                    "depends_on": ["temporal"],
                },
            }
        ],
    }
    manifest["wiring_intents"] = {"env_vars_required": ["BETA_FLAG"]}
    manifest_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return root


def _target_repo(tmp_path: Path, name: str = "destino") -> Path:
    """Repo destino con el skeleton (sin plugins todavía)."""
    root = tmp_path / name
    for rel in (
        "frontend_dashboard/src/plugins",
        "hubara_agency/src/plugins",
        "hubara_agency/tests/conformance",
        "hubara_agency/tests/plugins",
    ):
        (root / rel).mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# plan_export
# ---------------------------------------------------------------------------

def test_plan_export_resuelve_clausura_y_requirements(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    plan = plan_export(["beta"], repo_root=root)

    ids = [u.plugin_id for u in plan.units]
    assert ids == ["alpha", "beta"], "deps primero, orden determinista"

    beta = plan.units[-1]
    assert beta.archetype == "full_stack"
    assert beta.depends_on == ("alpha",)
    assert "WHATSAPP_PHONE_NUMBER_ID" in beta.env_vars, "interpolación ${} del compose"
    assert "BETA_FLAG" in beta.env_vars, "wiring_intents.env_vars_required"
    assert "TEMPORAL_URL" not in beta.env_vars, "env literal NO es requirement"
    assert beta.secrets == ("WHATSAPP_ACCESS_TOKEN",)


def test_plan_export_id_inexistente_falla_limpio(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    try:
        plan_export(["nope"], repo_root=root)
    except Exception as exc:  # noqa: BLE001 — el tipo exacto lo fija la impl
        assert "nope" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("plugin inexistente debe fallar con mensaje claro")


# ---------------------------------------------------------------------------
# build + inspect
# ---------------------------------------------------------------------------

def test_build_e_inspect_roundtrip(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    out = tmp_path / "dist" / "beta.acktospkg"
    plan = plan_export(["beta"], repo_root=root)
    pkg_path = build_package(plan, repo_root=root, out_path=out, name="beta-bundle")

    assert pkg_path == out and out.exists()
    info = read_package(out)  # verifica checksums — mismatch levanta error
    assert info.format == "acktospkg/1"
    assert info.name == "beta-bundle"
    unit_ids = sorted(u.unit_id for u in info.units)
    assert unit_ids == ["alpha", "beta"]
    assert all(u.kind == "plugin" for u in info.units)
    beta = next(u for u in info.units if u.unit_id == "beta")
    assert "alpha" in beta.requires_plugins
    assert "WHATSAPP_ACCESS_TOKEN" in beta.requires_secrets


# ---------------------------------------------------------------------------
# plan_install
# ---------------------------------------------------------------------------

def test_plan_install_clasifica_new_y_overwrite(tmp_path: Path) -> None:
    origen = _mini_repo(tmp_path)
    out = tmp_path / "beta.acktospkg"
    build_package(plan_export(["beta"], repo_root=origen), repo_root=origen, out_path=out)

    fresco = _target_repo(tmp_path, "fresco")
    plan = plan_install(out, repo_root=fresco)
    assert {u.unit_id: u.action for u in plan.units} == {"alpha": "new", "beta": "new"}
    assert plan.missing_plugins == ()

    con_alpha = _target_repo(tmp_path, "con_alpha")
    create_plugin("alpha", "api_only", repo_root=con_alpha)
    plan2 = plan_install(out, repo_root=con_alpha)
    actions = {u.unit_id: u.action for u in plan2.units}
    assert actions == {"alpha": "overwrite", "beta": "new"}


def test_plan_install_detecta_dependencia_faltante(tmp_path: Path) -> None:
    origen = _mini_repo(tmp_path)
    out = tmp_path / "solo-beta.acktospkg"
    # exportar beta SIN su dependencia (clausura recortada a mano)
    plan = plan_export(["beta"], repo_root=origen)
    solo_beta = plan.only(["beta"])
    build_package(solo_beta, repo_root=origen, out_path=out)

    fresco = _target_repo(tmp_path, "fresco2")
    iplan = plan_install(out, repo_root=fresco)
    assert iplan.missing_plugins == ("alpha",), (
        "alpha no está ni en el paquete ni en el destino → warning explícito"
    )


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

def test_install_escribe_solo_paths_del_plugin(tmp_path: Path) -> None:
    origen = _mini_repo(tmp_path)
    out = tmp_path / "beta.acktospkg"
    build_package(plan_export(["beta"], repo_root=origen), repo_root=origen, out_path=out)

    destino = _target_repo(tmp_path, "instalado")
    marcador = destino / "hubara_agency" / "src" / "main.py"
    marcador.parent.mkdir(parents=True, exist_ok=True)
    marcador.write_text("# central intocable\n", encoding="utf-8")

    result = install_package(out, repo_root=destino)

    # el payload aterrizó en los 4 paths single-owner
    assert (destino / "hubara_agency/src/plugins/beta/domain/logic.py").exists()
    assert (destino / "frontend_dashboard/src/plugins/beta/plugin.yaml").exists()
    assert (destino / "frontend_dashboard/src/plugins/beta/frontend/index.ts").exists()
    assert (destino / "hubara_agency/tests/plugins/beta/test_domain.py").exists()
    conformance = destino / "hubara_agency/tests/conformance/test_beta_conformance.py"
    assert 'conformance_suite("beta")' in conformance.read_text(encoding="utf-8")

    # INV-1: nada fuera de los paths del plugin fue tocado
    assert marcador.read_text(encoding="utf-8") == "# central intocable\n"
    owned_roots = (
        "frontend_dashboard/src/plugins/",
        "hubara_agency/src/plugins/",
        "hubara_agency/tests/conformance/test_",
        "hubara_agency/tests/plugins/",
    )
    for path in result.written:
        rel = path.relative_to(destino).as_posix()
        assert rel.startswith(owned_roots), f"escritura fuera de INV-1: {rel}"


def test_install_overwrite_reemplaza_el_dir_completo(tmp_path: Path) -> None:
    origen = _mini_repo(tmp_path)
    out = tmp_path / "beta.acktospkg"
    build_package(plan_export(["beta"], repo_root=origen), repo_root=origen, out_path=out)

    destino = _target_repo(tmp_path, "upgrade")
    create_plugin("beta", "full_stack", repo_root=destino)
    stale = destino / "hubara_agency/src/plugins/beta/domain/obsoleto.py"
    stale.write_text("# borrado en origen\n", encoding="utf-8")

    install_package(out, repo_root=destino)
    assert not stale.exists(), "overwrite = reemplazo del dir (propaga deletions)"
    assert (destino / "hubara_agency/src/plugins/beta/domain/logic.py").exists()


# ---------------------------------------------------------------------------
# smoke contra el repo real
# ---------------------------------------------------------------------------

def test_plan_export_marketing_repo_real() -> None:
    plan = plan_export(["marketing"], repo_root=REAL_REPO_ROOT)
    (unit,) = [u for u in plan.units if u.plugin_id == "marketing"]
    assert unit.archetype == "full_stack"
    assert "WHATSAPP_ACCESS_TOKEN" in unit.secrets
    assert "WHATSAPP_PHONE_NUMBER_ID" in unit.env_vars
