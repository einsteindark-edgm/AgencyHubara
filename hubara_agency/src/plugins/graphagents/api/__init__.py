"""API del buzón graphagents — el router del plugin.

Cognito-protegido por default (el loader le cuelga `Depends(require_auth)` salvo
`PUBLIC_ROUTER`). Endpoints:
- `GET  /agents`              — el catálogo del selector (agente + JSON de ejemplo del viewer)
- `POST /runs`               — dispara un run (crea record + despierta caja + despacha + poller)
- `GET  /runs/{id}`          — el snapshot del record (para refresh)
- `GET  /runs/{id}/events`   — SSE en vivo (reusa el DashboardEventBus, filtrado por run-id)
- `POST /runs/{id}/approve`  — resume el HITL con la decisión

El flujo real (poll de Conductor + relay) vive en `runs/orchestrator.py`; acá solo el HTTP.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.sdk.dashboardkit import get_dashboard_event_bus
from src.plugins.graphagents.runs import orchestrator, record

router = APIRouter()

#: El catálogo de agentes que el selector del dashboard ofrece — id + label + el JSON de
#: ejemplo que el usuario inyecta en el viewer hoy. Los agentes viven en GraphAgents; esto
#: es el menú de "qué corro + con qué entrada de prueba".
_AGENTS = [
    {"id": "greeter", "label": "Greeter (smoke test)", "example_input": {"name": "ada"}},
    {
        "id": "ctwa-campaign-funnel",
        "label": "Embudo CTWA por campaña",
        "example_input": {"entities_payload": {}, "insights_payload": {}},
    },
    {
        "id": "roas-cac",
        "label": "ROAS / CAC",
        "example_input": {
            "route": "roas-cac",
            "adsets": [{"id": "a", "roas": 2.0}, {"id": "b", "roas": 1.0}],
            "total_budget": 300.0,
        },
    },
    {"id": "numbers-qa", "label": "Numbers QA", "example_input": {}},
]


class TriggerBody(BaseModel):
    agent: str
    input: dict


class ApproveBody(BaseModel):
    decision: dict


def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def _conductor_base_url() -> str:
    """La REST de Conductor de la caja GraphAgents. En prod el Boto3Launcher resuelve la IP
    privada por tag; acá el default sirve para dev/local."""
    return os.getenv("GRAPHAGENTS_CONDUCTOR_URL", "http://localhost:6767")


def _get_launcher():
    """El Launcher real (boto3). Import perezoso: tests lo monkeypatchean con un fake, y boto3
    no se importa si no hace falta (NO-OP sin config AWS)."""
    from src.plugins.graphagents.runs.boto3_launcher import Boto3Launcher

    return Boto3Launcher()


def _spawn_poller(run_id: str, execution_id: str) -> None:
    """Arranca el poll_loop en background (un asyncio task en el mismo proceso uvicorn que
    sirve el SSE → el publish llega al subscriber). Tests lo monkeypatchean a no-op."""
    asyncio.create_task(
        orchestrator.poll_loop(
            run_id, execution_id, base_url=_conductor_base_url(), bus=get_dashboard_event_bus()
        )
    )


@router.get("/agents")
def list_agents() -> list[dict]:
    return _AGENTS


@router.post("/runs")
async def create_run(body: TriggerBody) -> dict:
    run_id = _new_run_id()
    rec = orchestrator.start_run(run_id, body.agent, body.input, launcher=_get_launcher())
    _spawn_poller(run_id, rec["execution_id"])
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    rec = record.read_run(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run no existe")
    return rec


@router.post("/runs/{run_id}/approve")
def approve(run_id: str, body: ApproveBody) -> dict:
    rec = record.read_run(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run no existe")
    _get_launcher().resume(rec["execution_id"], body.decision)
    return {"status": "resumed"}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> StreamingResponse:
    """SSE en vivo del run: snapshot inicial (el record) + cada evento del bus filtrado por
    run-id. Reusa el `DashboardEventBus` — el poll_loop publica acá."""
    bus = get_dashboard_event_bus()
    queue = bus.subscribe()

    async def gen():
        try:
            rec = record.read_run(run_id)
            if rec is not None:
                yield f"data: {json.dumps({'type': 'snapshot', 'run': rec}, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    if getattr(event, "payload", {}).get("run_id") == run_id:
                        yield event.to_sse()
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keep-alive
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
