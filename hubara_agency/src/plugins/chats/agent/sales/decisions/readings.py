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
from src.plugins.chats.agent.sales.decisions.bundled import bundled_capability
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas import Acuse, Compra, Retoma
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import (
    Cupon,
    CuponEnJuego,
    FueraDeCatalogo,
    PedidoDelCliente,
)
from src.plugins.chats.agent.sales.decisions.capability_rollout import DecisionMetrics
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.plugins.chats.agent.sales.decisions.guards import decide_for_session

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
            )

        # Paquete de decisión (PAQUETES_DE_DECISION.md F1): baja y cortesía
        # salen del YAML certificado, con paridad exacta con sus clases.
        baja = bundled_capability("baja")
        marketing = has_recent_marketing_context(dict(inbound.metadata), inbound.now_ms)
        compra_v, retoma_v, baja_v, cortesia_v = await asyncio.gather(
            run(Compra()),
            run(Retoma()),
            run(baja) if marketing else _completed(_rule_verdict_off(baja, inbound)),
            run(bundled_capability("cortesia")),
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
        capability = Acuse()
        provider = bot.provider(capability.name)
        redact = self._redact
        if provider != "reglas":
            redact = tuple(dict.fromkeys([*redact, *_last_episode_redact_terms(inbound.metadata)]))
        return await decide(
            capability, inbound, provider=provider, profile_id=bot.profile,
            disagreements=DisagreementLog(self._vault), session_id=inbound.session_id, redact=redact,
            metrics=DecisionMetrics(self._vault),
        )


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
    marketing (sticky: solo la revierte el operador). La usan el ingest y el
    sandbox del laboratorio: los dos escriben exactamente lo mismo."""
    from src.plugins.chats.shared.purchase_signals import apply_inbound_purchase_signal
    from src.sdk.messagingkit import OPT_OUT_SOURCE_TEXT, apply_reengagement_deferral, mark_marketing_opt_out

    signal = apply_inbound_purchase_signal(
        metadata, readings.purchase[0], readings.purchase[1], now_ms=now_ms, message_id=message_id, text=text
    )
    apply_reengagement_deferral(metadata, text, readings.deferral, courtesy=readings.courtesy, now_ms=now_ms, tz=tz)
    opted_out = False
    if not metadata.get("marketing_opt_out") and text and readings.opt_out:
        mark_marketing_opt_out(metadata, now_ms=now_ms, source=OPT_OUT_SOURCE_TEXT, campaign_id=opt_out_campaign_id)
        opted_out = True
    # La cortesía vale para ESTE mensaje: la leen la nota del episodio nuevo,
    # el paso desde remarketing y la guía del turno (con `reglas`, nunca).
    if readings.courtesy_only:
        metadata[COURTESY_KEY] = {"message_id": message_id, "at_ms": now_ms}
    else:
        metadata.pop(COURTESY_KEY, None)
    return Written(signal=signal, opted_out=opted_out)


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
        Cupon(), CuponEnJuego(metadata=metadata, text=text, events=tuple(events)),
        session_id=session_id, vault_dir=Path(vault_dir), redact=tuple(redact),
    )


async def read_catalog_gap(
    vault_dir: Path,
    *,
    session_id: str,
    text: str,
    products: Sequence[Any],
    redact: Sequence[str] = (),
) -> Verdict:
    """Lo que el cliente pide o muestra y no existe en el catálogo (capacidad
    `fuera_de_catalogo`; regla de hoy: `unavailable_terms`). El valor son los
    términos que quedan: la nota la arma el ingest con el código de hoy."""
    return await decide_for_session(
        FueraDeCatalogo(), PedidoDelCliente(text=text, products=tuple(products)),
        session_id=session_id, vault_dir=Path(vault_dir), redact=tuple(redact),
    )


def _rule_verdict_off(capability: Any, inbound: Inbound) -> Verdict:
    """Sin promoción reciente la baja no se lee (condición de hoy)."""
    return Verdict(
        capability=capability.name, value=False, by=BY_RULE, provider="reglas", rule=False, reason="sin_contexto",
        bundle=str(getattr(capability, "bundle", "") or ""),
    )


async def _completed(verdict: Verdict) -> Verdict:
    return verdict
