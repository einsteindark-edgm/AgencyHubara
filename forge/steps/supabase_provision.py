#!/usr/bin/env python3
"""Step S2 — Postgres del Medusa del cliente: proyecto Supabase NUEVO.

Supabase NO tiene namespaces (a diferencia de Temporal Cloud): la unidad de
aislamiento es el PROYECTO (1 proyecto = 1 Postgres propio). Los "branches"
son previews de desarrollo, no separación de clientes. Silo = proyecto nuevo
por cliente, creado acá vía la Management API.

AISLAMIENTO (crítico): este step habla SOLO con api.supabase.com — cero
comandos AWS, cero SSM, cero terraform. No puede tocar hubara por construcción.

  export SUPABASE_ACCESS_TOKEN=sbp_...   # PAT de https://supabase.com/dashboard/account/tokens
  python3 forge/steps/supabase_provision.py plan   <slug>
  python3 forge/steps/supabase_provision.py apply  <slug>
  python3 forge/steps/supabase_provision.py status <slug>

Outputs → forge/clients/<slug>/.outputs.supabase.json (gitignored) con las
DATABASE_URL (directa y pooler) para el step de Medusa/Railway.
"""

from __future__ import annotations

import json
import os
import secrets
import string
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STEPS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(STEPS_DIR.parent))

import forge  # noqa: E402  (guards + carga de client.yaml)

API = "https://api.supabase.com/v1"


def _token() -> str:
    tok = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
    if not tok:
        raise forge.ForgeError(
            "falta SUPABASE_ACCESS_TOKEN (PAT de supabase.com/dashboard/account/tokens)"
        )
    return tok


def http(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict | list:
    """Transporte HTTP inyectable en tests (los tests pasan un fake)."""
    req = urllib.request.Request(
        f"{API}{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {token or _token()}",
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise forge.ForgeError(f"Supabase API {e.code} en {method} {path}: {e.read()[:300]}")


def project_name(vars_: dict) -> str:
    return f"{vars_['slug']}-medusa"


def find_project(vars_: dict, api=http) -> dict | None:
    for p in api("GET", "/projects"):
        if p.get("name") == project_name(vars_):
            return p
    return None


def gen_db_pass() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(32))


def db_urls(ref: str, password: str, region: str) -> dict:
    return {
        # directa (migrations / uso persistente del server Medusa)
        "database_url": f"postgresql://postgres:{password}@db.{ref}.supabase.co:5432/postgres",
        # pooler transaccional (si Medusa corre serverless algún día)
        "database_url_pooler": (
            f"postgresql://postgres.{ref}:{password}"
            f"@aws-0-{region}.pooler.supabase.com:6543/postgres"
        ),
    }


def cmd_apply(vars_: dict, bundle: Path, api=http, sleep=time.sleep) -> dict:
    name = project_name(vars_)
    client = forge.load_client(bundle)
    medusa_cfg = client.get("medusa") or {}
    org = medusa_cfg.get("supabase_org", "")
    region = medusa_cfg.get("supabase_region", "us-east-1")
    if not org or org.startswith("TODO"):
        raise forge.ForgeError("client.yaml: medusa.supabase_org sin completar (id de la org)")
    existing = find_project(vars_, api)
    if existing:
        print(f"= proyecto {name} ya existe (ref {existing['id']}) — no-op")
        ref = existing["id"]
        outputs_file = bundle / ".outputs.supabase.json"
        if not outputs_file.exists():
            raise forge.ForgeError(
                f"{name} existe pero no tengo sus outputs locales — la DB pass no es "
                "recuperable por API; resetearla en el dashboard y escribir "
                f"{outputs_file} a mano"
            )
        return json.loads(outputs_file.read_text())
    password = gen_db_pass()
    created = api(
        "POST",
        "/projects",
        {
            "name": name,
            "organization_id": org,
            "db_pass": password,
            "region": region,
        },
    )
    ref = created["id"]
    print(f"+ proyecto {name} creado (ref {ref}) — esperando ACTIVE_HEALTHY…")
    for _ in range(60):
        status = api("GET", f"/projects/{ref}").get("status", "")
        if status == "ACTIVE_HEALTHY":
            break
        sleep(10)
    else:
        print("⚠ el proyecto no llegó a ACTIVE_HEALTHY en 10 min — revisá el dashboard")
    outputs = {"ref": ref, "region": region, **db_urls(ref, password, region)}
    out_file = bundle / ".outputs.supabase.json"
    out_file.write_text(json.dumps(outputs, indent=2))
    print(f"✓ outputs → {out_file} (gitignored; la DATABASE_URL lleva la credencial)")
    return outputs


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2 or args[0] not in {"plan", "apply", "status"}:
        print(__doc__)
        return 2
    cmd, slug = args
    bundle = forge.CLIENTS / slug
    try:
        vars_ = forge.render_vars(forge.load_client(bundle))  # guards anti-hubara adentro
        if cmd == "plan":
            existing = find_project(vars_)
            print(f"proyecto: {project_name(vars_)} — {'EXISTE' if existing else 'se crearía'}")
        elif cmd == "status":
            p = find_project(vars_)
            print(json.dumps({"name": project_name(vars_), "status": p and p.get("status")}, indent=2))
        else:
            cmd_apply(vars_, bundle)
    except forge.ForgeError as e:
        print(f"supabase_provision: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
