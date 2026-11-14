"""Los contenedores de la caja no alcanzan las credenciales de la instancia.

Con el AMI de AL2023 el servicio de metadatos acepta 2 saltos (hop limit 2):
desde cualquier contenedor se sacaban las credenciales del rol de la caja, que
lee todo `/hubara/<tenant>/*` y manda comandos a la caja de GraphAgents (revisión
de seguridad del 2026-10-26). Con hop limit 1 solo el host llega.

Los tres contenedores que sí usan AWS (la API guarda el token de Meta y despierta
a GraphAgents; order-sentinel y reengagement despiertan a GraphAgents) reciben
credenciales TEMPORALES de un rol propio con esos permisos y nada más: un timer
del host las pide cada 15 min (1 h de vida) y las deja en `/opt/hubara/aws/`, que
esos contenedores montan de solo lectura; botocore las lee con `credential_process`
y las renueva solo. No hay llaves fijas.

El HCL se lee como texto (patrón de `test_lab_box_iam.py`).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
_MODULES = _REPO / "infra" / "terraform" / "compute" / "modules"
_APP_TF = _MODULES / "app-instance" / "main.tf"
_COMPOSE = _REPO / "infra" / "compose" / "docker-compose.prod.yml"
_REFRESH = _REPO / "infra" / "compose" / "refresh-container-aws-creds.sh"
_HELPER = _REPO / "hubara_agency" / "deploy" / "aws" / "credential_process.py"
_AWS_CONFIG = _REPO / "hubara_agency" / "deploy" / "aws" / "config"
_COMPUTE_MAIN = _REPO / "infra" / "terraform" / "compute" / "main.tf"
_COMPUTE_TFVARS = _REPO / "infra" / "terraform" / "compute" / "tenants.auto.tfvars"

#: Los únicos contenedores que llaman a AWS (boto3) en producción.
_AWS_SERVICES = {"api", "worker-order-sentinel-cycle", "worker-reengagement-cycle"}
#: Los que llaman al laboratorio cuando está encendido: la API lee las corridas y
#: sales_eval las lanza (S3, EC2 y SSM sobre la caja del laboratorio).
_LAB_SERVICES = {"api", "worker-chats-sales_eval"}


def _block(text: str, header: str) -> str:
    start = text.index(header)
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[open_at + 1 : i]
    raise AssertionError(f"bloque sin cerrar: {header}")


def _statements(doc: str) -> list[str]:
    out, rest = [], doc
    while "statement {" in rest:
        body = _block(rest, "statement {")
        out.append(body)
        rest = rest[rest.index(body) + len(body) :]
    return out


def _load_helper():
    spec = importlib.util.spec_from_file_location("credential_process", _HELPER)
    assert spec and spec.loader, f"no se puede cargar {_HELPER}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Terraform ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("module", ["app-instance", "graphagents-instance", "observability-instance"])
def test_the_containers_of_the_box_cannot_reach_the_instance_metadata(module: str) -> None:
    tf = (_MODULES / module / "main.tf").read_text(encoding="utf-8")
    meta = _block(tf, "metadata_options {")
    assert re.search(r'http_tokens\s*=\s*"required"', meta), f"{module}: IMDSv2 dejó de ser obligatorio"
    assert re.search(r"http_put_response_hop_limit\s*=\s*1\b", meta), (
        f"{module}: con hop limit 2 cualquier contenedor saca las credenciales de la caja"
    )


def test_the_containers_role_can_only_be_assumed_by_the_box() -> None:
    tf = _APP_TF.read_text(encoding="utf-8")
    role = _block(tf, 'resource "aws_iam_role" "app_containers"')
    assert '"agencyhubara-${var.tenant}-app-containers"' in role
    trust = _block(tf, 'data "aws_iam_policy_document" "containers_assume"')
    assert '"sts:AssumeRole"' in trust
    assert "aws_iam_role.app.arn" in trust, "solo el rol de la caja puede asumir el de los contenedores"


def test_the_box_role_can_assume_the_containers_role_and_nothing_else_new() -> None:
    tf = _APP_TF.read_text(encoding="utf-8")
    doc = _block(tf, 'data "aws_iam_policy_document" "assume_containers"')
    (stmt,) = _statements(doc)
    assert '"sts:AssumeRole"' in stmt and "aws_iam_role.app_containers.arn" in stmt


def test_the_containers_role_only_reaches_the_meta_token_and_the_graphagents_box() -> None:
    tf = _APP_TF.read_text(encoding="utf-8")
    doc = _block(tf, 'data "aws_iam_policy_document" "containers"')
    allowed = {
        "ssm:GetParameter", "ssm:PutParameter", "ssm:DeleteParameter",
        "kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey",
        "ec2:StartInstances", "ec2:DescribeInstances",
        "ssm:SendCommand", "ssm:GetCommandInvocation", "ssm:DescribeInstanceInformation",
    }
    actions = set(re.findall(r'"([a-z0-9]+:[A-Za-z]+)"', doc))
    assert actions <= allowed, f"permisos de más para los contenedores: {sorted(actions - allowed)}"
    meta = next(s for s in _statements(doc) if "ssm:PutParameter" in s)
    assert "/hubara/${var.tenant}/meta/*" in meta and "/hubara/${var.tenant}/*" not in meta
    assert "data.aws_iam_policy_document.containers_wake_graphagents.json" in doc
    assert "data.aws_iam_policy_document.wake_graphagents.json" not in doc, (
        "el `wake_graphagents` de la caja da ssm:SendCommand sobre `*`: desde un contenedor alcanzaría la propia caja"
    )


def test_the_containers_can_only_send_commands_to_the_graphagents_box() -> None:
    # Quien puede mandarle comandos al agente SSM de una instancia es root en ella. Con
    # SendCommand sobre `*`, un contenedor con este rol correría AWS-RunShellScript en la
    # propia caja de app y el hop limit no serviría de nada (revisión 2026-11-06).
    tf = _APP_TF.read_text(encoding="utf-8")
    wake = _statements(_block(tf, 'data "aws_iam_policy_document" "containers_wake_graphagents"'))
    send = [s for s in wake if '"ssm:SendCommand"' in s]
    on_instances = [s for s in send if '"arn:aws:ec2:*:*:instance/*"' in s]
    on_document = [s for s in send if '"arn:aws:ssm:*::document/AWS-RunShellScript"' in s]

    assert len(send) == 2 and len(on_instances) == 1 and len(on_document) == 1, send
    for stmt in send:
        assert re.findall(r'"([a-z0-9]+:[A-Za-z]+)"', stmt.split("resources")[0]) == ["ssm:SendCommand"], stmt
        assert '"*"' not in stmt, stmt
    assert re.search(r'variable\s*=\s*"ssm:resourceTag/Role"', on_instances[0]) and '"graphagents"' in on_instances[0]
    start = next(s for s in wake if '"ec2:StartInstances"' in s)
    assert re.search(r'variable\s*=\s*"ec2:ResourceTag/Role"', start) and '"graphagents"' in start


def test_the_lab_permissions_also_reach_the_containers_role() -> None:
    # Con hop limit 1 la API y sales_eval llaman al laboratorio con el rol de los
    # contenedores: `launch-lab` tiene que llegar también a ese rol.
    lab = _block(_COMPUTE_MAIN.read_text(encoding="utf-8"), 'module "lab"')
    assert "m.containers_role_name" in lab
    assert re.search(
        r'output "containers_role_name"\s*\{\s*value\s*=\s*aws_iam_role\.app_containers\.name',
        _APP_TF.read_text(encoding="utf-8"),
    )


# ── El timer del host ────────────────────────────────────────────────────────


def test_the_refresh_script_assumes_exactly_the_terraform_role(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls.log"
    (fake_bin / "aws").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" >> {calls}
        case "$*" in
          *get-caller-identity*) echo 123456789012 ;;
          *assume-role*) echo '{{"AccessKeyId": "ASIAFAKE", "SecretAccessKey": "s3cr3t", "SessionToken": "tok", "Expiration": "2026-10-26T04:00:00+00:00"}}' ;;
        esac
        """))
    (fake_bin / "aws").chmod(0o755)
    box_env = tmp_path / "box.env"
    box_env.write_text("AWS_REGION=us-east-1\nTENANT=hubara\n")
    out_dir = tmp_path / "aws"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "BOX_ENV": str(box_env), "OUT_DIR": str(out_dir)}

    subprocess.run(["bash", str(_REFRESH)], check=True, env=env, capture_output=True, text=True)

    log = calls.read_text()
    assert "--role-arn arn:aws:iam::123456789012:role/agencyhubara-hubara-app-containers" in log
    assert "--duration-seconds 3600" in log
    creds = json.loads((out_dir / "creds.json").read_text())
    assert creds == {
        "Version": 1, "AccessKeyId": "ASIAFAKE", "SecretAccessKey": "s3cr3t",
        "SessionToken": "tok", "Expiration": "2026-10-26T04:00:00+00:00",
    }
    assert stat.S_IMODE((out_dir / "creds.json").stat().st_mode) == 0o600


