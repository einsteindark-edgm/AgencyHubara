"""Activity del egreso del turno de ventas (workflow V2): `decide_egress`.

El nombre es contrato con las historias grabadas: nunca cambia. Acá solo vive
el I/O: qué bot corre en ESTA conversación (registro de bots: en el laboratorio
el brazo, en producción el despliegue gradual), qué tapar de este cliente antes
de preguntarle a Jev y dónde anotar los desacuerdos. La lógica es del núcleo
(`egress.py`). Con `reglas` no sale a la red ni escribe nada: solo lee el
control del despliegue (el registro de bots).

NUNCA falla: si algo inesperado revienta, decide la regla de hoy y el motivo
queda en `error` (el turno sale como con el V1).
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import structlog
from temporalio import activity

from src.plugins.chats.agent.sales.decisions import egress
from src.plugins.chats.agent.sales.decisions.bots import DEFAULT_PROFILE, bot_for_session
from src.plugins.chats.agent.sales.decisions.capability_rollout import DecisionMetrics
from src.plugins.chats.agent.sales.decisions.decision_log import SessionDecisionLog
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.plugins.chats.agent.sales.decisions.guards import session_redact_terms

logger = structlog.get_logger()


def _vault_dir() -> Path:
    from src.sdk.runtime import WORKSPACE_VAULT_DIR

    return Path(WORKSPACE_VAULT_DIR)


def _rules_only(_capability: str) -> str:
    return "reglas"


@activity.defn(name="decide_egress")
async def decide_egress_activity(inp: EgressInput) -> EgressOutput:
    try:
        vault = _vault_dir()
        bot = bot_for_session(inp.session_id, vault_dir=vault)
        asks_jev = any(bot.provider(name) != "reglas" for name in egress.EGRESS_CAPABILITIES)
        return await egress.decide_egress(
            inp,
            provider_of=bot.provider,
            profile_id=bot.profile,
            disagreements=DisagreementLog(vault) if asks_jev else None,
            # Lo que hay que tapar de ESTE cliente (nombre, dirección,
            # teléfono del borrador) antes de que el texto salga hacia Jev.
            redact=session_redact_terms(inp.session_id, vault) if asks_jev else (),
            metrics=DecisionMetrics(vault) if asks_jev else None,
            # Calidad LLM las muestra en su turno (2026-10-02).
            decisions=SessionDecisionLog(vault) if asks_jev else None,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open: decide la regla de hoy
        logger.warning("decisions.egress_unexpected", error=repr(exc)[:200])
        out = await egress.decide_egress(inp, provider_of=_rules_only, profile_id=DEFAULT_PROFILE)
        return dataclasses.replace(out, error=f"unexpected: {exc!r}"[:300])


EGRESS_ACTIVITIES = [decide_egress_activity]
