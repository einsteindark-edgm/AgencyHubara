"""`Boto3Launcher` — el vendor REAL del `Launcher` port (ver `launcher.py`): habla con la caja
GraphAgents (EC2 dedicada, tag `Role=graphagents`) vía boto3 (ec2 + ssm). **TODO va por el plano de
control de AWS (instance-id / SSM)** — el backend NUNCA abre una conexión de red directa a la caja
(ni a su IP privada ni a `:6767`). Así no hace falta abrir el SG entre las cajas ni depender de la
IP dinámica del autostop: el único canal es SSM `send_command` contra el instance-id (resuelto por
tag).

Operaciones del port:
- `start_box()`    — resuelve el instance-id por tag, la arranca si está `stopped`, y espera a que
  AgentSpan esté listo. El readiness corre DENTRO de la caja por SSM (curl a su propio
  `localhost:6767`), no desde el backend. Idempotente.
- `dispatch()`     — SSM `sdk.cli start <agent> --input <json> --runtime agentspan` DENTRO del
  container `graphagents`; parsea el execution-id del stdout.
- `fetch_status()` — SSM `sdk.cli status <eid> --runtime agentspan` → el workflow JSON crudo de
  Conductor (la caja consulta su Conductor LOCAL). Es el POLL del progreso, por SSM.
- `resume()`       — despierta la caja + SSM `sdk.cli resume <eid> --decision <json>`.

Reglas que este adapter respeta (gotchas conocidos del subsistema):
- **Import perezoso de boto3** (dentro de `_clients`, NO al top) → en tests sin AWS el import del
  módulo NO rompe; el `api/analysis.py` lo importa perezoso a su vez.
- **Cero conexión directa a la caja** → ec2/ssm (instance-id por tag) para TODO; ni la IP ni el
  puerto `:6767` se tocan desde el backend (el readiness curlea `localhost` DENTRO de la caja).
- **Graceful sin config** → sin `AWS_REGION` falla con un `RuntimeError` claro AL USARSE (no en el
  import); construir el objeto nunca rompe.
"""
from __future__ import annotations

import json
import re
import time

# platform NO importa src.sdk (import-linter `platform-no-sdk`) — el config
# viene directo de platform.config; el SDK re-exporta este vendor en
# `src.sdk.graphagentskit`.
from src.platform.config import AWS_REGION as AWS_REGION
from src.platform.config import GRAPHAGENTS_INSTANCE_TAG as GRAPHAGENTS_INSTANCE_TAG

#: Puerto de AgentSpan/Conductor en la caja — usado SOLO por el chequeo de readiness, que corre
#: DENTRO de la caja (curl a su `localhost`), nunca desde el backend.
_AGENTSPAN_PORT = 6767

#: Compose project + service de la caja graphagents (infra/compose/graphagents/docker-compose.prod.yml:
#: `name: graphagents-prod`, service `graphagents`) → el `sdk.cli` corre DENTRO de ese container.
_COMPOSE_PROJECT = "graphagents-prod"
_COMPOSE_SERVICE = "graphagents"

#: `python3 -m sdk.cli ...` ejecutado en el container graphagents vía docker compose exec (-T:
#: sin TTY, requerido bajo SSM). El compose vive en /opt/graphagents en la caja (cloud-init).
#: Python del VENV del container, explícito (incidente 2026-07-09, rollout del
#: Window Strategist): la imagen instala deps en /opt/venv (UV_PROJECT_ENVIRONMENT)
#: pero NO exporta ese bin en PATH → el `python3` pelado es el del sistema y
#: `python3 -m sdk.cli` muere con ModuleNotFoundError: yaml. Verificado contra la
#: caja real por SSM (`which python3` → /usr/local/bin/python3).
_VENV_PYTHON = "/opt/venv/bin/python"

