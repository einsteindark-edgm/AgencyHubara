"""Contrato `remarketing-frequency@v1`: cuántos toques máximo hace el bot de
remarketing por cada vez que el cliente deja de contestar.

  GET /api/chats/remarketing/frequency   tope efectivo, techo de Terraform,
                                         lo guardado y la escalera (cuándo sale
                                         cada toque, para previsualizar)
  PUT /api/chats/remarketing/frequency   {max_touches: int}; 0 apaga la
                                         reactivación; nunca pasa el techo

El panel de Agents → Remarketing → Frecuencia lo consume por cast
(agents_admin). A diferencia del bot nuevo (solo por comando, ver
`perception.py`), este ajuste SÍ se edita desde el dashboard (operador,
2026-10-07): es solo una cantidad, y el techo lo fija Terraform
(`REMARKETING_MAX_TOUCHES`) — el dashboard elige dentro del techo, nunca lo
sube. Aplica desde ya: la central de envío, el ciclo y la etiqueta
`SIN_RESPUESTA` lo leen en cada decisión (`reengagement_frequency`).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictInt

from src.sdk.messagingkit import (
    LADDER_GAPS_MS,
    effective_max_touches,
    frequency_ceiling,
    read_frequency_state,
    set_max_touches,
)
from src.sdk.runtime import WORKSPACE_VAULT_DIR

router = APIRouter()

#: Quién firma el cambio: el dashboard no trae identidad individual del
#: operador (el Authorization solo prueba que está dentro).
ACTOR = "dashboard:operator"


class FrequencyBody(BaseModel):
    # StrictInt: «3» (texto), 2.5 y true no son una cantidad de toques.
    max_touches: StrictInt


def _vault_dir() -> Path:
    return Path(WORKSPACE_VAULT_DIR)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ladder() -> list[dict[str, int]]:
    """Cuándo sale cada toque, medido desde el último mensaje del cliente
    (huecos acumulados: +2h, +4h, +8h, +14h, +20h)."""
    out: list[dict[str, int]] = []
    elapsed = 0
    for index, gap in enumerate(LADDER_GAPS_MS, start=1):
        elapsed += gap
        out.append({"touch": index, "after_ms": elapsed})
    return out


def _snapshot() -> dict[str, Any]:
    state = read_frequency_state(_vault_dir())
    return {
        "max_touches": effective_max_touches(_vault_dir()),
        "ceiling": frequency_ceiling(),
        "saved": state.max_touches,
        "updated_at_ms": state.updated_at_ms,
        "updated_by": state.updated_by,
        "ladder": _ladder(),
    }


@router.get("/remarketing/frequency")
def get_frequency() -> dict[str, Any]:
    return _snapshot()


@router.put("/remarketing/frequency")
def put_frequency(body: FrequencyBody) -> dict[str, Any]:
    outcome = set_max_touches(
        _vault_dir(), body.max_touches, actor=ACTOR, now_ms=_now_ms()
    )
    if not outcome.applied:
        detail: dict[str, Any] = {"reason": outcome.reason}
        if outcome.reason == "above_ceiling":
            detail["ceiling"] = frequency_ceiling()
        raise HTTPException(status_code=422, detail=detail)
    return _snapshot()
