"""Router HTTP del plugin ``agents_admin`` — sección *Agents* del dashboard.

Registrado por ``src/main.py`` desde el manifest
``frontend_dashboard/src/plugins/agents_admin/plugin.yaml`` (``api.python_module``),
con ``prefix=/api/agents``. ``GET /api/agents`` devuelve los agentes reales del
sistema con el contenido REAL de sus archivos de workspace.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from src.plugins.agents_admin.service import build_mba_config, discover_agents

router = APIRouter()


@router.get("/{agent_id}/mba-config")
async def get_mba_config(agent_id: str) -> dict[str, Any]:
    """Configuración Meta Business Agent normalizada desde el workspace del agente.

    Devuelve, con la forma de los endpoints ``/agent_config/*`` de Meta, qué
    le mandaríamos a MBA (skills, business_info, faqs, settings) y qué queda
    fuera y por qué. Solo lectura: no llama a Meta.
    """
    cfg = build_mba_config(agent_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"agente desconocido: {agent_id}")
    return asdict(cfg)


@router.get("")
async def list_agents() -> dict[str, Any]:
    """Lista los agentes (sales, remarketing, …) con sus 5 prompts reales.

    Cada agente se descubre de los manifests (workers con bloque ``dashboard:``)
    y trae el contenido vivo de IDENTITY/SOUL/AGENTS/USER/TOOLS.md.
    """
    agents = discover_agents()
    return {"agents": [asdict(agent) for agent in agents]}
