"""Proveedor de lecturas del ingest (diseño v2 §08, enchufe 1).

El ingest le pide al motor las lecturas del mensaje del cliente y escribe los
MISMOS campos de hoy (`last_inbound_signal` y la confirmación de compra,
`reengagement_deferral`, `marketing_opt_out`). Esos campos los leen las tools
de ventas, remarketing, el watchdog y el ciclo de reactivación: ninguno se
toca, así las lecturas con Jev sirven a V1 sin esperar a V2.

Cada lectura es una capacidad (`capabilities/lecturas.py`) con el proveedor
que dice el registro de bots para la conversación: con `reglas` (así nace)
el resultado es el de hoy y Jev no se consulta. Las tres corren en paralelo y
cada una tiene un tiempo máximo corto (1,5 s): el workflow igual espera 1,5 s
de silencio antes del turno. El laboratorio usa este mismo proveedor, con el
bot del brazo.

Lecturas sueltas (fase F3): el cupón (`read_coupon_talk`) y lo que no existe
en el catálogo (`read_catalog_gap`) no escriben campos del metadata ni leen el
texto crudo: el ingest las pide más adelante, con el texto efectivo (un audio
o una foto ya leídos), y usa el valor para una relectura o una nota.

El acuse tras la despedida (`EngineReadings.read_ack`, capacidad `acuse`)
tampoco escribe campos: decide si el mensaje despierta al agente, así que el
ingest lo pide ANTES del ciclo de episodios, con el mismo proveedor.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from src.plugins.chats.agent.sales.decisions.bots import bot_for_session
from src.plugins.chats.agent.sales.decisions.capabilities import BY_RULE, Verdict, decide
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import CuponEnJuego, PedidoDelCliente
from src.plugins.chats.agent.sales.decisions.capability_rollout import DecisionMetrics
from src.plugins.chats.agent.sales.decisions.decision_log import SessionDecisionLog
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.plugins.chats.agent.sales.decisions.registry import capability
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session
from src.plugins.chats.agent.sales.decisions.retiro import en_retiro

logger = structlog.get_logger()


@dataclass(frozen=True)
class Inbound:
    """Un mensaje del cliente, como lo ve el ingest ANTES de escribir nada."""

    session_id: str
    text: str | None
    now_ms: int
    message_id: str | None = None
    interactive: Mapping[str, Any] | None = None
    order: Mapping[str, Any] | None = None
    # Metadata de la sesión tal como está antes de este mensaje (solo lectura).
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # Historial del dashboard ANTES de este mensaje (lo que el cliente vio).
    events: Sequence[Mapping[str, Any]] = ()
    # Etapa del embudo que calcula el código (para los hechos del pedido).
    stage: str = "etapa_descubrimiento"
    tz: Any = None
    # El texto lo escribió la visión (descripción de una foto): no es del cliente.
    synthetic: bool = False


#: Metadata: el último mensaje del cliente que solo agradece o saluda.
COURTESY_KEY = "last_inbound_courtesy"


def last_inbound_is_courtesy(metadata: Mapping[str, Any]) -> bool:
    """¿La marca de cortesía es del ÚLTIMO mensaje del cliente?"""
    mark = metadata.get(COURTESY_KEY)
    return isinstance(mark, Mapping) and bool(mark.get("message_id")) and (
        mark.get("message_id") == metadata.get("last_inbound_message_id")
    )


@dataclass(frozen=True)
class Readings:
    purchase: tuple[str | None, str]
    deferral: Any  # ReengagementDeferral | None
    courtesy: bool
    opt_out: bool
    verdicts: tuple[dict[str, Any], ...] = ()
    # El cliente solo agradece o saluda (capacidad `cortesia`): el turno no
    # abre venta. Con `reglas`, siempre False.
    courtesy_only: bool = False


@en_retiro("funcion:_rule_verdict")
def _rule_verdict(capability: Any, inp: Inbound) -> Verdict:
    rule = capability.rule(inp)
    return Verdict(capability=capability.name, value=rule, by=BY_RULE, provider="reglas", rule=rule)


class EngineReadings:
    """Las lecturas del cliente con el motor (el proveedor del ingest)."""

    def __init__(self, vault_dir: Path, *, redact: Sequence[str] = ()) -> None:
        self._vault = Path(vault_dir)
        self._redact = tuple(redact)

    async def read(self, inbound: Inbound) -> Readings:
        from src.sdk.messagingkit import ReengagementDeferral, has_recent_marketing_context

        bot = bot_for_session(inbound.session_id, vault_dir=self._vault)
        log = DisagreementLog(self._vault)
        metrics = DecisionMetrics(self._vault)

        def run(capability: Any):
            return decide(
                capability, inbound, provider=bot.provider(capability.name), profile_id=bot.profile,
                disagreements=log, session_id=inbound.session_id, redact=self._redact, metrics=metrics,
                decisions=SessionDecisionLog(self._vault), stage="ingest", message_id=inbound.message_id,
            )

        # Cada capacidad por su nombre: el resolutor la toma del paquete de
        # decisión de la tienda o de su clase (PAQUETES_DE_DECISION.md §10.1).
        baja = capability("baja")
        marketing = has_recent_marketing_context(dict(inbound.metadata), inbound.now_ms)
        compra_v, retoma_v, baja_v, cortesia_v = await asyncio.gather(
            run(capability("compra")),
            run(capability("retoma")),
            run(baja) if marketing else _completed(_rule_verdict_off(baja, inbound)),
            run(capability("cortesia")),
        )
        deferral = (retoma_v.value or {}).get("deferral")
        return Readings(
            purchase=(compra_v.value[0], compra_v.value[1]),
            deferral=ReengagementDeferral(until_ms=int(deferral["until_ms"]), kind=str(deferral["kind"])) if deferral else None,
            courtesy=bool((retoma_v.value or {}).get("courtesy")),
            opt_out=bool(baja_v.value),
            verdicts=tuple(v.to_trace() for v in (compra_v, retoma_v, baja_v, cortesia_v)),
            courtesy_only=bool(cortesia_v.value),
        )

    async def read_ack(self, inbound: Inbound) -> Verdict:
        """¿El mensaje solo le acusa recibo a la despedida del agente?
        (capacidad `acuse`; regla de hoy: `is_closing_ack`). El ingest la pide
        ANTES del ciclo de episodios y solo cuando lo estructural ya lo
        permite (el agente se despidió y el mensaje es texto). Lo que sale
        hacia Jev tapa los datos del borrador del episodio que se despidió
        (`session_redact_terms` solo mira el episodio abierto)."""
        bot = bot_for_session(inbound.session_id, vault_dir=self._vault)
        acuse = capability("acuse")
        provider = bot.provider(acuse.name)
        redact = self._redact
        if provider != "reglas":
            redact = tuple(dict.fromkeys([*redact, *_last_episode_redact_terms(inbound.metadata)]))
        return await decide(
            acuse, inbound, provider=provider, profile_id=bot.profile,
            disagreements=DisagreementLog(self._vault), session_id=inbound.session_id, redact=redact,
            metrics=DecisionMetrics(self._vault),
            decisions=SessionDecisionLog(self._vault), stage="ingest", message_id=inbound.message_id,
        )


def rules_readings(inbound: Inbound) -> Readings:
    """Las lecturas con las reglas del código, sin el motor (las del proveedor
    `reglas`): las usa el ingest si el motor no puede leer (p. ej. el paquete
    de la tienda no compila). El mensaje nunca se pierde y la baja explícita
    se respeta (premortem 2026-10-02)."""
    from src.plugins.chats.agent.sales.decisions import bundled_ingest as rules
    from src.sdk.messagingkit import ReengagementDeferral, has_recent_marketing_context

    kind, source = rules.purchase_signal(inbound)
    retoma = rules.reengagement(inbound)
    deferral = retoma.get("deferral")
    marketing = has_recent_marketing_context(dict(inbound.metadata), inbound.now_ms)
    return Readings(
        purchase=(kind, source),
        deferral=ReengagementDeferral(until_ms=int(deferral["until_ms"]), kind=str(deferral["kind"])) if deferral else None,
        courtesy=bool(retoma.get("courtesy")),
        opt_out=bool(marketing and rules.opt_out_text(inbound)),
    )


def rules_ack(inbound: Inbound) -> Verdict:
    """El acuse con la regla del código (`is_closing_ack`), sin el motor."""
    from src.plugins.chats.agent.sales.decisions import bundled_ingest as rules

    value = bool(rules.closing_ack(inbound))
    return Verdict(capability="acuse", value=value, by=BY_RULE, provider="reglas", rule=value, reason="engine_error")


def _last_episode_redact_terms(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """Los datos personales del borrador del ÚLTIMO episodio, abierto o no."""
    from src.plugins.chats.agent.sales.decisions.context import redact_terms_from_slots

    episodes = metadata.get("episodes") if isinstance(metadata, Mapping) else None
    last = episodes[-1] if isinstance(episodes, list) and episodes and isinstance(episodes[-1], Mapping) else {}
    draft = last.get("order_draft")
    slots = draft.get("slots") if isinstance(draft, Mapping) else None
    return tuple(redact_terms_from_slots(slots if isinstance(slots, Mapping) else {}))


@dataclass(frozen=True)
class Written:
    signal: str | None  # la señal de compra escrita (o None)
    opted_out: bool  # se marcó la baja en ESTE mensaje


def apply_readings(
    metadata: dict[str, Any],
    readings: Readings,
    *,
    text: str | None,
    now_ms: int,
    message_id: str | None,
    tz: Any,
    opt_out_campaign_id: str | None,
) -> Written:
    """ESCRIBE las lecturas en el metadata (mutación), igual que siempre: la
    señal de compra y la confirmación, la pausa de reactivación y la baja de
    marketing (sticky: solo la revierte el operador). La usa el sandbox del
    laboratorio; el ingest escribe las mismas dos partes en pasos distintos
    (`apply_lasting_readings` al leer, `apply_last_message_marks` en el orden
    de llegada) y el resultado es exactamente el mismo."""
    written = apply_lasting_readings(
        metadata, readings, text=text, now_ms=now_ms, opt_out_campaign_id=opt_out_campaign_id
    )
    apply_last_message_marks(
        metadata, last_message_marks(readings), text=text, now_ms=now_ms, message_id=message_id, tz=tz
    )
    return written


def apply_lasting_readings(
    metadata: dict[str, Any],
    readings: Readings,
    *,
    text: str | None,
    now_ms: int,
    opt_out_campaign_id: str | None,
) -> Written:
    """Lo que vale aunque después llegue otro mensaje: la confirmación de
    compra en el borrador (con producto, o el carrito) y la baja de marketing
    (sticky: solo la revierte el operador)."""
    from src.plugins.chats.shared.purchase_signals import confirm_inbound_purchase
    from src.sdk.messagingkit import OPT_OUT_SOURCE_TEXT, mark_marketing_opt_out

    signal = confirm_inbound_purchase(metadata, readings.purchase[0], readings.purchase[1], now_ms=now_ms)
    opted_out = False
    if not metadata.get("marketing_opt_out") and text and readings.opt_out:
        mark_marketing_opt_out(metadata, now_ms=now_ms, source=OPT_OUT_SOURCE_TEXT, campaign_id=opt_out_campaign_id)
        opted_out = True
    return Written(signal=signal, opted_out=opted_out)


def last_message_marks(readings: Readings) -> dict[str, Any]:
    """Las lecturas que describen al ÚLTIMO mensaje del cliente, como JSON
    (la entrada durable las pasa del paso del estado al del historial): la
    señal de compra, el aplazamiento y la cortesía."""
    deferral = readings.deferral
    return {
        "purchase": readings.purchase[0],
        "deferral": {"until_ms": deferral.until_ms, "kind": deferral.kind} if deferral is not None else None,
        "courtesy": readings.courtesy,
        "courtesy_only": readings.courtesy_only,
    }


def apply_last_message_marks(
    metadata: dict[str, Any],
    marks: Mapping[str, Any],
    *,
    text: str | None,
    now_ms: int,
    message_id: str | None,
    tz: Any,
) -> None:
    """ESCRIBE lo que describe al ÚLTIMO mensaje del cliente: la señal de
    compra (`SIGNAL_KEY`), la pausa por aplazamiento (una fecha; otro texto la
    levanta) y la cortesía. Cada mensaje pisa las del anterior, así que se
    escriben en el orden de llegada, con `last_inbound_message_id` (si la foto
    que reentra tarde las escribiera al leer, borraría las del texto que el
    cliente mandó después)."""
    from src.plugins.chats.shared.purchase_signals import mark_inbound_signal
    from src.sdk.messagingkit import ReengagementDeferral, apply_reengagement_deferral

    mark_inbound_signal(metadata, marks.get("purchase"), now_ms=now_ms, message_id=message_id, text=text)
    raw = marks.get("deferral")
    deferral = (
        ReengagementDeferral(until_ms=int(raw["until_ms"]), kind=str(raw["kind"])) if isinstance(raw, Mapping) else None
    )
    apply_reengagement_deferral(metadata, text, deferral, courtesy=bool(marks.get("courtesy")), now_ms=now_ms, tz=tz)
    # La cortesía vale para ESTE mensaje: la leen la nota del episodio nuevo,
    # el paso desde remarketing y la guía del turno (con `reglas`, nunca).
    if marks.get("courtesy_only"):
        metadata[COURTESY_KEY] = {"message_id": message_id, "at_ms": now_ms}
    else:
        metadata.pop(COURTESY_KEY, None)


async def read_coupon_talk(
    vault_dir: Path,
    *,
    session_id: str,
    metadata: Mapping[str, Any],
    text: str | None,
    events: Sequence[Mapping[str, Any]] = (),
    redact: Sequence[str] = (),
) -> Verdict:
    """¿Este mensaje habla del cupón aplicado? (capacidad `cupon`; regla de
    hoy: `coupon_in_play`). No escribe campos del metadata: el ingest decide
    con el valor si relee el cupo y qué nota arma. `events`: lo que el
    cliente vio ANTES de este mensaje."""
    return await decide_for_session(
        capability("cupon"), CuponEnJuego(metadata=metadata, text=text, events=tuple(events)),
        session_id=session_id, vault_dir=Path(vault_dir), redact=tuple(redact), stage="ingest",
    )


async def read_catalog_gap(
    vault_dir: Path,
    *,
    session_id: str,
    text: str,
    products: Sequence[Any],
    redact: Sequence[str] = (),
    stage: str = "ingest",
) -> Verdict:
    """Lo que el cliente pide o muestra y no existe en el catálogo (capacidad
    `fuera_de_catalogo`; regla de hoy: `unavailable_terms`). El valor son los
    términos que quedan: la nota la arma el ingest con el código de hoy."""
    return await decide_for_session(
        capability("fuera_de_catalogo"), PedidoDelCliente(text=text, products=tuple(products)),
        session_id=session_id, vault_dir=Path(vault_dir), redact=tuple(redact), stage=stage,
    )


def _rule_verdict_off(capability: Any, inbound: Inbound) -> Verdict:
    """Sin promoción reciente la baja no se lee (condición de hoy)."""
    return Verdict(
        capability=capability.name, value=False, by=BY_RULE, provider="reglas", rule=False, reason="sin_contexto",
        bundle=str(getattr(capability, "bundle", "") or ""),
    )


async def _completed(verdict: Verdict) -> Verdict:
    return verdict
