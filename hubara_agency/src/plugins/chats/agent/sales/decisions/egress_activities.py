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

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

import structlog
from temporalio import activity

from src.plugins.chats.agent.sales.decisions import egress
from src.plugins.chats.agent.sales.decisions.bots import DEFAULT_PROFILE, bot_for_session
from src.plugins.chats.agent.sales.decisions.bundled_claims import ClaimFacts
from src.plugins.chats.agent.sales.decisions.capability_rollout import DecisionMetrics
from src.plugins.chats.agent.sales.decisions.decision_log import SessionDecisionLog
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.plugins.chats.agent.sales.decisions.guards import session_redact_terms
from src.sdk.textkit import is_no_message_abstention

logger = structlog.get_logger()


def _vault_dir() -> Path:
    from src.sdk.runtime import WORKSPACE_VAULT_DIR

    return Path(WORKSPACE_VAULT_DIR)


def _rules_only(_capability: str) -> str:
    return "reglas"


def _reply_checker() -> Any:
    """`send_reply` armado como en el worker de ventas: sus revisiones (la
    foto, «te confirmo después», los hechos, la lista para el selector) son
    las que pasa también el texto final suelto."""
    from src.plugins.chats.agent.sales.composition import build_session_history_reader, build_vault_dir
    from src.plugins.chats.agent.sales.tools.fact_check import FactCheck
    from src.plugins.chats.agent.sales.tools.reply import SendReplyTool
    from src.sdk.connectorkit import get_order_facts_port, get_promotions_port

    vault, catalog = build_vault_dir(), _catalog_client()
    try:
        order_facts = get_order_facts_port()
    except Exception:  # noqa: BLE001 — sin Medusa, los hechos del pedido no se revisan
        order_facts = None
    fact_check = FactCheck(
        vault, catalog, promotions=get_promotions_port(), order_facts=order_facts,
        history_reader=build_session_history_reader(),
    )
    return SendReplyTool(workspace=str(vault), vault_dir=vault, catalog=catalog, fact_check=fact_check)


def _catalog_client() -> Any:
    from src.sdk.catalogkit import get_catalog_client

    return get_catalog_client()


#: Lo que se espera al catálogo por los precios: si no responde, el hecho del
#: precio no se afirma (el texto nunca espera más que esto por la puerta).
CATALOG_TIMEOUT_S = 3.0


async def _catalog_prices() -> tuple[int, ...]:
    from src.plugins.chats.agent.sales.catalog_scope import WHOLE_CATALOG
    from src.plugins.chats.agent.sales.pricing import catalog_prices

    found = await asyncio.wait_for(_catalog_client().search(q="", limit=WHOLE_CATALOG), timeout=CATALOG_TIMEOUT_S)
    return tuple(sorted({price for product in found.results for price in catalog_prices(product)}))


async def claim_facts(session_id: str, vault: Path) -> ClaimFacts:
    """Lo que el sistema sabe para la puerta de afirmaciones (`bundled_claims`):
    el metadata de la conversación (el pedido registrado), los precios del
    catálogo, los montos de la política (tarifas de envío y mínimo del contra
    entrega), los recargos del link, la llave pública de pago y la frase
    aprobada de tiempos del dominio de la tienda. Cada fuente que falla queda
    vacía (el hecho no se afirma) y nunca frena el texto."""
    from src.plugins.chats.agent.sales.config.payments import (
        PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA,
        PAYMENT_LINK_SURCHARGE_OTHER_BANKS,
        get_nequi_number,
    )
    from src.plugins.chats.agent.sales.config.shipping import (
        CASH_ON_DELIVERY_MIN_PRODUCTS_COP,
        SHIPPING_RATE_BOGOTA_COP,
        SHIPPING_RATE_NATIONAL_COP,
    )
    from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
    from src.plugins.chats.agent.sales.use_cases.fact_rules import percent
    from src.plugins.chats.shared.store_pack import store_domain

    def _quiet(source: str, exc: Exception) -> None:
        logger.warning("decisions.claim_facts_unavailable", source=source, error=repr(exc)[:200])

    metadata: dict[str, Any] = {}
    try:
        metadata = FilesystemMetadataStore(vault).read(session_id) or {}
    except Exception as exc:  # noqa: BLE001 — sin metadata, sin pedido conocido
        _quiet("metadata", exc)
    prices: tuple[int, ...] = ()
    try:
        prices = await _catalog_prices()
    except Exception as exc:  # noqa: BLE001 — sin catálogo, el precio no se juzga
        _quiet("catalog", exc)
    surcharges: list[float] = []
    for raw in (PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA, PAYMENT_LINK_SURCHARGE_OTHER_BANKS):
        try:
            surcharges.append(percent(raw))
        except ValueError:
            continue
    phrases = ""
    try:
        phrases = str(store_domain().get("delivery_time_phrases") or "")
    except Exception as exc:  # noqa: BLE001 — sin dominio, ninguna frase aprobada
        _quiet("domain", exc)
    key = "".join(ch for ch in get_nequi_number() if ch.isdigit())
    return ClaimFacts(
        metadata=metadata,
        known_prices=prices,
        policy_amounts=(CASH_ON_DELIVERY_MIN_PRODUCTS_COP, SHIPPING_RATE_BOGOTA_COP, SHIPPING_RATE_NATIONAL_COP),
        surcharges=tuple(surcharges),
        public_keys=(key,) if key else (),
        delivery_phrases=phrases,
    )


async def _loose_text_note(inp: EgressInput, text: str) -> str:
    """El mensaje con que `send_reply` retendría el texto final SUELTO que va
    a salir ("" = sale): el camino que se saltaba sus revisiones (testigo
    `texto_suelto_*`, plan del motor §6.6). Solo el texto que escribió el LLM
    sin `send_reply` (`raw_text`): el de `send_reply` ya pasó por ellas. Nunca
    falla: si la revisión no se puede hacer, el texto sale."""
    if inp.raw_text is None or inp.admin_turn or not text.strip() or is_no_message_abstention(text):
        return ""
    try:
        envelope = await _reply_checker().held_reason(inp.session_id, text)
        if not envelope:
            return ""
        return str(json.loads(envelope).get("message") or "").strip()
    except Exception as exc:  # noqa: BLE001 — sin revisión, el texto sale
        logger.warning("decisions.loose_text_unchecked", error=repr(exc)[:200])
        return ""


@activity.defn(name="decide_egress")
async def decide_egress_activity(inp: EgressInput) -> EgressOutput:
    try:
        vault = _vault_dir()
        bot = bot_for_session(inp.session_id, vault_dir=vault)
        asks_jev = any(bot.provider(name) != "reglas" for name in egress.EGRESS_CAPABILITIES)

        async def facts() -> ClaimFacts:
            return await claim_facts(inp.session_id, vault)

        out = await egress.decide_egress(
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
            # Los hechos de la puerta de afirmaciones: solo si la puerta pregunta.
            claim_facts=facts,
        )
        # El texto final suelto pasa por las mismas revisiones que `send_reply`.
        loose = await _loose_text_note(inp, out.text)
        if loose:
            out = dataclasses.replace(out, retain_note=" ".join(n for n in (loose, out.retain_note) if n))
        return out
    except Exception as exc:  # noqa: BLE001 — fail-open: decide la regla de hoy
        logger.warning("decisions.egress_unexpected", error=repr(exc)[:200])
        out = await egress.decide_egress(inp, provider_of=_rules_only, profile_id=DEFAULT_PROFILE)
        return dataclasses.replace(out, error=f"unexpected: {exc!r}"[:300])


EGRESS_ACTIVITIES = [decide_egress_activity]
