"""API del plugin ``mba`` (plano de gestión, protegido por auth del shell).

Rutas montadas bajo ``/api/mba``: lista de agentes MBA y la configuración
exacta que se enviaría a Meta por agente. Las tools del connector (públicas,
con API key propia) viven en ``connector.py``.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from src.plugins.mba.service import list_agents, load_agent

router = APIRouter()


@router.get("/agents")
async def get_agents() -> dict[str, Any]:
    return {"agents": [asdict(a) for a in list_agents()]}


@router.get("/agents/{agent_id}/config")
async def get_agent_config(agent_id: str) -> dict[str, Any]:
    cfg = load_agent(agent_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"agente MBA desconocido: {agent_id}")
    return asdict(cfg)
