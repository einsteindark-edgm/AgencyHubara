#!/usr/bin/env python3
"""
aws_bootstrap.py — automatiza el bootstrap-once de AWS para el IaC de AgencyHubara.

Hace lo que harías a mano con el AWS CLI, pero idempotente y en un comando. Cada
paso IMPRIME el comando `aws`/`gh` que corre (transparencia + te enseña el CLI).
Cero dependencias de pip: solo stdlib + los binarios `aws` y `gh` que ya tenés.

Sirve contra AWS REAL o contra robotocore (réplica local): pasá
`--endpoint-url http://localhost:4566` y usa credenciales dummy automáticamente.

Subcomandos
-----------
  state    Crea el bucket S3 + tabla DynamoDB del state de Terraform (huevo-gallina).
  template Regenera secrets.example.env desde `secret_keys` de Terraform (la lista
           de llaves de un tenant; `--check` solo compara).
  secrets  Sube los SECRETOS de un archivo .env a SSM (/hubara/<tenant>/<KEY>, SecureString).
           Rechaza llaves que Terraform no declara y no sube las vacías.
  github   Setea las GH variables (role ARNs, region, state) + el secret EC2_SSH_KEY.
  verify   Chequea que el bootstrap esté completo. Con `--tenant <t>`, además lista
           las llaves de `secret_keys` que faltan o siguen en placeholder (sin leer
           ningún valor: el filtro corre dentro del CLI de AWS).

Secretos de un tenant nuevo
---------------------------
  Terraform crea los NOMBRES (`secret_keys`, con placeholder); los VALORES se
  cargan aquí, nunca en git ni en el state:
    cp secrets.example.env secrets.<tenant>.env        # git lo ignora; llena los valores
    python3 aws_bootstrap.py secrets --tenant <tenant> --file secrets.<tenant>.env
    python3 aws_bootstrap.py verify  --tenant <tenant> # lo que falta antes del deploy

Ejemplos
--------
  # 1) State (real):
  python3 aws_bootstrap.py state --bucket agencyhubara-tfstate --table agencyhubara-tflock

  # 2) Secretos de un tenant (real). El archivo es KEY=VALUE, NUNCA se commitea:
  python3 aws_bootstrap.py secrets --tenant hubara --file secrets.hubara.env

  # 3) GH variables + SSH secret (lee outputs de Terraform ya aplicado):
  python3 aws_bootstrap.py github --repo einsteindark-edgm/AgencyHubara \\
      --platform-dir ../terraform/platform --ssh-key-file ~/.ssh/hubara_ops

  # Probar TODO contra robotocore (sin tocar AWS real):
  python3 aws_bootstrap.py state   --endpoint-url http://localhost:4566
  python3 aws_bootstrap.py secrets --tenant hubara --file secrets.prueba.env \\
      --endpoint-url http://localhost:4566     # una copia de la plantilla con valores falsos
"""
import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

REGION_DEFAULT = "us-east-1"
_HERE = Path(__file__).resolve().parent
#: Fuente de los NOMBRES de los secretos de un tenant: `variable "secret_keys"`.
VARIABLES_TF = _HERE.parent / "terraform" / "platform" / "variables.tf"
#: La plantilla que se copia para un tenant nuevo (generada, no se edita a mano).
TEMPLATE = _HERE / "secrets.example.env"
#: El valor con que Terraform crea cada secreto (modules/secrets): el real se
#: carga fuera de banda y `ignore_changes` evita que un apply lo pise.
PLACEHOLDER = "PLACEHOLDER_set_out_of_band"


# ── secret_keys: la lista de Terraform ──────────────────────────────────────
def secret_keys(variables_tf=VARIABLES_TF):
    """[(LLAVE, explicación)] de `variable "secret_keys"`, en el orden de
    Terraform. La explicación son los comentarios `#` escritos justo encima de
    la llave ("" si no tiene)."""
    text = Path(variables_tf).read_text(encoding="utf-8")
    start = text.index('variable "secret_keys"')
    block = text[start:text.index("\n}\n", start)]
    keys, notes = [], []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            notes.append(stripped.lstrip("#").strip())
            continue
        found = re.fullmatch(r'"([A-Z0-9_]+)",?', stripped)
        if found:
            keys.append((found.group(1), " ".join(notes)))
        notes = []
    return keys


