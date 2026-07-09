"""Sub-router `/api/ads/analysis/*` — el buzón de análisis con IA del plugin `ads`.

Dispara el agente `ads-analytics` (pod CTWA de GraphAgents) desde el dashboard de Ads,
relaya su progreso EN VIVO por SSE y resume el HITL. El agente real vive en la caja
GraphAgents (fuera del monorepo); este buzón solo lo despierta (Launcher port), pollea
Conductor (`runs/orchestrator.poll_loop`) y republica al `DashboardEventBus` (dominio
`ads`). Self-contained dentro de `ads`: importa `src.sdk.*` + sus propios `runs/*`, NO
otro plugin.

Se monta bajo el router de `ads` con prefix `/analysis` (`api/__init__.py`), así las
rutas quedan `/api/ads/analysis/...` sin colisionar con `/api/ads/campaigns`:
- `GET  /api/ads/analysis/agents`            — el catálogo (agente + JSON de ejemplo del viewer)
- `POST /api/ads/analysis/runs`              — dispara un run (record + despierta caja + despacha + poller)
- `GET  /api/ads/analysis/runs/{id}`         — snapshot del record (refresh)
- `GET  /api/ads/analysis/runs/{id}/events`  — SSE en vivo (reusa el bus, filtrado por run-id)
- `POST /api/ads/analysis/runs/{id}/approve` — resume el HITL con la decisión
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.plugins.ads.runs import orchestrator, record
from src.sdk.dashboardkit import get_dashboard_event_bus

router = APIRouter()

#: El envelope crudo de `ads_get_ad_entities` (MCP) — lo que la tool de Meta devuelve. El
#: agente lee `ad_entities` como STRING JSON (así viaja por el MCP), por eso lo armamos con
#: `json.dumps`. Es el mismo payload del caso `dia-del-padre-flujo` del viewer de GraphAgents.
_AD_ENTITIES = [
    {
        "id": "120238728477970317", "name": "Duo zodiacal", "objective": "OUTCOME_ENGAGEMENT",
        "amount_spent": "$ 896.823 COP",
        "results": {"value": "205 (Messaging conversations started)"},
        "cost_per_result": {"value": "$ 4.375 COP (Messaging conversations started)"},
        "date_start": "20 de mayo de 2026", "date_stop": "18 de junio de 2026",
        "actions:link_click": "571",
    },
    {
        "id": "120243118818600317", "name": "Día del padre 2026 - mundia", "objective": "OUTCOME_SALES",
        "amount_spent": "$ 239.433 COP",
        "results": {"value": "0 (Meta purchases)"},
        "cost_per_result": {"value": "$ 0 COP (Meta purchases)"},
        "date_start": "20 de mayo de 2026", "date_stop": "18 de junio de 2026",
        "actions:link_click": "446",
    },
    {
        "id": "120240351877200317", "name": "Dia de la madre", "objective": "OUTCOME_ENGAGEMENT",
        "amount_spent": "$ 0 COP",
        "results": {"value": "0 (Messaging conversations started)"},
        "cost_per_result": {"value": "$ 0 COP (Messaging conversations started)"},
        "date_start": "20 de mayo de 2026", "date_stop": "18 de junio de 2026",
        "actions:link_click": "Not available",
    },
]

#: El seed REAL del pod `ads-analytics` (el mismo del caso `dia-del-padre-flujo`): Graph
#: /insights de las campañas + ventas manuales + el envelope crudo de entities. El CLI de la
#: caja lo envuelve en `{acc: seed}` para el supervisor secuencial.
_ADS_ANALYTICS_INPUT = {
    "meta_insights": {
        "account_currency": "COP",
        "data": [
            {
                "date_start": "2026-06-15", "date_stop": "2026-06-15",
                "campaign_id": "120238728477970317", "campaign_name": "Duo zodiacal",
                "spend": "120000", "inline_link_clicks": "80",
                "actions": [
                    {"action_type": "link_click", "value": "80"},
                    {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "40"},
                ],
            },
            {
                "date_start": "2026-06-16", "date_stop": "2026-06-16",
                "campaign_id": "120238728477970317", "campaign_name": "Duo zodiacal",
                "spend": "100000", "inline_link_clicks": "50",
                "actions": [
                    {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "30"},
                ],
            },
            {
                "date_start": "2026-06-15", "date_stop": "2026-06-15",
                "campaign_id": "120243118818600317", "campaign_name": "Día del padre 2026",
                "spend": "176294", "inline_link_clicks": "344",
                "actions": [
                    {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "120"},
                ],
            },
            {
                "date_start": "2026-06-16", "date_stop": "2026-06-16",
                "campaign_id": "120243118818600317", "campaign_name": "Día del padre 2026",
                "spend": "90000", "inline_link_clicks": "60",
                "actions": [],
            },
        ],
    },
    "manual_sales": {
        "sales": [
            {"date": "2026-06-15", "total_orders": 12, "total_revenue": 600000},
            {"date": "2026-06-16", "total_orders": 4, "total_revenue": 150000},
        ],
    },
    "entities_payload": {
        "ad_entities": json.dumps(_AD_ENTITIES, ensure_ascii=False),
        "summary": {"total_count": 3},
    },
}

#: El catálogo del botón "Analizar con IA". UN agente: el pod `ads-analytics` (supervisor
#: secuencial de 5 nodos: 2 extractores → embudo CTWA → blended-economics → numbers-QA →
#: reporter). El `example_input` pre-carga el textarea con el seed real del viewer.
_AGENTS = [
    {
        "id": "ads-analytics",
        "label": "Unit-economics CTWA por campaña",
        "description": (
            "Analiza tus campañas de Meta que llevan a WhatsApp: cruza el gasto y los "
            "clicks de Meta con las ventas, arma el embudo click → conversación → venta "
            "por campaña, calcula CAC / ROAS / costo por conversación, verifica que los "
            "números cierren y redacta un reporte con sugerencias accionables (qué "
            "campaña escalar, cuál pausar). Corre en el pod GraphAgents; puede pedirte "
            "aprobación antes de sugerir cambios."
        ),
        "example_input": _ADS_ANALYTICS_INPUT,
    },
]

#: ids válidos del catálogo — el dispatch SOLO acepta agentes conocidos (defensa: `agent` se
#: interpola en el comando SSM de la caja). Un agente fuera del catálogo → 400, nunca llega al box.
_AGENT_IDS = frozenset(a["id"] for a in _AGENTS)

#: Referencias VIVAS a los tasks de launch en vuelo. `asyncio.create_task` no retiene el task
#: (L-7: el GC lo recolecta mid-run y mata el poll en silencio) → lo guardamos y lo soltamos al fin.
_launch_tasks: set[asyncio.Task] = set()


class TriggerBody(BaseModel):
    agent: str
    input: dict


class ApproveBody(BaseModel):
    decision: dict


def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def _get_launcher():
    """El Launcher real (boto3). Import perezoso: tests lo monkeypatchean con un fake, y boto3
    no se importa si no hace falta (NO-OP sin config AWS)."""
    from src.plugins.ads.runs.boto3_launcher import Boto3Launcher

    return Boto3Launcher()


def _spawn_launch(run_id: str, agent: str, input: dict) -> None:
    """Arranca el ciclo `launch_and_poll` en BACKGROUND (asyncio task en el proceso uvicorn que
    sirve el SSE → el publish llega al subscriber). El endpoint NO espera: despertar la caja puede
    tardar minutos y el IO bloqueante corre en threads. GUARDA la referencia del task (L-7). Tests
    lo monkeypatchean a no-op."""
    task = asyncio.create_task(
        orchestrator.launch_and_poll(
            run_id, agent, input, launcher=_get_launcher(), bus=get_dashboard_event_bus()
        )
    )
    _launch_tasks.add(task)
    task.add_done_callback(_launch_tasks.discard)


def _spawn_resume(run_id: str, execution_id: str, decision: dict) -> None:
    """Manda el resume del HITL en background (mismo patrón que `_spawn_launch`): el endpoint NO
    espera a que la caja despierte (puede tardar minutos tras una espera humana larga). GUARDA la
    referencia del task (L-7)."""
    task = asyncio.create_task(
        orchestrator.resume_run(run_id, execution_id, decision, launcher=_get_launcher())
    )
    _launch_tasks.add(task)
    task.add_done_callback(_launch_tasks.discard)


@router.get("/agents")
def list_agents() -> list[dict]:
    return _AGENTS


@router.get("/runs")
def list_runs(limit: int = 20) -> dict:
    """Historial de análisis (el versionado del inspector): cada corrida con su
    fecha, estado, snapshot de entrada (los números que había) y resultado (las
    sugerencias de esa versión), más nueva primero."""
    return {"runs": record.list_runs(limit=max(1, min(limit, 100)))}


@router.post("/runs")
async def create_run(body: TriggerBody) -> dict:
    if body.agent not in _AGENT_IDS:
        raise HTTPException(status_code=400, detail=f"agente desconocido: {body.agent!r}")
    run_id = _new_run_id()
    # Nace `pending` (write de vault rápido); el launch (despertar caja + despachar + pollear)
    # corre en background para no bloquear el request — el progreso llega por el SSE.
    record.create_run(run_id, agent=body.agent, input=body.input)
    _spawn_launch(run_id, body.agent, body.input)
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    rec = record.read_run(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run no existe")
    return rec


@router.post("/runs/{run_id}/approve")
async def approve(run_id: str, body: ApproveBody) -> dict:
    rec = record.read_run(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run no existe")
    execution_id = rec.get("execution_id")
    if not execution_id:
        # El run aún no se despachó (la caja está despertando) → no hay HUMAN task que resumir.
        raise HTTPException(status_code=409, detail="el run todavía no fue despachado")
    # El resume despierta la caja (puede tardar minutos tras una espera humana larga) → background,
    # NO bloquea el request (evita el timeout del proxy). El poller relaya la transición.
    _spawn_resume(run_id, execution_id, body.decision)
    return {"status": "resuming"}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> StreamingResponse:
    """SSE en vivo del run: snapshot inicial (el record) + cada evento del bus filtrado por
    run-id. Reusa el `DashboardEventBus` (dominio `ads`) — el poll_loop publica acá."""
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
