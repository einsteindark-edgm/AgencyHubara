#!/usr/bin/env python3
"""migrate — la migración completa a un cliente nuevo, como STEPS con estado.

  python3 forge/migrate.py status <slug> [--dest <clon>] [--json]
  python3 forge/migrate.py run    <slug> <step> --dest <clon> [--allow-todos]
  python3 forge/migrate.py done   <slug> <step>      # marcar un step guiado

Dos clases de step — y esta distinción ES la garantía de aislamiento:

  · AUTO    — hablan SOLO con APIs de terceros (Supabase, Railway, Medusa,
              Temporal Cloud) donde hubara ni existe. Se ejecutan de verdad.
  · GUIADO  — todo lo que toca AWS/terraform/git-push se IMPRIME como comandos
              exactos apuntando al CLON (cd <clon> && …); este runner NUNCA
              ejecuta un comando AWS. Corrés, verificás, y marcás `done`.

Guards duros además del diseño: slug/prefijos de hubara rechazados, y el clon
jamás puede vivir dentro del repo madre. Estado por cliente en
forge/clients/<slug>/.migration-state.json (gitignored).
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # forge/
sys.path.insert(0, str(ROOT))

import forge  # noqa: E402

PY = sys.executable or "python3"


# ── Definición de steps ───────────────────────────────────────────────────────


def _q(path: Path) -> str:
    """Una ruta lista para pegar en la terminal (forge ya rechaza las raras)."""
    return shlex.quote(str(path))



def _guide_whatsapp(vars_: dict, dest: Path) -> str:
    return f"""\
# S5 — WhatsApp/Meta: el CLI de aprobaciones, DESDE EL CLON (camino crítico:
#      Meta tarda días/semanas — arrancar este step lo antes posible)
cd {_q(dest / "infra/whatsapp-provisioning")}
cp tenants/{vars_["slug"]}.env.example tenants/{vars_["slug"]}.env   # completar con el BM de {vars_["company"]}
python3 whatsapp_provision.py discover  --config tenants/{vars_["slug"]}.env
python3 whatsapp_provision.py plan      --config tenants/{vars_["slug"]}.env
python3 whatsapp_provision.py apply     --config tenants/{vars_["slug"]}.env   # + --code <sms> al registrar la línea
python3 whatsapp_provision.py templates --config tenants/{vars_["slug"]}.env   # APROBACIÓN Meta por WABA (incluye campaign_promo_marketing)
python3 whatsapp_provision.py flows     --config tenants/{vars_["slug"]}.env   # re-publica shipping → flow_id NUEVO (WABA-scoped)
python3 whatsapp_provision.py capi      --config tenants/{vars_["slug"]}.env   # dataset CTWA propio
python3 whatsapp_provision.py ads-token --config tenants/{vars_["slug"]}.env   # imprime el seed {vars_["ssm_prefix"]}/{vars_["slug"]}/meta/oauth
# Pasos humanos irreducibles: conseguir la línea, código SMS, Business
# Verification y aprobación del display name — el CLI los deja marcados."""


def _guide_bootstrap(vars_: dict, dest: Path) -> str:
    return f"""\
# S7 — Bootstrap AWS del cliente (una vez, con TUS creds admin, DESDE EL CLON)
cd {_q(dest)}
python3 infra/scripts/aws_bootstrap.py state          # bucket {vars_["prefix"]}-tfstate + lock
#   ⚠ si dice «bucket ya existe» y esta tienda no lo creó, ese slug ya es de OTRO proyecto: parar
# repo del cliente en GitHub (privado), SIN push todavía: S8 le carga las variables y S9 empuja
gh repo create {vars_["repo"]} --private --source . --remote origin
ssh-keygen -t ed25519 -f ~/.ssh/{vars_["slug"]}_ops -C "{vars_["slug"]}-ops"
#   → pública a infra/terraform/compute/tenants.auto.tfvars (la privada la sube S8 a GitHub)
#   + crear el environment `production` en GitHub con required reviewers"""