# ── El ayudante de botocore (credential_process) ─────────────────────────────


def _write_creds(path: Path, minutes: int) -> None:
    exp = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    path.write_text(json.dumps({"Version": 1, "AccessKeyId": "ASIAFILE", "SecretAccessKey": "x", "SessionToken": "y", "Expiration": exp}))


def test_the_helper_hands_botocore_the_credentials_the_host_left(tmp_path: Path, capsys) -> None:
    creds = tmp_path / "creds.json"
    _write_creds(creds, minutes=50)
    helper = _load_helper()

    code = helper.main(creds_file=creds, imds=lambda: pytest.fail("no debe tocar IMDS con un archivo vigente"))

    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["AccessKeyId"] == "ASIAFILE" and out["Version"] == 1 and out["Expiration"]


def test_an_expired_file_falls_back_to_the_instance_metadata_during_the_transition(tmp_path: Path, capsys) -> None:
    creds = tmp_path / "creds.json"
    _write_creds(creds, minutes=-5)
    helper = _load_helper()
    from_imds = {"Version": 1, "AccessKeyId": "ASIAIMDS", "SecretAccessKey": "a", "SessionToken": "b", "Expiration": "2099-01-01T00:00:00Z"}

    code = helper.main(creds_file=creds, imds=lambda: from_imds)

    assert code == 0 and json.loads(capsys.readouterr().out)["AccessKeyId"] == "ASIAIMDS"


