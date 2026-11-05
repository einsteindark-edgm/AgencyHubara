"""API of the Costs tab — thin router (logic in ``domain/`` and ``service``).

``GET /api/costs/providers`` is cheap (no network): the cards, whether each is
connected, and the default range. ``GET /api/costs/providers/{id}`` asks ONE
provider for the range, so the page loads every card in parallel and a slow
provider only delays its own card. Auth is the default of every plugin router
(``require_auth``); this router is not public.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.plugins.costs.api.ads_cast import ads_cast_context
from src.plugins.costs.composition import get_costs_service
from src.plugins.costs.domain.period import BOGOTA, MAX_DAYS, DateRange, InvalidDateRange
from src.plugins.costs.service import CostsService, UnknownProvider

router = APIRouter()

DEFAULT_DAYS = 14


def _today(service: CostsService) -> date:
    return service.now().astimezone(BOGOTA).date()


@router.get("/providers")
def list_providers(service: CostsService = Depends(get_costs_service)) -> dict:
    today = _today(service)
    default = DateRange.last_days(DEFAULT_DAYS, today=today)
    return {
        "providers": service.catalog(),
        "today": today.isoformat(),
        "default_start": default.start.isoformat(),
        "default_end": default.end.isoformat(),
        "max_days": MAX_DAYS,
    }


@router.get("/providers/{provider_id}")
async def provider_report(
    request: Request,
    provider_id: str,
    start: str | None = Query(None, description="Primer día (AAAA-MM-DD, Bogotá)"),
    end: str | None = Query(None, description="Último día (AAAA-MM-DD, Bogotá)"),
    refresh: bool = Query(False, description="Ignorar la caché y preguntar de nuevo"),
    service: CostsService = Depends(get_costs_service),
) -> dict:
    today = _today(service)
    try:
        rng = (
            DateRange.parse(start, end, today=today)
            if start and end
            else DateRange.last_days(DEFAULT_DAYS, today=today)
        )
    except InvalidDateRange as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        served = await service.report(
            provider_id, rng, refresh=refresh, ctx=ads_cast_context(request)
        )
    except UnknownProvider:
        raise HTTPException(status_code=404, detail="No existe ese proveedor.") from None
    return served.to_json()