def render_template(keys):
    """La plantilla de secretos de un tenant: cada llave vacía, con su
    explicación de Terraform encima. Nunca lleva un valor."""
    lines = [
        "# Secretos de UN tenant: /hubara/<tenant>/<LLAVE> en SSM (SecureString).",
        "#",
        "# GENERADO desde `secret_keys` (infra/terraform/platform/variables.tf) con",
        "#   python3 infra/scripts/aws_bootstrap.py template",
        "# No se edita a mano: una llave nueva va primero a Terraform (y al apply).",
        "#",
        "# Uso: copia este archivo a secrets.<tenant>.env (git lo ignora), llena los",
        "# valores y súbelos con",
        "#   python3 infra/scripts/aws_bootstrap.py secrets --tenant <tenant> --file secrets.<tenant>.env",
        "# Lo que quede vacío no se sube. Antes del deploy, lo que falta:",
        "#   python3 infra/scripts/aws_bootstrap.py verify --tenant <tenant>",
        "",
    ]
    for key, note in keys:
        lines += ["# " + part for part in textwrap.wrap(note, 76, break_on_hyphens=False, break_long_words=False)]
        lines += [f"{key}=", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def plan_upload(pairs, known):
    """(a subir, vacías, desconocidas). Una llave que Terraform no declara no se
    crea a mano (quedaría fuera del state y el próximo tenant no la tendría);
    una vacía no pisa lo que ya haya en SSM."""
    upload = [(k, v) for k, v in pairs if k in known and v]
    empty = [k for k, v in pairs if k in known and not v]
    unknown = [k for k, _ in pairs if k not in known]
    return upload, empty, unknown


def pending(expected, *, present, placeholders):
    """(faltan, en placeholder) en el orden de `expected`. Faltan = no hubo
    apply de platform para el tenant; placeholder = nadie cargó el valor."""
    missing = [k for k in expected if k not in present]
    placeholder = [k for k in expected if k in present and k in placeholders]
    return missing, placeholder


def run(cmd, *, env=None, capture=False, mask=None):
    """Corre un comando, lo imprime (enmascarando `mask` si se pasa), y falla ruidoso."""
    shown = " ".join(cmd)
    if mask:
        shown = shown.replace(mask, "********")
    print(f"  $ {shown}", file=sys.stderr)
    res = subprocess.run(cmd, env=env, capture_output=capture, text=True)
    if res.returncode != 0:
        if capture:
            print(res.stderr, file=sys.stderr)
        sys.exit(f"✗ falló (exit {res.returncode}): {shown}")
    return res.stdout if capture else ""


def aws_env(endpoint):
    """Env para el subproceso aws. Contra robotocore inyecta creds dummy."""
    env = dict(os.environ)
    if endpoint:
        env.setdefault("AWS_ACCESS_KEY_ID", "test")
        env.setdefault("AWS_SECRET_ACCESS_KEY", "test")
    return env


def aws_base(region, endpoint):
    cmd = ["aws", "--region", region]
    if endpoint:
        cmd += ["--endpoint-url", endpoint]
    return cmd


def exists(cmd, env):
    """True si el comando de chequeo sale 0 (recurso ya existe)."""
    return subprocess.run(cmd, env=env, capture_output=True, text=True).returncode == 0


# ── state ───────────────────────────────────────────────────────────────────
def cmd_state(a):
    env = aws_env(a.endpoint_url)
    base = aws_base(a.region, a.endpoint_url)

    print(f"▶ State store: bucket={a.bucket} table={a.table} region={a.region}", file=sys.stderr)

    if exists(base + ["s3api", "head-bucket", "--bucket", a.bucket], env):
        print("  • bucket ya existe — skip", file=sys.stderr)
    else:
        create = base + ["s3api", "create-bucket", "--bucket", a.bucket]
        # us-east-1 NO acepta LocationConstraint; las demás regiones SÍ.
        if a.region != "us-east-1":
            create += ["--create-bucket-configuration", f"LocationConstraint={a.region}"]
        run(create, env=env)
    # Versioning (recuperar state si se corrompe) + cifrado + bloquear público.
    run(base + ["s3api", "put-bucket-versioning", "--bucket", a.bucket,
                "--versioning-configuration", "Status=Enabled"], env=env)
    run(base + ["s3api", "put-bucket-encryption", "--bucket", a.bucket,
                "--server-side-encryption-configuration",
                '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'], env=env)
    run(base + ["s3api", "put-public-access-block", "--bucket", a.bucket,
                "--public-access-block-configuration",
                "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"], env=env)

    if exists(base + ["dynamodb", "describe-table", "--table-name", a.table], env):
        print("  • tabla DynamoDB ya existe — skip", file=sys.stderr)
    else:
        run(base + ["dynamodb", "create-table", "--table-name", a.table,
                    "--attribute-definitions", "AttributeName=LockID,AttributeType=S",
                    "--key-schema", "AttributeName=LockID,KeyType=HASH",
                    "--billing-mode", "PAY_PER_REQUEST"], env=env)
    print("✓ state listo. Usalo en `terraform init -backend-config=...` (bucket+table).", file=sys.stderr)


# ── secrets ─────────────────────────────────────────────────────────────────
#: SSM Standard (el tier del módulo `secrets`) no guarda más de 4 KB por parámetro.
SSM_MAX_CHARS = 4096


def _file_value(key, ref, base_dir):
    """`KEY=@ruta`: el contenido del archivo (relativa al .env). Un JSON va en UNA línea: el deploy arma el `.env`
    del contenedor con una línea por clave (`render-env-from-ssm.sh`) y compose no acepta `$` sin escapar."""
    path = os.path.join(base_dir, os.path.expanduser(ref))
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        sys.exit(f"✗ {key}: no se pudo leer {path} ({exc.strerror}). No se subió nada.")
    try:
        value = json.dumps(json.loads(raw), ensure_ascii=False, separators=(",", ":"))
    except ValueError:
        value = raw.strip()
    problem = ("tiene varias líneas (solo un JSON se junta en una)" if "\n" in value or "\t" in value
               else "tiene un «$» (docker compose lo leería como variable)" if "$" in value
               else f"pasa de {SSM_MAX_CHARS} caracteres (el límite de SSM Standard)" if len(value) > SSM_MAX_CHARS
               else None)
    if problem:
        sys.exit(f"✗ {key}: {path} {problem}. No se subió nada.")
    return value


def parse_env_file(path):
    """Lee KEY=VALUE (ignora blancos y #comentarios). Devuelve lista [(k,v)].
    `KEY=@ruta/archivo.json` sube el contenido del archivo (ver `_file_value`)."""
    out = []
    base_dir = os.path.dirname(os.path.abspath(path))
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k, v = k.strip(), v.strip()
            out.append((k, _file_value(k, v[1:], base_dir) if v.startswith("@") else v))
    return out


def cmd_secrets(a):
    env = aws_env(a.endpoint_url)
    base = aws_base(a.region, a.endpoint_url)
    prefix = f"{a.prefix}/{a.tenant}"
    pairs = parse_env_file(a.file)
    if not pairs:
        sys.exit(f"✗ {a.file} no tiene pares KEY=VALUE")
    upload, empty, unknown = plan_upload(pairs, {k for k, _ in secret_keys(a.variables)})
    if unknown:
        sys.exit("✗ Terraform no conoce: " + ", ".join(unknown) + ". Una llave nueva va primero a "
                 "`secret_keys` (infra/terraform/platform/variables.tf) y al apply; nunca se crea a mano. "
                 "No se subió nada.")
    if empty:
        print(f"  • sin valor, no se suben ({len(empty)}): {', '.join(empty)}", file=sys.stderr)

    print(f"▶ Subiendo {len(upload)} parámetros a SSM bajo {prefix}/ (type {a.type})", file=sys.stderr)
    for k, v in upload:
        name = f"{prefix}/{k}"
        run(base + ["ssm", "put-parameter", "--overwrite", "--name", name,
                    "--type", a.type, "--value", v], env=env, mask=v)
    print(f"✓ {len(upload)} secretos en {prefix}/. Lo que falta: "
          f"python3 aws_bootstrap.py verify --tenant {a.tenant}", file=sys.stderr)


# ── github ──────────────────────────────────────────────────────────────────
def tf_output(platform_dir):
    out = subprocess.run(["terraform", f"-chdir={platform_dir}", "output", "-json"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("✗ no pude leer `terraform output` — ¿aplicaste platform? "
                 + out.stderr.strip())
    return json.loads(out.stdout)


def gh_var(repo, key, value):
    run(["gh", "variable", "set", key, "--repo", repo, "--body", value])


def cmd_github(a):
    o = tf_output(a.platform_dir)

    def val(k):
        return o[k]["value"]

    print(f"▶ Seteando GH variables en {a.repo}", file=sys.stderr)
    gh_var(a.repo, "AWS_REGION", a.region)
    gh_var(a.repo, "AWS_TERRAFORM_ROLE_ARN", val("github_terraform_role_arn"))
    # SEC-03: el rol READ-ONLY que asumen terraform-plan + los deploys
    # (backend/frontend/observability solo hacen `terraform output`). Los 4
    # workflows lo referencian como `vars.AWS_TF_READONLY_ROLE_ARN`; sin setearlo,
    # el paso configure-aws-credentials falla al asumir rol.
    gh_var(a.repo, "AWS_TF_READONLY_ROLE_ARN", val("github_terraform_readonly_role_arn"))
    gh_var(a.repo, "AWS_DEPLOY_ROLE_ARN", val("github_deploy_role_arn"))
    gh_var(a.repo, "TF_STATE_BUCKET", a.bucket)
    gh_var(a.repo, "TF_STATE_LOCK_TABLE", a.table)

    if a.ssh_key_file:
        print(f"▶ Seteando GH secret EC2_SSH_KEY desde {a.ssh_key_file}", file=sys.stderr)
        with open(a.ssh_key_file) as f:
            key_material = f.read()
        subprocess.run(["gh", "secret", "set", "EC2_SSH_KEY", "--repo", a.repo],
                       input=key_material, text=True, check=True)
        print("  $ gh secret set EC2_SSH_KEY --repo " + a.repo + " < " + a.ssh_key_file, file=sys.stderr)
    print("✓ GH variables/secret listos. Los workflows ya pueden asumir el rol OIDC.", file=sys.stderr)


# ── verify ──────────────────────────────────────────────────────────────────
def cmd_verify(a):
    env = aws_env(a.endpoint_url)
    base = aws_base(a.region, a.endpoint_url)
    ok = True

    def check(label, cmd):
        nonlocal ok
        good = exists(cmd, env)
        print(f"  {'✓' if good else '✗'} {label}", file=sys.stderr)
        ok = ok and good

    print("▶ Verificando bootstrap", file=sys.stderr)
    check(f"bucket {a.bucket}", base + ["s3api", "head-bucket", "--bucket", a.bucket])
    check(f"tabla {a.table}", base + ["dynamodb", "describe-table", "--table-name", a.table])
    check("SSM /hubara/hubara/scheduler/ORDER_RECONCILE_INTERVAL_MINUTES",
          base + ["ssm", "get-parameter", "--name",
                  "/hubara/hubara/scheduler/ORDER_RECONCILE_INTERVAL_MINUTES"])
    keys = [k for k, _ in secret_keys(a.variables)]
    for tenant in a.tenant:
        ok = check_tenant_secrets(base, env, f"{a.prefix}/{tenant}", keys) and ok
    sys.exit(0 if ok else "✗ bootstrap incompleto")


def _parameter_names(base, env, prefix, query, *extra):
    """Nombres (la última parte del path) que devuelve `query` sobre
    `prefix`. Con `--with-decryption` el filtro corre DENTRO del CLI de AWS:
    al script solo le llegan nombres, nunca un valor."""
    res = subprocess.run(base + ["ssm", "get-parameters-by-path", "--path", prefix, *extra,
                                 "--query", query, "--output", "json"],
                         env=env, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"exit {res.returncode}")
    return {name.rsplit("/", 1)[-1] for name in json.loads(res.stdout or "[]") or []}


def check_tenant_secrets(base, env, prefix, keys):
    """Las llaves de `secret_keys` que le faltan al tenant o siguen en
    placeholder. True si no falta ninguna."""
    try:
        present = _parameter_names(base, env, prefix, "Parameters[].Name")
        placeholders = _parameter_names(base, env, prefix, f"Parameters[?Value=='{PLACEHOLDER}'].Name",
                                        "--with-decryption")
    except (RuntimeError, ValueError) as exc:
        print(f"  ✗ {prefix}: no pude leer SSM ({exc})", file=sys.stderr)
        return False
    missing, placeholder = pending(keys, present=present, placeholders=placeholders)
    if not missing and not placeholder:
        print(f"  ✓ {prefix}: las {len(keys)} llaves tienen valor", file=sys.stderr)
        return True
    if missing:
        verb = "falta" if len(missing) == 1 else "faltan"
        print(f"  ✗ {prefix}: {verb} {len(missing)} (no hubo apply de platform con ellas): "
              + ", ".join(missing), file=sys.stderr)
    if placeholder:
        verb = "sigue" if len(placeholder) == 1 else "siguen"
        print(f"  ✗ {prefix}: {len(placeholder)} {verb} en placeholder (falta cargar el valor): "
              + ", ".join(placeholder), file=sys.stderr)
        print("    Si una función no se usa en este sitio, su llave puede quedar así a propósito; "
              "la lista es para que ninguna se olvide.", file=sys.stderr)
    return False


# ── template ────────────────────────────────────────────────────────────────
def cmd_template(a):
    keys = secret_keys(a.variables)
    text = render_template(keys)
    out = Path(a.out)
    if a.check:
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            sys.exit(f"✗ {out} está desfasada de secret_keys: corre `python3 aws_bootstrap.py template`")
        print(f"✓ {out} al día ({len(keys)} llaves)", file=sys.stderr)
        return
    out.write_text(text, encoding="utf-8")
    print(f"✓ {out} regenerada desde secret_keys ({len(keys)} llaves)", file=sys.stderr)


def main():
    # Flags comunes vía parent parser → válidas DESPUÉS del subcomando
    # (ej. `... state --endpoint-url X`, que es lo natural).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--region", default=REGION_DEFAULT)
    common.add_argument("--endpoint-url", default=os.environ.get("AWS_ENDPOINT_URL", ""),
                        help="http://localhost:4566 para robotocore (usa creds dummy)")

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("state", parents=[common], help="crear bucket S3 + tabla DynamoDB del state")
    s.add_argument("--bucket", default="agencyhubara-tfstate")
    s.add_argument("--table", default="agencyhubara-tflock")
    s.set_defaults(func=cmd_state)

    se = sub.add_parser("secrets", parents=[common], help="subir secretos de un archivo a SSM")
    se.add_argument("--tenant", required=True)
    se.add_argument("--file", required=True, help="archivo KEY=VALUE (NO commitear)")
    se.add_argument("--prefix", default="/hubara")
    se.add_argument("--type", default="SecureString", choices=["SecureString", "String"])
    se.add_argument("--variables", default=str(VARIABLES_TF), help="variables.tf con `secret_keys`")
    se.set_defaults(func=cmd_secrets)

    t = sub.add_parser("template", help="regenerar secrets.example.env desde secret_keys")
    t.add_argument("--variables", default=str(VARIABLES_TF), help="variables.tf con `secret_keys`")
    t.add_argument("--out", default=str(TEMPLATE))
    t.add_argument("--check", action="store_true", help="solo comprobar que la plantilla esté al día")
    t.set_defaults(func=cmd_template)

    g = sub.add_parser("github", parents=[common], help="setear GH variables + secret EC2_SSH_KEY")
    g.add_argument("--repo", required=True, help="owner/repo")
    g.add_argument("--platform-dir", default="../terraform/platform")
    g.add_argument("--bucket", default="agencyhubara-tfstate")
    g.add_argument("--table", default="agencyhubara-tflock")
    g.add_argument("--ssh-key-file", default="", help="clave privada para el GH secret EC2_SSH_KEY")
    g.set_defaults(func=cmd_github)

    v = sub.add_parser("verify", parents=[common], help="chequear el bootstrap")
    v.add_argument("--bucket", default="agencyhubara-tfstate")
    v.add_argument("--table", default="agencyhubara-tflock")
    v.add_argument("--tenant", action="append", default=[],
                   help="además, las llaves de secret_keys que le faltan o siguen en placeholder (repetible)")
    v.add_argument("--prefix", default="/hubara")
    v.add_argument("--variables", default=str(VARIABLES_TF), help="variables.tf con `secret_keys`")
    v.set_defaults(func=cmd_verify)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
