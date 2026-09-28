"""Guardia del motor para las tools (diseño v2 §03, regla 5): lo ÚNICO que las
tools importan del motor. Sin Temporal (contrato `tools-no-temporal`).

Una tool que hoy decide con una regla de texto (un mapeo a una lista
cerrada, una revisión del texto del LLM) le pide la decisión al motor con
`decide_for_session`: el motor corre la capacidad con el proveedor que el
registro de bots dice para esa conversación (`reglas`, `sombra` o `jev`),
anota los desacuerdos para que los califique Claude Code y devuelve un
`Verdict`. Con `reglas` (así nace todo) el resultado es el de la regla de hoy
y Jev no se consulta.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.bots import bot_for_session
from src.plugins.chats.agent.sales.decisions.capabilities import Verdict, decide
from src.plugins.chats.agent.sales.decisions.capability_rollout import DecisionMetrics
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog

__all__ = [
    "Verdict",
    "catalog_choice_buttons",
    "decide_for_session",
    "product_quote_sentences",
    "safe_customer_text",
    "session_redact_terms",
]


async def decide_for_session(
    capability: Any,
    inp: Any,
    *,
    session_id: str,
    vault_dir: Path,
    redact: tuple[str, ...] = (),
) -> Verdict:
    """La decisión de la capacidad para ESTA conversación (ver el módulo)."""
    bot = bot_for_session(session_id, vault_dir=Path(vault_dir))
    return await decide(
        capability,
        inp,
        provider=bot.provider(capability.name),
        profile_id=bot.profile,
        disagreements=DisagreementLog(Path(vault_dir)),
        session_id=session_id,
        redact=redact,
        metrics=DecisionMetrics(Path(vault_dir)),
    )


def session_redact_terms(session_id: str, vault_dir: Path) -> tuple[str, ...]:
    """Lo que hay que tapar de ESTE cliente antes de que un texto salga hacia
    Jev (las casillas personales del borrador). Ilegible = nada propio: el
    adaptador igual tapa lo genérico (teléfonos, correos, direcciones)."""
    from src.plugins.chats.agent.sales.decisions.context import redact_terms_from_slots
    from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
    from src.plugins.chats.agent.sales.turn_trace import draft_slots
    from src.plugins.chats.shared.funnel import active_episode

    try:
        slots = draft_slots(active_episode(FilesystemMetadataStore(Path(vault_dir)).read(session_id)))
    except Exception:  # noqa: BLE001 — anonimizar nunca tumba la tool
        return ()
    return tuple(redact_terms_from_slots(slots))


async def safe_customer_text(raw: str | None, *, session_id: str, vault_dir: Path) -> str:
    """El texto para el cliente sin las oraciones que no debe leer (F5): las
    que dejan ver que quien atiende es un bot (capacidad `persona`, con el
    proveedor del bot de esta conversación) y las que huelen a reporte
    interno (la regla de hoy, hasta que `destinatario` la reemplace). El corte
    y la unión de oraciones son los de `keep_customer_safe_sentences`: con
    `reglas` el resultado es idéntico al de hoy. "" = nada era seguro."""
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import PERSONA, Frases
    from src.sdk.textkit import customer_sentences, keep_customer_safe_sentences, looks_like_admin_leak

    parts = customer_sentences(raw)
    if not parts:
        return ""
    persona = await decide_for_session(
        PERSONA,
        Frases(parts=tuple(parts)),
        session_id=session_id,
        vault_dir=vault_dir,
        redact=session_redact_terms(session_id, vault_dir),
    )
    drop = set(persona.value) | {i for i, part in enumerate(parts) if looks_like_admin_leak(part)}
    return keep_customer_safe_sentences(raw, drop=drop)


async def product_quote_sentences(sentences: Sequence[str], *, session_id: str, vault_dir: Path) -> frozenset[str]:
    """De las oraciones donde las palabras de política aceptan un monto, las
    que cotizan el precio de un producto (capacidad `monto`, F5): pierden el
    contexto de política en `find_unexplained_amounts`. Con `reglas`,
    ninguna (idéntico a hoy)."""
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import MONTO, OracionesPrecio

    if not sentences:
        return frozenset()
    verdict = await decide_for_session(
        MONTO,
        OracionesPrecio(sentences=tuple(sentences)),
        session_id=session_id,
        vault_dir=vault_dir,
        redact=session_redact_terms(session_id, vault_dir),
    )
    return frozenset(verdict.value)


async def catalog_choice_buttons(
    body: str,
    titles: Sequence[str],
    *,
    rule_rejected: Sequence[str],
    by_id: Sequence[str],
    session_id: str,
    vault_dir: Path,
) -> tuple[str, ...]:
    """Los botones de respuesta rápida que eligen del catálogo (capacidad
    `selector`, F5): vacío = pasan. Con `reglas`, los que ya rechazó el
    vocabulario del catálogo (idéntico a hoy); el namespace del id es piso."""
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import SELECTOR, Botones

    verdict = await decide_for_session(
        SELECTOR,
        Botones(body=body, titles=tuple(titles), rule_rejected=tuple(rule_rejected), by_id=tuple(by_id)),
        session_id=session_id,
        vault_dir=vault_dir,
        redact=session_redact_terms(session_id, vault_dir),
    )
    return tuple(verdict.value)

