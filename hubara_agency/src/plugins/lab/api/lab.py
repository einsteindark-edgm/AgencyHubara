"""CAST lab→chats (`lab@v1` → vistas del laboratorio) — plan §3.7 y §11.

Los datos del laboratorio son de chats (el lanzador, el banco y las corridas
viven en `src/plugins/chats/api/lab.py`). El frontend de lab SOLO habla con
`/api/lab/*` (P-23); este cast reenvía al contrato publicado del provider:

    lab/plugin.yaml:
      depends_on: [chats]
      consumes:
        - { provider: chats, contract: lab@v1, into: lab-views, cast: api/lab }

Cada segmento que manda el navegador se valida en `domain/logic.py` antes de
reenviar (422 sin tocar chats). El reenvío va por `src.sdk.castkit.forward`:
porta el `Authorization` del operador y traduce los fallos con honestidad
(L-1) — importa en "Nueva corrida", que prende una caja que cuesta plata: un
timeout es 504 "PUEDE haberse aplicado" y la UI lo dice así.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from src.plugins.lab.domain.logic import (
    PROVIDER_PREFIX,
    LabPathError,
    clean_query,
    conversation_path,
    run_path,
)
from src.sdk import castkit

router = APIRouter()

_READ_TIMEOUT_S = 15.0
# Estimar y lanzar recorren el vault para contar el banco nuevo y arrancan un
# workflow: más que una lectura.
_LAUNCH_TIMEOUT_S = 45.0
_CAST_LABEL = "lab→chats"


def _provider_base() -> str:
    return os.environ.get("CHATS_API_BASE", "http://127.0.0.1:8000").rstrip("/")


async def _forward(
    request: Request,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = _READ_TIMEOUT_S,
) -> dict[str, Any]:
    return await castkit.forward(
        request,
        method,
        path,
        base_url=_provider_base(),
        timeout=timeout,
        cast_label=_CAST_LABEL,
        params=params or None,
        body=body,
    )


def _valid(build: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return build(*args, **kwargs)
    except LabPathError as exc:
        raise HTTPException(422, detail=str(exc)) from None


# ── Lanzador ────────────────────────────────────────────────────────────────


@router.get("/estimate")
async def estimate(
    request: Request,
    arms: str = Query("A1", max_length=20),
    reps: int = Query(1),
    bench: str = Query("new", max_length=80),
) -> dict[str, Any]:
    params = _valid(clean_query, arms=arms, reps=reps, bench=bench)
    return await _forward(request, "GET", f"{PROVIDER_PREFIX}/estimate", params=params, timeout=_LAUNCH_TIMEOUT_S)


@router.post("/runs", status_code=202)
async def launch(request: Request, body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await _forward(request, "POST", f"{PROVIDER_PREFIX}/runs", body=body, timeout=_LAUNCH_TIMEOUT_S)


@router.get("/runs/active")
async def active(request: Request) -> dict[str, Any]:
    return await _forward(request, "GET", f"{PROVIDER_PREFIX}/runs/active")


@router.post("/runs/active/cancel", status_code=202)
async def cancel(request: Request) -> dict[str, Any]:
    return await _forward(request, "POST", f"{PROVIDER_PREFIX}/runs/active/cancel")


# ── Lecturas de una corrida ─────────────────────────────────────────────────


@router.get("/runs")
async def runs(request: Request) -> dict[str, Any]:
    return await _forward(request, "GET", f"{PROVIDER_PREFIX}/runs")


@router.get("/runs/{run}/bench")
async def bench(request: Request, run: str) -> dict[str, Any]:
    return await _forward(request, "GET", _valid(run_path, run, "bench"))


@router.get("/runs/{run}/conversations")
async def conversations(request: Request, run: str) -> dict[str, Any]:
    return await _forward(request, "GET", _valid(run_path, run, "conversations"))


@router.get("/runs/{run}/conversations/{sid}")
async def thread(request: Request, run: str, sid: str, episode: str | None = Query(None, max_length=20)) -> dict[str, Any]:
    path = _valid(conversation_path, run, sid)
    return await _forward(request, "GET", path, params=_valid(clean_query, episode=episode))


@router.get("/runs/{run}/conversations/{sid}/turns/trace")
async def turn_trace(
    request: Request,
    run: str,
    sid: str,
    turn_key: str = Query(..., max_length=200),
    arm: str = Query("A0"),
    rep: int = Query(0),
) -> dict[str, Any]:
    path = _valid(conversation_path, run, sid, "turns", "trace")
    return await _forward(request, "GET", path, params=_valid(clean_query, turn_key=turn_key, arm=arm, rep=rep))


@router.get("/runs/{run}/conversations/{sid}/evaluations")
async def evaluations(request: Request, run: str, sid: str, arm: str = Query("A0"), rep: int = Query(0)) -> dict[str, Any]:
    path = _valid(conversation_path, run, sid, "evaluations")
    return await _forward(request, "GET", path, params=_valid(clean_query, arm=arm, rep=rep))


@router.get("/runs/{run}/summary")
async def summary(request: Request, run: str, arm: str = Query("A0")) -> dict[str, Any]:
    return await _forward(request, "GET", _valid(run_path, run, "summary"), params=_valid(clean_query, arm=arm))


@router.get("/runs/{run}/diff")
async def diff(request: Request, run: str, base: str = Query("A1"), cand: str = Query("B")) -> dict[str, Any]:
    return await _forward(request, "GET", _valid(run_path, run, "diff"), params=_valid(clean_query, base=base, cand=cand))
