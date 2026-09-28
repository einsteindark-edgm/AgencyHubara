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
from src.plugins.chats.agent.sales.decisions.capabilities.lecturas import Baja, Compra, Retoma
from src.plugins.chats.agent.sales.decisions.capability_rollout import DecisionMetrics
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog

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


@dataclass(frozen=True)
class Readings:
    purchase: tuple[str | None, str]
    deferral: Any  # ReengagementDeferral | None
    courtesy: bool
    opt_out: bool
    verdicts: tuple[dict[str, Any], ...] = ()


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

        baja = Baja()
        marketing = has_recent_marketing_context(dict(inbound.metadata), inbound.now_ms)
        compra_v, retoma_v, baja_v = await asyncio.gather(
            run(Compra()),
            run(Retoma()),
            run(baja) if marketing else _completed(_rule_verdict_off(baja, inbound)),
        )
        deferral = (retoma_v.value or {}).get("deferral")
        return Readings(
            purchase=(compra_v.value[0], compra_v.value[1]),
            deferral=ReengagementDeferral(until_ms=int(deferral["until_ms"]), kind=str(deferral["kind"])) if deferral else None,
            courtesy=bool((retoma_v.value or {}).get("courtesy")),
            opt_out=bool(baja_v.value),
            verdicts=tuple(v.to_trace() for v in (compra_v, retoma_v, baja_v)),
        )


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
    return Written(signal=signal, opted_out=opted_out)


def _rule_verdict_off(capability: Any, inbound: Inbound) -> Verdict:
    """Sin promoción reciente la baja no se lee (condición de hoy)."""
    return Verdict(capability=capability.name, value=False, by=BY_RULE, provider="reglas", rule=False, reason="sin_contexto")


async def _completed(verdict: Verdict) -> Verdict:
    return verdict
