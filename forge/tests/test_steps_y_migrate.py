"""Steps de migración + runner: lógica pura con transportes fake, y el
contrato de aislamiento (nada puede apuntar a hubara, nada ejecuta AWS)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

FORGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE_DIR))
sys.path.insert(0, str(FORGE_DIR / "steps"))

import forge  # noqa: E402
import medusa_provision  # noqa: E402
import migrate  # noqa: E402
import supabase_provision  # noqa: E402
import temporal_provision  # noqa: E402

CLIENT = {
    "slug": "acme",
    "company": "Acme",
    "repo": "einsteindark-edgm/AgencyAcme",
    "aws": {"resource_prefix": "agencyacme", "ssm_prefix": "/acme"},
    "business": {"country": "CO", "currency": "COP"},
    "medusa": {"repo": "acme-org/medusa-backend", "supabase_org": "org_123",
               "supabase_region": "us-east-1"},
}


@pytest.fixture()
def bundle(tmp_path):
    b = tmp_path / "acme"
    b.mkdir()
    (b / "client.yaml").write_text(yaml.safe_dump(CLIENT), encoding="utf-8")
    return b


def vars_():
    return forge.render_vars(CLIENT)


# ── Supabase ──────────────────────────────────────────────────────────────────


def test_supabase_apply_crea_proyecto_y_escribe_outputs(bundle):
    calls = []

    def fake_api(method, path, body=None, token=None):
        calls.append((method, path, body))
        if (method, path) == ("GET", "/projects"):
            return []
        if (method, path) == ("POST", "/projects"):
            assert body["name"] == "acme-medusa"
            assert body["organization_id"] == "org_123"
            assert body["region"] == "us-east-1"
            assert len(body["db_pass"]) >= 24
            return {"id": "refacme"}
        if path == "/projects/refacme":
            return {"status": "ACTIVE_HEALTHY"}
        raise AssertionError(f"llamada inesperada {method} {path}")

    out = supabase_provision.cmd_apply(vars_(), bundle, api=fake_api, sleep=lambda s: None)
    assert out["ref"] == "refacme"
    assert out["database_url"].startswith("postgresql://postgres:")
    assert "db.refacme.supabase.co:5432" in out["database_url"]
    assert "pooler.supabase.com:6543" in out["database_url_pooler"]
    saved = json.loads((bundle / ".outputs.supabase.json").read_text())
    assert saved["database_url"] == out["database_url"]


def test_supabase_existente_sin_outputs_locales_falla_claro(bundle):
    def fake_api(method, path, body=None, token=None):
        return [{"name": "acme-medusa", "id": "refacme"}]

    with pytest.raises(forge.ForgeError, match="no es .*recuperable|outputs"):
        supabase_provision.cmd_apply(vars_(), bundle, api=fake_api, sleep=lambda s: None)


# ── Medusa ────────────────────────────────────────────────────────────────────


def test_medusa_apply_necesita_supabase_primero(bundle):
    with pytest.raises(forge.ForgeError, match="supabase"):
        medusa_provision.cmd_apply(vars_(), bundle, api=lambda q, v: {})


def test_medusa_apply_crea_proyecto_servicio_env_y_dominio(bundle):
    (bundle / ".outputs.supabase.json").write_text(
        json.dumps({"database_url": "postgresql://postgres:x@db.ref.supabase.co:5432/postgres"})
    )
    seen = {"env_vars": None}

    def fake_gql(query, variables):
        if query.startswith("query($name"):
            return {"projects": {"edges": []}}
        if "projectCreate" in query:
            assert variables["input"]["name"] == "acme-medusa"
            return {"projectCreate": {"id": "P1", "environments": {"edges": [{"node": {"id": "E1", "name": "production"}}]}}}
        if "serviceCreate" in query:
            assert variables["input"]["source"]["repo"] == "acme-org/medusa-backend"
            return {"serviceCreate": {"id": "S1"}}
        if "variableCollectionUpsert" in query:
            seen["env_vars"] = variables["input"]["variables"]
            return {"variableCollectionUpsert": True}
        if "serviceDomainCreate" in query:
            return {"serviceDomainCreate": {"domain": "acme-medusa.up.railway.app"}}
        raise AssertionError(f"query inesperada: {query[:60]}")

    out = medusa_provision.cmd_apply(vars_(), bundle, api=fake_gql)
    assert out["base_url"] == "https://acme-medusa.up.railway.app"
    env = seen["env_vars"]
    assert env["DATABASE_URL"].startswith("postgresql://")
    assert len(env["JWT_SECRET"]) > 20 and len(env["COOKIE_SECRET"]) > 20


def test_medusa_seed_es_idempotente(bundle):
    posts = []

    def fake_http(base, method, path, body, token):
        if path == "/auth/user/emailpass":
            return {"token": "tok"}
        if method == "GET" and path.startswith("/admin/regions"):
            return {"regions": [{"id": "reg_1", "currency_code": "cop"}]}  # ya existe
        if method == "GET" and path.startswith("/admin/sales-channels"):
            return {"sales_channels": []}
        if method == "POST" and path == "/admin/sales-channels":
            posts.append(path)
            return {"sales_channel": {"id": "sc_1", "name": body["name"]}}
        if method == "GET" and path.startswith("/admin/api-keys"):
            return {"api_keys": []}
        if method == "POST" and path == "/admin/api-keys":
            posts.append(path)
            assert body["type"] == "secret"
            return {"api_key": {"id": "ak_1", "token": "sk_nuevo"}}
        raise AssertionError(f"{method} {path}")

    r = medusa_provision.seed(vars_(), "https://acme.up.railway.app", "a@a", "pw", http=fake_http)
    assert r["region"] == "existente" and r["region_id"] == "reg_1"
    assert r["sales_channel"] == "creado"
    assert r["admin_token"] == "sk_nuevo"
    assert posts == ["/admin/sales-channels", "/admin/api-keys"]  # NO creó región


def test_medusa_ssm_block_usa_el_prefijo_del_clon(bundle, capsys):
    medusa_provision.print_ssm_block(vars_(), "https://x", {"region_id": "r", "sales_channel_id": "s"}, bundle)
    out = capsys.readouterr().out
    assert "--name /acme/acme/MEDUSA_BASE_URL" in out
    assert "/hubara/" not in out


# ── Temporal ──────────────────────────────────────────────────────────────────


def test_temporal_commands_usan_el_slug_y_rechazan_hubara():
    cmds = temporal_provision.build_commands("acme")
    flat = " ".join(" ".join(c) for c in cmds)
    assert "--namespace acme" in flat and "acme=Write" in flat
    with pytest.raises(forge.ForgeError):
        temporal_provision.build_commands("hubara")


# ── migrate (runner) ──────────────────────────────────────────────────────────


def test_migrate_step_guiado_imprime_y_no_ejecuta(bundle, tmp_path, capsys):
    dest = tmp_path / "AgencyAcme"

    def boom(argv):  # si ejecuta algo, el test truena
        raise AssertionError(f"un step guiado ejecutó: {argv}")

    code = migrate.run_step("acme", "platform", bundle, dest, False, runner=boom)
    out = capsys.readouterr().out
    assert code == 0
    assert "terraform apply" in out and str(dest) in out
    assert "aws_bootstrap.py secrets --tenant acme" in out
    assert "/hubara" not in out


def test_migrate_step_whatsapp_imprime_el_ladder_de_aprobaciones(bundle, tmp_path, capsys):
    """El CLI de aprobaciones WhatsApp (número, templates, flows, CAPI,
    ads-token) es un step de primera clase — guiado, desde el CLON."""
    dest = tmp_path / "AgencyAcme"

    def boom(argv):
        raise AssertionError(f"step guiado ejecutó: {argv}")

    code = migrate.run_step("acme", "whatsapp", bundle, dest, False, runner=boom)
    out = capsys.readouterr().out
    assert code == 0
    for frag in ["whatsapp_provision.py", "tenants/acme.env", "templates", "flows",
                 "capi", "ads-token", str(dest)]:
        assert frag in out, f"falta {frag} en la guía"
    assert "/hubara" not in out and "hubara.env" not in out


def test_migrate_rechaza_dest_dentro_del_repo_madre(bundle):
    with pytest.raises(forge.ForgeError, match="repo madre"):
        migrate.run_step("acme", "clone", bundle, forge.REPO / "x", False, runner=lambda a: None)


def test_migrate_step_auto_marca_done(bundle, tmp_path):
    class R:
        returncode = 0

    code = migrate.run_step("acme", "medusa-seed", bundle, None, False, runner=lambda a: R())
    assert code == 0
    assert migrate.load_state(bundle)["steps"]["medusa-seed"] == "done"


def test_migrate_step_desconocido(bundle):
    with pytest.raises(forge.ForgeError, match="desconocido"):
        migrate.run_step("acme", "nope", bundle, None, False, runner=lambda a: None)


def test_migrate_rechaza_cliente_hubara(tmp_path):
    b = tmp_path / "hubara"
    b.mkdir()
    (b / "client.yaml").write_text(yaml.safe_dump({**CLIENT, "slug": "hubara"}), encoding="utf-8")
    with pytest.raises(forge.ForgeError, match="hubara"):
        migrate.run_step("hubara", "supabase", b, None, False, runner=lambda a: None)


def test_status_json_para_acktos_studio(bundle, tmp_path, monkeypatch, capsys):
    """Acktos Studio pinta los steps desde `status --json` (no parsea íconos)."""
    monkeypatch.setattr(forge, "CLIENTS", bundle.parent)
    migrate.mark(bundle, "whatsapp", "done")
    assert migrate.main(["status", "acme", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["slug"] == "acme" and data["company"] == "Acme"
    ids = [s["id"] for s in data["steps"]]
    assert ids == migrate.STEP_IDS
    by_id = {s["id"]: s for s in data["steps"]}
    assert by_id["whatsapp"]["done"] is True and by_id["whatsapp"]["kind"] == "guided"
    assert by_id["clone"]["done"] is False and by_id["clone"]["kind"] == "auto"
    assert all(s["title"] for s in data["steps"])


# ── Premortem 2026-10-09 ──────────────────────────────────────────────────────


def test_el_token_de_medusa_no_se_imprime_queda_en_un_archivo_privado(bundle, capsys):
    """Studio guarda en disco el canal de salida: el token impreso quedaba en
    los logs de VS Code."""
    seeded = {"admin_token": "sk_live_123", "region_id": "r", "sales_channel_id": "s"}
    medusa_provision.print_ssm_block(vars_(), "https://x", seeded, bundle)
    out = capsys.readouterr().out
    secret = bundle / ".secret.medusa_admin_token"
    assert "sk_live_123" not in out
    assert secret.read_text() == "sk_live_123"
    assert secret.stat().st_mode & 0o777 == 0o600
    assert f"--value file://{secret}" in out


def test_supabase_guarda_la_clave_antes_de_esperar_y_falla_si_no_queda_sana(bundle):
    """La clave de la base de datos solo existe al crear el proyecto: si la
    espera se cortaba (o un 503), se perdía y el paso quedaba sin arreglo."""

    def fake_api(method, path, body=None, token=None):
        if (method, path) == ("GET", "/projects"):
            return []
        if (method, path) == ("POST", "/projects"):
            return {"id": "refacme"}
        if path == "/projects/refacme":
            assert (bundle / ".outputs.supabase.json").exists(), "la clave ya tiene que estar a salvo"
            return {"status": "COMING_UP"}
        raise AssertionError(f"{method} {path}")

    with pytest.raises(forge.ForgeError, match="ACTIVE_HEALTHY"):
        supabase_provision.cmd_apply(vars_(), bundle, api=fake_api, sleep=lambda s: None)
    out = bundle / ".outputs.supabase.json"
    assert json.loads(out.read_text())["ref"] == "refacme"
    assert out.stat().st_mode & 0o777 == 0o600


def test_supabase_existente_vuelve_a_esperar_que_quede_sano(bundle):
    (bundle / ".outputs.supabase.json").write_text(json.dumps({"ref": "refacme"}))
    statuses = iter(["COMING_UP", "ACTIVE_HEALTHY"])

    def fake_api(method, path, body=None, token=None):
        if (method, path) == ("GET", "/projects"):
            return [{"name": "acme-medusa", "id": "refacme"}]
        if path == "/projects/refacme":
            return {"status": next(statuses)}
        raise AssertionError(f"{method} {path}")

    assert supabase_provision.cmd_apply(vars_(), bundle, api=fake_api, sleep=lambda s: None)["ref"] == "refacme"
    assert next(statuses, "agotado") == "agotado", "volvió a preguntar hasta ACTIVE_HEALTHY"


class _Run:
    def __init__(self, rc, out):
        self.returncode, self.stdout, self.stderr = rc, out, ""


def test_temporal_no_adopta_un_namespace_que_no_creo(bundle, monkeypatch, capsys):
    """La cuenta de Temporal Cloud es compartida: un namespace con ese nombre
    que este bundle no creó es de otro proyecto, y el service account nuevo
    recibiría permiso de escritura sobre él."""
    monkeypatch.setattr(forge, "CLIENTS", bundle.parent)
    monkeypatch.setattr(temporal_provision.shutil, "which", lambda b: "/usr/local/bin/tcld")
    monkeypatch.setattr(temporal_provision.subprocess, "run", lambda c, **kw: _Run(1, "namespace already exists"))
    assert temporal_provision.main(["apply", "acme"]) == 1
    assert "no lo creó" in capsys.readouterr().err


def test_temporal_reintento_del_mismo_bundle_sigue(bundle, monkeypatch):
    monkeypatch.setattr(forge, "CLIENTS", bundle.parent)
    monkeypatch.setattr(temporal_provision.shutil, "which", lambda b: "/usr/local/bin/tcld")
    monkeypatch.setattr(temporal_provision.subprocess, "run", lambda c, **kw: _Run(0, "ok"))
    assert temporal_provision.main(["apply", "acme"]) == 0
    assert json.loads((bundle / ".outputs.temporal.json").read_text())["namespace"] == "acme"
    monkeypatch.setattr(temporal_provision.subprocess, "run", lambda c, **kw: _Run(1, "already exists"))
    assert temporal_provision.main(["apply", "acme"]) == 0


def test_un_clon_sin_su_primer_commit_no_cuenta_como_forjado(bundle, tmp_path):
    import subprocess

    dest = tmp_path / "AgencyAcme"
    dest.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    assert migrate.auto_done("clone", bundle, dest) is False


def test_el_paso_clone_recuerda_la_carpeta_y_status_la_devuelve(bundle, tmp_path, capsys):
    """Una migración dura días: la consola tiene que volver a encontrar el clon."""
    dest = tmp_path / "AgencyAcme"

    class R:
        returncode = 0

    assert migrate.run_step("acme", "clone", bundle, dest, False, runner=lambda a: R()) == 0
    migrate.cmd_status("acme", bundle, None, as_json=True)
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["dest"] == str(dest.resolve())


def test_las_guias_citan_la_carpeta_del_clon_entre_comillas():
    guide = migrate._guide_platform(vars_(), Path("/tmp/Mis Clientes/AgencyAcme"))
    assert "cd '/tmp/Mis Clientes/AgencyAcme/infra/terraform/platform'" in guide


def test_migrate_rechaza_una_carpeta_con_espacios(bundle, tmp_path):
    with pytest.raises(forge.ForgeError, match="destino"):
        migrate.run_step("acme", "platform", bundle, tmp_path / "Mis Clientes" / "AgencyAcme", False,
                         runner=lambda a: None)


def test_medusa_retoma_donde_quedo_si_railway_falla_a_mitad(bundle):
    """Un error después de crear el proyecto dejaba un proyecto Railway a medias
    y el reintento pedía completar los outputs a mano."""
    (bundle / ".outputs.supabase.json").write_text(
        json.dumps({"database_url": "postgresql://postgres:x@db.ref.supabase.co:5432/postgres"})
    )
    created = {"projects": [], "services": 0}

    def fake_gql(query, variables, fail_service=[True]):
        if query.startswith("query($name"):
            return {"projects": {"edges": [{"node": p} for p in created["projects"]]}}
        if "projectCreate" in query:
            created["projects"].append({"id": "P1", "name": variables["input"]["name"]})
            return {"projectCreate": {"id": "P1", "environments": {"edges": [{"node": {"id": "E1", "name": "production"}}]}}}
        if "serviceCreate" in query:
            if fail_service[0]:
                fail_service[0] = False
                raise RuntimeError("502 de Railway")
            created["services"] += 1
            return {"serviceCreate": {"id": "S1"}}
        if "variableCollectionUpsert" in query:
            return {"variableCollectionUpsert": True}
        if "serviceDomainCreate" in query:
            return {"serviceDomainCreate": {"domain": "acme-medusa.up.railway.app"}}
        raise AssertionError(query[:60])

    with pytest.raises(RuntimeError, match="502"):
        medusa_provision.cmd_apply(vars_(), bundle, api=fake_gql)
    out = medusa_provision.cmd_apply(vars_(), bundle, api=fake_gql)
    assert out["base_url"] == "https://acme-medusa.up.railway.app"
    assert len(created["projects"]) == 1 and created["services"] == 1


def test_un_medusa_a_medias_no_cuenta_como_hecho(bundle):
    (bundle / ".outputs.medusa.json").write_text(json.dumps({"project_id": "P1", "environment_id": "E1"}))
    assert migrate.auto_done("medusa", bundle, None) is False
    (bundle / ".outputs.medusa.json").write_text(json.dumps({"project_id": "P1", "base_url": "https://x"}))
    assert migrate.auto_done("medusa", bundle, None) is True
