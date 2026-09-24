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

El reenvío va por `src.sdk.castkit.forward` (Canal 3): porta el `Authorization`
del request entrante al hop del provider — sin eso, con `require_auth` enforced
el 2º hop daba 401 (incidente 2026-06-23). El swap del provider = tocar SOLO
`_provider_base()`; la mecánica del cast (auth, timeouts, errores) es del kit.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, Path, Query, Request

from src.sdk import castkit

router = APIRouter()

_TIMEOUT_S = 15.0
_CAST_LABEL = "agents_admin→chats"


def _provider_base() -> str:
    return os.environ.get("CHATS_API_BASE", "http://127.0.0.1:8000").rstrip("/")


async def _forward(
    request: Request,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reenvía al contrato `evals@v1` de chats portando la identidad del edge."""
    return await castkit.forward(
        request,
        method,
        path,
        base_url=_provider_base(),
        timeout=_TIMEOUT_S,
        cast_label=_CAST_LABEL,
        params=params,
        body=body,
    )


@router.get("/evals/history")
async def eval_history(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    suite: str = Query(default="online"),
) -> dict[str, Any]:
    """Serie de scores del eval del agente (hoy: sales; mañana: agregado)."""
    return await _forward(
        request, "GET", "/api/chats/evals/history",
        params={"days": days, "suite": suite},
    )


@router.get("/evals/conversations")
async def eval_conversations(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    suite: str = Query(default="online"),
) -> dict[str, Any]:
    """Evaluaciones por conversación (sesión + episodio) con timeline + curación."""
    return await _forward(
        request, "GET", "/api/chats/evals/conversations",
        params={"days": days, "suite": suite},
    )


@router.get("/evals/transcript")
async def eval_transcript(
    request: Request,
    session_id: str = Query(..., min_length=1, max_length=120),
    episode_id: str = Query(default=""),
) -> dict[str, Any]:
    """Transcript del episodio evaluado (el mismo segmento que vio el juez)."""
    return await _forward(
        request,
        "GET",
        "/api/chats/evals/transcript",
        params={"session_id": session_id, "episode_id": episode_id},
    )


@router.get("/evals/candidates")
async def list_candidates(request: Request) -> dict[str, Any]:
    """Candidatos a golden pendientes de curación."""
    return await _forward(request, "GET", "/api/chats/evals/candidates")


@router.get("/evals/candidates/{candidate_id}")
async def get_candidate(
    request: Request,
    candidate_id: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    return await _forward(
        request, "GET", f"/api/chats/evals/candidates/{candidate_id}"
    )


@router.post("/evals/candidates/{candidate_id}/approve")
async def approve_candidate(
    request: Request,
    candidate_id: str = Path(..., min_length=1, max_length=200),
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    return await _forward(
        request, "POST", f"/api/chats/evals/candidates/{candidate_id}/approve",
        body=body,
    )


@router.delete("/evals/candidates/{candidate_id}")
async def discard_candidate(
    request: Request,
    candidate_id: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    return await _forward(
        request, "DELETE", f"/api/chats/evals/candidates/{candidate_id}"
    )


# ── HU-SC-2: scorecard por etapa (mismo contrato evals@v1, passthrough) ──────
# El juez del recálculo corre en el worker del provider: este cast solo espera
# el recálculo de código, que entra en el timeout.


@router.get("/evals/checks")
async def scorecard_checks(request: Request) -> dict[str, Any]:
    """Registro de checks del scorecard (taxonomía de fallos)."""
    return await _forward(request, "GET", "/api/chats/evals/checks")


#: Filtro de Calidad LLM durante el encendido del bot nuevo (lab PR 18).
_BOT_PATTERN = "^(actual|nuevo)$"


def _with_bot(params: dict[str, Any], bot: str | None) -> dict[str, Any]:
    return {**params, "bot": bot} if bot else params


@router.get("/evals/scorecards")
async def list_scorecards(
    request: Request,
    days: int = Query(default=30, ge=1, le=180),
    bot: str | None = Query(default=None, pattern=_BOT_PATTERN),
) -> dict[str, Any]:
    """Último scorecard por episodio (lista y matriz de cumplimiento)."""
    return await _forward(
        request, "GET", "/api/chats/evals/scorecards", params=_with_bot({"days": days}, bot)
    )


@router.get("/evals/scorecard")
async def get_scorecard(
    request: Request,
    session_id: str = Query(..., min_length=1, max_length=120),
    episode_id: str = Query(..., min_length=1, max_length=20),
) -> dict[str, Any]:
    """Scorecard de un episodio + su trayectoria + el puntaje legado."""
    return await _forward(
        request,
        "GET",
        "/api/chats/evals/scorecard",
        params={"session_id": session_id, "episode_id": episode_id},
    )


@router.post("/evals/scorecard/rescore")
async def rescore_scorecard(
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    return await _forward(request, "POST", "/api/chats/evals/scorecard/rescore", body=body)


@router.get("/evals/checks/stats")
async def scorecard_stats(
    request: Request,
    days: int = Query(default=56, ge=7, le=365),
    bot: str | None = Query(default=None, pattern=_BOT_PATTERN),
) -> dict[str, Any]:
    """Pareto, tendencia semanal por check y embudo de etapa final."""
    return await _forward(
        request, "GET", "/api/chats/evals/checks/stats", params=_with_bot({"days": days}, bot)
    )


@router.post("/evals/labels")
async def create_label(
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    return await _forward(request, "POST", "/api/chats/evals/labels", body=body)


@router.get("/evals/labels")
async def list_labels(
    request: Request,
    session_id: str = Query(..., min_length=1, max_length=120),
    episode_id: str = Query(..., min_length=1, max_length=20),
) -> dict[str, Any]:
    return await _forward(
        request,
        "GET",
        "/api/chats/evals/labels",
        params={"session_id": session_id, "episode_id": episode_id},
    )


@router.get("/evals/labels/queue")
async def label_queue(
    request: Request,
    days: int = Query(default=30, ge=1, le=180),
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    return await _forward(
        request, "GET", "/api/chats/evals/labels/queue", params={"days": days, "limit": limit}
    )


@router.get("/evals/calibration")
async def judge_calibration(request: Request) -> dict[str, Any]:
    """Acuerdo juez vs humano por check de juez (TPR, TNR, kappa)."""
    return await _forward(request, "GET", "/api/chats/evals/calibration")
