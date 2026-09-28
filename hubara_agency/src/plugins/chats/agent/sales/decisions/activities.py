"""Activities del motor de decisiones en el turno de ventas: ① antes del turno
(`perceive_burst`) y ③ antes de enviar (`verify_coverage`).

Los nombres de las activities son contrato con las historias grabadas: nunca
cambian. Acá solo vive el I/O (qué tapar de ESTE cliente, leído del vault);
la lógica es del núcleo (`engine.py`). NUNCA fallan: el núcleo ya es
fail-open y cualquier excepción inesperada también da un resultado vacío. Así
el turno sigue como hoy y la traza guarda el motivo.
"""
from __future__ import annotations

import re
from pathlib import Path

import structlog
from temporalio import activity

from src.plugins.chats.agent.sales.decisions import engine
from src.plugins.chats.agent.sales.decisions.contracts import (
    CONTRACT_VERSION,
    PerceiveInput,
    TurnDecisions,
    VerifyInput,
    VerifyOutput,
)

logger = structlog.get_logger()

# Datos de envío del borrador que Jev no necesita (decisión 2 del plan del
# laboratorio). Los nombres se tapan también palabra por palabra: el cliente o
# el asesor repiten solo el nombre ("Listo Carolina"). El barrio y la
# dirección, completos: partirlos taparía palabras del producto ("alto").
_PERSONAL_SLOTS = ("nombre_recibe", "direccion", "barrio", "telefono")
_NAME_SLOTS = ("nombre_recibe",)
_NAME_TOKEN_RE = re.compile(r"[^\W\d_]{3,}")


def _redact_terms(session_id: str) -> list[str]:
    """Lo que hay que tapar de ESTE cliente antes de que el turno salga hacia
    Jev. Sin borrador legible, queda lo genérico (teléfonos, correos,
    direcciones, nombres anunciados)."""
    from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
    from src.plugins.chats.agent.sales.turn_trace import draft_slots
    from src.plugins.chats.shared.funnel import active_episode
    from src.sdk.runtime import WORKSPACE_VAULT_DIR

    try:
        slots = draft_slots(active_episode(FilesystemMetadataStore(Path(WORKSPACE_VAULT_DIR)).read(session_id)))
    except Exception as exc:  # noqa: BLE001 — anonimizar nunca tumba el turno
        logger.warning("perception.redact_terms_unavailable", error=repr(exc)[:200])
        return []
    terms: set[str] = set()
    for key in _PERSONAL_SLOTS:
        value = slots.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        terms.add(value.strip())
        if key in _NAME_SLOTS:
            terms.update(_NAME_TOKEN_RE.findall(value))
    return sorted(terms)


@activity.defn(name="perceive_burst")
async def perceive_burst_activity(inp: PerceiveInput) -> TurnDecisions:
    try:
        return await engine.perceive(inp, redact=_redact_terms(inp.session_id))
    except Exception as exc:  # noqa: BLE001 — fail-open: el turno sale como hoy
        return TurnDecisions(ok=False, profile=inp.profile, error=f"unexpected: {exc!r}"[:300], contract=CONTRACT_VERSION)


@activity.defn(name="verify_coverage")
async def verify_coverage_activity(inp: VerifyInput) -> VerifyOutput:
    try:
        return await engine.verify(inp, redact=_redact_terms(inp.session_id))
    except Exception as exc:  # noqa: BLE001 — fail-open
        return VerifyOutput(ok=False, decision="send", error=f"unexpected: {exc!r}"[:300])


PERCEPTION_ACTIVITIES = [perceive_burst_activity, verify_coverage_activity]
