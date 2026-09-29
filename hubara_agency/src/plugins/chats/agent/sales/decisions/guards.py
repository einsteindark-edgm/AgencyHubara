"""Guardia del motor para las tools (diseño v2 §03, regla 5): lo ÚNICO que las
tools importan del motor. Sin Temporal (contrato `tools-no-temporal`).

Una tool que hoy decide con una regla de texto (un mapeo a una lista
cerrada, una revisión del texto del LLM) le pide la decisión al motor con
`decide_for_session`: el motor corre la capacidad con el proveedor que el
registro de bots dice para esa conversación (`reglas`, `sombra` o `jev`),
anota los desacuerdos para que los califique Claude Code y devuelve un
`Verdict`. Con `reglas` (así nace todo) el resultado es el de la regla de hoy
y Jev no se consulta.

También reexporta las capacidades que piden los consumidores de fuera del
motor (fase F3): la cantidad de una respuesta compuesta (activity del prompt)
y los mapeos a listas cerradas de las tools (categoría, familia de color,
ítem del pedido y zona de envío).

El texto del LLM que una tool recibe para el cliente pasa primero por
`clean_llm_text`: el saneador de la plataforma con la muletilla del modelo
decidida por el motor (capacidad `preambulo`).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.bots import bot_for_session
from src.plugins.chats.agent.sales.decisions.capabilities import Verdict, decide
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import Cantidad, RespuestaDeCantidad
from src.plugins.chats.agent.sales.decisions.capabilities.mapeos import (
    Categoria,
    CategoriaPedida,
    CiudadDeEnvio,
    ColorPedido,
    DatoDelItem,
    FamiliaDeColor,
    ItemDelPedido,
    ZonaDeEnvio,
)
from src.plugins.chats.agent.sales.decisions.capability_rollout import DecisionMetrics
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog

__all__ = [
    "Cantidad",
    "Categoria",
    "CategoriaPedida",
    "CiudadDeEnvio",
    "ColorPedido",
    "DatoDelItem",
    "FamiliaDeColor",
    "ItemDelPedido",
    "RespuestaDeCantidad",
    "Verdict",
    "ZonaDeEnvio",
    "catalog_choice_buttons",
    "clean_llm_text",
    "customer_reply_text",
    "decide_for_session",
    "is_internal_text",
    "product_quote_sentences",
    "safe_customer_text",
    "session_redact_terms",
    "unconfirmed_order_data",
]


async def decide_for_session(
    capability: Any,
    inp: Any,
    *,
    session_id: str,
    vault_dir: Path | None,
    redact: tuple[str, ...] = (),
) -> Verdict:
    """La decisión de la capacidad para ESTA conversación (ver el módulo).
    Sin vault (una tool armada sin él) no hay control del despliegue ni cola
    de desacuerdos: decide el bot fijado del laboratorio o la regla de hoy.

    Lo que sale hacia Jev va con los datos personales del borrador de ESTA
    conversación tapados aunque la tool no los pase (se leen solo si de
    verdad se le pregunta a Jev: con `reglas` no hay I/O extra)."""
    vault = Path(vault_dir) if vault_dir is not None else None
    bot = bot_for_session(session_id, vault_dir=vault)
    provider = bot.provider(capability.name)
    if not redact and vault is not None and provider != "reglas":
        redact = session_redact_terms(session_id, vault)
    return await decide(
        capability,
        inp,
        provider=provider,
        profile_id=bot.profile,
        disagreements=DisagreementLog(vault) if vault is not None else None,
        session_id=session_id,
        redact=redact,
        metrics=DecisionMetrics(vault) if vault is not None else None,
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
    import asyncio

    from src.plugins.chats.agent.sales.decisions.capabilities.texto import PERSONA, Frases
    from src.plugins.chats.agent.sales.decisions.egress import DestinatarioPorOracion, OracionesCheck
    from src.sdk.textkit import customer_sentences, keep_customer_safe_sentences

    parts = customer_sentences(raw)
    if not parts:
        return ""
    # Las dos preguntas sobre las mismas oraciones, en paralelo.
    persona, destinatario = await asyncio.gather(
        decide_for_session(PERSONA, Frases(parts=tuple(parts)), session_id=session_id, vault_dir=vault_dir),
        decide_for_session(
            DestinatarioPorOracion(), OracionesCheck(parts=tuple(parts)), session_id=session_id, vault_dir=vault_dir
        ),
    )
    drop = set(persona.value) | set(destinatario.value)
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
    )
    return tuple(verdict.value)


async def unconfirmed_order_data(
    values: Mapping[str, Any],
    events: Callable[[], Sequence[Mapping[str, Any]]],
    *,
    session_id: str,
    vault_dir: Path | None,
) -> tuple[str, ...]:
    """Los datos de envío o de pago que el cliente NO dio (capacidad `datos`,
    F6): no se guardan. Con `reglas` (así nace), ninguno, y ni siquiera se
    lee el historial (`events` es perezoso)."""
    from src.plugins.chats.agent.sales.decisions.capabilities.datos import CHECKED_SLOTS, DATOS, DatosDelPedido

    pairs = tuple(
        (slot, value) for slot, value in values.items()
        if slot in CHECKED_SLOTS and isinstance(value, str) and value.strip()
    )
    if not pairs:
        return ()
    vault = Path(vault_dir) if vault_dir is not None else None
    if bot_for_session(session_id, vault_dir=vault).provider(DATOS.name) == "reglas":
        return ()
    verdict = await decide_for_session(
        DATOS,
        DatosDelPedido(values=pairs, events=tuple(events())),
        session_id=session_id,
        vault_dir=vault_dir,
    )
    return tuple(verdict.value)


async def customer_reply_text(text: str, *, session_id: str, vault_dir: Path | None) -> str:
    """Lo que sale al cliente de un `send_reply` (capacidades `destinatario` y
    `rescate`, F5; las mismas del egreso de V2): si el texto no es para el
    cliente, se rescatan los párrafos que sí; "" = nada. Con `reglas`,
    idéntico a hoy (`looks_like_admin_leak` extendido + `salvage_customer_text`)."""
    from src.plugins.chats.agent.sales.decisions.egress import Destinatario, Rescate, TextCheck

    if not text:
        return ""
    leak = await decide_for_session(Destinatario(), TextCheck(text), session_id=session_id, vault_dir=vault_dir)
    if not leak.value:
        return text
    rescued = await decide_for_session(Rescate(), TextCheck(text), session_id=session_id, vault_dir=vault_dir)
    return str(rescued.value or "")


async def clean_llm_text(raw: str | None, *, session_id: str, vault_dir: Path | None) -> str:
    """El texto del LLM limpio por el saneador de la plataforma (escapes,
    comillas, duplicados, rayas), con la muletilla de presentación del modelo
    («Aquí tienes:») decidida por el motor: capacidad `preambulo`, con el
    proveedor del bot de esta conversación. Con `reglas` (así nace),
    idéntico a `sanitize_llm_text(raw).text`."""
    from src.plugins.chats.agent.sales.decisions.egress import Preambulo, PreambuloCheck, text_without_preamble
    from src.sdk.textkit import preamble_stage, sanitize_llm_text

    stage = preamble_stage(raw)
    if not stage:
        return sanitize_llm_text(raw or "").text
    verdict = await decide_for_session(Preambulo(), PreambuloCheck(stage), session_id=session_id, vault_dir=vault_dir)
    return sanitize_llm_text(raw or "", without_preamble=text_without_preamble(stage, str(verdict.value or ""))).text


async def is_internal_text(text: str, *, session_id: str, vault_dir: Path | None) -> bool:
    """¿El texto de un intent (intro, cuerpo, pie de foto) NO es para el
    cliente? (capacidad `destinatario`, F5; regla de hoy del flush: el set
    básico de `looks_like_admin_leak`)."""
    from src.plugins.chats.agent.sales.decisions.egress import Destinatario, TextCheck

    verdict = await decide_for_session(
        Destinatario(), TextCheck(text, extended=False), session_id=session_id, vault_dir=vault_dir
    )
    return bool(verdict.value)

