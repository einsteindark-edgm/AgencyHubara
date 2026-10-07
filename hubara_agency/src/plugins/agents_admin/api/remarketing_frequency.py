"""CAST agents_admin→chats (`remarketing-frequency@v1`): la frecuencia del
remarketing (cuántos toques máximo hace el bot) se ve y se EDITA en la sección
Agents → Remarketing → Frecuencia. Los datos y las reglas (techo de
Terraform, 0..techo) son de chats (`/api/chats/remarketing/frequency`); este
cast los sirve bajo `/api/agents/remarketing/frequency` con
`castkit.forward`: porta el Authorization del operador y traduce los fallos
con honestidad (L-1: un timeout en el PUT es 504 "PUEDE haberse aplicado").

    agents_admin/plugin.yaml:
      consumes:
        - { provider: chats, contract: remarketing-frequency@v1, into: agent-config,
            cast: api/remarketing_frequency }
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, StrictInt

from src.sdk import castkit

router = APIRouter()

_TIMEOUT_S = 15.0
_CAST_LABEL = "agents_admin→chats (remarketing-frequency)"
_PATH = "/api/chats/remarketing/frequency"


class FrequencyBody(BaseModel):
    max_touches: StrictInt


def _provider_base() -> str:
    return os.environ.get("CHATS_API_BASE", "http://127.0.0.1:8000").rstrip("/")


@router.get("/remarketing/frequency")
async def get_frequency(request: Request) -> dict[str, Any]:
    return await castkit.forward(
        request, "GET", _PATH, base_url=_provider_base(), timeout=_TIMEOUT_S, cast_label=_CAST_LABEL
    )


@router.put("/remarketing/frequency")
async def put_frequency(body: FrequencyBody, request: Request) -> dict[str, Any]:
    return await castkit.forward(
        request,
        "PUT",
        _PATH,
        base_url=_provider_base(),
        timeout=_TIMEOUT_S,
        cast_label=_CAST_LABEL,
        body=body.model_dump(),
    )
