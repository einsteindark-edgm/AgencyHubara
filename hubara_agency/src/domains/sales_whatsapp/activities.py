"""Activities especificas del dominio Sales.

Aqui viven los "puentes" entre el workflow y los policies de `domain/policies/`. El
proposito es mantener los workflows finos (driving adapters) y permitir que los
prompts evolucionen sin tocar la shape de history (la activity stub se mantiene).
"""
from __future__ import annotations

from temporalio import activity

from src.domains.sales_whatsapp.domain.policies.prompts import build_ghosting_prompt


@activity.defn(name="decide_ghosting_action")
async def decide_ghosting_action() -> str:
    """Devuelve el prompt inyectado cuando se detecta ghosting.

    No tiene side effects ni I/O. Existe como activity (no como llamada directa
    en el workflow) para mantener los strings de negocio fuera del workflow code.
    """
    return build_ghosting_prompt()
