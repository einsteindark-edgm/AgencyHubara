"""Decisiones del contexto del gancho de remarketing (motor de decisiones, F3).

`register_catalog_context_decision()` lo conecta al enchufe de `chats/shared`
(lo llama el worker de remarketing al arrancar):

* producto nombrado: qué ficha ve el gancho (Jev suma el producto que el
  cliente nombró con otras palabras; los exactos se quedan);
* fuera de catálogo: lo que el cliente pidió y no existe (Jev solo QUITA
  candidatos falsos; nunca inventa uno);
* el motivo que el watchdog manda como variable de una plantilla: si no es un
  texto para el cliente, va el genérico (`register_label_decision`).

El proveedor de cada una sale del registro de bots de la conversación. Con
`reglas` (así nace), las reglas de hoy. Sin Temporal.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.capabilities.mapeos import ProductosDeLaCharla
from src.plugins.chats.agent.sales.decisions.guards import capability, decide_for_session
from src.plugins.chats.agent.sales.decisions.readings import read_catalog_gap


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
    titles = tuple(str(getattr(p, "title", "") or "") for p in products if getattr(p, "title", None))
    named_verdict = await decide_for_session(
        capability("producto_nombrado"),
        ProductosDeLaCharla(text=text, titles=titles, named=tuple(named)),
        session_id=session_id,
        vault_dir=vault_dir,
        stage="remarketing",
    )
    gap = await read_catalog_gap(
        Path(vault_dir), session_id=session_id, text=customer_text, products=products, stage="remarketing"
    )
    return list(named_verdict.value or ()), list(gap.value or [])


def register_catalog_context_decision() -> None:
    from src.plugins.chats.shared.agent_decisions import register_catalog_context_decider

    register_catalog_context_decider(decide_catalog_context)


async def decide_template_label(*, session_id: str, text: str, vault_dir: Path) -> bool:
    """¿El motivo que va como variable de una plantilla NO es para el cliente?
    (capacidad `destinatario`, variante de plantilla: hoy no se revisa)."""
    from src.plugins.chats.agent.sales.decisions.egress import TextCheck

    verdict = await decide_for_session(
        capability("destinatario_plantilla"), TextCheck(text), session_id=session_id, vault_dir=vault_dir,
        stage="remarketing",
    )
    return bool(verdict.value)


def register_label_decision() -> None:
    from src.plugins.chats.shared.agent_decisions import register_label_decider

    register_label_decider(decide_template_label)