_DOCKER_EXEC = (
    f"docker compose -p {_COMPOSE_PROJECT} exec -T {_COMPOSE_SERVICE} {_VENV_PYTHON} -m sdk.cli"
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

    def _instance_id(self, ec2) -> str:
        return self._describe(ec2)["InstanceId"]

    def start_box(self) -> None:
        """Despierta la caja y espera a que AgentSpan esté listo. Idempotente: si ya corre, solo
        verifica readiness; si está `stopped`, la arranca y espera `running` antes del readiness.
        El readiness corre DENTRO de la caja por SSM — NO hay conexión directa desde el backend.

        Dos esperas que NO son opcionales (caso real 2026-07-09, arranque frío post-autostop):
        - `stopping` → esperar `instance_stopped` antes de `start_instances` (si no,
          IncorrectInstanceState).
        - EC2 `running` ≠ agente SSM registrado: `send_command` contra una caja recién arrancada
          rechaza con InvalidInstanceId hasta que el agente pinguea `Online` → se pollea
          `describe_instance_information` antes del primer comando."""
        ec2, ssm = self._clients()
        instance = self._describe(ec2)
        instance_id = instance["InstanceId"]
        state = (instance.get("State") or {}).get("Name")

        if state == "stopping":
            ec2.get_waiter("instance_stopped").wait(InstanceIds=[instance_id])
            state = "stopped"
        if state == "stopped":
            ec2.start_instances(InstanceIds=[instance_id])
            ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])

        self._wait_ssm_online(ssm, instance_id)
        self._wait_ready(ssm, instance_id)

    def _wait_ssm_online(
        self, ssm, instance_id: str, *, timeout: float = 240.0, interval: float = 3.0
    ) -> None:
        """Espera a que el AGENTE SSM de la caja esté `Online` (registrado en el plano de
        control). EC2 `running` no alcanza: tras un arranque frío el agente tarda ~decenas de
        segundos en registrarse y todo `send_command` previo muere con InvalidInstanceId."""
        deadline = time.monotonic() + timeout
        while True:
            resp = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            infos = resp.get("InstanceInformationList", [])
            if any(info.get("PingStatus") == "Online" for info in infos):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Boto3Launcher: el agente SSM de {instance_id} no llegó a Online tras "
                    f"{timeout:.0f}s — la caja corre pero SSM no puede hablarle (revisar el "
                    f"agente SSM / el instance profile de la caja graphagents)."
                )
            time.sleep(interval)

    def _wait_ready(self, ssm, instance_id: str) -> None:
        """Espera a que AgentSpan responda — por SSM, corriendo el chequeo DENTRO de la caja (curl a
        su propio `localhost:6767`), NUNCA conectándose desde el backend. AgentSpan tarda en levantar
        la JVM tras un arranque frío; el loop reintenta ~3 min y falla LOUD (`exit 1`) si no levanta."""
        loop = (
            f"for i in $(seq 1 60); do "
            f"curl -fsS -o /dev/null http://localhost:{_AGENTSPAN_PORT}/ && {{ echo READY; exit 0; }}; "
            f"sleep 3; done; echo NOTREADY; exit 1"
        )
        # `_send` falla LOUD si el comando SSM no termina en Success (NOTREADY → exit 1 → Failed).
        self._send(ssm, instance_id, loop)

    # ------------------------------------------------------------ SSM / run

    def _send(
        self, ssm, instance_id: str, command: str, *, send_timeout: float = 90.0,
        send_interval: float = 3.0,
    ) -> str:
        """Manda un comando shell por SSM (`AWS-RunShellScript`), pollea hasta terminal y devuelve el
        stdout. Falla LOUD si no termina en `Success`. Es el ÚNICO canal hacia la caja (instance-id),
        nunca una conexión de red directa.

        Un `InvalidInstanceId` transitorio (el agente SSM recién registrado, el API aún
        propagando) se REINTENTA hasta `send_timeout` — cinturón además del `_wait_ssm_online`
        de `start_box` (caso real 2026-07-09). Cualquier otro error propaga intacto."""
        deadline = time.monotonic() + send_timeout
        while True:
            try:
                resp = ssm.send_command(
                    InstanceIds=[instance_id],
                    DocumentName="AWS-RunShellScript",
                    Parameters={"commands": [command]},
                )
                break
            except Exception as exc:  # noqa: BLE001 — filtramos por código; el resto propaga
                if "InvalidInstanceId" not in str(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Boto3Launcher: SSM siguió rechazando send_command con InvalidInstanceId "
                        f"tras {send_timeout:.0f}s — el agente SSM de {instance_id} no está "
                        f"disponible (¿caja recién arrancada o agente caído?)."
                    ) from exc
                time.sleep(send_interval)
        return self._await_invocation(ssm, resp["Command"]["CommandId"], instance_id)

    def _run_cli(self, ssm, instance_id: str, cli_args: str) -> str:
        """`sdk.cli <cli_args>` DENTRO del container graphagents, por SSM."""
        return self._send(ssm, instance_id, f"{_DOCKER_EXEC} {cli_args}")

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
        instance_id = self._instance_id(ec2)
        payload = _shell_quote(json.dumps(input, ensure_ascii=False))
        cli_args = f"start {_shell_quote(agent)} --input {payload} --runtime agentspan"
        stdout = self._run_cli(ssm, instance_id, cli_args)
        match = _EID_RE.search(stdout)
        if not match:
            raise RuntimeError(
                f"Boto3Launcher: no pude parsear el execution-id del stdout de `sdk.cli start` "
                f"(esperaba `execution <id>: <status>`). Stdout: {stdout!r}"
            )
        return match.group(1)

    def fetch_status(self, execution_id: str) -> dict:
        """El workflow JSON crudo de Conductor (con `tasks[]`) — leído por SSM corriendo
        `sdk.cli status <eid>` DENTRO de la caja (que consulta su Conductor LOCAL). Es el POLL del
        progreso: va por SSM/instance-id como el dispatch, SIN ninguna conexión directa a la caja."""
        ec2, ssm = self._clients()
        instance_id = self._instance_id(ec2)
        cli_args = f"status {_shell_quote(execution_id)} --runtime agentspan"
        stdout = self._run_cli(ssm, instance_id, cli_args)
        try:
            return json.loads(stdout.strip())
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                f"Boto3Launcher: el stdout de `sdk.cli status` no es JSON parseable: {stdout!r}"
            ) from exc

    def resume(self, execution_id: str, decision: dict) -> None:
        """Completa la HUMAN task: despierta la caja (puede estar dormida por autostop) y manda el
        `sdk.cli resume`."""
        self.start_box()
        ec2, ssm = self._clients()
        instance_id = self._instance_id(ec2)
        payload = _shell_quote(json.dumps(decision, ensure_ascii=False))
        cli_args = f"resume {_shell_quote(execution_id)} --decision {payload}"
        self._run_cli(ssm, instance_id, cli_args)
