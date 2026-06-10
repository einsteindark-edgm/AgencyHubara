"""CAST agents_admin→chats (evals@v1 → vistas del plano de gestión) — §5.3.

Decisión 2026-06-05 (PLUGIN_CONTRACT §5.2): los evals son PER-AGENTE (el de
sales vive en chats como worker `sales_eval`; el de eta vivirá en plugins/eta)
y la UI "Calidad LLM" de agents_admin es el **plano de gestión** que los
agrega. Pre-F5, el frontend de agents_admin llamaba `/api/chats/evals/*`
directo (los únicos matches del xfail P-9 — vía comentarios — y la deuda
P-23). Este módulo formaliza el consumo server-side:

    agents_admin/plugin.yaml:
      depends_on: [chats]
      consumes:
        - { provider: chats, contract: evals@v1, into: eval-views,
            cast: api/evals }

El frontend del plano de gestión SOLO ve `/api/agents/evals/*`; este cast
reenvía al contrato publicado del provider del eval del agente sales. Cuando
un segundo agente tenga eval propio (p.ej. eta), este cast pasa de
passthrough a AGREGADOR (escanea manifests por workers con eval — mismo
patrón genérico que `discover_agents`) sin que el frontend cambie.

Mismas reglas que el cast de chats→orders (`chats/api/order_actions.py`):
contrato publicado (no imports cross-plugin — P-3), loopback IPv4 explícito,
swap = tocar SOLO este archivo.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Body, HTTPException, Path, Query

router = APIRouter()

_TIMEOUT_S = 15.0


def _provider_base() -> str:
    return os.environ.get("CHATS_API_BASE", "http://127.0.0.1:8000").rstrip("/")


async def _forward(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{_provider_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.request(method, url, params=params, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"cast agents_admin→chats: el provider de evals no respondió "
                f"({exc.__class__.__name__}). ¿`chats` está habilitado? "
                f"(depends_on lo exige al boot)"
            ),
        ) from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    data = resp.json()
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=502, detail="cast agents_admin→chats: respuesta no-dict"
        )
    return data


@router.get("/evals/history")
async def eval_history(
    days: int = Query(default=30, ge=1, le=365),
    suite: str = Query(default="online"),
) -> dict[str, Any]:
    """Serie de scores del eval del agente (hoy: sales; mañana: agregado)."""
    return await _forward(
        "GET", "/api/chats/evals/history", params={"days": days, "suite": suite}
    )


@router.get("/evals/candidates")
async def list_candidates() -> dict[str, Any]:
    """Candidatos a golden pendientes de curación."""
    return await _forward("GET", "/api/chats/evals/candidates")


@router.get("/evals/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    return await _forward("GET", f"/api/chats/evals/candidates/{candidate_id}")


@router.post("/evals/candidates/{candidate_id}/approve")
async def approve_candidate(
    candidate_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    return await _forward(
        "POST", f"/api/chats/evals/candidates/{candidate_id}/approve", body=body
    )


@router.delete("/evals/candidates/{candidate_id}")
async def discard_candidate(
    candidate_id: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    return await _forward("DELETE", f"/api/chats/evals/candidates/{candidate_id}")
