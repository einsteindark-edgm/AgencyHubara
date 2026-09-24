"""El cupón que anuncia una campaña, validado contra Medusa — el ÚNICO I/O
de esa validación. La usan la API (enviar / programar / probar) y el envío
al dispararse (una campaña programada se re-valida a la hora del disparo).

El cupón tiene que regir en el INSTANTE en que el mensaje le llega al
cliente: el mismo chequeo que hace el bot con `apply_coupon`. Incidente
2026-09-22: la campaña anunció "AMOR" y el código real era AMOR26. El texto
de por qué no sirve es del dominio (`campaign_coupon_problem`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.plugins.marketing.domain.coupons import campaign_coupon_problem


@dataclass(frozen=True)
class CampaignCouponCheck:
    code: str
    #: El `PromotionDTO` del cupón (None si Medusa no lo tiene).
    promotion: Any
    #: Por qué no sirve, para el operador ("está pausado en Medusa"); None = sirve.
    problem: str | None


async def check_campaign_coupon(
    port: Any,
    campaign: dict[str, Any],
    *,
    at_ms: int,
    scheduled: bool = False,
    allow_not_started: bool = False,
) -> CampaignCouponCheck | None:
    """Valida el cupón de la campaña para el instante `at_ms` (la hora
    programada del envío, o ahora). None = la campaña no anuncia cupón.

    `allow_not_started`: un cupón que todavía no empieza sirve si en su
    inicio rige (el envío de prueba de una campaña con cupón programado).
    Levanta `PromotionsUnavailableError` si Medusa no responde."""
    from src.sdk.connectorkit import resolve_coupon

    code = (campaign.get("coupon_code") or "").strip()
    if not code:
        return None
    promotions = list(await port.list_active())
    extra = await port.get_by_code(code)
    if extra is not None and all(p.id != extra.id for p in promotions):
        promotions.append(extra)
    resolution = resolve_coupon(code, promotions, now_ms=at_ms)
    starts_at = getattr(resolution.promotion, "starts_at_ms", None)
    if allow_not_started and resolution.reason == "not_started" and starts_at is not None:
        # Lo demás (vencido, agotado, reglas ilegibles) se juzga en su inicio.
        resolution = resolve_coupon(code, promotions, now_ms=starts_at)
    if resolution.ok:
        return CampaignCouponCheck(code, resolution.promotion, None)
    problem = campaign_coupon_problem(resolution.reason, resolution.promotion, scheduled=scheduled)
    return CampaignCouponCheck(code, resolution.promotion, problem)


__all__ = ["CampaignCouponCheck", "check_campaign_coupon"]
