"""Orchestrator del buzón de análisis (plugin ads) — convierte el POLL de Conductor de la caja GraphAgents en eventos del bridge.

`apply_state(run_id, state)` diffea el estado interpretado (`conductor.interpret`) contra el
record y, si cambió, appendea el evento (idempotente por status) y lo devuelve para que el
caller lo publique al bus SSE. `start_run` (el disparo) y el poller (loop async) lo usan.
"""
from __future__ import annotations

from src.plugins.ads.runs import record

#: status lógico (de conductor.interpret) → tipo de evento del bridge.
_EVENT = {
    "running": "run.started",
    "awaiting_approval": "run.awaiting_approval",
    "completed": "run.result",
    "failed": "run.failed",
}


def apply_state(run_id: str, state: dict) -> dict | None:
    """Si el estado interpretado difiere del record, appendea el evento y lo devuelve.
    Sin cambio → `None` (idempotente: no re-emite el mismo status)."""
    rec = record.read_run(run_id)
    if rec is None or rec["status"] == state["status"]:
        return None
    status = state["status"]
    payload: dict = {}
    if status == "awaiting_approval":
        payload = {"context": state.get("awaiting")}
    elif status == "completed":
        payload = {"output": state.get("result")}
    elif status == "failed":
        payload = {"error": state.get("error")}
    event = {"event_id": f"{run_id}:{status}", "type": _EVENT[status], "payload": payload}
    record.append_event(run_id, event)
    return event


def start_run(run_id: str, agent: str, input: dict, *, launcher) -> dict:
    """Dispara un run: crea el record, despierta la caja (Launcher port), despacha a AgentSpan
    (→ execution-id) y deja el record en `running`. El caller arranca el `poll_loop` aparte."""
    record.create_run(run_id, agent=agent, input=input)
    launcher.start_box()
    execution_id = launcher.dispatch(agent, input, run_id=run_id)
    record.append_event(
        run_id,
        {"event_id": f"{run_id}:started", "type": "run.started", "payload": {"execution_id": execution_id}},
    )
    return record.read_run(run_id)


async def poll_loop(run_id, execution_id, *, base_url, bus, interval=2.0, fetch=None, max_polls=900) -> None:
    """Pollea Conductor hasta que el run termina (relay): cada poll interpreta el workflow,
    deriva el evento (`apply_state`) y lo publica al bus SSE. Un poll fallido reintenta (la
    caja puede estar arrancando fría) sin tumbar el loop."""
    import asyncio

    from src.plugins.ads.runs import conductor

    fetch = fetch or conductor.fetch_workflow
    for _ in range(max_polls):
        try:
            wf = fetch(execution_id, base_url)
        except Exception:  # noqa: BLE001 — poll fallido (caja fría / blip de red) → reintenta
            await asyncio.sleep(interval)
            continue
        state = conductor.interpret(wf)
        event = apply_state(run_id, state)
        if event is not None:
            bus.publish("ads", event["type"], id=run_id, payload={"run_id": run_id, **event})
        if state["status"] in ("completed", "failed"):
            return
        await asyncio.sleep(interval)
