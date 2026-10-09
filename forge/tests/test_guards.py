"""Guards anti-hubara: NINGUNA ejecución de forge/steps puede apuntar al
proyecto productivo. Estos tests son el contrato de esa garantía."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

FORGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE_DIR))

import forge  # noqa: E402


def _client(**over):
    base = {
        "slug": "acme",
        "company": "Acme",
        "repo": "einsteindark-edgm/AgencyAcme",
        "aws": {"resource_prefix": "agencyacme", "ssm_prefix": "/acme"},
        "business": {},
    }
    base.update(over)
    return base


def test_slug_hubara_rechazado():
    with pytest.raises(forge.ForgeError, match="hubara"):
        forge.render_vars(_client(slug="hubara"))


def test_prefijo_de_recursos_de_hubara_rechazado():
    with pytest.raises(forge.ForgeError, match="agencyhubara"):
        forge.render_vars(_client(aws={"resource_prefix": "agencyhubara", "ssm_prefix": "/acme"}))


def test_ssm_prefix_de_hubara_rechazado():
    with pytest.raises(forge.ForgeError, match="/hubara"):
        forge.render_vars(_client(aws={"resource_prefix": "agencyacme", "ssm_prefix": "/hubara"}))


def test_repo_de_hubara_rechazado():
    with pytest.raises(forge.ForgeError, match="AgencyHubara"):
        forge.render_vars(_client(repo="einsteindark-edgm/AgencyHubara"))


def test_api_url_de_hubara_rechazada():
    for bad in ["https://98-88-237-207.sslip.io", "https://api.hubara.com.co"]:
        with pytest.raises(forge.ForgeError, match="hubara|98-88"):
            forge.render_vars(_client(api_url=bad))


def test_apply_rechaza_dest_dentro_del_repo_madre(tmp_path):
    """Forjar DENTRO del repo madre mezclaría el clon con hubara — prohibido."""
    inner = forge.REPO / "algo" / "clon"
    with pytest.raises(forge.ForgeError, match="repo madre"):
        forge.run_apply(
            src=forge.REPO,
            dest=inner,
            client_dir=tmp_path,  # ni llega a leerse: el guard va primero
            manifest=forge.load_manifest(),
        )


def test_main_repo_root_resuelve_worktrees():
    """Si forge corre desde un worktree (<repo>/.claude/worktrees/<x>), el repo
    a proteger es <repo> — no el worktree."""
    wt = Path("/Users/x/Proyectos/AgencyHubara/.claude/worktrees/rama-123")
    assert forge.main_repo_root(wt) == Path("/Users/x/Proyectos/AgencyHubara")
    normal = Path("/Users/x/Proyectos/AgencyHubara")
    assert forge.main_repo_root(normal) == normal


def test_apply_desde_worktree_rechaza_dest_dentro_del_repo_principal(tmp_path):
    """El caso real de la sesión: src = worktree, dest = carpeta dentro del
    checkout PRINCIPAL de hubara. El guard debe cubrir el repo principal, no
    solo el worktree."""
    main = tmp_path / "AgencyHubara"
    src = main / ".claude" / "worktrees" / "rama-x"
    src.mkdir(parents=True)
    dest = main / "AgencyAcme"  # dentro del repo productivo — prohibido
    with pytest.raises(forge.ForgeError, match="repo madre|productivo"):
        forge.run_apply(src=src, dest=dest, client_dir=tmp_path, manifest=forge.load_manifest())


def test_init_rechaza_slug_hubara_sin_crear_nada(tmp_path):
    with pytest.raises(forge.ForgeError, match="hubara"):
        forge.run_init("hubara", forge.load_manifest(), clients_dir=tmp_path)
    assert not (tmp_path / "hubara").exists()


def test_verify_sobre_carpeta_inexistente_no_da_verde(tmp_path, capsys):
    import yaml

    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "client.yaml").write_text(yaml.safe_dump(_client()), encoding="utf-8")
    code = forge.main(["verify", str(tmp_path / "no-existe"), "--client", str(tmp_path / "acme")])
    assert code == 1
    assert "no existe" in capsys.readouterr().err


# ── Premortem 2026-10-09 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("slug", ["cafe_aurora", "a", "x" * 21, "Aurora", "cafe-aurora"])
def test_slug_que_no_cabe_en_los_nombres_de_aws_rechazado(slug):
    """El slug termina en buckets S3 y en el dominio de Cognito (globales, sin
    `_`) y en el nombre más largo, `agency<slug>-<slug>-frontend-oac` (64)."""
    with pytest.raises(forge.ForgeError, match="slug"):
        forge.render_vars(_client(slug=slug))


@pytest.mark.parametrize("slug", ["graphagents", "lab", "awsshop", "ssmtienda"])
def test_slugs_reservados_de_la_cuenta_rechazados(slug):
    """`/graphagents/*` es el árbol de la madre (su caja lo lee entero, por
    ruta) y SSM no acepta nombres que empiecen por aws/ssm."""
    with pytest.raises(forge.ForgeError, match="reservado"):
        forge.render_vars(_client(slug=slug, aws={"resource_prefix": f"agency{slug}", "ssm_prefix": f"/{slug}"}))


@pytest.mark.parametrize("prefix", ["/graphagents", "/hubara-lab", "/aws", "/Acme", "acme"])
def test_ssm_prefix_que_pisa_un_arbol_de_la_cuenta_rechazado(prefix):
    with pytest.raises(forge.ForgeError, match="ssm_prefix"):
        forge.render_vars(_client(aws={"resource_prefix": "agencyacme", "ssm_prefix": prefix}))


@pytest.mark.parametrize("name", ["Mis Clientes", "a;b", "a$b", "a'b", "a(b)"])
def test_un_destino_que_rompe_el_cd_de_los_pasos_guiados_rechazado(tmp_path, name):
    """Los pasos guiados imprimen `cd <destino>`: con un espacio el `cd` falla
    y lo que sigue corre en la carpeta donde estaba el operador (el repo madre)."""
    with pytest.raises(forge.ForgeError, match="destino"):
        forge.check_dest(tmp_path / name / "AgencyAcme")


def test_el_slug_del_client_yaml_es_el_nombre_de_su_carpeta(tmp_path):
    """Los CLIs buscan el bundle por carpeta (`clients/<slug>`): un client.yaml
    copiado a otra carpeta con el slug viejo hacía que la ficha de una tienda
    actuara sobre el bundle de la otra."""
    import yaml

    d = tmp_path / "boreal"
    d.mkdir()
    (d / "client.yaml").write_text(yaml.safe_dump(_client()), encoding="utf-8")  # slug: acme
    with pytest.raises(forge.ForgeError, match="carpeta"):
        forge.load_client(d)


def test_los_bundles_de_clientes_viven_en_el_checkout_principal():
    """forge/clients/ está en .gitignore: un bundle creado desde un worktree
    (estado de la migración, la clave de la base de datos) se perdía al borrarlo."""
    root = Path("/x/AgencyHubara")
    assert forge.clients_root(root / ".claude" / "worktrees" / "w" / "forge") == root / "forge" / "clients"
    assert forge.clients_root(root / "forge") == root / "forge" / "clients"
