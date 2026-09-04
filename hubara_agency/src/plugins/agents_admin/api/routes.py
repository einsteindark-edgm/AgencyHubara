"""Router HTTP del plugin ``agents_admin`` — sección *Agents* del dashboard.

Registrado por ``src/main.py`` desde el manifest
``frontend_dashboard/src/plugins/agents_admin/plugin.yaml`` (``api.python_module``),
con ``prefix=/api/agents``. ``GET /api/agents`` devuelve los agentes reales del
sistema con el contenido REAL de sus archivos de workspace.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from src.plugins.agents_admin.service import discover_agents

router = APIRouter()


@router.get("")
async def list_agents() -> dict[str, Any]:
    """Lista los agentes (sales, remarketing, …) con sus 5 prompts reales.

    Cada agente se descubre de los manifests (workers con bloque ``dashboard:``)
    y trae el contenido vivo de IDENTITY/SOUL/AGENTS/USER/TOOLS.md.
    """
    agents = discover_agents()
    return {"agents": [asdict(agent) for agent in agents]}
