"""El flujo del buzón: `launch_and_poll` (despierta la caja por el Launcher port + despacha →
execution-id + pollea) y `poll_loop` (deriva eventos vía apply_state, los publica al bus, y para en
terminal o emite `failed` por timeout). Con un Launcher fake y un fetch fake — sin AWS ni red.

El POLL del estado va por `launcher.fetch_status` (en prod, SSM `sdk.cli status` DENTRO de la caja):
el backend NUNCA se conecta directo a la caja. El ciclo corre en background y el IO bloqueante se
delega a `asyncio.to_thread`; acá lo corremos con `asyncio.run`.
"""
import asyncio

from src.plugins.ads.runs import orchestrator, record


class _FakeLauncher:
    def __init__(self, *, start_raises: Exception | None = None, status_workflow: dict | None = None) -> None:
        self.started = False
        self.dispatched: list = []
        self.resumed: list = []
        self._start_raises = start_raises
        self._status_workflow = status_workflow or {"status": "COMPLETED", "output": {"result": "{}"}, "tasks": []}

    def start_box(self) -> None:
        if self._start_raises is not None:
            raise self._start_raises
        self.started = True

    def dispatch(self, agent: str, input: dict, *, run_id: str) -> str:
        self.dispatched.append((agent, input, run_id))
        return "exec-9"

    def resume(self, execution_id: str, decision: dict) -> None:
        self.resumed.append((execution_id, decision))

    def fetch_status(self, execution_id: str) -> dict:
        # El poll por SSM (en prod corre `sdk.cli status` DENTRO de la caja); acá un workflow fijo.
        return self._status_workflow


class _FakeBus:
    def __init__(self) -> None:
        self.published: list = []

    def publish(self, domain: str, type: str, *, id: str, payload: dict) -> None:
        self.published.append((domain, type, id, payload))


def _seq_fetch(items):
    it = iter(items)

    def _fetch(execution_id: str) -> dict:  # firma del fetch: SOLO el execution-id (la URL/SSM la maneja el launcher)
        return next(it)

    return _fetch


def test_launch_and_poll_despierta_despacha_y_relaya() -> None:
    record.create_run("r1", agent="ads-analytics", input={"x": 1})
    lz = _FakeLauncher()
    bus = _FakeBus()
    fetch = _seq_fetch([
        {"status": "RUNNING", "tasks": [
            {"taskType": "HUMAN", "status": "IN_PROGRESS", "inputData": {"context": {"q": "?"}}}]},
        {"status": "COMPLETED", "output": {"result": "{'n': 5}"}, "tasks": []},
    ])

    asyncio.run(orchestrator.launch_and_poll(
        "r1", "ads-analytics", {"x": 1}, launcher=lz, bus=bus, fetch=fetch, interval=0.0,
    ))

    assert lz.started is True
    assert lz.dispatched == [("ads-analytics", {"x": 1}, "r1")]
    types = [t for (_, t, _, _) in bus.published]
    assert "run.started" in types and "run.awaiting_approval" in types and "run.result" in types
    rec = record.read_run("r1")
    assert rec["status"] == "completed"
    assert rec["execution_id"] == "exec-9"
    assert rec["result"] == {"n": 5}


def test_launch_and_poll_pollea_por_el_launcher_sin_fetch_inyectado() -> None:
    # Sin `fetch` inyectado, el ciclo pollea vía `launcher.fetch_status` (por SSM, sin conexión directa).
    record.create_run("r2", agent="ads-analytics", input={})
    lz = _FakeLauncher(status_workflow={"status": "COMPLETED", "output": {"result": "{'ok': 1}"}, "tasks": []})
    asyncio.run(orchestrator.launch_and_poll("r2", "ads-analytics", {}, launcher=lz, bus=_FakeBus(), interval=0.0))
    rec = record.read_run("r2")
    assert rec["status"] == "completed"
    assert rec["result"] == {"ok": 1}  # vino de launcher.fetch_status, no de un fetch HTTP


def test_launch_and_poll_marca_failed_si_la_caja_no_despierta() -> None:
    record.create_run("r3", agent="ads-analytics", input={})
    lz = _FakeLauncher(start_raises=RuntimeError("AgentSpan no respondió"))
    bus = _FakeBus()
    asyncio.run(orchestrator.launch_and_poll(
        "r3", "ads-analytics", {}, launcher=lz, bus=bus, fetch=_seq_fetch([]), interval=0.0,
    ))
    assert lz.dispatched == []  # nunca despachó
    rec = record.read_run("r3")
    assert rec["status"] == "failed"
    assert "AgentSpan" in str(rec["error"])
    assert [t for (_, t, _, _) in bus.published] == ["run.failed"]


