"""Cloud-init y compose de la caja del laboratorio (plan del laboratorio, PR 6).

* El user_data de EC2 tiene un límite de 16 KB: el cloud-init lleva el
  compose y los scripts, comprimidos con gzip.
* El `litellm_config.yaml` NO viaja en el user_data: sale de la imagen de cada
  corrida (`dispatch.sh`), la misma que corre producción. Una copia del día en
  que se creó la caja dejaría al bot simulado con otros ids de modelo.
* Las imágenes van FIJADAS POR DIGEST (plan §3.3): si el digest no coincide,
  Docker no las corre. LiteLLM es la misma versión y el mismo healthcheck que
  producción.
* Temporal en modo desarrollo con base en archivo y namespace propio.
* El autoapagado corre con un timer de systemd (no depende de instalar cron),
  el contador de minutos sin trabajo arranca en cero en cada arranque, y un
  apagado de respaldo corta la caja si todo lo demás falla.
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
_PROD_COMPOSE = _REPO / "infra" / "compose" / "docker-compose.prod.yml"
_EC2_USER_DATA_MAX = 16 * 1024
_MAX_RUN_HOURS = 12


def _rendered() -> str:
    text = (_MODULE / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
    files = {
        "compose_b64": _LAB / "docker-compose.lab.yml",
        "dispatch_b64": _LAB / "dispatch.sh",
        "cancel_b64": _LAB / "cancel.sh",
        "autostop_b64": _LAB / "autostop.sh",
    }
    values = {k: base64.b64encode(p.read_bytes()).decode() for k, p in files.items()}
    values |= {
        "region": "us-east-1", "bucket": "agencyhubara-lab-000000000000", "ghcr_owner": "owner",
        "autostop_minutes": "10", "max_run_hours": str(_MAX_RUN_HOURS),
        "backstop_minutes": str((_MAX_RUN_HOURS + 1) * 60),
    }
    for key, value in values.items():
        text = text.replace("${" + key + "}", value)
    assert "${" not in text, "variable del template sin valor"
    return text


def _files() -> dict[str, dict]:
    return {f["path"]: f for f in yaml.safe_load(_rendered())["write_files"]}


def _compose(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_user_data_fits_in_ec2_with_margin() -> None:
    size = len(gzip.compress(_rendered().encode()))

    assert size < _EC2_USER_DATA_MAX * 0.85, f"user_data gzip = {size} bytes"


def test_cloud_init_is_valid_and_installs_every_file() -> None:
    paths = set(_files())

    assert {
        "/opt/lab/docker-compose.lab.yml",
        "/opt/lab/dispatch.sh",
        "/opt/lab/cancel.sh",
        "/opt/lab/autostop.sh",
        "/etc/systemd/system/lab-temporal-reset.service",
        "/etc/systemd/system/lab-autostop.service",
        "/etc/systemd/system/lab-autostop.timer",
        "/etc/systemd/system/lab-backstop.service",
    } <= paths


def test_the_litellm_config_comes_from_the_run_image_not_from_user_data() -> None:
    assert "/opt/lab/litellm_config.yaml" not in _files()
    main_tf = (_MODULE / "main.tf").read_text(encoding="utf-8")
    assert "litellm_config" not in re.sub(r"#.*", "", main_tf), "un cambio del config reemplazaría la caja"
    assert "/app/exoclaw-temporal/litellm_config.yaml" in (_LAB / "dispatch.sh").read_text(encoding="utf-8")


def test_temporal_is_wiped_before_docker_on_every_boot_and_the_idle_count_restarts() -> None:
    unit = _files()["/etc/systemd/system/lab-temporal-reset.service"]["content"]

    assert "Before=docker.service" in unit
    assert "rm -rf /lab/temporal" in unit
    assert "rm -f /var/lib/lab/idle_count" in unit


def test_autostop_runs_from_a_systemd_timer_not_from_cron() -> None:
    data = yaml.safe_load(_rendered())
    files = _files()

    assert "cronie" not in (data.get("packages") or [])
    assert not [p for p in files if p.startswith("/etc/cron")]
    timer = files["/etc/systemd/system/lab-autostop.timer"]["content"]
    assert "OnUnitActiveSec=5min" in timer
    service = files["/etc/systemd/system/lab-autostop.service"]["content"]
    assert "ExecStart=/opt/lab/autostop.sh" in service
    assert "IDLE_MIN=10" in service and f"LAB_MAX_RUN_HOURS={_MAX_RUN_HOURS}" in service
    assert any("lab-autostop.timer" in str(cmd) for cmd in data["runcmd"])


def test_a_backstop_shuts_the_box_down_after_the_longest_allowed_run() -> None:
    data = yaml.safe_load(_rendered())
    unit = _files()["/etc/systemd/system/lab-backstop.service"]["content"]

    assert f"shutdown -h +{(_MAX_RUN_HOURS + 1) * 60}" in unit
    assert "backstop_minutes = (var.max_run_hours + 1) * 60" in (_MODULE / "main.tf").read_text(encoding="utf-8")
    assert any("lab-backstop.service" in str(cmd) for cmd in data["runcmd"])


def test_the_compose_plugin_download_fails_loud_and_retries() -> None:
    [curl] = [str(cmd) for cmd in yaml.safe_load(_rendered())["runcmd"] if "curl" in str(cmd)]

    assert re.search(r"curl -[a-zA-Z]*f", curl), "sin -f una página de error queda instalada como docker compose"
    assert "--retry" in curl


def test_compose_pins_every_image_by_digest_and_runs_temporal_dev_on_a_file() -> None:
    services = _compose(_LAB / "docker-compose.lab.yml")["services"]

    for name in ("temporal", "litellm"):
        assert re.search(r"@sha256:[a-f0-9]{64}$", services[name]["image"]), name
    cmd = services["temporal"]["command"]
    assert cmd[:2] == ["server", "start-dev"]
    assert cmd[cmd.index("--namespace") + 1] == "hubara-lab"
    assert cmd[cmd.index("--db-filename") + 1] == "/data/lab.sqlite"
    assert services["temporal"]["ports"] == ["127.0.0.1:8233:8233"]


def test_lab_litellm_is_the_same_proxy_as_production() -> None:
    lab = _compose(_LAB / "docker-compose.lab.yml")["services"]["litellm"]
    prod = _compose(_PROD_COMPOSE)["services"]["litellm"]

    assert lab["image"].startswith(prod["image"] + "@sha256:")
    assert lab["healthcheck"] == prod["healthcheck"]


def test_each_service_reads_only_its_env_and_the_worker_waits_for_a_healthy_proxy() -> None:
    services = _compose(_LAB / "docker-compose.lab.yml")["services"]

    assert services["litellm"]["env_file"] == "/opt/lab/litellm.env"
    worker = services["sales_lab"]
    assert worker["env_file"] == "/opt/lab/sales_lab.env"
    assert worker["depends_on"]["litellm"]["condition"] == "service_healthy"
    # el runner no ve la base SQLite de Temporal ni nada fuera de sus carpetas
    assert sorted(worker["volumes"]) == ["/lab/bench:/lab/bench", "/lab/runs:/lab/runs"]


def test_lab_worker_points_to_the_box_temporal_and_never_to_cloud() -> None:
    env = _compose(_LAB / "docker-compose.lab.yml")["services"]["sales_lab"]["environment"]

    assert env["TEMPORAL_URL"] == "temporal:7233"
    assert env["TEMPORAL_NAMESPACE"] == "hubara-lab"
    assert not {"TEMPORAL_API_KEY", "TEMPORAL_ADDRESS"} & set(env)


def test_the_worker_runs_from_the_backend_folder_like_production() -> None:
    """La imagen trabaja en /app y `hubara_agency` no se instala como paquete
    (uv virtual): sin este working_dir, `python -m src.plugins...` muere con
    ModuleNotFoundError y la corrida nunca reporta avance."""
    prod = _compose(_PROD_COMPOSE)["x-app"]
    worker = _compose(_LAB / "docker-compose.lab.yml")["services"]["sales_lab"]

    assert worker["working_dir"] == prod["working_dir"] == "/app/hubara_agency"


_PROD_ENV_SCRIPT = _REPO / "infra" / "compose" / "render-env-from-ssm.sh"


def test_the_worker_prices_llm_turns_with_the_production_table() -> None:
    """El costo del LLM de cada turno sale de la tabla de precios de OpenLIT
    (`OPENLIT_PRICING_JSON`). Producción la fija en su .env; sin ella la caja
    reporta US$0 por turno y los topes de gasto (por corrida y del mes) quedan
    ciegos."""
    prod = re.search(r"^OPENLIT_PRICING_JSON=(\S+)$", _PROD_ENV_SCRIPT.read_text(encoding="utf-8"), re.MULTILINE)
    env = _compose(_LAB / "docker-compose.lab.yml")["services"]["sales_lab"]["environment"]

    assert prod is not None
    assert env.get("OPENLIT_PRICING_JSON") == prod.group(1)


def test_a_decision_run_fits_in_the_default_run_limit() -> None:
    """Una corrida de decisión (A1, B y C × 3 repeticiones ≈ 3.600 turnos con
    4 casos a la vez, más el juez de 10 pasadas) pasa de 12 h: el tope por
    defecto la cortaba a mitad, con el gasto hecho. 20 h + 1 h de respaldo
    siguen dentro de las 24 h que el lanzador espera a la caja."""
    variables = (_REPO / "infra" / "terraform" / "compute" / "variables.tf").read_text(encoding="utf-8")

    assert re.search(r"max_run_hours\s*=\s*optional\(number,\s*20\)", variables)
