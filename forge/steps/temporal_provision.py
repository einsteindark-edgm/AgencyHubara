#!/usr/bin/env python3
"""Step S6 — Temporal Cloud del cliente: namespace + service account + API key.

Temporal Cloud SÍ tiene namespaces (a diferencia de Supabase): la cuenta se
comparte (decisión D-2 del plan — un solo piso de facturación) y cada cliente
recibe su namespace + un service account con API key scoped. Aislamiento
operacional real; hubara vive en OTRO namespace y este step no puede tocarlo.

AISLAMIENTO (crítico): envuelve `tcld` (el CLI oficial) si está instalado, o
imprime los comandos exactos. Cero AWS/SSM — los `aws ssm put-parameter` se
IMPRIMEN con el prefijo del clon.

  export TEMPORAL_CLOUD_API_KEY=...   # key de cuenta (Settings → API Keys)
  python3 forge/steps/temporal_provision.py plan  <slug>
  python3 forge/steps/temporal_provision.py apply <slug>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

STEPS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(STEPS_DIR.parent))

import forge  # noqa: E402

REGION = "aws-us-east-1"
RETENTION_DAYS = "30"


def build_commands(slug: str) -> list[list[str]]:
    """Los comandos tcld, en orden. Función pura → testeable sin tcld."""
    if slug == "hubara":  # defensa en profundidad; render_vars ya lo rechaza
        raise forge.ForgeError("namespace 'hubara' es del proyecto productivo — prohibido")
    sa_name = f"{slug}-backend"
    return [
        ["tcld", "namespace", "create", "--namespace", slug, "--region", REGION,
         "--retention-days", RETENTION_DAYS],
        ["tcld", "service-account", "create", "--name", sa_name,
         "--namespace-permission", f"{slug}=Write", "--account-role", "none"],
        # el id del SA se resuelve tras crearlo; tcld acepta --service-account-id
        ["tcld", "apikey", "create", "--name", f"{sa_name}-key", "--duration", "8760h",
         "--service-account-id", f"<id de {sa_name}>"],
    ]


def print_ssm_block(vars_: dict) -> None:
    prefix = f"{vars_['ssm_prefix']}/{vars_['slug']}"
    assert not prefix.startswith("/hubara"), "guard: jamás imprimir paths de hubara"
    print("\n  Con el namespace y la key creados, correr a mano (este CLI NO toca AWS):")
    print(f"  aws ssm put-parameter --name {prefix}/TEMPORAL_ADDRESS --type SecureString --overwrite --value 'us-east-1.aws.api.temporal.io:7233'")
    print(f"  aws ssm put-parameter --name {prefix}/TEMPORAL_NAMESPACE --type SecureString --overwrite --value '{vars_['slug']}.<account-id>'")
    print(f"  aws ssm put-parameter --name {prefix}/TEMPORAL_API_KEY --type SecureString --overwrite --value '<la api key>'")
    print("  # fuente de verdad de los NOMBRES exactos: los SSM vivos del proyecto madre")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2 or args[0] not in {"plan", "apply"}:
        print(__doc__)
        return 2
    cmd, slug = args
    try:
        bundle = forge.CLIENTS / slug
        vars_ = forge.render_vars(forge.load_client(bundle))  # guards
        commands = build_commands(vars_["slug"])
        record = bundle / ".outputs.temporal.json"
        ours = json.loads(record.read_text()).get("namespace") if record.exists() else None
        if cmd == "plan" or not shutil.which("tcld"):
            if cmd == "apply":
                print("tcld no está instalado (brew install temporalio/brew/tcld) — comandos a correr:")
            for c in commands:
                print("  " + " ".join(c))
            print("  # login previo: tcld login  (o TEMPORAL_CLOUD_API_KEY en el env)")
            print("  # ⚠ sintaxis NO verificada contra tcld vivo — confirmar flags con")
            print("  #   `tcld namespace create --help` o usar la consola web (cloud.temporal.io)")
        else:
            for i, c in enumerate(commands[:2]):
                print("$ " + " ".join(c))
                r = subprocess.run(c, capture_output=True, text=True)
                print(r.stdout.strip() or r.stderr.strip())
                if r.returncode == 0:
                    if i == 0:  # el namespace es de esta tienda: un reintento lo reconoce
                        record.write_text(json.dumps({"namespace": vars_["slug"]}))
                        ours = vars_["slug"]
                    continue
                if "already exists" not in (r.stdout + r.stderr):
                    raise forge.ForgeError(f"tcld falló: {' '.join(c)}")
                # La cuenta de Temporal Cloud es compartida: un namespace con ese
                # nombre que este bundle no creó es de OTRO proyecto, y el service
                # account nuevo recibiría escritura sobre él.
                if ours != vars_["slug"]:
                    raise forge.ForgeError(
                        f"el namespace {vars_['slug']!r} ya existe en Temporal Cloud y este bundle no lo creó "
                        "(es de otro proyecto de la cuenta): elegí otro slug o revisalo a mano"
                    )
            print("→ la API key requiere el id del service account (tcld service-account list):")
            print("  " + " ".join(commands[2]))
        print_ssm_block(vars_)
    except forge.ForgeError as e:
        print(f"temporal_provision: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