def test_without_credentials_the_helper_fails_loudly(tmp_path: Path, capsys) -> None:
    helper = _load_helper()

    code = helper.main(creds_file=tmp_path / "missing.json", imds=lambda: None)

    captured = capsys.readouterr()
    assert code != 0 and captured.out == "" and "app-aws-creds" in captured.err


def test_botocore_is_pointed_at_the_helper() -> None:
    text = _AWS_CONFIG.read_text(encoding="utf-8")
    assert "[default]" in text
    assert "credential_process = /app/.venv/bin/python /app/hubara_agency/deploy/aws/credential_process.py" in text


# ── El compose ───────────────────────────────────────────────────────────────


def _services() -> dict[str, dict]:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]


@pytest.mark.parametrize("name", sorted(_AWS_SERVICES))
def test_only_the_aws_containers_see_the_temporary_credentials(name: str) -> None:
    svc = _services()[name]
    env = svc.get("environment") or {}
    assert env.get("AWS_CONFIG_FILE") == "/app/hubara_agency/deploy/aws/config"
    assert "/opt/hubara/aws:/run/app-aws:ro" in svc.get("volumes", [])


def _lab_enabled() -> bool:
    lab = _block(_COMPUTE_TFVARS.read_text(encoding="utf-8"), "lab = {")
    found = re.search(r"enabled\s*=\s*(true|false)", lab)
    assert found, "compute/tenants.auto.tfvars: `lab` sin `enabled`"
    return found.group(1) == "true"


def test_turning_the_lab_on_needs_the_temporary_credentials_where_it_runs() -> None:
    # Hoy el laboratorio está apagado y sales_eval no llama a AWS: no recibe
    # credenciales. El PR que lo encienda tiene que montárselas (y agregarlo a
    # _AWS_SERVICES) o sales_eval no podrá lanzar corridas.
    needs = _LAB_SERVICES if _lab_enabled() else set()
    missing = sorted(needs - _AWS_SERVICES)
    assert not missing, f"con el laboratorio encendido {missing} llaman a AWS: montales las credenciales temporales"


def test_no_other_container_mounts_the_credentials() -> None:
    for name, svc in _services().items():
        if name in _AWS_SERVICES:
            continue
        mounts = " ".join(svc.get("volumes", []))
        env = svc.get("environment") or {}
        assert "/opt/hubara/aws" not in mounts and "AWS_CONFIG_FILE" not in env, name
