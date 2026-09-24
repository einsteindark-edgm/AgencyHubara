"""Cloud-init y compose de la caja del laboratorio (plan del laboratorio, PR 6).

* El user_data de EC2 tiene un límite de 16 KB: el cloud-init lleva el
  compose, el litellm_config y los scripts, comprimidos con gzip.
* Las imágenes van FIJADAS POR DIGEST (plan §3.3): si el digest no coincide,
  Docker no las corre.
* La caja monta el MISMO litellm_config.yaml que producción (mismos alias,
  mismos ids revisados por el guard L-23).
* Temporal en modo desarrollo con base en archivo y namespace propio.
"""
from __future__ import annotations

import base64
import gzip
import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]
_MODULE = _REPO / "infra" / "terraform" / "compute" / "modules" / "lab-instance"
_LAB = _REPO / "infra" / "compose" / "lab"
_EC2_USER_DATA_MAX = 16 * 1024


def _rendered() -> str:
    text = (_MODULE / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
    files = {
        "compose_b64": _LAB / "docker-compose.lab.yml",
        "litellm_config_b64": _REPO / "exoclaw-temporal" / "litellm_config.yaml",
        "dispatch_b64": _LAB / "dispatch.sh",
        "cancel_b64": _LAB / "cancel.sh",
        "autostop_b64": _LAB / "autostop.sh",
    }
    values = {k: base64.b64encode(p.read_bytes()).decode() for k, p in files.items()}
    values |= {"region": "us-east-1", "bucket": "agencyhubara-lab-000000000000", "ghcr_owner": "owner", "autostop_minutes": "10"}
    for key, value in values.items():
        text = text.replace("${" + key + "}", value)
    assert "${" not in text, "variable del template sin valor"
    return text


def test_user_data_fits_in_ec2_with_margin() -> None:
    size = len(gzip.compress(_rendered().encode()))

    assert size < _EC2_USER_DATA_MAX * 0.85, f"user_data gzip = {size} bytes"


def test_cloud_init_is_valid_and_installs_every_file() -> None:
    data = yaml.safe_load(_rendered())

    paths = {f["path"] for f in data["write_files"]}
    assert {
        "/opt/lab/docker-compose.lab.yml",
        "/opt/lab/litellm_config.yaml",
        "/opt/lab/dispatch.sh",
        "/opt/lab/cancel.sh",
        "/opt/lab/autostop.sh",
        "/etc/systemd/system/lab-temporal-reset.service",
    } <= paths
    installed = {f["path"]: f for f in data["write_files"]}
    assert base64.b64decode(installed["/opt/lab/litellm_config.yaml"]["content"]) == (
        _REPO / "exoclaw-temporal" / "litellm_config.yaml"
    ).read_bytes()


def test_temporal_is_wiped_before_docker_on_every_boot() -> None:
    unit = next(f for f in yaml.safe_load(_rendered())["write_files"] if f["path"].endswith("lab-temporal-reset.service"))

    assert "Before=docker.service" in unit["content"]
    assert "rm -rf /lab/temporal" in unit["content"]


def test_compose_pins_every_image_by_digest_and_runs_temporal_dev_on_a_file() -> None:
    compose = yaml.safe_load((_LAB / "docker-compose.lab.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    for name in ("temporal", "litellm"):
        assert re.search(r"@sha256:[a-f0-9]{64}$", services[name]["image"]), name
    cmd = services["temporal"]["command"]
    assert cmd[:2] == ["server", "start-dev"]
    assert cmd[cmd.index("--namespace") + 1] == "hubara-lab"
    assert cmd[cmd.index("--db-filename") + 1] == "/data/lab.sqlite"
    assert services["temporal"]["ports"] == ["127.0.0.1:8233:8233"]


def test_lab_worker_points_to_the_box_temporal_and_never_to_cloud() -> None:
    env = yaml.safe_load((_LAB / "docker-compose.lab.yml").read_text(encoding="utf-8"))["services"]["sales_lab"]["environment"]

    assert env["TEMPORAL_URL"] == "temporal:7233"
    assert env["TEMPORAL_NAMESPACE"] == "hubara-lab"
    assert not {"TEMPORAL_API_KEY", "TEMPORAL_ADDRESS"} & set(env)
