#!/usr/bin/env python3
"""Reparte `/hubara/<tenant>/*` de SSM en un `.env` compartido y un archivo de secretos por servicio.

Lo llama `render-env-from-ssm.sh` EN la caja (python3 del sistema: 3.9 en AL2023,
solo biblioteca estándar). Revisión de seguridad del 2026-10-26: antes los 14
contenedores cargaban TODO en un solo `.env`.

Salidas (en --out-dir, modo 0600):
- `.env`                 config no secreta (parámetros String + el bloque fijo).
- `secrets/<servicio>.env`  los SecureString de ese servicio según `service-secrets.json`.
- `.env.host`            lo que solo usa la caja (`_host`: el token de GHCR del deploy).
- `litellm.env`          el entorno SOLO del proxy LiteLLM (memoria de la caja de app,
                         2026-11-05): las llaves que nombra su config (`os.environ/X`)
                         con su valor de SSM y la huella del config. Compose lo recrea
                         solo si cambia eso o su imagen, no con cada deploy.

Reglas:
- Cada variable conserva el valor que tenía en el `.env` de un solo archivo: gana la
  ÚLTIMA aparición en el orden de SSM y, encima, el bloque fijo. Así un nombre
  repetido (en prod: `/WATCHDOG_ENABLED` SecureString=true y
  `/scheduler/WATCHDOG_ENABLED` String=false, hoy gana false) no cambia de valor.
- Un SecureString sin dueño en el mapa va a todos los servicios de la app (nunca a
  LiteLLM) y avisa: no se rompe nada, pero el mapa quedó viejo. Lo que solo usa
  LiteLLM (lo nombra su config o el mapa lo anota en `_litellm`) no va a ninguno.
- Sin los parámetros de auth (o con el relleno de Terraform) no se escribe nada y
  sale con error: el deploy aborta y el contenedor viejo sigue vivo (SEC-01/SEC-02).

Guardas: hubara_agency/tests/infra/test_service_secrets.py y test_prod_litellm_env.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

PLACEHOLDER = "PLACEHOLDER_set_out_of_band"
REQUIRED_AUTH = ("COGNITO_USER_POOL_ID", "COGNITO_APP_CLIENT_ID", "WHATSAPP_APP_SECRET", "HUBARA_SERVICE_TOKEN")
_LITELLM_KEY = re.compile(r"os\.environ/([A-Za-z0-9_]+)")


def _from_ssm(params: list[dict]) -> OrderedDict:
    """Nombre → (tipo, valor) de SSM: la última aparición gana y queda al final."""
    eff: OrderedDict = OrderedDict()
    for p in params:
        name = p["Name"].rsplit("/", 1)[-1]
        eff.pop(name, None)
        eff[name] = (p["Type"], p["Value"])
    return eff


def _effective(ssm: OrderedDict, static_lines: list[str]) -> OrderedDict:
    """Lo de SSM sin lo que pisa el bloque fijo (que va al final del `.env`)."""
    eff = OrderedDict(ssm)
    for line in static_lines:
        if "=" in line and not line.lstrip().startswith("#"):
            eff.pop(line.split("=", 1)[0], None)  # el bloque fijo gana sobre SSM
    return eff


def _litellm_env(config: Path, ssm: OrderedDict) -> tuple[set[str], list[str]]:
    """Las llaves que nombra la config de LiteLLM y su `litellm.env`.

    Sin la huella, cambiar solo el contenido del config (un bind-mount) no recrea el
    contenedor y LiteLLM seguiría con el config viejo en memoria (caso: alias
    gemini-multimodal).
    """
    if not config.is_file():
        print(f"AVISO: falta {config} (lo copia backend-deploy.yml)", file=sys.stderr)
        return set(), []
    raw = config.read_bytes()
    keys = set(_LITELLM_KEY.findall(raw.decode("utf-8", "replace")))
    lines = []
    for key in sorted(keys):
        value = ssm.get(key, ("", ""))[1]
        if value:
            lines.append(f"{key}={value}")
        else:
            print(f"AVISO: {key} lo nombra {config.name} pero no está en /hubara/<tenant>/", file=sys.stderr)
    lines.append(f"LITELLM_CONFIG_SHA256={hashlib.sha256(raw).hexdigest()}")
    return keys, lines


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".")
    with os.fdopen(fd, "w") as fh:
        fh.write("".join(line + "\n" for line in lines))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--params", required=True, help="salida JSON de aws ssm get-parameters-by-path")
    ap.add_argument("--map", required=True, help="service-secrets.json")
    ap.add_argument("--static", required=True, help="bloque fijo (KEY=VALUE), va al final del .env")
    ap.add_argument("--litellm-config", required=True, help="litellm_config.yaml que monta el proxy")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)

    params = json.loads(Path(args.params).read_text())["Parameters"]
    owners_map = json.loads(Path(args.map).read_text())
    static_lines = Path(args.static).read_text().splitlines()
    ssm = _from_ssm(params)
    eff = _effective(ssm, static_lines)

    missing = [k for k in REQUIRED_AUTH if not ssm.get(k, ("", ""))[1] or ssm[k][1] == PLACEHOLDER]
    if missing:
        for key in missing:
            print(f"ABORT deploy: /hubara/<tenant>/{key} falta o es placeholder en SSM.", file=sys.stderr)
        print(
            "  HUBARA_ENV=production exige los 4 params de auth provisionados:\n"
            "  COGNITO_* (via 'terraform apply' de platform) + WHATSAPP_APP_SECRET\n"
            "  y HUBARA_SERVICE_TOKEN (via 'aws ssm put-parameter --overwrite').\n"
            "  Detalle y orden: infra/DEPLOY_RUNBOOK.md FASE 2.1.",
            file=sys.stderr,
        )
        return 1

    services = [s for s in owners_map if not s.startswith("_")]
    host_only = set(owners_map.get("_host", []))
    nowhere = set(owners_map.get("_none", []))
    litellm_keys, litellm = _litellm_env(Path(args.litellm_config), ssm)
    litellm_only = litellm_keys | set(owners_map.get("_litellm", []))

    shared = [f"{n}={v}" for n, (t, v) in eff.items() if t != "SecureString"] + static_lines
    per_service: dict[str, list[str]] = {s: [] for s in services}
    host: list[str] = []
    for name, (kind, value) in eff.items():
        if kind != "SecureString" or name in nowhere:
            continue
        line = f"{name}={value}"
        if name in host_only:
            host.append(line)
            continue
        owners = [s for s in services if name in owners_map[s]]
        if not owners and name in litellm_only:
            continue  # solo la usa LiteLLM: va a litellm.env, a ningún servicio de la app
        if not owners:
            owners = services
            print(
                f"AVISO: el secreto {name} no tiene dueño en service-secrets.json; va a todos los servicios "
                "de la app. Regenerá el mapa: cd hubara_agency && uv run python scripts/service_secrets.py --write",
                file=sys.stderr,
            )
        for svc in owners:
            per_service[svc].append(line)

    out = Path(args.out_dir)
    for svc, lines in per_service.items():
        _write(out / "secrets" / f"{svc}.env", lines)
    _write(out / ".env.host", host)
    _write(out / "litellm.env", litellm)
    _write(out / ".env", shared)
    print(
        f"Escrito {out / '.env'} ({sum(1 for line in shared if '=' in line and not line.startswith('#'))} vars), "
        f"{len(per_service)} archivos de secretos por servicio y {out / 'litellm.env'} ({len(litellm)} vars)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
