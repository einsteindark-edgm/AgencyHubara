"""Aislamiento IAM entre proyectos de la MISMA cuenta AWS (forge, 2026-10-09).

forge clona el motor para otra empresa en la misma cuenta: prefijos
`agency<slug>-*`, SSM `/<slug>/*`, tags `graphagents-<slug>` / `lab-<slug>`.
Los nombres no alcanzan si los permisos son de cuenta entera:

* `AmazonSSMManagedInstanceCore` (lo llevan todas las cajas) permite
  `ssm:GetParameter(s)` sobre `*`, y los SecureString usan la llave `aws/ssm`:
  sin un Deny explícito, la caja de app de un clon leería
  `/hubara/hubara/WHATSAPP_ACCESS_TOKEN`. La caja del laboratorio ya lo cerraba
  así (test_lab_box_iam.py); acá, las demás.
* `ssm:SendCommand` sobre `*` dejaba a la caja de app de un clon correr shell
  en la caja de producción de Hubara. Solo a SU caja GraphAgents (tag) y solo
  con `AWS-RunShellScript`.
* Lo único fuera de los árboles SSM del proyecto es la config del agente de
  CloudWatch (`AmazonCloudWatch-<prefijo>-<tenant>-app`): la caja la lee y el
  plan la refresca. Sin ella el Deny apagaría la métrica de memoria.
* `ReadOnlyAccess` (rol de plan/deploy de CI) trae `ssm:Get*` de toda la
  cuenta y el rol sumaba `kms:Decrypt` sobre `*`: el CI de un clon leía los
  secretos de Hubara. Deny fuera de los prefijos del proyecto + Decrypt solo
  de sus parámetros.
* `ReadOnlyAccess` también trae `s3:Get*`/`s3:List*` de toda la cuenta: el
  state de Terraform del OTRO proyecto guarda sus SecureString en claro, así
  que leerlo es saltarse el Deny de SSM. Deny de objetos fuera de los buckets
  del proyecto, el lock solo en sus tablas, y nada de salidas de comandos ni
  usuarios de Cognito (Terraform no los lee).

Se lee el HCL como texto (como test_lab_box_iam.py): sin AWS no hay otra forma.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_MODULES = _REPO / "infra" / "terraform" / "compute" / "modules"
_OIDC = _REPO / "infra" / "terraform" / "platform" / "modules" / "github-oidc" / "main.tf"
_GET = {"ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "ssm:GetParameterHistory"}


def _block(text: str, header: str) -> str:
    start = text.index(header)
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[open_at + 1 : i]
    raise AssertionError(f"bloque sin cerrar: {header}")


def _statements(path: Path, policy: str) -> list[str]:
    body = _block(path.read_text(encoding="utf-8"), f'data "aws_iam_policy_document" "{policy}"')
    return [_block(body[m.start():], "statement") for m in re.finditer(r"\bstatement\s*\{", body)]


def _list(statement: str, key: str) -> list[str]:
    found = re.search(rf"\b{key}\s*=\s*\[([^\]]*)\]", statement)
    return re.findall(r'"([^"]+)"', found.group(1)) if found else []


def _is_deny(statement: str) -> bool:
    return re.search(r'effect\s*=\s*"Deny"', statement) is not None


def _deny_reads(path: Path, policy: str) -> str:
    [deny] = [s for s in _statements(path, policy) if _is_deny(s) and _GET <= set(_list(s, "actions"))]
    return deny


@pytest.mark.parametrize(
    ("module", "policy", "own_tree"),
    [
        # + la config del agente de CloudWatch (monitoring.tf): el agente la lee
        # con el rol de la caja; sin ella se apaga la métrica de memoria y su alarma.
        # + CloudWatchAgentEnableWorkloadDetection: config del agente A NIVEL CUENTA
        # (la creó la consola, no es secreto de nadie); el agente puede consultarla.
        (
            "app-instance",
            "ssm_read",
            [
                "/hubara/${var.tenant}",
                "/hubara/${var.tenant}/*",
                "/AmazonCloudWatch-agencyhubara-${var.tenant}-app",
                "/CloudWatchAgentEnableWorkloadDetection",
            ],
        ),
        ("graphagents-instance", "ssm_read", ["/graphagents", "/graphagents/*"]),
    ],
)
def test_a_box_cannot_read_parameters_outside_its_own_tree(module: str, policy: str, own_tree: list[str]) -> None:
    deny = _deny_reads(_MODULES / module / "main.tf", policy)

    assert [r.split(":parameter")[-1] for r in _list(deny, "not_resources")] == own_tree


def test_the_observability_box_reads_no_parameter_at_all() -> None:
    deny = _deny_reads(_MODULES / "observability-instance" / "main.tf", "deny_ssm_parameters")

    assert _list(deny, "resources") == ["*"]


def test_the_app_box_only_commands_its_graphagents_box_with_the_shell_document() -> None:
    statements = _statements(_MODULES / "app-instance" / "main.tf", "wake_graphagents")
    send = [s for s in statements if "ssm:SendCommand" in _list(s, "actions")]

    assert all("*" not in _list(s, "resources") for s in send), "SendCommand sobre * corre shell en cualquier caja"
    [to_instance] = [s for s in send if _list(s, "resources") == ["arn:aws:ec2:*:*:instance/*"]]
    assert "ssm:resourceTag/Role" in to_instance and _list(to_instance, "values") == ["graphagents"]
    [document] = [s for s in send if _list(s, "resources") != ["arn:aws:ec2:*:*:instance/*"]]
    assert _list(document, "resources") == ["arn:aws:ssm:*::document/AWS-RunShellScript"]


def test_ci_readonly_cannot_read_other_projects_parameters() -> None:
    deny = _deny_reads(_OIDC, "tf_readonly_extra")

    assert sorted(r.split(":parameter")[-1] for r in _list(deny, "not_resources")) == sorted(
        ["/hubara/*", "/graphagents/*", "/hubara-lab/*", "/AmazonCloudWatch-agencyhubara-*"]
    )


def test_ci_readonly_cannot_read_objects_of_other_projects_buckets() -> None:
    [deny] = [
        s
        for s in _statements(_OIDC, "tf_readonly_extra")
        if _is_deny(s) and "s3:GetObject*" in _list(s, "actions")
    ]

    assert "s3:ListBucket*" in _list(deny, "actions")
    assert sorted(_list(deny, "not_resources")) == ["arn:aws:s3:::agencyhubara-*", "arn:aws:s3:::agencyhubara-*/*"]


def test_ci_readonly_only_locks_its_own_state_tables() -> None:
    [lock] = [s for s in _statements(_OIDC, "tf_readonly_extra") if "dynamodb:PutItem" in _list(s, "actions")]

    assert _list(lock, "resources") == ["arn:aws:dynamodb:*:*:table/agencyhubara-*"]


def test_ci_readonly_reads_no_command_outputs_nor_cognito_users() -> None:
    [deny] = [
        s
        for s in _statements(_OIDC, "tf_readonly_extra")
        if _is_deny(s) and "ssm:GetCommandInvocation" in _list(s, "actions")
    ]

    assert {"ssm:ListCommandInvocations", "cognito-idp:ListUsers", "cognito-idp:AdminGetUser"} <= set(
        _list(deny, "actions")
    )
    assert _list(deny, "resources") == ["*"]


def test_ci_readonly_only_decrypts_its_own_parameters() -> None:
    [decrypt] = [s for s in _statements(_OIDC, "tf_readonly_extra") if "kms:Decrypt" in _list(s, "actions")]

    assert "kms:EncryptionContext:PARAMETER_ARN" in decrypt
    values = [v for group in re.findall(r"\bvalues\s*=\s*\[([^\]]*)\]", decrypt) for v in re.findall(r'"([^"]+)"', group)]
    assert sorted(v.split(":parameter")[-1] for v in values if ":parameter/" in v) == sorted(
        ["/hubara/*", "/graphagents/*", "/hubara-lab/*"]
    )
