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
    medusa_provision.print_ssm_block(vars_(), "https://x", {"region_id": "r", "sales_channel_id": "s"})
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
