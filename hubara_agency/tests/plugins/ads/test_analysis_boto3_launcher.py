"""El adapter REAL del Launcher port — `Boto3Launcher` (ec2 + ssm) contra la caja
GraphAgents (tag `Role=graphagents`). Acá con boto3 MOCKEADO (fakes inyectados por el
`_clients` factory que el módulo expone): sin AWS, sin red. Aserta el contrato del port:

- `start_box()` resuelve el instance-id por tag, arranca SOLO si está `stopped`, y espera
  healthy (poll HTTP a `:6767`). Idempotente.
- `dispatch()` manda el SSM send_command con el comando `sdk.cli start ... --runtime agentspan`
  y parsea el execution-id del stdout (`execution <id>: <status>`).
- `resume()` despierta la caja + manda el comando de resume.

El import de `boto3` es perezoso (dentro de los métodos) → este import del módulo NO rompe
sin boto3/AWS. Por eso el test puede importar el adapter siempre.
"""
from __future__ import annotations

import json

import pytest

from src.platform.graphagents import boto3_launcher


# ---------------------------------------------------------------- fakes de AWS

class _FakeEC2:
    """ec2 fake: describe_instances filtra por el tag pedido y devuelve un estado fijo.
    `start_instances` y el waiter se registran para asertar."""

    def __init__(self, *, instance_id: str = "i-abc", state: str = "running",
                 private_ip: str = "10.0.0.5") -> None:
        self._instance_id = instance_id
        self._state = state
        self._private_ip = private_ip
        self.described: list = []
        self.started: list = []
        self.waited: list = []

    def describe_instances(self, *, Filters):  # noqa: N803 — boto3 kwarg
        self.described.append(Filters)
        return {
            "Reservations": [
                {"Instances": [{
                    "InstanceId": self._instance_id,
                    "State": {"Name": self._state},
                    "PrivateIpAddress": self._private_ip,
                }]}
            ]
        }

    def start_instances(self, *, InstanceIds):  # noqa: N803
        self.started.append(InstanceIds)
        self._state = "running"

    def get_waiter(self, name):
        fake = self

        class _Waiter:
            def wait(self, *, InstanceIds, **_):  # noqa: N803
                fake.waited.append((name, InstanceIds))

        return _Waiter()


class _FakeSSM:
    """ssm fake: send_command devuelve un command-id; get_command_invocation devuelve un
    output configurable (status + stdout). Registra cada comando enviado.

    `ping_sequence` simula el registro del agente SSM tras un arranque frío (la secuencia
    de PingStatus que devuelve `describe_instance_information`; el último valor se repite).
    `send_failures` simula el race real (run fe86d4e4 / reporte 2026-07-09): send_command
    rechaza con InvalidInstanceId las primeras N veces aunque EC2 ya reporte `running`.
    `self.calls` es el log ORDENADO de interacciones (para asertar ping-antes-de-send)."""

    def __init__(self, *, stdout: str = "execution exec-42: RUNNING",
                 status: str = "Success", ping_sequence: list[str] | None = None,
                 send_failures: int = 0) -> None:
        self._stdout = stdout
        self._status = status
        self._pings = list(ping_sequence) if ping_sequence else ["Online"]
        self._send_failures = send_failures
        self.sent: list = []
        self.calls: list[str] = []

    def describe_instance_information(self, *, Filters):  # noqa: N803
        ping = self._pings.pop(0) if len(self._pings) > 1 else self._pings[0]
        self.calls.append(f"ping:{ping}")
        if ping == "Absent":  # el agente ni registrado aún → lista vacía
            return {"InstanceInformationList": []}
        iid = Filters[0]["Values"][0]
        return {"InstanceInformationList": [{"InstanceId": iid, "PingStatus": ping}]}

    def send_command(self, *, InstanceIds, DocumentName, Parameters):  # noqa: N803
        if self._send_failures > 0:
            self._send_failures -= 1
            self.calls.append("send:rejected")
            raise Exception(  # noqa: TRY002 — mismo texto del ClientError real de botocore
                "An error occurred (InvalidInstanceId) when calling the SendCommand "
                "operation: Instances not in a valid state for account"
            )
        self.calls.append("send:ok")
        self.sent.append({
            "InstanceIds": InstanceIds,
            "DocumentName": DocumentName,
            "Parameters": Parameters,
        })
        return {"Command": {"CommandId": "cmd-1"}}

    def get_command_invocation(self, *, CommandId, InstanceId):  # noqa: N803
        return {
            "Status": self._status,
            "StandardOutputContent": self._stdout,
            "StandardErrorContent": "",
        }


@pytest.fixture
def fake_clients(monkeypatch):
    """Inyecta fakes ec2/ssm vía el factory `_clients` (lo que el adapter llama en vez de
    `boto3.client(...)`). Devuelve un setter para que cada test configure el par que quiere."""
    state = {"ec2": _FakeEC2(), "ssm": _FakeSSM()}

    def _factory(self):
        return state["ec2"], state["ssm"]

    monkeypatch.setattr(boto3_launcher.Boto3Launcher, "_clients", _factory, raising=True)
    # El readiness (`_wait_ready`) corre por SSM contra el FakeSSM (Success) — sin red, sin patch HTTP.
    return state


