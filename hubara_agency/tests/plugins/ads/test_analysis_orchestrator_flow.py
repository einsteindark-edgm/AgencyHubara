"""El flujo del buzón: `launch_and_poll` (despierta la caja por el Launcher port + despacha →
execution-id + resuelve la URL de Conductor + pollea) y `poll_loop` (deriva eventos vía
apply_state, los publica al bus, y para en terminal o emite `failed` por timeout). Con un
Launcher fake y un fetch fake — sin AWS ni red.

El ciclo corre en BACKGROUND y el IO bloqueante (start_box/dispatch/fetch) se delega a
`asyncio.to_thread` para no congelar el event loop; acá lo corremos con `asyncio.run`.
"""
import asyncio

from src.plugins.ads.runs import orchestrator, record


class _FakeLauncher:
    def __init__(self, *, start_raises: Exception | None = None) -> None:
        self.started = False
        self.dispatched: list = []
        self.resumed: list = []
        self._start_raises = start_raises

    def start_box(self) -> None:
        if self._start_raises is not None:
            raise self._start_raises
        self.started = True

    def dispatch(self, agent: str, input: dict, *, run_id: str) -> str:
        self.dispatched.append((agent, input, run_id))
        return "exec-9"

    def resume(self, execution_id: str, decision: dict) -> None:
        self.resumed.append((execution_id, decision))

    def conductor_base_url(self) -> str:
        return "http://box-ip:6767"


class _FakeBus:
    def __init__(self) -> None:
        self.published: list = []

    def publish(self, domain: str, type: str, *, id: str, payload: dict) -> None:
        self.published.append((domain, type, id, payload))


def _seq_fetch(items):
    it = iter(items)

    def _fetch(eid: str, base_url: str, timeout: float = 8) -> dict:
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


def test_launch_and_poll_resuelve_la_url_de_conductor_por_el_launcher() -> None:
    # base_url=None → el ciclo pide la URL FRESCA al launcher (la IP de la caja es dinámica).
    record.create_run("r2", agent="ads-analytics", input={})
    seen: dict = {}

    class _LZ(_FakeLauncher):
        def conductor_base_url(self) -> str:
            seen["asked"] = True
            return "http://box-ip:6767"

    def _fetch(eid: str, base_url: str, timeout: float = 8) -> dict:
        seen["base_url"] = base_url
        return {"status": "COMPLETED", "output": {"result": "{}"}, "tasks": []}

    asyncio.run(orchestrator.launch_and_poll(
        "r2", "ads-analytics", {}, launcher=_LZ(), bus=_FakeBus(), fetch=_fetch, interval=0.0,
    ))

    assert seen.get("asked") is True
    # el poll usó la URL resuelta por el launcher, NO un localhost estático.
    assert seen.get("base_url") == "http://box-ip:6767"


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
    # La caja nunca llega a terminal (siempre RUNNING) → al agotar max_polls, failed (no colgado).
    record.create_run("r4", agent="ads-analytics", input={})
    record.append_event(
        "r4", {"event_id": "r4:started", "type": "run.started", "payload": {"execution_id": "e"}}
    )
    bus = _FakeBus()

    def _always_running(eid: str, base_url: str, timeout: float = 8) -> dict:
        return {"status": "RUNNING", "tasks": []}

    asyncio.run(orchestrator.poll_loop(
        "r4", "e", base_url="http://x:6767", bus=bus, interval=0.0, fetch=_always_running, max_polls=3,
    ))

    rec = record.read_run("r4")
    assert rec["status"] == "failed"
    assert "timeout" in str(rec["error"])
    assert [t for (_, t, _, _) in bus.published] == ["run.failed"]


def test_launch_and_poll_marca_failed_si_algo_falla_tras_started() -> None:
    # B-1: si algo explota DESPUÉS de run.started (p.ej. resolver la IP de Conductor porque la caja
    # autostopeó), el ciclo NO debe morir en silencio dejando el run en `running` — emite run.failed.
    record.create_run("r5", agent="ads-analytics", input={})

    class _LZ(_FakeLauncher):
        def conductor_base_url(self) -> str:
            raise RuntimeError("la caja autostopeó, sin IP")

    bus = _FakeBus()
    asyncio.run(orchestrator.launch_and_poll(
        "r5", "ads-analytics", {}, launcher=_LZ(), bus=bus, fetch=_seq_fetch([]), interval=0.0,
    ))

    rec = record.read_run("r5")
    assert rec["status"] == "failed"  # NO quedó colgado en running
    assert "autostopeó" in str(rec["error"])
    assert [t for (_, t, _, _) in bus.published] == ["run.started", "run.failed"]


def test_poll_loop_tolera_una_respuesta_no_dict_de_conductor() -> None:
    # B-1: si Conductor devuelve algo que no es dict (página de error / lista), `interpret` explotaría;
    # el poll lo trata como transient y reintenta — no tumba el loop ni cuelga el run.
    record.create_run("r6", agent="ads-analytics", input={})
    record.append_event("r6", {"event_id": "r6:s", "type": "run.started", "payload": {"execution_id": "e"}})
    bus = _FakeBus()

    def _non_dict(eid: str, base_url: str, timeout: float = 8):
        return ["not", "a", "dict"]  # interpret().get(...) → AttributeError → transient

    asyncio.run(orchestrator.poll_loop(
        "r6", "e", base_url="http://x:6767", bus=bus, interval=0.0, fetch=_non_dict, max_polls=2,
    ))

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
    # M-1: el resume corre en background (no bloquea /approve); solo manda el comando — el poller
    # (único escritor) relaya la transición. Acá verificamos que el comando llega al launcher.
    lz = _FakeLauncher()
    asyncio.run(orchestrator.resume_run("r8", "exec-9", {"approved": True, "by": "ed"}, launcher=lz))
    assert lz.resumed == [("exec-9", {"approved": True, "by": "ed"})]
