"""La línea de acceso de la API llega a CloudWatch (memoria de la caja de app,
2026-11-05).

Tres puntas que tienen que coincidir o los logs dejan de llegar sin aviso:
la API escribe en `HUBARA_ACCESS_LOG_FILE` (compose de prod), ese archivo vive
en una carpeta del host montada en el contenedor, y el agente de CloudWatch
(ya instalado por `monitoring.tf`) lee ESA ruta del host y la guarda 14 días en
un grupo de logs que crea Terraform. Los logs de docker no sirven: se borran
cada vez que el deploy recrea el contenedor.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]
_COMPOSE = _REPO / "infra" / "compose" / "docker-compose.prod.yml"
_MONITORING = _REPO / "infra" / "terraform" / "compute" / "modules" / "app-instance" / "monitoring.tf"


def _hcl(path: Path) -> str:
    return "\n".join(line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def _host_path_of_the_api_log() -> PurePosixPath:
    api = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]["api"]
    in_container = PurePosixPath(api["environment"]["HUBARA_ACCESS_LOG_FILE"])
    for volume in api["volumes"]:
        host, _, rest = volume.partition(":")
        target = PurePosixPath(rest.split(":", 1)[0])
        if in_container.is_relative_to(target) and host.startswith("/"):
            assert not rest.endswith(":ro"), "la API tiene que poder escribir el archivo"
            return PurePosixPath(host) / in_container.relative_to(target)
    raise AssertionError("el archivo de la línea de acceso no está en una carpeta del host")


def _shipped_files(text: str) -> list[str]:
    """Las rutas que el agente lee (`file_path`), literales o por `local.X`."""
    out = []
    for value in re.findall(r"\bfile_path\s*=\s*(\"[^\"]+\"|local\.\w+)", text):
        if value.startswith("local."):
            local = re.search(rf'^\s*{value.removeprefix("local.")}\s*=\s*"([^"]+)"', text, re.M)
            assert local, f"{value} no está definido"
            value = local.group(1)
        out.append(value.strip('"'))
    return out


def test_the_agent_ships_the_file_the_api_writes() -> None:
    assert str(_host_path_of_the_api_log()) in _shipped_files(_hcl(_MONITORING))


def test_the_log_group_is_created_by_terraform_and_kept_14_days() -> None:
    text = _hcl(_MONITORING)
    group = re.search(r'resource\s+"aws_cloudwatch_log_group"\s+"api_access"\s*\{(.*?)\n\}', text, re.S)

    assert group, "falta el grupo de logs de la línea de acceso"
    assert re.search(r"retention_in_days\s*=\s*14\b", group.group(1))
    assert re.search(r"log_group_name\s*=\s*aws_cloudwatch_log_group\.api_access\[0\]\.name", text)