def test_poll_loop_timeout_marca_failed() -> None:
    record.create_run("r4", agent="ads-analytics", input={})
    record.append_event("r4", {"event_id": "r4:started", "type": "run.started", "payload": {"execution_id": "e"}})
    bus = _FakeBus()

    def _always_running(execution_id: str) -> dict:
        return {"status": "RUNNING", "tasks": []}

    asyncio.run(orchestrator.poll_loop("r4", "e", bus=bus, interval=0.0, fetch=_always_running, max_polls=3))
    rec = record.read_run("r4")
    assert rec["status"] == "failed"
    assert "timeout" in str(rec["error"])
    assert [t for (_, t, _, _) in bus.published] == ["run.failed"]


def test_launch_and_poll_no_deja_el_run_colgado_si_el_poll_siempre_falla() -> None:
    # B-1: tras run.started, si el fetch (SSM) falla SIEMPRE, el poll reintenta y al agotar max_polls
    # marca failed — el run NUNCA queda colgado en `running`.
    record.create_run("r5", agent="ads-analytics", input={})

    def _boom(execution_id: str) -> dict:
        raise RuntimeError("fetch_status (SSM) reventó")

    bus = _FakeBus()
    asyncio.run(orchestrator.launch_and_poll(
        "r5", "ads-analytics", {}, launcher=_FakeLauncher(), bus=bus, fetch=_boom, interval=0.0, max_polls=3,
    ))
    rec = record.read_run("r5")
    assert rec["status"] == "failed"
    types = [t for (_, t, _, _) in bus.published]
    assert types[0] == "run.started" and types[-1] == "run.failed"


def test_poll_loop_tolera_una_respuesta_no_dict_de_conductor() -> None:
    # B-1: si Conductor devuelve algo que no es dict (página de error / lista), `interpret` explotaría;
    # el poll lo trata como transient y reintenta — no tumba el loop ni cuelga el run.
    record.create_run("r6", agent="ads-analytics", input={})
    record.append_event("r6", {"event_id": "r6:s", "type": "run.started", "payload": {"execution_id": "e"}})
    bus = _FakeBus()

    def _non_dict(execution_id: str):
        return ["not", "a", "dict"]  # interpret().get(...) → AttributeError → transient

    asyncio.run(orchestrator.poll_loop("r6", "e", bus=bus, interval=0.0, fetch=_non_dict, max_polls=2))
    rec = record.read_run("r6")
    assert rec["status"] == "failed"  # agotó max_polls sin terminal → timeout failed (no crash)
    assert "timeout" in str(rec["error"])


def test_apply_state_captura_un_segundo_awaiting_distinto() -> None:
    # H-1: un agente multi-gate pausa, resume, y pausa OTRA vez. El 2º awaiting NO debe perderse por
    # un event_id keyed-por-status (sería el mismo id que el 1º y lo dropearía la dedup de append_event).
    record.create_run("r7", agent="ads-analytics", input={})
    record.append_event("r7", {"event_id": "r7:s", "type": "run.started", "payload": {"execution_id": "e"}})
    base = {"result": None, "error": None}
    orchestrator.apply_state("r7", {"status": "awaiting_approval", "awaiting": {"gate": 1}, **base})
    orchestrator.apply_state("r7", {"status": "running", "awaiting": None, **base})
    ev = orchestrator.apply_state("r7", {"status": "awaiting_approval", "awaiting": {"gate": 2}, **base})

    assert ev is not None  # el 2º awaiting SÍ se emitió (no se dropeó)
    rec = record.read_run("r7")
    assert rec["status"] == "awaiting_approval"
    assert rec["awaiting"] == {"gate": 2}  # el contexto NUEVO, no el viejo


def test_resume_run_manda_el_resume_a_la_caja() -> None:
    # M-1: el resume corre en background (no bloquea /approve); solo manda el comando — el poller relaya.
    lz = _FakeLauncher()
    asyncio.run(orchestrator.resume_run("r8", "exec-9", {"approved": True, "by": "ed"}, launcher=lz))
    assert lz.resumed == [("exec-9", {"approved": True, "by": "ed"})]
