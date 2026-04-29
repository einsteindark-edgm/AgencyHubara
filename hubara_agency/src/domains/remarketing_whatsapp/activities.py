"""Activities especificas del dominio Remarketing."""
from __future__ import annotations

from temporalio import activity

from src.domains.remarketing_whatsapp.domain.policies.prompts import build_remarketing_trigger


@activity.defn(name="build_remarketing_trigger_activity")
async def build_remarketing_trigger_activity(motivo: str, memory_context: str) -> str:
    """Devuelve el prompt proactivo inicial del agente de Remarketing."""
    return build_remarketing_trigger(motivo, memory_context)