def _guide_platform(vars_: dict, dest: Path) -> str:
    slug = vars_["slug"]
    return f"""\
# S8 — Platform + secretos (DESDE EL CLON; state propio {vars_["prefix"]}-tfstate)
cd {_q(dest / "infra/terraform/platform")}
cp envs/real.s3.tfbackend.example envs/real.s3.tfbackend   # (primera vez; el bucket es {vars_["prefix"]}-tfstate)
terraform init -backend-config=envs/real.s3.tfbackend && terraform apply
#   (project.auto.tfvars ya trae create_github_oidc_provider=false)
# GitHub del repo — DESPUÉS del apply (lee sus outputs): vars AWS_*/TF_STATE_* + secret EC2_SSH_KEY
cd {_q(dest)}
python3 infra/scripts/aws_bootstrap.py github --repo {vars_["repo"]} --platform-dir infra/terraform/platform --ssh-key-file ~/.ssh/{slug}_ops
# Secretos reales → SSM {vars_["ssm_prefix"]}/{slug}/* (la copia la ignora git):
cp infra/scripts/secrets.example.env infra/scripts/secrets.{slug}.env   # llenar
python3 infra/scripts/aws_bootstrap.py secrets --tenant {slug} --file infra/scripts/secrets.{slug}.env
python3 infra/scripts/aws_bootstrap.py verify --tenant {slug}    # lo que falta antes del deploy
#   obligatorios para el primer deploy: WHATSAPP_APP_SECRET, HUBARA_SERVICE_TOKEN, COGNITO_* (apply)
# + los bloques `aws ssm put-parameter` que imprimieron los steps S4 (seed de Medusa) y S6 (Temporal)
# + llave Nequi de la tienda: store.payment_nequi_number en tenants.auto.tfvars → apply"""


def _guide_compute(vars_: dict, dest: Path) -> str:
    return f"""\
# S9 — Compute + primer deploy + schedules (DESDE EL CLON)
cd {_q(dest / "infra/terraform/compute")}
cp envs/real.s3.tfbackend.example envs/real.s3.tfbackend   # (primera vez)
terraform init -backend-config=envs/real.s3.tfbackend && terraform apply   # caja + EIP
#   → poner domain "<ip-con-guiones>.sslip.io" en tenants.auto.tfvars + api_url en platform → re-apply
cd {_q(dest)} && git push origin main       # dispara backend-deploy + frontend-deploy del CLON
# Después: webhook Meta + Sync del catálogo + schedules + App Operador — checklist en {_q(dest / "NEXT_STEPS.md")} (F7/F8)"""


STEPS: list[dict] = [
    {"id": "clone", "title": "S1 Forjar el repo del cliente", "kind": "auto"},
    {"id": "supabase", "title": "S2 Postgres (proyecto Supabase nuevo)", "kind": "auto"},
    {"id": "medusa", "title": "S3 Medusa en Railway (desde la URL del repo)", "kind": "auto"},
    {"id": "medusa-seed", "title": "S4 Seed de Medusa (región/canal/key)", "kind": "auto"},
    {"id": "whatsapp", "title": "S5 WhatsApp/Meta — aprobaciones (número/templates/flows/CAPI/ads-token)", "kind": "guided", "guide": _guide_whatsapp},
    {"id": "temporal", "title": "S6 Temporal Cloud (namespace + API key)", "kind": "guided"},
    {"id": "aws-bootstrap", "title": "S7 Bootstrap AWS (state/keys/GH)", "kind": "guided", "guide": _guide_bootstrap},
    {"id": "platform", "title": "S8 Platform + secretos SSM", "kind": "guided", "guide": _guide_platform},
    {"id": "compute", "title": "S9 Compute + deploy + schedules", "kind": "guided", "guide": _guide_compute},
]
STEP_IDS = [s["id"] for s in STEPS]


# ── Estado ────────────────────────────────────────────────────────────────────


def state_file(bundle: Path) -> Path:
    return bundle / ".migration-state.json"


def load_state(bundle: Path) -> dict:
    f = state_file(bundle)
    return json.loads(f.read_text()) if f.exists() else {"steps": {}}


def mark(bundle: Path, step: str, status: str) -> None:
    st = load_state(bundle)
    st["steps"][step] = status
    state_file(bundle).write_text(json.dumps(st, indent=2))


def has_commit(dest: Path) -> bool:
    """El clon existe de verdad: `.git` CON su primer commit (si el commit falló,
    queda `.git` sin HEAD y no es un clon)."""
    return (dest / ".git").exists() and subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True
    ).returncode == 0


def auto_done(step: str, bundle: Path, dest: Path | None) -> bool:
    """Evidencia en disco de que un step auto ya corrió (además del estado)."""
    if step == "clone":
        return dest is not None and has_commit(dest)
    if step == "supabase":
        return (bundle / ".outputs.supabase.json").exists()
    if step == "medusa":  # el avance se guarda a mitad: hecho = con su base_url
        out = bundle / ".outputs.medusa.json"
        return out.exists() and bool(json.loads(out.read_text()).get("base_url"))
    return False


# ── Ejecución ─────────────────────────────────────────────────────────────────


def guard_dest(dest: Path) -> None:
    forge.check_dest(dest)  # sin espacios: los pasos guiados la imprimen en `cd <destino>`
    protected = forge.main_repo_root(forge.REPO)  # cubre también correr desde un worktree
    if dest.resolve().is_relative_to(protected.resolve()):
        raise forge.ForgeError(
            f"dest {dest} está dentro del repo madre/productivo ({protected}) — el clon vive afuera"
        )


