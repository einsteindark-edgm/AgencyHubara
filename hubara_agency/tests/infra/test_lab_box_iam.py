"""IAM de la caja del laboratorio (plan del laboratorio §3.5, candado 2).

La caja NO puede leer las llaves de producción. El agente SSM necesita la
política administrada `AmazonSSMManagedInstanceCore`, que trae
`ssm:GetParameter`/`ssm:GetParameters` sobre `*`. Los permisos se suman: sin un
Deny explícito, restringir el Allow propio a `/hubara-lab/*` no alcanza, y
desde la caja se podría leer `/hubara/<tenant>/WHATSAPP_ACCESS_TOKEN`,
`TEMPORAL_API_KEY`, etc. (los SecureString usan la llave `aws/ssm`, que
descifra para cualquier identidad de la cuenta que llegue por SSM).

Además, S3 contesta 404 a una clave que no existe SOLO si el rol tiene
`s3:ListBucket` sobre el bucket; con un ListBucket condicionado por
`s3:prefix` contesta 403, y el primer `progress.json` de una corrida (que
todavía no existe) rompería la caja y el seguimiento de producción.

Se lee el HCL como texto: el módulo no tiene otra forma de probarse sin AWS.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_COMPUTE = _REPO / "infra" / "terraform" / "compute"
_MODULE = _COMPUTE / "modules" / "lab-instance" / "main.tf"


def _block(text: str, header: str) -> str:
    start = text.index(header)
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[open_at + 1 : i]
    raise AssertionError(f"bloque sin cerrar: {header}")


def _statements(policy: str) -> list[str]:
    out = []
    for match in re.finditer(r"\bstatement\s*\{", policy):
        out.append(_block(policy[match.start() :], "statement"))
    return out


def _policy(name: str) -> list[str]:
    return _statements(_block(_MODULE.read_text(encoding="utf-8"), f'data "aws_iam_policy_document" "{name}"'))


def _actions(statement: str) -> set[str]:
    found = re.search(r"actions\s*=\s*\[([^\]]*)\]", statement)
    return set(re.findall(r'"([^"]+)"', found.group(1))) if found else set()


def test_the_box_role_denies_every_parameter_outside_the_lab_tree() -> None:
    denies = [s for s in _policy("lab_box") if re.search(r'effect\s*=\s*"Deny"', s)]

    [deny] = [s for s in denies if {"ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"} <= _actions(s)]
    not_resources = re.search(r"not_resources\s*=\s*\[([^\]]*)\]", deny)
    assert not_resources, "el Deny tiene que ser por not_resources (todo menos /hubara-lab)"
    allowed = set(re.findall(r'"([^"]+)"', not_resources.group(1)))
    assert allowed == {"arn:aws:ssm:*:*:parameter/hubara-lab", "arn:aws:ssm:*:*:parameter/hubara-lab/*"}


def test_decrypt_only_works_for_lab_parameters_through_ssm() -> None:
    [decrypt] = [s for s in _policy("lab_box") if "kms:Decrypt" in _actions(s)]

    assert "kms:ViaService" in decrypt
    assert "kms:EncryptionContext:PARAMETER_ARN" in decrypt
    assert "parameter/hubara-lab/*" in decrypt


def test_both_roles_can_list_the_bucket_so_missing_keys_answer_404() -> None:
    for name in ("lab_box", "app_launch_lab"):
        [listing] = [s for s in _policy(name) if "s3:ListBucket" in _actions(s)]
        assert "s3:prefix" not in listing, f"{name}: ListBucket condicionado → GetObject de una clave nueva da 403"


def test_unfinished_uploads_do_not_pile_up_in_the_bucket() -> None:
    lifecycle = _block(_MODULE.read_text(encoding="utf-8"), 'resource "aws_s3_bucket_lifecycle_configuration" "lab"')

    assert "abort_incomplete_multipart_upload" in lifecycle


def test_only_the_lab_tenants_can_launch_runs_or_read_the_bench() -> None:
    """El bucket es uno (sin prefijo por tenant): la política de lanzar corridas
    va SOLO a los roles de los tenants del laboratorio, no a toda caja de app.
    Por defecto ninguno (un clon de forge no hereda el laboratorio); hubara lo
    declara en su tfvars."""
    variables = (_COMPUTE / "variables.tf").read_text(encoding="utf-8")
    lab_var = _block(variables, 'variable "lab"')
    assert re.search(r"tenants\s*=\s*optional\(list\(string\),\s*\[\]\)", lab_var)
    tfvars = _block((_COMPUTE / "tenants.auto.tfvars").read_text(encoding="utf-8"), "lab =")
    assert re.search(r'tenants\s*=\s*\["hubara"\]', tfvars)

    module = _block((_COMPUTE / "main.tf").read_text(encoding="utf-8"), 'module "lab"')
    roles = re.search(r"app_role_names\s*=\s*(.+)", module).group(1)
    assert "var.lab.tenants" in roles
    assert re.search(r"tenants\s*=\s*var\.lab\.tenants", module)
