"""`Boto3Launcher` — el vendor REAL del `Launcher` port (ver `launcher.py`): habla con la caja
GraphAgents (EC2 dedicada, tag `Role=graphagents`) vía boto3 (ec2 + ssm).

Tres operaciones del port:
- `start_box()`  — resuelve el instance-id por tag, la arranca si está `stopped`, y espera a que
  AgentSpan responda en `:6767` (la IP privada). Idempotente (si ya corre, no-op de start).
- `dispatch()`   — SSM `send_command` (`AWS-RunShellScript`) que corre `sdk.cli start <agent>
  --input <json> --runtime agentspan` DENTRO del container `graphagents` de la caja; pollea
  `get_command_invocation` hasta `Success` y parsea el execution-id del stdout.
- `resume()`     — despierta la caja + SSM `send_command` `sdk.cli resume <eid> --decision <json>`.

Reglas que este adapter respeta (gotchas conocidos del subsistema):
- **Import perezoso de boto3** (dentro de `_clients`, NO al top) → en tests sin AWS el import del
  módulo NO rompe; el `api/__init__.py` lo importa perezoso a su vez.
- **IP dinámica + autostop** → SIEMPRE se resuelve la instancia por tag, NUNCA se hardcodea IP.
- **Graceful sin config** → sin `AWS_REGION` falla con un `RuntimeError` claro AL USARSE (no en el
  import); construir el objeto nunca rompe.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from src.sdk.runtime import AWS_REGION as AWS_REGION
from src.sdk.runtime import GRAPHAGENTS_INSTANCE_TAG as GRAPHAGENTS_INSTANCE_TAG

#: Puerto de AgentSpan/Conductor en la caja (ver infra/compose/graphagents + compute outputs).
_AGENTSPAN_PORT = 6767

#: Compose project + service de la caja graphagents (infra/compose/graphagents/docker-compose.prod.yml:
#: `name: graphagents-prod`, service `graphagents`) → el `sdk.cli` corre DENTRO de ese container.
_COMPOSE_PROJECT = "graphagents-prod"
_COMPOSE_SERVICE = "graphagents"

#: `python3 -m sdk.cli ...` ejecutado en el container graphagents vía docker compose exec (-T:
#: sin TTY, requerido bajo SSM). El compose vive en /opt/graphagents en la caja (cloud-init).
_DOCKER_EXEC = (
    f"docker compose -p {_COMPOSE_PROJECT} exec -T {_COMPOSE_SERVICE} python3 -m sdk.cli"
)

#: Parsea el execution-id del stdout de Conductor: `execution <id>: <status>`.
_EID_RE = re.compile(r"execution\s+(\S+)\s*:", re.IGNORECASE)


def _default_region() -> str:
    """La región AWS de deploy (del SDK runtime). Monkeypatcheable en tests. Vacío → graceful fail."""
    return AWS_REGION


def _default_instance_tag() -> str:
    """El valor del tag `Role` que identifica la caja graphagents (default `graphagents`)."""
    return GRAPHAGENTS_INSTANCE_TAG


def _shell_quote(value: str) -> str:
    """Quoting POSIX seguro para meter un JSON arbitrario en la línea de comando (single-quote
    wrap + escape de las single-quotes internas)."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


