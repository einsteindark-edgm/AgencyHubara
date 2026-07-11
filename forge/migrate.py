#!/usr/bin/env python3
"""migrate — la migración completa a un cliente nuevo, como STEPS con estado.

  python3 forge/migrate.py status <slug> [--dest <clon>]
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
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # forge/
sys.path.insert(0, str(ROOT))

import forge  # noqa: E402

PY = sys.executable or "python3"


# ── Definición de steps ───────────────────────────────────────────────────────


def _guide_bootstrap(vars_: dict, dest: Path) -> str:
    return f"""\
# S6 — Bootstrap AWS del cliente (una vez, con TUS creds admin, DESDE EL CLON)
cd {dest}
python3 infra/scripts/aws_bootstrap.py state          # bucket {vars_["prefix"]}-tfstate + lock
ssh-keygen -t ed25519 -f ~/.ssh/{vars_["slug"]}_ops -C "{vars_["slug"]}-ops"
#   → pública a infra/terraform/compute/tenants.auto.tfvars, privada al secret EC2_SSH_KEY
python3 infra/scripts/aws_bootstrap.py github --repo {vars_["repo"]}
#   + crear el environment `production` en GitHub con required reviewers"""


def _guide_platform(vars_: dict, dest: Path) -> str:
    return f"""\
# S7 — Platform + secretos (DESDE EL CLON; state propio {vars_["prefix"]}-tfstate)
cd {dest}/infra/terraform/platform
terraform init -backend-config=envs/real.s3.tfbackend && terraform apply
#   (project.auto.tfvars ya trae create_github_oidc_provider=false)
# Secretos reales → SSM {vars_["ssm_prefix"]}/{vars_["slug"]}/* :
cd {dest}
python3 infra/scripts/aws_bootstrap.py secrets --tenant {vars_["slug"]} --file secrets.{vars_["slug"]}.env
# + los bloques `aws ssm put-parameter` que imprimieron los steps S4 (Medusa) y S5 (Temporal)"""


def _guide_compute(vars_: dict, dest: Path) -> str:
    return f"""\
# S8 — Compute + primer deploy + schedules (DESDE EL CLON)
cd {dest}/infra/terraform/compute
terraform init -backend-config=envs/real.s3.tfbackend && terraform apply   # caja + EIP
#   → poner domain "<ip-con-guiones>.sslip.io" en tenants.auto.tfvars + api_url en platform → re-apply
cd {dest} && git push origin main       # dispara backend-deploy + frontend-deploy del CLON
# Después: webhook Meta + seed de catálogo + schedules — checklist completo en {dest}/NEXT_STEPS.md (F7/F8)"""


STEPS: list[dict] = [
    {"id": "clone", "title": "S1 Forjar el repo del cliente", "kind": "auto"},
    {"id": "supabase", "title": "S2 Postgres (proyecto Supabase nuevo)", "kind": "auto"},
    {"id": "medusa", "title": "S3 Medusa en Railway (desde la URL del repo)", "kind": "auto"},
    {"id": "medusa-seed", "title": "S4 Seed de Medusa (región/canal/key)", "kind": "auto"},
    {"id": "temporal", "title": "S5 Temporal Cloud (namespace + API key)", "kind": "guided"},
    {"id": "aws-bootstrap", "title": "S6 Bootstrap AWS (state/keys/GH)", "kind": "guided", "guide": _guide_bootstrap},
    {"id": "platform", "title": "S7 Platform + secretos SSM", "kind": "guided", "guide": _guide_platform},
    {"id": "compute", "title": "S8 Compute + deploy + schedules", "kind": "guided", "guide": _guide_compute},
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


def auto_done(step: str, bundle: Path, dest: Path | None) -> bool:
    """Evidencia en disco de que un step auto ya corrió (además del estado)."""
    if step == "clone":
        return dest is not None and (dest / ".git").exists()
    if step == "supabase":
        return (bundle / ".outputs.supabase.json").exists()
    if step == "medusa":
        return (bundle / ".outputs.medusa.json").exists()
    return False


# ── Ejecución ─────────────────────────────────────────────────────────────────


def guard_dest(dest: Path) -> None:
    if dest.resolve() == forge.REPO.resolve() or dest.resolve().is_relative_to(forge.REPO.resolve()):
        raise forge.ForgeError(
            f"dest {dest} es (o está dentro de) el repo madre hubara — el clon vive afuera"
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
    return code


def cmd_status(slug: str, bundle: Path, dest: Path | None) -> None:
    vars_ = forge.render_vars(forge.load_client(bundle))
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
    p = sub.add_parser("run"); p.add_argument("slug"); p.add_argument("step")
    p.add_argument("--dest"); p.add_argument("--allow-todos", action="store_true")
    p = sub.add_parser("done"); p.add_argument("slug"); p.add_argument("step")
    a = ap.parse_args(argv)
    bundle = forge.CLIENTS / a.slug
    try:
        if a.cmd == "status":
            cmd_status(a.slug, bundle, Path(a.dest) if a.dest else None)
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
