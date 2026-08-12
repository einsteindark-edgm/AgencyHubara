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
  secrets  Sube los SECRETOS de un archivo .env a SSM (/hubara/<tenant>/<KEY>, SecureString).
  github   Setea las GH variables (role ARNs, region, state) + el secret EC2_SSH_KEY.
  verify   Chequea que el bootstrap esté completo.

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
  python3 aws_bootstrap.py secrets --tenant hubara --file secrets.example.env \\
      --endpoint-url http://localhost:4566
"""
import argparse
import json
import os
import subprocess
import sys

REGION_DEFAULT = "us-east-1"


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
def parse_env_file(path):
    """Lee KEY=VALUE (ignora blancos y #comentarios). Devuelve lista [(k,v)]."""
    out = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            out.append((k.strip(), v.strip()))
    return out


def cmd_secrets(a):
    env = aws_env(a.endpoint_url)
    base = aws_base(a.region, a.endpoint_url)
    prefix = f"{a.prefix}/{a.tenant}"
    pairs = parse_env_file(a.file)
    if not pairs:
        sys.exit(f"✗ {a.file} no tiene pares KEY=VALUE")

    print(f"▶ Subiendo {len(pairs)} parámetros a SSM bajo {prefix}/ (type {a.type})", file=sys.stderr)
    for k, v in pairs:
        name = f"{prefix}/{k}"
        run(base + ["ssm", "put-parameter", "--overwrite", "--name", name,
                    "--type", a.type, "--value", v], env=env, mask=v)
    print(f"✓ {len(pairs)} secretos en {prefix}/. Verificá con: "
          f"aws ssm get-parameters-by-path --path {prefix} --with-decryption", file=sys.stderr)


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
    sys.exit(0 if ok else "✗ bootstrap incompleto")


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
    se.set_defaults(func=cmd_secrets)

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
    v.set_defaults(func=cmd_verify)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
