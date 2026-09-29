"""Enchufe de las decisiones del agente (motor de decisiones, F8).

Remarketing no puede importar el motor de decisiones (vive en el paquete de
ventas y los agentes son independientes: contrato `agents-independent`). El
worker de remarketing, que conoce los dos lados, le conecta el decisor al
arrancar (`register_contact_decider`); la activity que lee el contexto del
gancho solo conoce este enchufe. Sin decisor conectado no hay decisión: el
LLM decide como hoy.

Sin Temporal y sin I/O propio.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

#: `(session_id, transcript, touch_number, silence_minutes, vault_dir) -> (sobra, traza)`.
ContactDecider = Callable[..., Awaitable[tuple[bool, dict[str, Any]]]]

_contact_decider: ContactDecider | None = None


def register_contact_decider(decider: ContactDecider) -> None:
    """Lo llama el worker de remarketing al arrancar."""
    global _contact_decider
    _contact_decider = decider


def clear_contact_decider() -> None:
    global _contact_decider
    _contact_decider = None


async def decide_contact(
    *,
    session_id: str,
    transcript: str,
    touch_number: int | None,
    silence_minutes: int | None,
    vault_dir: Path,
) -> tuple[bool, dict[str, Any]]:
    """¿Sobra el gancho? `(False, {})` sin decisor conectado; un decisor
    roto nunca frena el gancho (decide el LLM, como hoy)."""
    if _contact_decider is None:
        return False, {}
    try:
        return await _contact_decider(
            session_id=session_id,
            transcript=transcript,
            touch_number=touch_number,
            silence_minutes=silence_minutes,
            vault_dir=vault_dir,
        )
    except Exception as exc:  # noqa: BLE001 — el motor nunca tumba el gancho
        logger.warning("decisions.contact_failed", error=repr(exc)[:200])
        return False, {"error": repr(exc)[:200]}


#: `(session_id, text, customer_text, products, named, terms, vault_dir) ->
#: (títulos nombrados, términos fuera de catálogo)`.
CatalogContextDecider = Callable[..., Awaitable[tuple[list[str], list[str]]]]

_catalog_context_decider: CatalogContextDecider | None = None


def register_catalog_context_decider(decider: CatalogContextDecider) -> None:
    """Lo llama el worker de remarketing al arrancar (F3: producto nombrado y
    fuera de catálogo en el contexto del gancho)."""
    global _catalog_context_decider
    _catalog_context_decider = decider


async def decide_catalog_context(
    *,
    session_id: str,
    text: str,
    customer_text: str,
    products: Sequence[Any],
    named: list[str],
    terms: list[str],
    vault_dir: Path,
) -> tuple[list[str], list[str]]:
    """(títulos que ve el gancho, términos que no existen). Sin decisor
    conectado, o si falla, las reglas de hoy (`named`, `terms`)."""
    if _catalog_context_decider is None:
        return named, terms
    try:
        return await _catalog_context_decider(
            session_id=session_id,
            text=text,
            customer_text=customer_text,
            products=products,
            named=named,
            terms=terms,
            vault_dir=vault_dir,
        )
    except Exception as exc:  # noqa: BLE001 — el motor nunca tumba el gancho
        logger.warning("decisions.catalog_context_failed", error=repr(exc)[:200])
        return named, terms

