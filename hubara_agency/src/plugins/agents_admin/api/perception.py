"""CAST agents_admin→chats (`perception-rollout@v1`) — plan del laboratorio PR 16.

El encendido del bot nuevo (capas con clasificador) se controla desde la
sección Agents, donde vive la configuración del agente. Los datos y el estado
son de chats (`/api/chats/perception/rollout`); este cast los sirve bajo
`/api/agents/perception/rollout` con `castkit.forward`: porta el
Authorization del operador y traduce los fallos con honestidad (L-1: un
timeout es 504 "PUEDE haberse aplicado").

    agents_admin/plugin.yaml:
      consumes:
        - { provider: chats, contract: perception-rollout@v1, into: agent-config,
            cast: api/perception }
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, Request

from src.sdk import castkit

router = APIRouter()

_TIMEOUT_S = 15.0
_CAST_LABEL = "agents_admin→chats (perception-rollout)"
_PATH = "/api/chats/perception/rollout"


def _provider_base() -> str:
    return os.environ.get("CHATS_API_BASE", "http://127.0.0.1:8000").rstrip("/")


@router.get("/perception/rollout")
async def get_rollout(request: Request) -> dict[str, Any]:
    return await castkit.forward(
        request, "GET", _PATH, base_url=_provider_base(), timeout=_TIMEOUT_S, cast_label=_CAST_LABEL
    )


@router.put("/perception/rollout")
async def put_rollout(request: Request, body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await castkit.forward(
        request, "PUT", _PATH, base_url=_provider_base(), timeout=_TIMEOUT_S, cast_label=_CAST_LABEL, body=body
    )
