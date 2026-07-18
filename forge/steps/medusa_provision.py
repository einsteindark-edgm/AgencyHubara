#!/usr/bin/env python3
"""Step S3/S4 — Medusa del cliente: servicio en Railway desde la URL del repo
+ seed vía Admin API.

AISLAMIENTO (crítico): habla SOLO con backboard.railway.com (GraphQL) y con el
Medusa NUEVO del cliente. Cero AWS/SSM/terraform — los `aws ssm put-parameter`
se IMPRIMEN con el prefijo del clon para que el operador los corra (misma
filosofía que `whatsapp_provision.py ads-token`). No puede tocar hubara.

  export RAILWAY_API_TOKEN=...            # token de cuenta/workspace de Railway
  python3 forge/steps/medusa_provision.py plan  <slug>
  python3 forge/steps/medusa_provision.py apply <slug>     # proyecto+servicio+env+dominio
  export MEDUSA_ADMIN_EMAIL=... MEDUSA_ADMIN_PASSWORD=...
  python3 forge/steps/medusa_provision.py seed  <slug>     # región+canal+key (idempotente)

Prerequisitos humanos (Railway no los expone por API):
  · La Railway GitHub App debe tener acceso al repo del Medusa del cliente.
  · El PRIMER usuario admin de Medusa se crea con `railway run npx medusa user
    -e <email> -p <pass>` (o en el shell del servicio) — una sola vez.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

STEPS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(STEPS_DIR.parent))

import forge  # noqa: E402

RAILWAY_GQL = "https://backboard.railway.com/graphql/v2"


# ── Transportes (inyectables en tests) ───────────────────────────────────────


def gql(query: str, variables: dict) -> dict:
    tok = os.environ.get("RAILWAY_API_TOKEN", "")
    if not tok:
        raise forge.ForgeError("falta RAILWAY_API_TOKEN")
    req = urllib.request.Request(
        RAILWAY_GQL,
        method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        data=json.dumps({"query": query, "variables": variables}).encode(),
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise forge.ForgeError(f"Railway API {e.code}: {e.read()[:300]}")
    if out.get("errors"):
        raise forge.ForgeError(f"Railway GraphQL: {out['errors'][0].get('message')}")
    return out["data"]


def medusa_http(base: str, method: str, path: str, body: dict | None, token: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{base}{path}",
        method=method,
        headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise forge.ForgeError(f"Medusa {e.code} en {method} {path}: {e.read()[:300]}")


# ── Railway: proyecto + servicio desde repo + env + dominio ──────────────────


def build_env(vars_: dict, database_url: str) -> dict:
    """Env mínimo de un Medusa v2 productivo. Secretos generados acá, una vez."""
    return {
        "DATABASE_URL": database_url,
        "JWT_SECRET": secrets.token_urlsafe(32),
        "COOKIE_SECRET": secrets.token_urlsafe(32),
        "STORE_CORS": "*",
        "ADMIN_CORS": "*",
        "AUTH_CORS": "*",
        "PORT": "9000",
        "NODE_ENV": "production",
    }


def cmd_apply(vars_: dict, bundle: Path, api=gql) -> dict:
    client = forge.load_client(bundle)
    repo = (client.get("medusa") or {}).get("repo", "")
    if not repo or repo.startswith("TODO"):
        raise forge.ForgeError("client.yaml: medusa.repo sin completar (owner/name del repo Medusa)")
    sup_file = bundle / ".outputs.supabase.json"
    if not sup_file.exists():
        raise forge.ForgeError("corré primero el step supabase (falta .outputs.supabase.json)")
    database_url = json.loads(sup_file.read_text())["database_url"]
    name = f"{vars_['slug']}-medusa"

    existing = api(
        "query($name:String){ projects(first:50){ edges{ node{ id name } } } }", {"name": name}
    )["projects"]["edges"]
    match = next((e["node"] for e in existing if e["node"]["name"] == name), None)
    if match:
        print(f"= proyecto Railway {name} ya existe ({match['id']}) — no-op")
        out_file = bundle / ".outputs.medusa.json"
        if out_file.exists():
            return json.loads(out_file.read_text())
        raise forge.ForgeError(
            f"{name} existe pero sin outputs locales — completá {out_file} con la base_url"
        )

    project = api(
        "mutation($input:ProjectCreateInput!){ projectCreate(input:$input){ id environments{ edges{ node{ id name } } } } }",
        {"input": {"name": name}},
    )["projectCreate"]
    env_id = project["environments"]["edges"][0]["node"]["id"]
    service = api(
        "mutation($input:ServiceCreateInput!){ serviceCreate(input:$input){ id } }",
        {"input": {"projectId": project["id"], "source": {"repo": repo}}},
    )["serviceCreate"]
    env_vars = build_env(vars_, database_url)
    api(
        "mutation($input:VariableCollectionUpsertInput!){ variableCollectionUpsert(input:$input) }",
        {
            "input": {
                "projectId": project["id"],
                "environmentId": env_id,
                "serviceId": service["id"],
                "variables": env_vars,
            }
        },
    )
    domain = api(
        "mutation($input:ServiceDomainCreateInput!){ serviceDomainCreate(input:$input){ domain } }",
        {"input": {"environmentId": env_id, "serviceId": service["id"]}},
    )["serviceDomainCreate"]["domain"]
    base_url = f"https://{domain}"
    outputs = {
        "project_id": project["id"],
        "service_id": service["id"],
        "environment_id": env_id,
        "base_url": base_url,
    }
    (bundle / ".outputs.medusa.json").write_text(json.dumps(outputs, indent=2))
    print(f"✓ Railway {name}: {base_url} (deploy corre al conectar el repo)")
    print("⚠ CORS quedó ABIERTO (*) para el arranque — cerrarlo a los dominios")
    print("  reales del cliente en las variables de Railway antes de producción.")
    print("⚠ Railway GraphQL no verificado contra API viva: si algo falla, es")
    print("  ruidoso y sin efectos parciales — crear proyecto/servicio a mano y")
    print(f"  completar {bundle / '.outputs.medusa.json'} con la base_url.")
    print("→ PASO HUMANO (una vez): crear el primer admin —")
    print(f"  railway run --project {project['id']} npx medusa user -e <email> -p <pass>")
    return outputs


# ── Seed idempotente vía Admin API ───────────────────────────────────────────


def seed(vars_: dict, base_url: str, email: str, password: str, http=medusa_http) -> dict:
    token = http(base_url, "POST", "/auth/user/emailpass", {"email": email, "password": password}, None)[
        "token"
    ]
    result: dict = {}

    regions = http(base_url, "GET", "/admin/regions?limit=100", None, token).get("regions", [])
    region = next((r for r in regions if r["currency_code"] == vars_["currency"].lower()), None)
    if region is None:
        region = http(
            base_url,
            "POST",
            "/admin/regions",
            {
                "name": vars_["country"],
                "currency_code": vars_["currency"].lower(),
                "countries": [vars_["country"].lower()],
            },
            token,
        )["region"]
        result["region"] = "creada"
    else:
        result["region"] = "existente"
    result["region_id"] = region["id"]

    channels = http(base_url, "GET", "/admin/sales-channels?limit=100", None, token).get(
        "sales_channels", []
    )
    ch_name = f"{vars_['company']} WhatsApp"
    channel = next((c for c in channels if c["name"] == ch_name), None)
    if channel is None:
        channel = http(
            base_url, "POST", "/admin/sales-channels", {"name": ch_name}, token
        )["sales_channel"]
        result["sales_channel"] = "creado"
    else:
        result["sales_channel"] = "existente"
    result["sales_channel_id"] = channel["id"]

    keys = http(base_url, "GET", "/admin/api-keys?limit=100", None, token).get("api_keys", [])
    key_title = f"{vars_['slug']}-backend"
    secret_key = next((k for k in keys if k.get("title") == key_title), None)
    if secret_key is None:
        secret_key = http(
            base_url,
            "POST",
            "/admin/api-keys",
            {"title": key_title, "type": "secret"},
            token,
        )["api_key"]
        result["admin_token"] = secret_key.get("token", "")  # visible SOLO al crear
    result["api_key_id"] = secret_key["id"]
    return result


def print_ssm_block(vars_: dict, base_url: str, seed_result: dict) -> None:
    prefix = f"{vars_['ssm_prefix']}/{vars_['slug']}"
    assert not prefix.startswith("/hubara"), "guard: jamás imprimir paths de hubara"
    print("\n  Correr a mano (este CLI NO toca AWS — mismo patrón que ads-token):")
    print(f"  aws ssm put-parameter --name {prefix}/MEDUSA_BASE_URL --type SecureString --overwrite --value '{base_url}'")
    tok = seed_result.get("admin_token")
    print(
        f"  aws ssm put-parameter --name {prefix}/MEDUSA_ADMIN_TOKEN --type SecureString --overwrite --value '{tok}'"
        if tok
        else "  # MEDUSA_ADMIN_TOKEN: la key secreta ya existía — el token solo es visible al crearla (rotála si lo perdiste)"
    )
    print(f"  # extras del backend: MEDUSA_REGION_ID={seed_result.get('region_id')} MEDUSA_SALES_CHANNEL_ID={seed_result.get('sales_channel_id')}")
    print("  # shipping option: crearla en el Admin UI del Medusa nuevo (requiere stock location + fulfillment set) → MEDUSA_DEFAULT_SHIPPING_OPTION_ID")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2 or args[0] not in {"plan", "apply", "seed", "status"}:
        print(__doc__)
        return 2
    cmd, slug = args
    bundle = forge.CLIENTS / slug
    try:
        vars_ = forge.render_vars(forge.load_client(bundle))  # guards anti-hubara
        if cmd == "plan":
            client = forge.load_client(bundle)
            print(json.dumps({
                "railway_project": f"{slug}-medusa",
                "repo": (client.get("medusa") or {}).get("repo"),
                "supabase_outputs": (bundle / ".outputs.supabase.json").exists(),
            }, indent=2))
        elif cmd == "apply":
            cmd_apply(vars_, bundle)
        elif cmd == "status":
            out = bundle / ".outputs.medusa.json"
            print(out.read_text() if out.exists() else "{} — sin apply todavía")
        else:  # seed
            out = bundle / ".outputs.medusa.json"
            if not out.exists():
                raise forge.ForgeError("corré apply primero (falta .outputs.medusa.json)")
            base_url = json.loads(out.read_text())["base_url"]
            email = os.environ.get("MEDUSA_ADMIN_EMAIL", "")
            password = os.environ.get("MEDUSA_ADMIN_PASSWORD", "")
            if not email or not password:
                raise forge.ForgeError("faltan MEDUSA_ADMIN_EMAIL / MEDUSA_ADMIN_PASSWORD")
            result = seed(vars_, base_url, email, password)
            print(json.dumps({k: v for k, v in result.items() if k != "admin_token"}, indent=2))
            print_ssm_block(vars_, base_url, result)
    except forge.ForgeError as e:
        print(f"medusa_provision: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
