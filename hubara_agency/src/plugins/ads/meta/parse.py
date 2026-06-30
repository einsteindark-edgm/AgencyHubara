"""Parser PURO del Graph API insights → métricas tipadas (skeleton)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetaCampaignMetrics:
    """Métricas de una campaña Meta para el dashboard. Frozen + JSON-safe (R-JSON)."""

    campaign_id: str
    campaign_name: str
    spend: float
    impressions: int
    reach: int
    clicks: int
    messaging_conversations_started: int


# La conversación CTWA (click-to-WhatsApp) vive en este action_type del Graph.
# SUPUESTO (premortem #7): ventana de atribución de 7 días — el default razonable
# para CTWA. Si una cuenta reportara solo `_1d`/`_28d`, las conversaciones saldrían
# 0; volverlo configurable/sumar variantes es un follow-up si aparece el caso.
_CTWA_ACTION_TYPE = "onsite_conversion.messaging_conversation_started_7d"


def _num(value: object) -> float:
    """Graph manda números como strings limpios (`"896823"`). Vacío/ausente/no-numérico → 0.

    El guard ante no-numérico evita que un valor inesperado del boundary externo
    (p.ej. `"N/A"`) tumbe el parseo de TODAS las campañas (premortem #6)."""
    if value in (None, ""):
        return 0.0
    try:
        return float(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return 0.0


def _conversations(actions: list[dict]) -> int:
    for action in actions or []:
        if action.get("action_type") == _CTWA_ACTION_TYPE:
            return int(_num(action.get("value")))
    return 0


def parse_campaign_insights(payload: dict) -> list[MetaCampaignMetrics]:
    """Graph `/insights` (level=campaign) → métricas tipadas por campaña.

    Números limpios string→numérico; la conversación CTWA sale del array `actions`.
    """
    rows: list[MetaCampaignMetrics] = []
    for row in payload.get("data", []):
        rows.append(
            MetaCampaignMetrics(
                campaign_id=str(row.get("campaign_id", "")),
                campaign_name=str(row.get("campaign_name", "")),
                spend=_num(row.get("spend")),
                impressions=int(_num(row.get("impressions"))),
                reach=int(_num(row.get("reach"))),
                clicks=int(_num(row.get("clicks"))),
                messaging_conversations_started=_conversations(row.get("actions", [])),
            )
        )
    return rows
