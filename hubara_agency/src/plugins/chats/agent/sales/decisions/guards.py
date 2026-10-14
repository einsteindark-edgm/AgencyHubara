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
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import RespuestaDeCantidad
from src.plugins.chats.agent.sales.decisions.capabilities.mapeos import (
    CategoriaPedida,
    CiudadDeEnvio,
    ColorPedido,
    DatoDelItem,
)
from src.plugins.chats.agent.sales.decisions.capability_rollout import DecisionMetrics
from src.plugins.chats.agent.sales.decisions.decision_log import SessionDecisionLog
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.plugins.chats.agent.sales.decisions.registry import capability, capability_if_present

__all__ = [
    "CategoriaPedida",
    "CiudadDeEnvio",
    "ColorPedido",
    "DatoDelItem",
    "RespuestaDeCantidad",
    "Verdict",
    "capability",
    "catalog_choice_buttons",
    "clean_llm_text",
    "customer_reply_text",
    "decide_for_session",
    "FACT_CAPABILITIES",
    "check_facts",
    "fact_words",
    "facts_in_bundle",
    "is_internal_text",
    "option_list",
    "product_quote_sentences",
    "promised_handoff",
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
    stage: str = "turno",
    message_id: str | None = None,
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
        decisions=SessionDecisionLog(vault) if vault is not None else None,
        stage=stage,
        message_id=message_id,
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

    from src.plugins.chats.agent.sales.decisions.capabilities.texto import Frases
    from src.plugins.chats.agent.sales.decisions.egress import OracionesCheck
    from src.sdk.textkit import customer_sentences, keep_customer_safe_sentences

    parts = customer_sentences(raw)
    if not parts:
        return ""
    # Las dos preguntas sobre las mismas oraciones, en paralelo.
    persona, destinatario = await asyncio.gather(
        decide_for_session(capability("persona"), Frases(parts=tuple(parts)), session_id=session_id, vault_dir=vault_dir),
        decide_for_session(
            capability("destinatario_oracion"), OracionesCheck(parts=tuple(parts)), session_id=session_id, vault_dir=vault_dir
        ),
    )
    drop = set(persona.value) | set(destinatario.value)
    return keep_customer_safe_sentences(raw, drop=drop)


async def product_quote_sentences(sentences: Sequence[str], *, session_id: str, vault_dir: Path) -> frozenset[str]:
    """De las oraciones donde las palabras de política aceptan un monto, las
    que cotizan el precio de un producto (capacidad `monto`, F5): pierden el
    contexto de política en `find_unexplained_amounts`. Con `reglas`,
    ninguna (idéntico a hoy)."""
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import OracionesPrecio

    if not sentences:
        return frozenset()
    verdict = await decide_for_session(
        capability("monto"),
        OracionesPrecio(sentences=tuple(sentences)),
        session_id=session_id,
        vault_dir=vault_dir,
    )
    return frozenset(verdict.value)


async def option_list(
    text: str, *, aromas: Sequence[str], colors: Sequence[str], session_id: str, vault_dir: Path | None
) -> tuple[Any, ...]:
    """¿El texto le enumera al cliente aromas o colores para que escoja uno?
    (capacidad `enumeracion`, la misma de la protección del workflow):
    `("scent" | "color", etiquetas)`, o `()` si no. Con `reglas`, cuatro o más
    etiquetas del catálogo."""
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import TextoCatalogo

    verdict = await decide_for_session(
        capability("enumeracion"),
        TextoCatalogo(text=text, aromas=tuple(aromas), colors=tuple(colors)),
        session_id=session_id,
        vault_dir=vault_dir,
    )
    return tuple(verdict.value or ())


#: Las decisiones de los hechos antes de enviar (ventas-7, 2026-10-13).
FACT_CAPABILITIES: tuple[str, ...] = (
    "aroma_ajeno", "color_ajeno", "variante_ajena", "precio_ajeno",
    "pago_ajeno", "estado_ajeno", "cupon_ajeno", "negacion_falsa", "cifra_del_cliente",
)


def facts_in_bundle() -> bool:
    """¿El paquete activo trae alguna decisión de los hechos? Con uno anterior
    (ventas-6 y antes) no hay nada que revisar ni que leer."""
    return any(capability_if_present(name) is not None for name in FACT_CAPABILITIES)


def fact_words(name: str) -> dict[str, Any] | None:
    """Las frases de una regla de palabras de los hechos (`estado_ajeno`,
    `cupon_ajeno`…): el `with:` de sus ítems en el paquete activo. None si el
    paquete no trae la decisión (no hay nada que revisar ni que consultar)."""
    cap = capability_if_present(name)
    return None if cap is None else cap.items_params


async def check_facts(text: str, facts: Any, *, session_id: str, vault_dir: Path | None) -> Any:
    """Lo que el texto le dice al cliente y no coincide con los hechos que
    armó el código (`use_cases.reply_facts.ReplyFacts`): cada capacidad del
    paquete revisa lo suyo, en paralelo (incidente 2026-10-13). Con un paquete
    anterior a una decisión (ventas-6 y antes), esa no revisa nada: todavía no
    existe para la tienda. Devuelve `reply_facts.Findings`."""
    import asyncio

    from src.plugins.chats.agent.sales.decisions.capabilities.texto import (
        CifraDelCliente,
        CuponDelTexto,
        EstadoDelTexto,
        NegacionDelTexto,
        OpcionesDelTexto,
        PagoDelTexto,
        PreciosDelTexto,
        VariantesDelTexto,
    )
    from src.plugins.chats.agent.sales.use_cases.reply_facts import Findings

    async def _nothing() -> tuple[()]:
        return ()

    async def ask(name: str, inp: Any, default: Any) -> Any:
        cap = capability_if_present(name)
        if cap is None:
            return default
        verdict = await decide_for_session(cap, inp, session_id=session_id, vault_dir=vault_dir)
        return verdict.value if verdict.value is not None else default

    from src.plugins.chats.shared.product_truth import without_names

    # El nombre del cliente no es un aroma ni un color («¡Hola Rosa!»).
    label_text = without_names(text, facts.names)

    def labels(own: Sequence[str], found: Sequence[str], others: Sequence[str]) -> OpcionesDelTexto:
        return OpcionesDelTexto(
            text=label_text, products=tuple(facts.titles), own=tuple(own), labels=tuple(found), others=tuple(others)
        )

    prices = PreciosDelTexto(
        text=text, products=tuple(facts.price_lines), prices=tuple(facts.prices), policy=tuple(facts.policy),
        surcharges=tuple(facts.surcharge_rates),
    )
    payment = PagoDelTexto(text=text, subtotal=facts.subtotal, cod_min=facts.cod_min, surcharges=tuple(facts.surcharges))
    status = EstadoDelTexto(text=text, order=facts.order, stage=facts.stage, pay_status=facts.pay_status)
    coupon = CuponDelTexto(
        text=text, known=tuple(facts.coupons_known), valid=tuple(facts.coupons_valid),
        valid_lines=tuple(facts.coupons_lines), valid_percents=tuple(facts.coupons_percents),
    )
    denial = NegacionDelTexto(
        text=text, titles=tuple(facts.titles_all), own=tuple(facts.own_labels), products=tuple(facts.titles)
    )
    figure = CifraDelCliente(
        text=text, customer=facts.customer_text, figures=tuple(facts.order_figures), order=facts.figure_truth
    )
    (aromas, colors, variant, wrong_prices, payments, states, coupons, denials, figures) = await asyncio.gather(
        ask("aroma_ajeno", labels(facts.own_aromas, facts.aromas, facts.colors), ()),
        ask("color_ajeno", labels(facts.own_colors, facts.colors, facts.aromas), ()),
        ask("variante_ajena", VariantesDelTexto(text=text, sheets=tuple(facts.sheets), single=tuple(facts.single)), False),
        ask("precio_ajeno", prices, ()),
        ask("pago_ajeno", payment, ()),
        ask("estado_ajeno", status, ()),
        # Sin los cupones leídos no se juzga ninguno (no saber no es «no hay»).
        ask("cupon_ajeno", coupon, ()) if facts.coupons_checked else _nothing(),
        ask("negacion_falsa", denial, ()),
        ask("cifra_del_cliente", figure, ()),
    )

    def pairs(name: str, terms: Sequence[str], inp: Any) -> tuple[tuple[str, str], ...]:
        """Lo que quedó, con lo cierto que leyó Jev (los mismos ítems del paquete)."""
        cap = capability_if_present(name)
        truth = {item["term"]: item["fact"] for item in (cap.items(inp) if cap is not None and terms else ())}
        return tuple((term, truth.get(term) or "lo que dice el sistema es distinto") for term in terms)

    return Findings(
        aromas=tuple(aromas), colors=tuple(colors), variant=bool(variant), prices=tuple(wrong_prices),
        payments=pairs("pago_ajeno", payments, payment), states=pairs("estado_ajeno", states, status),
        coupons=pairs("cupon_ajeno", coupons, coupon), denials=pairs("negacion_falsa", denials, denial),
        figures=pairs("cifra_del_cliente", figures, figure),
    )


def boxes_in_bundle() -> bool:
    """¿El paquete activo trae `cajas`? Con uno anterior (ventas-6 y antes),
    `set_order_slot` anota la cantidad como siempre."""
    return capability_if_present("cajas") is not None


async def boxes_reading(
    producto: str, cantidad: int, por_caja: int, precio: int | None, charla: str,
    *, session_id: str, vault_dir: Path | None,
) -> str | None:
    """¿La cantidad de un producto que se cobra por caja son cajas o velas?
    (capacidad `cajas`, laboratorio de ···0686, 2026-10-14): «cajas», «velas»
    o «duda». None si el paquete no trae la decisión (ventas-6 y antes): la
    cantidad se anota como siempre."""
    from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import CajasDelPedido

    cap = capability_if_present("cajas")
    if cap is None:
        return None
    verdict = await decide_for_session(
        cap,
        CajasDelPedido(producto=producto, cantidad=cantidad, por_caja=por_caja, precio=precio, charla=charla),
        session_id=session_id,
        vault_dir=vault_dir,
    )
    return verdict.value


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
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import Botones

    verdict = await decide_for_session(
        capability("selector"),
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
    saved: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Los datos de envío o de pago que el cliente NO dio (capacidad `datos`,
    F6): no se guardan. Con `reglas` (así nace), ninguno, y ni siquiera se
    lee el historial (`events` es perezoso). Lo que ya estaba guardado con el
    mismo valor (`saved`) no se vuelve a revisar: al confirmar, el LLM manda
    todo otra vez y el mensaje donde el cliente lo dio ya quedó atrás."""
    from src.plugins.chats.agent.sales.decisions.capabilities.datos import CHECKED_SLOTS, DatosDelPedido, same_value

    before = saved or {}
    pairs = tuple(
        (slot, value) for slot, value in values.items()
        if slot in CHECKED_SLOTS and isinstance(value, str) and value.strip() and not same_value(before.get(slot), value)
    )
    if not pairs:
        return ()
    vault = Path(vault_dir) if vault_dir is not None else None
    if bot_for_session(session_id, vault_dir=vault).provider("datos") == "reglas":
        return ()
    verdict = await decide_for_session(
        capability("datos"),
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
    from src.plugins.chats.agent.sales.decisions.egress import TextCheck

    if not text:
        return ""
    leak = await decide_for_session(capability("destinatario"), TextCheck(text), session_id=session_id, vault_dir=vault_dir)
    if not leak.value:
        return text
    rescued = await decide_for_session(capability("rescate"), TextCheck(text), session_id=session_id, vault_dir=vault_dir)
    return str(rescued.value or "")


async def clean_llm_text(raw: str | None, *, session_id: str, vault_dir: Path | None) -> str:
    """El texto del LLM limpio por el saneador de la plataforma (escapes,
    comillas, duplicados, rayas), con la muletilla de presentación del modelo
    («Aquí tienes:») decidida por el motor: capacidad `preambulo`, con el
    proveedor del bot de esta conversación. Con `reglas` (así nace),
    idéntico a `sanitize_llm_text(raw).text`."""
    from src.plugins.chats.agent.sales.decisions.egress import PreambuloCheck, text_without_preamble
    from src.sdk.textkit import preamble_stage, sanitize_llm_text

    stage = preamble_stage(raw)
    if not stage:
        return sanitize_llm_text(raw or "").text
    verdict = await decide_for_session(capability("preambulo"), PreambuloCheck(stage), session_id=session_id, vault_dir=vault_dir)
    return sanitize_llm_text(raw or "", without_preamble=text_without_preamble(stage, str(verdict.value or ""))).text


async def is_internal_text(text: str, *, session_id: str, vault_dir: Path | None) -> bool:
    """¿El texto de un intent (intro, cuerpo, pie de foto) NO es para el
    cliente? (capacidad `destinatario`, F5; regla de hoy del flush: el set
    básico de `looks_like_admin_leak`)."""
    from src.plugins.chats.agent.sales.decisions.egress import TextCheck

    verdict = await decide_for_session(
        capability("destinatario"), TextCheck(text, extended=False), session_id=session_id, vault_dir=vault_dir
    )
    return bool(verdict.value)


async def promised_handoff(text: str, *, session_id: str, vault_dir: Path | None) -> bool:
    """¿El texto final le promete al cliente que una persona del equipo lo va
    a atender? (capacidad `relevo`: las frases del relevo son piso; con Jev,
    también sus paráfrasis). Lo consulta la red de seguridad antes de enviar."""
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import TextoAlCliente

    verdict = await decide_for_session(capability("relevo"), TextoAlCliente(text=text), session_id=session_id, vault_dir=vault_dir)
    return bool(verdict.value)
