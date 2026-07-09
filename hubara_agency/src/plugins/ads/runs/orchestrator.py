"""Orchestrator del buzón de análisis (plugin ads) — convierte el POLL de Conductor de la caja GraphAgents en eventos del bridge.

`apply_state(run_id, state)` diffea el estado interpretado (`conductor.interpret`) contra el
record y, si cambió, appendea el evento (idempotente por status) y lo devuelve para que el
caller lo publique al bus SSE.

`launch_and_poll` es el ciclo de vida COMPLETO de un run, corriendo en BACKGROUND (un asyncio
task) para NO bloquear el event loop del API: despierta la caja, despacha, y pollea hasta
terminal. Todo el IO bloqueante (boto3 + SSM + `time.sleep` + urllib) se delega a un thread
(`asyncio.to_thread`) — el proceso uvicorn es UNO solo y sirve TODO el dashboard, así que un
`start_box()` de ~1-3 min (cold start) NO puede congelar las demás requests ni el SSE.
"""
from __future__ import annotations

import asyncio
import logging

from src.plugins.ads.runs import record

_log = logging.getLogger(__name__)

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
    # event_id MONOTÓNICO por evento (el índice en el log), NO keyed por status: un status que
    # se REPITE (p.ej. un 2º `awaiting_approval` en un agente multi-gate) tendría el mismo id y lo
    # dropearía la dedup de `append_event` → el run se colgaría. El índice lo hace único por ciclo.
    event = {"event_id": f"{run_id}:{len(rec['events'])}:{status}", "type": _EVENT[status], "payload": payload}
    record.append_event(run_id, event)
    return event


def _emit(bus, run_id: str, event: dict) -> None:
    """Publica un evento del bridge al bus SSE (dominio `ads`), filtrable por `run_id`."""
    bus.publish("ads", event["type"], id=run_id, payload={"run_id": run_id, **event})


async def launch_and_poll(
    run_id: str,
    agent: str,
    input: dict,
    *,
    launcher,
    bus,
    fetch=None,
    interval: float = 2.0,
    max_polls: int = 900,
) -> None:
    """Ciclo de vida completo de un run, en background y SIN bloquear el event loop.

    El record ya existe en `pending` (lo creó el endpoint con un write de vault rápido). Acá:
    1. Despierta la caja (`start_box`) y despacha (`dispatch`) — AMBOS bloqueantes → a un thread.
    2. Si la caja no despierta / el dispatch falla → `run.failed` (NO deja el record en `pending`
       para siempre; la UI muestra el error).
    3. Pollea hasta terminal (`poll_loop`); el fetch del estado va por SSM (`launcher.fetch_status`),
       SIN conexión directa a la caja.
    """
    try:
        await asyncio.to_thread(launcher.start_box)
        execution_id = await asyncio.to_thread(launcher.dispatch, agent, input, run_id=run_id)
        started = {
            "event_id": f"{run_id}:started",
            "type": "run.started",
            "payload": {"execution_id": execution_id},
        }
        record.append_event(run_id, started)
        _emit(bus, run_id, started)
        await poll_loop(
            run_id, execution_id, bus=bus, fetch=fetch or launcher.fetch_status,
            interval=interval, max_polls=max_polls,
        )
    except Exception as exc:  # noqa: BLE001 — CUALQUIER fallo del ciclo (caja, dispatch, resolver IP, poll) → failed
        # Sin este wrap, una excepción tras `run.started` (p.ej. el fetch del estado por SSM falla, o
        # Conductor devolvió algo no-dict) moriría en silencio en el task de background
        # (el GC reapea el task) → el run quedaría en `running` PARA SIEMPRE, sin evento terminal.
        ev = {"event_id": f"{run_id}:failed", "type": "run.failed", "payload": {"error": str(exc)}}
        record.append_event(run_id, ev)
        _emit(bus, run_id, ev)


async def resume_run(run_id: str, execution_id: str, decision: dict, *, launcher) -> None:
    """Manda el resume del HITL en BACKGROUND (no bloquea el request `/approve`): la caja puede haber
    autostopeado durante la espera humana → `resume` la despierta (bloqueante) en un thread. El
    POLLER de `launch_and_poll` (ÚNICO escritor del record) relaya la transición awaiting→running;
    acá NO escribimos el record para no competir con él (un doble-escritor re-mostraría el HITL por
    un race con un poll stale). Un resume fallido se loguea — el poller lo cubre por timeout."""
    try:
        await asyncio.to_thread(launcher.resume, execution_id, decision)
    except Exception:  # noqa: BLE001 — el poller marcará el run failed por timeout si esto no prospera
        _log.exception("resume del run %s (execution %s) falló", run_id, execution_id)


async def poll_loop(run_id, execution_id, *, bus, fetch, interval=2.0, max_polls=900) -> None:
    """Pollea Conductor hasta que el run termina (relay): cada poll trae el workflow vía `fetch`
    (`launcher.fetch_status`, por SSM — SIN conexión directa a la caja), `interpret`a el estado,
    deriva el evento (`apply_state`) y lo publica al bus SSE. El `fetch` (SSM, BLOQUEANTE) va a un
    thread (`asyncio.to_thread`) — no congela el loop. Un poll fallido reintenta (la caja puede
    estar arrancando fría) sin tumbar el loop. Si se agota `max_polls` SIN llegar a terminal, emite
    `run.failed` (timeout) para NO dejar la UI colgada en `running` para siempre."""
    from src.sdk import graphagentskit as conductor  # bridge promovido (WS-B0)

    for _ in range(max_polls):
        try:
            wf = await asyncio.to_thread(fetch, execution_id)
            state = conductor.interpret(wf)
        except Exception:  # noqa: BLE001 — poll/interpret fallido (caja fría, respuesta no-dict, blip) → reintenta
            await asyncio.sleep(interval)
            continue
        event = apply_state(run_id, state)
        if event is not None:
            _emit(bus, run_id, event)
        if state["status"] in ("completed", "failed"):
            return
        await asyncio.sleep(interval)

    # max_polls agotado sin terminal → timeout: marca failed (la UI no se cuelga en `running`).
    timeout = apply_state(
        run_id,
        {
            "status": "failed",
            "awaiting": None,
            "result": None,
            "error": f"timeout: el run no alcanzó un estado terminal tras {max_polls} polls",
        },
    )
    if timeout is not None:
        _emit(bus, run_id, timeout)