def run_step(slug: str, step: str, bundle: Path, dest: Path | None, allow_todos: bool,
             runner=subprocess.run) -> int:
    vars_ = forge.render_vars(forge.load_client(bundle))  # guards anti-hubara
    if step not in STEP_IDS:
        raise forge.ForgeError(f"step desconocido {step!r} — válidos: {', '.join(STEP_IDS)}")
    spec = next(s for s in STEPS if s["id"] == step)
    if dest is not None:
        guard_dest(dest)

    if spec["kind"] == "guided" and "guide" in spec:
        if dest is None:
            raise forge.ForgeError("los steps guiados necesitan --dest (el clon)")
        print(spec["guide"](vars_, dest))
        print(f"\n→ cuando termine: python3 forge/migrate.py done {slug} {step}")
        return 0

    argv: list[str]
    if step == "clone":
        if dest is None:
            raise forge.ForgeError("clone necesita --dest")
        argv = [PY, str(ROOT / "forge.py"), "apply", slug, "--dest", str(dest)]
        if allow_todos:
            argv.append("--allow-todos")
    elif step == "supabase":
        argv = [PY, str(ROOT / "steps" / "supabase_provision.py"), "apply", slug]
    elif step == "medusa":
        argv = [PY, str(ROOT / "steps" / "medusa_provision.py"), "apply", slug]
    elif step == "medusa-seed":
        argv = [PY, str(ROOT / "steps" / "medusa_provision.py"), "seed", slug]
    else:  # temporal (guided sin guide function: delega en su CLI, que imprime)
        argv = [PY, str(ROOT / "steps" / "temporal_provision.py"), "apply", slug]
    code = runner(argv).returncode
    if code == 0 and spec["kind"] == "auto":
        mark(bundle, step, "done")
    if code == 0 and step == "clone" and dest is not None:
        # una migración dura días: la consola vuelve a encontrar el clon (y sabe
        # si es uno de prueba, que no va a producción)
        st = load_state(bundle)
        st["dest"] = str(dest.resolve())
        st["test_clone"] = bool(allow_todos)
        state_file(bundle).write_text(json.dumps(st, indent=2))
    return code


def step_rows(bundle: Path, dest: Path | None) -> list[dict]:
    st = load_state(bundle)["steps"]
    return [
        {"id": s["id"], "title": s["title"], "kind": s["kind"],
         "done": st.get(s["id"]) == "done" or auto_done(s["id"], bundle, dest)}
        for s in STEPS
    ]


def cmd_status(slug: str, bundle: Path, dest: Path | None, as_json: bool = False) -> None:
    vars_ = forge.render_vars(forge.load_client(bundle))
    if as_json:  # lo consume Acktos Studio (Forge Console → Migración)
        st = load_state(bundle)
        print(json.dumps({"slug": slug, "company": vars_["company"], "dest": st.get("dest"),
                          "test_clone": st.get("test_clone", False), "steps": step_rows(bundle, dest)},
                         ensure_ascii=False))
        return
    st = load_state(bundle)["steps"]
    print(f"Migración de {vars_['company']} ({slug}) — SSM {vars_['ssm_prefix']}/{slug}, "
          f"recursos {vars_['prefix']}-*\n")
    for s in STEPS:
        done = st.get(s["id"]) == "done" or auto_done(s["id"], bundle, dest)
        icon = "✓" if done else ("⧖" if s["kind"] == "guided" else "○")
        print(f"  {icon} {s['title']}  [{s['kind']}]")
    print("\n○ pendiente · ⧖ guiado (imprime comandos, marcás done) · ✓ hecho")
    print("Regla de la casa: este runner JAMÁS ejecuta comandos AWS — los imprime apuntando al clon.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="migrate", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status"); p.add_argument("slug"); p.add_argument("--dest")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("run"); p.add_argument("slug"); p.add_argument("step")
    p.add_argument("--dest"); p.add_argument("--allow-todos", action="store_true")
    p = sub.add_parser("done"); p.add_argument("slug"); p.add_argument("step")
    a = ap.parse_args(argv)
    bundle = forge.CLIENTS / a.slug
    try:
        if a.cmd == "status":
            cmd_status(a.slug, bundle, Path(a.dest) if a.dest else None, a.json)
        elif a.cmd == "done":
            forge.render_vars(forge.load_client(bundle))  # guards
            if a.step not in STEP_IDS:
                raise forge.ForgeError(f"step desconocido {a.step!r}")
            mark(bundle, a.step, "done")
            print(f"✓ {a.step} marcado done")
        else:
            return run_step(a.slug, a.step, bundle, Path(a.dest) if a.dest else None,
                            a.allow_todos)
    except forge.ForgeError as e:
        print(f"migrate: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
