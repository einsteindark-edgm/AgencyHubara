"""El flujo del buzón: `start_run` (crea record + despierta la caja por el Launcher port +
despacha → execution-id) y `poll_loop` (pollea Conductor, deriva eventos vía apply_state, los
publica al bus, y para en terminal). Con un Launcher fake y un fetch fake — sin AWS ni red.
"""
import asyncio

from src.plugins.graphagents.runs import orchestrator, record


class _FakeLauncher:
    def __init__(self) -> None:
        self.started = False
        self.dispatched: list = []
        self.resumed: list = []

    def start_box(self) -> None:
        self.started = True

    def dispatch(self, agent: str, input: dict, *, run_id: str) -> str:
        self.dispatched.append((agent, input, run_id))
        return "exec-9"

    def resume(self, execution_id: str, decision: dict) -> None:
        self.resumed.append((execution_id, decision))


class _FakeBus:
    def __init__(self) -> None:
        self.published: list = []

    def publish(self, domain: str, type: str, *, id: str, payload: dict) -> None:
        self.published.append((domain, type, id, payload))


def test_start_run_crea_record_despierta_caja_y_despacha() -> None:
    lz = _FakeLauncher()
    rec = orchestrator.start_run("r1", "greeter", {"name": "ada"}, launcher=lz)
    assert lz.started is True
    assert lz.dispatched == [("greeter", {"name": "ada"}, "r1")]
    assert rec["status"] == "running"
    assert rec["execution_id"] == "exec-9"


def test_poll_loop_relaya_eventos_y_para_en_terminal() -> None:
    record.create_run("r2", agent="a", input={})
    record.append_event("r2", {"event_id": "r2:started", "type": "run.started", "payload": {}})
    bus = _FakeBus()
    seq = iter([
        {"status": "RUNNING", "tasks": [
            {"taskType": "HUMAN", "status": "IN_PROGRESS", "inputData": {"context": {"q": "?"}}}]},
        {"status": "COMPLETED", "output": {"result": "{'n': 5}"}, "tasks": []},
    ])

    def fake_fetch(eid: str, base_url: str, timeout: float = 8) -> dict:
        return next(seq)

    asyncio.run(orchestrator.poll_loop(
        "r2", "exec-9", base_url="http://x:6767", bus=bus, interval=0.0, fetch=fake_fetch,
    ))

    types = [t for (_, t, _, _) in bus.published]
    assert "run.awaiting_approval" in types and "run.result" in types
    assert record.read_run("r2")["status"] == "completed"
    assert record.read_run("r2")["result"] == {"n": 5}
