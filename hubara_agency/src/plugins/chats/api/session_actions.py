"""Contrato HTTP ``session-actions@v1`` de chats (provider del cast ``mba→chats``).

Las 4 tools de ESCRITURA que Meta Business Agent invoca vía el connector del
plugin ``mba`` (``set_order_slot``, ``register_order``,
``manage_conversation_tag``, ``escalate_to_human``) son lógica de ``chats``:
draft del pedido en el episodio activo, registro del pedido + cierre "pago
pendiente" + escalación + instrucciones de pago, etiquetas de cierre y
``EpisodeClosedEvent``, ruta humana. P-3 impide que ``mba`` importe nada de
esto, así que ``chats`` lo publica como contrato HTTP y ``mba`` lo consume
por cast (``castkit.forward`` en modo ``service``: Meta no trae bearer).

Endpoints (todos ``POST``, protegidos por ``require_auth`` vía el loader —
el service token interno es el paso 1 de esa auth)::

    /api/chats/session-actions/{session_key}/draft     → SetOrderSlotTool
    /api/chats/session-actions/{session_key}/order     → precio server-side +
        RegisterOrderTool + cierre CONFIRMADO_PAGO_PENDIENTE + escalación
        PAYMENT_VERIFICATION_PENDING + flush de instrucciones de pago
    /api/chats/session-actions/{session_key}/tag       → reconciliación
        (`use_cases/tag_reconcile`: MBA propone, Hubara decide) + tag + cierre
        formal + escalación ORDER_PENDING_SHIPPING_DETAILS si quedó
        CONFIRMADO_SIN_DATOS, todo en UN `store.update` bajo el lock de sesión
        (+ EpisodeClosedEvent si cerró episodio)
    /api/chats/session-actions/{session_key}/escalate  → route=humano + tag HUMANO

Reglas:

* Todo resuelve el episodio ACTIVO de la sesión (§D1.10 del roadmap MBA).
* ``/order`` recibe ítems SIN precio (Meta no los manda): se recalculan desde
  el snapshot del catálogo (`use_cases/order_pricing`) y la tarifa mínima de
  envío por ciudad (`config/shipping`, #241). Idempotente por contenido: el
  mismo pedido dos veces devuelve la misma orden sin re-registrar ni
  re-enviar las instrucciones de pago.
* Lo que en el workflow Sales hacen las redes de seguridad
  (`ensure_payment_pending_closure`, `flush_pending_ui_intents`, dispatch de
  `EpisodeClosedEvent`) acá lo hace el endpoint: con MBA al frente no hay
  turno del bot que lo garantice.
* Invariante handoff: ``active_route=humano`` y ``tag=HUMANO`` van juntos
  (la bandeja humana filtra por el tag). ``/escalate`` no pisa a un humano
  que ya tiene el hilo.
* Este módulo importa SOLO ``src.sdk`` + módulos de chats (P-28: un archivo
  nuevo de plugin no importa ``src.platform``). El literal de la ruta humana
  se verifica contra la constante de platform en los tests.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable, Literal

from exoclaw.agent.tools import ToolContext
from fastapi import APIRouter, Depends, HTTPException, Path as PathParam
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    flush_pending_ui_intents,
)
from src.plugins.chats.agent.sales.config.shipping import shipping_rate_for_city
from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool
from src.plugins.chats.agent.sales.tools.order_registration import (
    RegisterOrderTool,
    _order_reference,
)
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    CLOSING_TAGS,
    close_episode,
    count_session_jsonl_lines,
    get_active_episode,
)
from src.plugins.chats.agent.sales.use_cases.order_pricing import price_order_items
from src.plugins.chats.agent.sales.use_cases.tag_reconcile import TagDecision, reconcile_tag_proposal
from src.plugins.chats.shared.contracts.events import EpisodeClosedEvent
from src.sdk.connectorkit import (
    ProductNotFoundError,
    get_catalog_client,
    get_order_registration_port,
)
from src.sdk.eventkit import dispatch_envelope_with_client, envelope_for
from src.sdk.runtime import WORKSPACE_VAULT_DIR, FilesystemMetadataStore, get_temporal_client

router = APIRouter()

#: Mismo valor que ``src.platform.constants.ROUTE_HUMANO`` (test lo verifica).
ROUTE_HUMANO = "humano"
ROUTE_VENTAS = "ventas"
PAYMENT_PENDING_TAG = "CONFIRMADO_PAGO_PENDIENTE"
PAYMENT_VERIFICATION_REASON = "PAYMENT_VERIFICATION_PENDING"
_SOURCE = "session_actions"
_SESSION_RE = re.compile(r"^wa_[0-9]{8,15}$")

#: Lo único que un agente externo PROPONE (D1.3); el resto lo decide Hubara.
PROPOSED_TAGS = Literal["INTERESADO", "RECHAZO"]
PAYMENT_METHODS = Literal["transfer", "payment_link", "cash_on_delivery"]


# ── DI ────────────────────────────────────────────────────────────────────────


@dataclass
class SessionActionsDeps:
    vault_dir: Path
    catalog: Any | None  # CatalogPort (snapshot) — None si no hay en este proceso
    order_port: Any | None  # OrderRegistrationPort — None → stub de la tool
    flush: Callable[[str], Awaitable[int]]  # instrucciones de pago
    notify_episode_closed: Callable[[str, str, str], Awaitable[None]]


async def _notify_episode_closed(session_key: str, episode_id: str, closing_tag: str) -> None:
    """``EpisodeClosedEvent`` por el dispatcher (cancela el watchdog del
    episodio, dispara la eval) — lo que en el workflow hace ``dispatch_event_activity``."""
    client = await get_temporal_client()
    await dispatch_envelope_with_client(
        envelope_for(
            EpisodeClosedEvent(session_id=session_key, episode_id=episode_id, closing_tag=closing_tag),
            source_plugin="chats",
            source_worker="sales",
        ),
        client,
    )


def _try(name: str, factory: Callable[[], Any]) -> Any | None:
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001 — sin config = endpoint degradado con error explícito
        logger.warning("[chats.session_actions] {} no disponible en este proceso: {}", name, exc)
        return None


@lru_cache(maxsize=1)
def get_session_actions_deps() -> SessionActionsDeps:
    return SessionActionsDeps(
        vault_dir=WORKSPACE_VAULT_DIR,
        catalog=_try("catalog", get_catalog_client),
        order_port=_try("order_port", get_order_registration_port),
        flush=flush_pending_ui_intents,
        notify_episode_closed=_notify_episode_closed,
    )


Deps = Annotated[SessionActionsDeps, Depends(get_session_actions_deps)]

#: Serializa `/order` por sesión dentro del proceso: dos requests iguales en
#: paralelo (Meta reintenta tras su timeout) pasarían ambas el pre-check de
#: idempotencia y encolarían/enviarían dos veces las instrucciones de pago.
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


def _session_lock(session_key: str) -> asyncio.Lock:
    lock = _SESSION_LOCKS.get(session_key)
    if lock is None:
        lock = _SESSION_LOCKS[session_key] = asyncio.Lock()
    return lock


def _release_session_lock(session_key: str) -> None:
    lock = _SESSION_LOCKS.get(session_key)
    if lock is not None and not lock.locked() and not lock._waiters:  # noqa: SLF001 — sin esperas: liberar la entrada
        _SESSION_LOCKS.pop(session_key, None)
SessionKey = Annotated[str, PathParam(min_length=1, max_length=64)]


def _session(session_key: str) -> str:
    """Solo ``wa_<dígitos>``: el segmento llega al filesystem del vault."""
    if not _SESSION_RE.fullmatch(session_key):
        raise HTTPException(status_code=422, detail="session_key inválida (esperado wa_<dígitos>)")
    return session_key


def _ctx(session_key: str) -> ToolContext:
    return ToolContext(session_key=session_key, channel="whatsapp", chat_id=session_key)


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── bodies ────────────────────────────────────────────────────────────────────


class DraftBody(BaseModel):
    """Los slots de ``set_order_slot`` (mismo nombre que la tool del agente)."""

    model_config = ConfigDict(extra="forbid")
    producto: str | None = None
    aroma: str | None = None
    color: str | None = None
    diseno: str | None = None
    cantidad: int | str | None = None
    ciudad: str | None = None
    barrio: str | None = None
    direccion: str | None = None
    telefono: str | None = None
    nombre_recibe: str | None = None
    cedula: str | None = None
    metodo_pago: str | None = None
    notas: str | None = None


class OrderItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handle: str = Field(min_length=1, max_length=128)
    variant_label: str | None = Field(default=None, max_length=120)
    quantity: int = Field(ge=1, le=500)


class ShippingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str = Field(min_length=1, max_length=120)
    neighborhood: str = Field(default="", max_length=120)
    address: str = Field(min_length=1, max_length=300)
    phone: str = Field(min_length=7, max_length=32)
    receiver_name: str = Field(min_length=1, max_length=120)
    national_id: str | None = Field(default=None, max_length=32)

    @field_validator("city", "address", "phone", "receiver_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("no puede estar vacío")
        return value.strip()


class OrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[OrderItemBody] = Field(min_length=1, max_length=50)
    shipping: ShippingBody
    payment_method: PAYMENT_METHODS


class TagBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tag: PROPOSED_TAGS
    motivo: str = Field(min_length=1, max_length=2000)


class EscalateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_category: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    summary: str = Field(min_length=1, max_length=2000)


# ── mutadores puros sobre metadata ───────────────────────────────────────────


def _escalate(data: dict[str, Any], *, reason_category: str, motivo: str, now_ms: int) -> bool:
    """route=humano + tag=HUMANO juntos (invariante handoff). Idempotente: si
    un humano ya tiene el hilo, NO pisa su estado (devuelve False)."""
    if data.get("active_route") == ROUTE_HUMANO:
        return False
    data["active_route"] = ROUTE_HUMANO
    data["tag"] = "HUMANO"
    data["motivo"] = motivo
    data["escalation_reason"] = reason_category
    data.setdefault("status_history", []).append(
        {
            "tag": "HUMANO",
            "motivo": motivo,
            "active_route": ROUTE_HUMANO,
            "reason_category": reason_category,
            "timestamp": now_ms / 1000.0,
            "source": _SOURCE,
        }
    )
    return True


def _close_payment_pending(
    data: dict[str, Any], *, order_id: str, motivo: str, now_ms: int, msgs_at_close: int
) -> tuple[str | None, bool]:
    """Lo que garantiza ``ensure_payment_pending_closure`` en el workflow:
    cierre del episodio activo con CONFIRMADO_PAGO_PENDIENTE + escalación
    PAYMENT_VERIFICATION_PENDING. Devuelve ``(episode_id cerrado | None, escaló)``."""
    closed_id: str | None = None
    if get_active_episode(data) is not None:
        closed = close_episode(
            data,
            closing_tag=PAYMENT_PENDING_TAG,
            closing_motivo=motivo,
            now_ms=now_ms,
            order_id=order_id,
            msgs_count_at_close=msgs_at_close,
        )
        if closed is not None:
            closed_id = str(closed.get("episode_id") or "") or None
            # Invariante handoff: si un humano ya tiene el hilo, el tag visible
            # sigue siendo HUMANO (la bandeja filtra por él); el estado real del
            # cierre queda en `closing_tag` del episodio + `status_history`.
            if data.get("active_route") != ROUTE_HUMANO:
                data["tag"] = PAYMENT_PENDING_TAG
            data.setdefault("status_history", []).append(
                {
                    "tag": PAYMENT_PENDING_TAG,
                    "motivo": motivo,
                    "active_route": data.get("active_route", ROUTE_VENTAS),
                    "timestamp": now_ms / 1000.0,
                    "source": _SOURCE,
                }
            )
    escalated = _escalate(data, reason_category=PAYMENT_VERIFICATION_REASON, motivo=motivo, now_ms=now_ms)
    return closed_id, escalated


def _apply_tag(
    data: dict[str, Any], *, decision: TagDecision, motivo: str, now_ms: int, msgs_at_close: int
) -> tuple[str | None, bool]:
    """Lo que hace ``ManageConversationTagTool`` + la red de seguridad
    ``ensure_closing_escalation``, en un solo paso (sobre el dict bajo flock):
    tag visible + historial + cierre formal si es tag de cierre + escalación si
    la decisión la exige. Devuelve ``(episode_id cerrado | None, escaló)``."""
    tag = decision.applied
    data["tag"] = tag
    data["motivo"] = motivo
    data.setdefault("status_history", []).append(
        {
            "tag": tag,
            "motivo": motivo,
            "active_route": data.get("active_route", ROUTE_VENTAS),
            "timestamp": now_ms / 1000.0,
            "source": _SOURCE,
            "proposed_tag": decision.proposed,
        }
    )
    closed_id: str | None = None
    if tag in CLOSING_TAGS:
        closed = close_episode(
            data, closing_tag=tag, closing_motivo=motivo, now_ms=now_ms, msgs_count_at_close=msgs_at_close
        )
        if closed is not None:
            closed_id = str(closed.get("episode_id") or "") or None
    escalated = False
    if decision.escalate_reason:
        escalated = _escalate(data, reason_category=decision.escalate_reason, motivo=motivo, now_ms=now_ms)
    return closed_id, escalated


def _order_fingerprint(items: list[dict[str, Any]], payment_method: str, total_cop: int) -> tuple:
    return (
        tuple(sorted((str(i.get("handle")), str(i.get("variant_label") or ""), int(i.get("quantity", 0)),
                      int(i.get("unit_price_cop", 0))) for i in items)),
        payment_method,
        int(total_cop),
    )


async def _notify(deps: SessionActionsDeps, session_key: str, episode_id: str | None, tag: str) -> None:
    if not episode_id:
        return
    try:
        await deps.notify_episode_closed(session_key, episode_id, tag)
    except Exception as exc:  # noqa: BLE001 — best-effort: el vault ya está escrito
        logger.warning("[chats.session_actions] EpisodeClosedEvent no despachado session={} err={}", session_key, exc)


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.post("/session-actions/{session_key}/draft")
async def draft(session_key: SessionKey, body: DraftBody, deps: Deps) -> dict[str, Any]:
    """``set_order_slot``: mergea los slots en el draft del episodio activo
    (valida aroma/color/diseño contra el catálogo cuando hay snapshot)."""
    session = _session(session_key)
    tool = SetOrderSlotTool(str(deps.vault_dir), vault_dir=deps.vault_dir, catalog=deps.catalog)
    slots = {k: (str(v) if isinstance(v, int) and not isinstance(v, bool) else v)
             for k, v in body.model_dump(exclude_none=True).items()}
    raw = await tool.execute_with_context(_ctx(session), **slots)
    return json.loads(raw)


@router.post("/session-actions/{session_key}/order")
async def order(session_key: SessionKey, body: OrderBody, deps: Deps) -> dict[str, Any]:
    """``register_order`` completo para un pedido que llega SIN precios."""
    session = _session(session_key)
    if deps.catalog is None:
        return {"registered": False, "order_id": None, "error_detail": "catalog_unavailable"}
    products: dict[str, Any] = {}
    for handle in {it.handle for it in body.items}:
        try:
            products[handle] = await deps.catalog.get_by_handle(handle)
        except ProductNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001 — el texto del vendor va al log
            logger.warning("[chats.session_actions] catálogo no disponible handle={} err={}", handle, exc)
            return {"registered": False, "order_id": None, "error_detail": "catalog_unavailable"}
    priced = price_order_items(products, [it.model_dump() for it in body.items])
    if priced.problems:
        return {
            "registered": False,
            "order_id": None,
            "error_detail": priced.problems[0].split(":", 1)[0],
            "problems": priced.problems,
        }
    async with _session_lock(session):
        try:
            return await _register(session, body, priced, deps)
        finally:
            _release_session_lock(session)


async def _register(session: str, body: OrderBody, priced: Any, deps: SessionActionsDeps) -> dict[str, Any]:
    shipping_cop = shipping_rate_for_city(body.shipping.city)
    subtotal_cop = priced.subtotal_cop
    total_cop = subtotal_cop + shipping_cop
    store = FilesystemMetadataStore(deps.vault_dir)
    tool = RegisterOrderTool(str(deps.vault_dir), vault_dir=deps.vault_dir, port=deps.order_port, catalog=deps.catalog)

    # Idempotencia por contenido, acotada al EPISODIO: la orden previa cuenta
    # solo si el último episodio de la sesión ya la tiene anotada (el mismo
    # pedido semanas después, en un episodio nuevo, es una venta nueva).
    data_before = store.read(session)
    existing = data_before.get("registered_order")
    episodes = data_before.get("episodes") or []
    last_episode = episodes[-1] if episodes and isinstance(episodes[-1], dict) else {}
    already = (
        isinstance(existing, dict)
        and existing.get("success") is True
        and str(last_episode.get("order_id") or "") == str(existing.get("order_id") or "")
        and _order_fingerprint(existing.get("items") or [], str(existing.get("payment_method")), int(existing.get("total_cop") or 0))
        == _order_fingerprint(priced.items, body.payment_method, total_cop)
    )
    if already:
        order_id = str(existing["order_id"])
        raw_payload = existing.get("raw_provider_payload")
        portavelas_handles = await tool._portavelas_handles(priced.items)
        provider = existing.get("provider")
    else:
        tool_items = [
            {"handle": it["handle"], "quantity": it["quantity"], "unit_price_cop": it["unit_price_cop"],
             **({"variant_label": it["variant_label"]} if it.get("variant_label") else {})}
            for it in priced.items
        ]
        envelope = json.loads(
            await tool.execute_with_context(
                _ctx(session),
                items=tool_items,
                shipping=body.shipping.model_dump(),
                payment_method=body.payment_method,
                subtotal_cop=subtotal_cop,
                shipping_cop=shipping_cop,
                total_cop=total_cop,
            )
        )
        if not envelope.get("registered"):
            return {
                "registered": False,
                "order_id": None,
                "error_detail": envelope.get("error_detail") or "registration_failed",
                "subtotal_cop": subtotal_cop,
                "shipping_cop": shipping_cop,
                "total_cop": total_cop,
            }
        order_id = str(envelope["order_id"])
        provider = envelope.get("provider")
        portavelas_handles = list((envelope.get("portavelas") or {}).get("handles") or [])
        raw_payload = (store.read(session).get("registered_order") or {}).get("raw_provider_payload")

    # Cierre "pago pendiente" + escalación (idempotentes) bajo el lock del store.
    now_ms = _now_ms()
    motivo = (
        f"Cliente confirmó pedido {order_id} por ${total_cop} COP, método {body.payment_method}; "
        "falta verificación humana del pago."
    )
    if portavelas_handles:
        motivo += " Pendiente: definir con el cliente el color del portavelas (según disponibilidad)."
    outcome: dict[str, Any] = {}

    def _mutate(data: dict[str, Any]) -> dict[str, Any]:
        msgs = count_session_jsonl_lines(deps.vault_dir, session)
        outcome["closed_id"], outcome["escalated"] = _close_payment_pending(
            data, order_id=order_id, motivo=motivo, now_ms=now_ms, msgs_at_close=msgs
        )
        return data

    if not already:
        # El cierre + la escalación ya ocurrieron con el registro original.
        store.update(session, _mutate)
        await _notify(deps, session, outcome.get("closed_id"), PAYMENT_PENDING_TAG)

    sent = 0
    if not already and body.payment_method in ("transfer", "payment_link"):
        try:
            sent = await deps.flush(session)
        except Exception as exc:  # noqa: BLE001 — el pedido ya está registrado; el intent queda encolado
            logger.warning("[chats.session_actions] flush de instrucciones de pago falló session={} err={}", session, exc)

    logger.info(
        "[chats.session_actions] order session={} order_id={} already={} closed={} escalated={} payinstr_sent={}",
        session, order_id, already, outcome.get("closed_id"), outcome.get("escalated"), sent,
    )
    return {
        "registered": True,
        "already_registered": bool(already),
        "order_id": order_id,
        "order_reference": _order_reference(raw_payload if isinstance(raw_payload, dict) else None),
        "provider": provider,
        "payment_method": body.payment_method,
        "currency": "COP",
        "subtotal_cop": subtotal_cop,
        "shipping_cop": shipping_cop,
        "total_cop": total_cop,
        "items": priced.items,
        "portavelas_included": bool(portavelas_handles),
        "portavelas_handles": portavelas_handles,
        "episode_closed": (
            {"episode_id": outcome["closed_id"], "closing_tag": PAYMENT_PENDING_TAG} if outcome.get("closed_id") else None
        ),
        "escalated": bool(outcome.get("escalated")),
        "payment_instructions_sent": sent > 0,
    }


@router.post("/session-actions/{session_key}/tag")
async def tag(session_key: SessionKey, body: TagBody, deps: Deps) -> dict[str, Any]:
    """``manage_conversation_tag``: la PROPUESTA del agente externo se reconcilia
    con el estado real (orden registrada gana; datos de envío sin orden →
    CONFIRMADO_SIN_DATOS + escalación) y recién ahí se etiqueta (+ cierre formal
    si es tag de cierre + ``EpisodeClosedEvent``). Idempotente por (sesión, tag).

    Chequeo de ruta, reconciliación, tag, cierre y escalación ocurren sobre el
    MISMO dict bajo el flock del store y bajo el lock de sesión del proceso
    (el que serializa ``/order``): MBA puede emitir ``register_order`` y
    ``manage_conversation_tag`` en el mismo turno, y un tag tardío nunca debe
    sacar la sesión de la bandeja humana ni dejar CONFIRMADO_SIN_DATOS sin
    su escalación."""
    session = _session(session_key)
    store = FilesystemMetadataStore(deps.vault_dir)
    now_ms = _now_ms()
    outcome: dict[str, Any] = {}

    def _mutate(data: dict[str, Any]) -> dict[str, Any] | None:
        if data.get("active_route") == ROUTE_HUMANO:
            # Invariante handoff: con un humano en el hilo el tag visible es
            # HUMANO; etiquetar acá lo sacaría de la bandeja humana.
            outcome["already_human"] = True
            return None
        decision = outcome["decision"] = reconcile_tag_proposal(data, proposed=body.tag)
        if decision.action != "apply":
            return None
        motivo = body.motivo
        if decision.reconciled:
            motivo = f"[{_SOURCE}] propuesta {decision.proposed} reconciliada → {decision.applied}: {body.motivo}"
        outcome["closed_id"], outcome["escalated"] = _apply_tag(
            data, decision=decision, motivo=motivo, now_ms=now_ms,
            msgs_at_close=count_session_jsonl_lines(deps.vault_dir, session),
        )
        return data

    async with _session_lock(session):
        try:
            store.update(session, _mutate)
        finally:
            _release_session_lock(session)

    if outcome.get("already_human"):
        raise HTTPException(status_code=409, detail="already_human: un colega tiene la conversación; no se etiqueta")
    decision: TagDecision = outcome["decision"]
    closed_id = outcome.get("closed_id")
    response: dict[str, Any] = {
        "tag": decision.applied or "NO_ETIQUETADO",
        "proposed_tag": decision.proposed,
        "applied": decision.action == "apply",
        "reconciled": decision.reconciled,
        "reason": decision.reason,
        "motivo": body.motivo,
        "episode_closed": {"episode_id": closed_id, "closing_tag": decision.applied} if closed_id else None,
        "escalated": bool(outcome.get("escalated")),
    }
    await _notify(deps, session, closed_id, decision.applied)
    logger.info(
        "[chats.session_actions] tag session={} proposed={} → {} ({}) closed={} escalated={}",
        session, decision.proposed, decision.applied, decision.reason, closed_id, response["escalated"],
    )
    return response


@router.post("/session-actions/{session_key}/escalate")
async def escalate(session_key: SessionKey, body: EscalateBody, deps: Deps) -> dict[str, Any]:
    """``escalate_to_human``: la conversación pasa a un humano (route + tag juntos)."""
    session = _session(session_key)
    store = FilesystemMetadataStore(deps.vault_dir)
    now_ms = _now_ms()
    outcome: dict[str, Any] = {}

    def _mutate(data: dict[str, Any]) -> dict[str, Any]:
        outcome["escalated"] = _escalate(
            data, reason_category=body.reason_category, motivo=body.summary, now_ms=now_ms
        )
        return data

    store.update(session, _mutate)
    escalated = bool(outcome.get("escalated"))
    logger.info("[chats.session_actions] escalate session={} reason={} applied={}", session, body.reason_category, escalated)
    return {"escalated": escalated, "already_human": not escalated, "active_route": ROUTE_HUMANO, "tag": "HUMANO"}
