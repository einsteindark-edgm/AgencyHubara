"""Connector tools de Meta Business Agent (``PUBLIC_ROUTER``: las llama Meta, no el shell).

Una ruta por tool declarada en ``agents/*/agent.yaml``, bajo ``/api/mba/tools/<name>``,
protegida con la API key del connector (header ``X-API-Key`` = ``HUBARA_MBA_API_KEY``).
Fail-closed: sin la variable configurada responde 503.

Hasta D1.2 el contrato existe pero la lógica no: cada tool responde 501 con su
nombre, así el registro en Meta y la verificación de conectividad son reales.
"""
from __future__ import annotations

import hmac
import os
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

from src.plugins.mba.service import list_agents, load_agent

PUBLIC_ROUTER = True
API_KEY_ENV = "HUBARA_MBA_API_KEY"

router = APIRouter()


@lru_cache(maxsize=1)
def declared_tools() -> dict[str, str]:
    """``{tool_name: method}`` de todos los agentes MBA autorados.

    Cacheado: los archivos del agente son estáticos por deploy y este endpoint
    es público (no re-parsear 10 archivos por request).
    """
    out: dict[str, str] = {}
    for agent in list_agents():
        cfg = load_agent(agent.id)
        if cfg is None or cfg.connector is None:
            continue
        for t in cfg.connector.tools:
            out.setdefault(t.name, t.method)
    return out


def _check_api_key(presented: str | None, *, client: str, tool: str) -> None:
    expected = os.environ.get(API_KEY_ENV, "")
    if not expected:
        # el nombre de la variable va al log (operador), no al cliente anónimo
        logger.warning("[mba] connector sin {} configurada; 503 a {} para {}", API_KEY_ENV, client, tool)
        raise HTTPException(status_code=503, detail="connector no configurado")
    # compare_digest sobre bytes: con str lanza TypeError ante no-ASCII (y los
    # headers llegan decodificados latin-1) → sería un 500 en un endpoint público.
    if not presented or not hmac.compare_digest(
        presented.encode("utf-8", "surrogateescape"), expected.encode("utf-8")
    ):
        logger.warning("[mba] API key inválida desde {} para {}", client, tool)
        raise HTTPException(status_code=401, detail="API key inválida")


@router.api_route("/tools/{tool_name}", methods=["GET", "POST"])
async def run_tool(
    tool_name: str,
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Any:
    client = request.client.host if request.client else "?"
    _check_api_key(x_api_key, client=client, tool=tool_name)
    tools = declared_tools()
    if tool_name not in tools:
        raise HTTPException(status_code=404, detail=f"tool desconocida: {tool_name}")
    if request.method != tools[tool_name]:
        raise HTTPException(status_code=405, detail=f"{tool_name} se llama con {tools[tool_name]}")
    return JSONResponse(
        status_code=501,
        content={
            "tool": tool_name,
            "status": "not_implemented",
            "message": "Contrato registrado; la lógica llega en D1.2 (MBA_PRODUCTION_ROADMAP.md).",
        },
    )