def _make(state, **kw):
    """Helper: setea el par ec2/ssm del estado inyectado y devuelve el launcher."""
    if "ec2" in kw:
        state["ec2"] = kw["ec2"]
    if "ssm" in kw:
        state["ssm"] = kw["ssm"]
    return boto3_launcher.Boto3Launcher(region="us-east-1", instance_tag="graphagents")


# ----------------------------------------------------------------- start_box

def test_module_importa_sin_boto3_ni_aws() -> None:
    """El import del módulo NO debe requerir boto3 (import perezoso dentro de los métodos)."""
    assert hasattr(boto3_launcher, "Boto3Launcher")


def test_start_box_resuelve_por_tag(fake_clients) -> None:
    ec2 = _FakeEC2(state="running")
    lz = _make(fake_clients, ec2=ec2)
    lz.start_box()
    # Resolvió por el tag Role=graphagents (no hardcodeó IP).
    flat = json.dumps(ec2.described)
    assert "tag:Role" in flat and "graphagents" in flat


def test_start_box_no_arranca_si_ya_corre(fake_clients) -> None:
    ec2 = _FakeEC2(state="running")
    lz = _make(fake_clients, ec2=ec2)
    lz.start_box()
    assert ec2.started == []  # idempotente: ya corre → no start_instances


def test_start_box_arranca_y_espera_si_stopped(fake_clients) -> None:
    ec2 = _FakeEC2(instance_id="i-xyz", state="stopped")
    lz = _make(fake_clients, ec2=ec2)
    lz.start_box()
    assert ec2.started == [["i-xyz"]]  # arrancó la instancia stopped
    assert any(name == "instance_running" for (name, _) in ec2.waited)  # esperó running


def test_start_box_espera_ssm_online_antes_de_mandar_comandos(fake_clients, monkeypatch) -> None:
    """EC2 `running` ≠ agente SSM `Online`: tras un arranque frío (autostop) el agente tarda
    en registrarse y `send_command` rechaza con InvalidInstanceId (caso real 2026-07-09, la
    caja lanzó 22:05 UTC y el comando salió antes del registro). `start_box` debe POLLEAR
    `describe_instance_information` hasta PingStatus=Online ANTES del primer send_command."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    ec2 = _FakeEC2(instance_id="i-xyz", state="stopped")
    ssm = _FakeSSM(status="Success", stdout="READY",
                   ping_sequence=["Absent", "ConnectionLost", "Online"])
    lz = _make(fake_clients, ec2=ec2, ssm=ssm)

    lz.start_box()

    assert "ping:Online" in ssm.calls  # esperó el registro del agente SSM
    first_send = ssm.calls.index("send:ok")
    assert first_send > ssm.calls.index("ping:Online")  # ningún comando antes de Online


def test_send_command_reintenta_invalid_instance_id_transitorio(fake_clients, monkeypatch) -> None:
    """Cinturón y tiradores: si aún así send_command rechaza con InvalidInstanceId (el agente
    se registró y el API de SSM todavía propaga), se REINTENTA en vez de tumbar el run."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    ec2 = _FakeEC2(state="running")
    ssm = _FakeSSM(status="Success", stdout="READY", send_failures=2)
    lz = _make(fake_clients, ec2=ec2, ssm=ssm)

    lz.start_box()  # no revienta: reintenta hasta que SSM acepta

    assert ssm.calls.count("send:rejected") == 2
    assert "send:ok" in ssm.calls


def test_start_box_espera_stopped_antes_de_arrancar_si_stopping(fake_clients) -> None:
    """`start_instances` sobre una caja `stopping` (autostop a mitad de camino) es
    IncorrectInstanceState: hay que esperar `instance_stopped` ANTES de arrancarla."""
    ec2 = _FakeEC2(instance_id="i-xyz", state="stopping")
    lz = _make(fake_clients, ec2=ec2)

    lz.start_box()

    names = [name for (name, _) in ec2.waited]
    assert "instance_stopped" in names  # esperó el fin del stop
    assert names.index("instance_stopped") < names.index("instance_running")
    assert ec2.started == [["i-xyz"]]


# ------------------------------------------------------------------ dispatch

def test_dispatch_manda_el_comando_correcto_y_parsea_el_eid(fake_clients) -> None:
    ec2 = _FakeEC2(state="running")
    ssm = _FakeSSM(stdout="execution exec-42: RUNNING")
    lz = _make(fake_clients, ec2=ec2, ssm=ssm)

    eid = lz.dispatch("ads-analytics", {"name": "ada"}, run_id="run-1")

    assert eid == "exec-42"  # parseó del stdout `execution <id>: <status>`
    assert len(ssm.sent) == 1
    cmd = ssm.sent[0]
    assert cmd["DocumentName"] == "AWS-RunShellScript"
    assert cmd["InstanceIds"] == ["i-abc"]
    script = " ".join(cmd["Parameters"]["commands"])
    assert "sdk.cli start 'ads-analytics'" in script
    assert "--runtime agentspan" in script
    # El input viaja como JSON.
    assert json.dumps({"name": "ada"}) in script