class Boto3Launcher:
    """Vendor boto3 del Launcher port. `region`/`instance_tag` se inyectan (tests) o caen a
    `config` (prod). Construir NUNCA toca AWS — el primer contacto es en el primer método."""

    def __init__(self, *, region: str | None = None, instance_tag: str | None = None) -> None:
        self._region = region
        self._instance_tag = instance_tag

    # --------------------------------------------------------------- config

    def _resolved_region(self) -> str:
        region = self._region if self._region is not None else _default_region()
        if not region:
            raise RuntimeError(
                "Boto3Launcher: AWS_REGION no configurada — no puedo hablar con la caja "
                "GraphAgents. Seteá AWS_REGION (y credenciales AWS) en el entorno del backend."
            )
        return region

    def _resolved_tag(self) -> str:
        return self._instance_tag if self._instance_tag is not None else _default_instance_tag()

    def _clients(self):
        """Crea los clientes ec2/ssm. Import de boto3 PEREZOSO (acá, no al top) → el módulo se
        importa sin boto3/AWS; tests monkeypatchean este método con fakes."""
        import boto3  # noqa: PLC0415 — perezoso a propósito (gotcha #6 / port docstring)

        region = self._resolved_region()
        return boto3.client("ec2", region_name=region), boto3.client("ssm", region_name=region)

    # ------------------------------------------------------------ EC2 / box

    def _describe(self, ec2) -> dict:
        """La instancia graphagents (por tag `Role=<tag>`). Falla LOUD si no hay ninguna."""
        resp = ec2.describe_instances(
            Filters=[{"Name": "tag:Role", "Values": [self._resolved_tag()]}]
        )
        for reservation in resp.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                state = (instance.get("State") or {}).get("Name")
                if state in (None, "running", "pending", "stopped", "stopping"):
                    return instance
        raise RuntimeError(
            f"Boto3Launcher: no encontré ninguna instancia con tag Role={self._resolved_tag()}."
        )

    def start_box(self) -> None:
        """Despierta la caja y espera a que AgentSpan esté healthy. Idempotente: si ya corre,
        solo verifica healthy; si está `stopped`, la arranca y espera `running` antes del poll."""
        ec2, _ = self._clients()
        instance = self._describe(ec2)
        instance_id = instance["InstanceId"]
        state = (instance.get("State") or {}).get("Name")

        if state in ("stopped", "stopping"):
            ec2.start_instances(InstanceIds=[instance_id])
            ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
            # Re-describe: la IP privada puede haber cambiado al re-arrancar (autostop + IP dinámica).
            instance = self._describe(ec2)

        private_ip = instance.get("PrivateIpAddress")
        if not private_ip:
            raise RuntimeError(
                f"Boto3Launcher: la instancia {instance_id} no tiene IP privada todavía."
            )
        self._wait_healthy(private_ip)

    def _wait_healthy(self, private_ip: str, *, timeout: float = 180.0, interval: float = 3.0) -> None:
        """Pollea AgentSpan (`http://<private_ip>:6767`) hasta que responde, o `RuntimeError` al
        timeout. AgentSpan tarda en levantar la JVM tras un arranque frío."""
        url = f"http://{private_ip}:{_AGENTSPAN_PORT}/"
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=5):  # noqa: S310 — host interno por tag
                    return
            except urllib.error.HTTPError:
                return  # respondió HTTP (incluso 4xx) → el server está vivo
            except Exception as exc:  # noqa: BLE001 — connection refused / DNS mientras levanta
                last_err = exc
                time.sleep(interval)
        raise RuntimeError(
            f"Boto3Launcher: AgentSpan no respondió en {url} tras {timeout:.0f}s ({last_err})."
        )

    # ------------------------------------------------------------ SSM / run

    def _run_cli(self, ssm, instance_id: str, cli_args: str) -> str:
        """Manda `<docker exec> <cli_args>` por SSM, pollea hasta terminal y devuelve el stdout.
        Falla LOUD si el comando no termina en `Success`."""
        command = f"{_DOCKER_EXEC} {cli_args}"
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [command]},
        )
        command_id = resp["Command"]["CommandId"]
        return self._await_invocation(ssm, command_id, instance_id)

    def _await_invocation(
        self, ssm, command_id: str, instance_id: str, *, timeout: float = 300.0, interval: float = 2.0
    ) -> str:
        """Pollea `get_command_invocation` hasta un status terminal. `Success` → stdout; cualquier
        otro terminal → `RuntimeError` con el stderr."""
        terminal = {"Success", "Cancelled", "TimedOut", "Failed", "Cancelling"}
        deadline = time.monotonic() + timeout
        while True:
            try:
                inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            except Exception as exc:  # noqa: BLE001 — InvocationDoesNotExist: aún propagándose
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Boto3Launcher: SSM invocation no apareció: {exc}") from exc
                time.sleep(interval)
                continue
            status = inv.get("Status")
            if status == "Success":
                return inv.get("StandardOutputContent", "")
            if status in terminal:
                stderr = inv.get("StandardErrorContent", "")
                raise RuntimeError(
                    f"Boto3Launcher: comando SSM terminó en {status}: "
                    f"{stderr or inv.get('StandardOutputContent', '')}"
                )
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Boto3Launcher: comando SSM no terminó tras {timeout:.0f}s (último: {status})."
                )
            time.sleep(interval)

    def dispatch(self, agent: str, input: dict, *, run_id: str) -> str:  # noqa: A002 — port signature
        """Despacha un run a AgentSpan y devuelve el execution-id de Conductor. El `run_id` viaja
        como metadata para correlación; el id que pollea el bridge es el de Conductor."""
        ec2, ssm = self._clients()
        instance = self._describe(ec2)
        instance_id = instance["InstanceId"]
        payload = _shell_quote(json.dumps(input, ensure_ascii=False))
        cli_args = f"start {agent} --input {payload} --runtime agentspan"
        stdout = self._run_cli(ssm, instance_id, cli_args)
        match = _EID_RE.search(stdout)
        if not match:
            raise RuntimeError(
                f"Boto3Launcher: no pude parsear el execution-id del stdout de `sdk.cli start` "
                f"(esperaba `execution <id>: <status>`). Stdout: {stdout!r}"
            )
        return match.group(1)

    def resume(self, execution_id: str, decision: dict) -> None:
        """Completa la HUMAN task: despierta la caja (puede estar dormida por autostop) y manda el
        `sdk.cli resume`."""
        self.start_box()
        ec2, ssm = self._clients()
        instance = self._describe(ec2)
        instance_id = instance["InstanceId"]
        payload = _shell_quote(json.dumps(decision, ensure_ascii=False))
        cli_args = f"resume {execution_id} --decision {payload}"
        self._run_cli(ssm, instance_id, cli_args)
