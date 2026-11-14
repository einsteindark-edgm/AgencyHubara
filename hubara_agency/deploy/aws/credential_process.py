"""`credential_process` de botocore para los contenedores que usan AWS.

Con hop limit 1 los contenedores no llegan a las credenciales de la caja. El
timer `app-aws-creds` del host deja credenciales temporales del rol
`agencyhubara-<tenant>-app-containers` en `/run/app-aws/creds.json` (montado
de solo lectura) y este script se las pasa a botocore en el formato de
`credential_process` (con `Expiration`, botocore lo vuelve a llamar antes de que
venzan y relee el archivo nuevo).

Durante la transición (código desplegado, Terraform todavía sin aplicar) el
archivo no existe y el hop limit sigue en 2: se cae a las credenciales de la
caja por IMDSv2, como hasta ahora. Con hop limit 1 ese camino ya no responde.

Solo biblioteca estándar: corre con el python de la imagen, sin `src`.
Guarda: hubara_agency/tests/infra/test_container_aws_credentials.py.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_FILE = "/run/app-aws/creds.json"
#: Margen para no entregar una credencial que vence mientras se usa.
_MIN_LEFT = timedelta(minutes=2)
_IMDS = "http://169.254.169.254/latest"


def _still_valid(creds: dict) -> bool:
    try:
        expires = datetime.fromisoformat(creds["Expiration"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return False
    return expires - datetime.now(timezone.utc) > _MIN_LEFT


def _from_file(path: Path) -> dict | None:
    try:
        creds = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    keys = ("AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration")
    if not all(creds.get(k) for k in keys) or not _still_valid(creds):
        return None
    return {"Version": 1, **{k: creds[k] for k in keys}}


def _from_imds() -> dict | None:
    try:
        token_req = urllib.request.Request(
            f"{_IMDS}/api/token", method="PUT", headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"}
        )
        token = urllib.request.urlopen(token_req, timeout=1).read().decode()
        headers = {"X-aws-ec2-metadata-token": token}
        base = f"{_IMDS}/meta-data/iam/security-credentials/"
        role = urllib.request.urlopen(urllib.request.Request(base, headers=headers), timeout=1).read().decode().strip()
        raw = json.loads(urllib.request.urlopen(urllib.request.Request(base + role, headers=headers), timeout=1).read())
    except (OSError, ValueError):
        return None
    return {
        "Version": 1,
        "AccessKeyId": raw["AccessKeyId"],
        "SecretAccessKey": raw["SecretAccessKey"],
        "SessionToken": raw["Token"],
        "Expiration": raw["Expiration"],
    }


def main(creds_file: Path | None = None, imds=_from_imds) -> int:
    path = Path(creds_file or os.environ.get("HUBARA_AWS_CREDS_FILE", DEFAULT_FILE))
    creds = _from_file(path) or imds()
    if not creds:
        print(
            f"sin credenciales de AWS: {path} no existe o venció, y el contenedor no llega a las de la caja. "
            "Mirá el timer `app-aws-creds` en el host (systemctl status app-aws-creds.service).",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(creds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