def test_dispatch_falla_loud_si_el_comando_no_tiene_success(fake_clients) -> None:
    ec2 = _FakeEC2(state="running")
    ssm = _FakeSSM(status="Failed", stdout="")
    lz = _make(fake_clients, ec2=ec2, ssm=ssm)
    with pytest.raises(RuntimeError):
        lz.dispatch("ads-analytics", {}, run_id="run-2")


def test_dispatch_falla_loud_si_no_puede_parsear_eid(fake_clients) -> None:
    ec2 = _FakeEC2(state="running")
    ssm = _FakeSSM(status="Success", stdout="todo bien pero sin id")
    lz = _make(fake_clients, ec2=ec2, ssm=ssm)
    with pytest.raises(RuntimeError):
        lz.dispatch("ads-analytics", {}, run_id="run-3")


# -------------------------------------------------------------------- resume

def test_resume_despierta_la_caja_y_manda_el_comando(fake_clients) -> None:
    ec2 = _FakeEC2(state="running")
    ssm = _FakeSSM(status="Success", stdout="resumed")
    lz = _make(fake_clients, ec2=ec2, ssm=ssm)

    lz.resume("exec-42", {"approved": True})

    assert ec2.described  # start_box() corrió (despertó la caja, readiness por SSM)
    # 2 comandos SSM: el readiness (de start_box) + el resume. El resume es el último.
    assert len(ssm.sent) == 2
    script = " ".join(ssm.sent[-1]["Parameters"]["commands"])
    assert "sdk.cli resume 'exec-42'" in script
    assert "--decision" in script
    assert json.dumps({"approved": True}) in script


# ------------------------------------------------------ config / graceful fail

def test_sin_region_falla_claro_no_en_import(monkeypatch) -> None:
    """Sin region configurada, el adapter falla con un error claro al USARSE (no al importar)."""
    monkeypatch.setattr(boto3_launcher, "_default_region", lambda: "", raising=True)
    lz = boto3_launcher.Boto3Launcher()  # construir NO debe romper
    with pytest.raises(RuntimeError, match="AWS_REGION"):
        lz.start_box()


# --------------------------------------- status (poll por SSM) / readiness / seguridad

def test_fetch_status_lee_el_workflow_por_ssm(fake_clients) -> None:
    """El POLL del progreso va por SSM (`sdk.cli status`), NO una conexión directa a la caja: corre el
    comando DENTRO de la caja (por instance-id) y parsea el workflow JSON del stdout."""
    import json as _json

    ec2 = _FakeEC2(state="running")
    wf = {"workflowId": "w1", "status": "RUNNING", "tasks": [{"taskType": "HUMAN", "status": "IN_PROGRESS"}]}
    ssm = _FakeSSM(status="Success", stdout=_json.dumps(wf))
    lz = _make(fake_clients, ec2=ec2, ssm=ssm)

    got = lz.fetch_status("exec-42")

    assert got == wf  # parseó el JSON del stdout de `sdk.cli status`
    script = " ".join(ssm.sent[0]["Parameters"]["commands"])
    assert "sdk.cli status 'exec-42'" in script  # por SSM, quoteado
    assert ssm.sent[0]["InstanceIds"] == ["i-abc"]  # por instance-id (no IP)


def test_start_box_readiness_corre_dentro_de_la_caja_por_ssm(fake_clients) -> None:
    """El readiness NO es un curl directo desde el backend: es un comando SSM que curlea el
    `localhost:6767` DE LA CAJA (cero conexión directa, cero dependencia de la IP privada)."""
    ec2 = _FakeEC2(state="running")
    ssm = _FakeSSM(status="Success", stdout="READY")
    lz = _make(fake_clients, ec2=ec2, ssm=ssm)

    lz.start_box()

    assert len(ssm.sent) == 1  # un solo comando SSM (el readiness)
    script = " ".join(ssm.sent[0]["Parameters"]["commands"])
    assert f"localhost:{boto3_launcher._AGENTSPAN_PORT}" in script  # curlea SU PROPIO localhost
    assert ssm.sent[0]["InstanceIds"] == ["i-abc"]


def test_dispatch_quotea_el_agente_contra_inyeccion(fake_clients) -> None:
    """El `agent` va SINGLE-QUOTED en el comando SSM → un `;` no corta el comando en la caja."""
    ec2 = _FakeEC2(state="running")
    ssm = _FakeSSM(stdout="execution exec-1: RUNNING")
    lz = _make(fake_clients, ec2=ec2, ssm=ssm)
    lz.dispatch("ads-analytics; rm -rf /", {}, run_id="run-x")
    script = " ".join(ssm.sent[0]["Parameters"]["commands"])
    assert "'ads-analytics; rm -rf /'" in script  # quoteado entero
    assert "start ads-analytics; rm -rf /" not in script  # NO inyectado crudo
