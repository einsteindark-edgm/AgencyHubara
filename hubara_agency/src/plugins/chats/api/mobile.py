"""Contrato HTTP de la app móvil del operador (Android).

Rutas delgadas: juntan los hechos (metadata del vault, catálogo, ``OrderFacts``)
y arman lo legal las reglas puras de ``chats/shared/mobile_rules``. Qué burbuja
va primero y cómo se clasifica un incendio de chat lo decide el motor de
decisiones oficial (paquete ``operador``, ``mobile_decisions.py``), con el modo
que el panel «Motor de decisiones» le da a cada conversación; las reglas son
su respaldo.

    GET  /api/chats/mobile/suggestions/{session_id}   burbujas por etapa
    GET  /api/chats/mobile/fires                      incendios (chats + pedidos)
    GET  /api/chats/mobile/hot                        ventas calientes (widget)
    POST /api/chats/mobile/devices                    registra el token FCM del operador
    DEL  /api/chats/mobile/devices/{token}            lo borra
    GET  /api/chats/catalog                           catálogo del selector de productos

P-28: este módulo importa SOLO ``src.sdk`` + módulos de ``chats``.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    _render_payment_instructions_text,
)
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode
from src.plugins.chats.agent.sales.use_cases.funnel_stage import STAGE_POSTCIERRE, resolve_funnel_stage
from src.plugins.chats.agent.sales.use_cases.order_pricing import price_order_items
from src.plugins.chats.agent.sales.use_cases.quantity_capture import parse_leading_quantity
from src.plugins.chats.api.dashboard import (
    _compute_pending_payment_order_id,
    _order_ref_candidate_ids,
)
from src.plugins.chats.api.mobile_decisions import OperatorDecisions
from src.plugins.chats.api.order_intake import _read_events
from src.plugins.chats.shared.draft_items import draft_items, product_key
from src.plugins.chats.shared.mobile_rules import (
    ChatFireFacts,
    DraftItemFacts,
    HotFacts,
    HotItem,
    OperatorMove,
    OrderFireFacts,
    SuggestionFacts,
    detect_fires,
    display_name,
    hot_sales,
    last_sent_action,
    suggest_actions,
    unanswered_since,
)
from src.plugins.chats.shared.order_intake import normalize_payment_method
from src.plugins.chats.shared.purchase_signals import (
    has_purchase_confirmation,
    is_current_inbound_deferral,
)
from src.sdk.connectorkit import (
    InMemoryOrderFacts,
    OrderFactsSnapshot,
    get_catalog_client,
    get_order_facts_port,
    parse_variant_tags,
)
from src.sdk.castkit import current_actor
from src.sdk.mediakit import derive_image_label
from src.sdk.messagingkit import is_service_window_closed
from src.sdk.runtime import (
    WORKSPACE_VAULT_DIR,
    FilesystemMetadataStore,
    atomic_write_json,
    is_vault_session_id,
)

router = APIRouter()

#: Mismo valor que ``src.platform.constants.ROUTE_HUMANO``.
ROUTE_HUMANO = "humano"
#: Cuántos productos del snapshot se leen para cruzar el borrador.
_CATALOG_LIMIT = 200

#: Día calendario de Orders ("retrasado" = día agendado < hoy en Colombia —
#: mismo corte que `OrderSummaryDTO.overdue`). Colombia no tiene horario de
#: verano: sin tzdata en el container, UTC-5 fijo es exacto.
try:
    from zoneinfo import ZoneInfo

    _ORDERS_TZ: Any = ZoneInfo("America/Bogota")
except Exception:  # noqa: BLE001 — container sin tzdata
    _ORDERS_TZ = timezone(timedelta(hours=-5))


@dataclass
class MobileDeps:
    vault_dir: Path
    catalog: Any | None  # CatalogPort (snapshot) — None si este proceso no lo tiene
    order_facts: Any  # OrderFactsReadPort
    now_ms: Callable[[], int]
    #: Texto fijo de medios de pago (o None si no hay datos de pago configurados).
    payment_instructions_text: Callable[[], str | None]
    #: El motor de decisiones para la app (`mobile_decisions.OperatorDecisions`); None = solo reglas.
    decisions: Any = None


def _try(name: str, factory: Callable[[], Any]) -> Any | None:
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001 — sin config = endpoint degradado, no 500
        logger.warning("[chats.mobile] {} no disponible en este proceso: {}", name, exc)
        return None


def _payment_text() -> str | None:
    """El MISMO texto de medios de pago que manda `register_order` tras un
    pedido por transferencia (sin monto ni referencia: no hay pedido)."""
    return _render_payment_instructions_text({"method": "transfer"})


def _now_ms() -> int:
    return int(time.time() * 1000)


@lru_cache(maxsize=1)
def get_mobile_deps() -> MobileDeps:
    return MobileDeps(
        vault_dir=WORKSPACE_VAULT_DIR,
        catalog=_try("catalog", get_catalog_client),
        # Sin Medusa configurado: todo pedido queda `unresolved` → reglas viejas.
        order_facts=_try("order_facts", get_order_facts_port) or InMemoryOrderFacts(available=False),
        now_ms=_now_ms,
        payment_instructions_text=_payment_text,
        decisions=OperatorDecisions(vault_dir=WORKSPACE_VAULT_DIR, now_ms=_now_ms),
    )


Deps = Annotated[MobileDeps, Depends(get_mobile_deps)]


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


def _session_dir(deps: MobileDeps, session_id: str) -> Path | None:
    """Directorio de la sesión en el vault, o None (id inválido o inexistente).

    ``is_vault_session_id`` ANTES de armar el Path: ``..`` es el padre del vault.
    """
    if not is_vault_session_id(session_id):
        return None
    path = deps.vault_dir / session_id
    return path if path.is_dir() else None


def _version_ms(session_dir: Path, session_id: str) -> int:
    """Última escritura de la sesión (metadata o historial), en ms: la app
    descarta respuestas más viejas que la que ya pintó."""
    stamps = []
    for path in (session_dir / "metadata.json", session_dir / "sessions" / f"{session_id}.jsonl"):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            continue
    return int(max(stamps, default=0.0) * 1000)


async def _catalog_products(deps: MobileDeps) -> list[Any] | None:
    """Productos visibles del snapshot, o None si no hay catálogo."""
    if deps.catalog is None:
        return None
    try:
        result = await deps.catalog.search("", limit=_CATALOG_LIMIT)
    except Exception as exc:  # noqa: BLE001 — sin snapshot la app degrada, no 500
        logger.warning("[chats.mobile] catálogo no disponible: {}", exc)
        return None
    return [p for p in result.results if getattr(p, "status", "published") == "published"]


def find_product(products: list[Any], name: Any) -> Any | None:
    """Producto cuyo título o handle es ``name`` (sin acentos/mayúsculas)."""
    wanted = product_key(name)
    if not wanted:
        return None
    return next(
        (p for p in products if wanted in (product_key(p.title), product_key(p.handle))),
        None,
    )


def _image_count(product: Any) -> int:
    urls = [img.url for img in (product.images or []) if getattr(img, "url", None)]
    if product.thumbnail and product.thumbnail not in urls:
        urls.insert(0, product.thumbnail)
    return len(urls)


def unit_price_cop(product: Any) -> int | None:
    priced = price_order_items({product.handle: product}, [{"handle": product.handle, "quantity": 1}])
    return int(priced.items[0]["unit_price_cop"]) if priced.items else None


def _item_facts(item: dict[str, Any], products: list[Any]) -> DraftItemFacts:
    product = find_product(products, item.get("producto"))
    quantity = parse_leading_quantity(str(item.get("cantidad") or ""))
    if product is None:
        return DraftItemFacts(quantity=quantity)
    attrs = parse_variant_tags(product.tags)
    offered = {"aroma": attrs.aromas, "color": attrs.colors}
    missing = tuple(a for a, options in offered.items() if not item.get(a) and len(options) >= 2)
    return DraftItemFacts(
        handle=product.handle,
        quantity=quantity,
        missing=missing,
        image_count=_image_count(product),
        unit_price_cop=unit_price_cop(product),
    )


#: Pago por confirmar según `OrderFacts.pay_status`.
_UNPAID = ("pending", "partial")


async def _payment_pending(deps: MobileDeps, metadata: dict[str, Any], episode: dict[str, Any]) -> bool:
    """¿El pedido del episodio sigue sin pago confirmado? (regla 13: lo dice
    ``OrderFacts``, nunca una copia del vault). Contra entrega se paga al
    recibir: nada que cobrar por chat. Sin dato del pedido (Medusa caído) vale
    la regla vieja de las marcas del chat, como en la bandeja."""
    order_id = str(episode.get("order_id") or "")
    if not order_id:
        return False
    registered = metadata.get("registered_order")
    if (
        isinstance(registered, dict)
        and registered.get("order_id") == order_id
        and registered.get("payment_method") == "cash_on_delivery"
    ):
        return False
    try:
        snapshot = await deps.order_facts.get_facts([order_id])
    except Exception as exc:  # noqa: BLE001 — Medusa caído no tumba las burbujas
        logger.warning("[chats.mobile] OrderFacts no disponible order={}: {}", order_id, exc)
        snapshot = None
    if snapshot is not None and order_id not in snapshot.unresolved:
        fact = snapshot.facts.get(order_id)
        return fact is not None and fact.stage != "cancelled" and fact.pay_status in _UNPAID
    if metadata.get("tag") in ("COMPRA_EXITOSA", "RECHAZO"):
        return False
    return not episode.get("payment_confirmed_at_ms")


# ── GET /mobile/suggestions/{session_id} ─────────────────────────────────────


#: Mismo valor que ``operator_tools.LEDGER_KEY`` (ese módulo importa este, no al
#: revés). El e2e de `test_mobile_api` escribe el ledger con el writer real.
_OPERATOR_LEDGER_KEY = "operator_tool_actions"


def _operator_moves(metadata: dict[str, Any]) -> tuple[OperatorMove, ...]:
    """Lo que el operador ejecutó desde la app, leído del ledger."""
    ledger = metadata.get(_OPERATOR_LEDGER_KEY)
    if not isinstance(ledger, list):
        return ()
    return tuple(
        OperatorMove(
            action=entry["tool"],
            at_ms=entry["at_ms"],
            # entradas viejas sin args: la regla las cuenta por nombre
            args=entry["args"] if isinstance(entry.get("args"), dict) else None,
        )
        for entry in ledger
        if isinstance(entry, dict) and isinstance(entry.get("tool"), str) and isinstance(entry.get("at_ms"), int)
    )


def _stage_and_episode(metadata: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(etapa del embudo, episodio del que salen el borrador y el pedido).

    ``resolve_funnel_stage`` (la etapa del BOT) mira solo el episodio activo,
    pero el episodio se cierra en el MISMO turno en que se registra el pedido
    y, con un humano al mando, el siguiente mensaje del cliente no abre otro.
    Para las burbujas, sin episodio activo manda el ÚLTIMO: si registró un
    pedido, la conversación está en post-venta (el pago, no el catálogo).
    """
    episode = get_active_episode(metadata)
    if episode is not None:
        return resolve_funnel_stage(metadata), episode
    episodes = metadata.get("episodes")
    last = episodes[-1] if isinstance(episodes, list) and episodes else None
    if isinstance(last, dict) and last.get("order_id"):
        return STAGE_POSTCIERRE, last
    return resolve_funnel_stage(metadata), {}


@router.get("/mobile/suggestions/{session_id}")
async def suggestions(session_id: str, deps: Deps) -> Any:
    """Burbujas de acción para el chat: jugadas legales de la etapa del embudo."""
    session_dir = _session_dir(deps, session_id)
    if session_dir is None:
        return _error(404, "session_not_found")
    metadata = FilesystemMetadataStore(deps.vault_dir).read(session_id)
    now_ms = deps.now_ms()
    window_open = not is_service_window_closed(now_ms, metadata)
    stage, episode = _stage_and_episode(metadata)
    draft = episode.get("order_draft") if isinstance(episode.get("order_draft"), dict) else {}
    slots = draft.get("slots") if isinstance(draft.get("slots"), dict) else {}
    products = await _catalog_products(deps) if window_open else None
    last_inbound = metadata.get("last_inbound_at_ms")
    events = _read_events(deps.vault_dir, session_id)
    facts = SuggestionFacts(
        session_id=session_id,
        version=_version_ms(session_dir, session_id),
        stage=stage,
        window_open=window_open,
        in_control="human" if metadata.get("active_route") == ROUTE_HUMANO else "bot",
        catalog_available=bool(products),
        payment_methods_available=deps.payment_instructions_text() is not None,
        items=tuple(_item_facts(i, products or []) for i in draft_items(draft)),
        purchase_confirmed=has_purchase_confirmation(metadata),
        customer_deferred=is_current_inbound_deferral(metadata),
        shipping_ready=bool(
            slots.get("ciudad") and slots.get("direccion") and normalize_payment_method(slots.get("metodo_pago"))
        ),
        payment_pending=(
            window_open and stage == STAGE_POSTCIERRE and await _payment_pending(deps, metadata, episode)
        ),
        last_action=last_sent_action(events),
        operator_moves=_operator_moves(metadata),
        last_inbound_ms=last_inbound if isinstance(last_inbound, int) else None,
    )
    payload = suggest_actions(facts)
    if deps.decisions is None:
        return payload
    # El motor decide cuál de las jugadas legales va primero (capacidad `burbuja`).
    return await deps.decisions.suggestions(payload, events=events, metadata=metadata)


# ── GET /mobile/fires ────────────────────────────────────────────────────────


def _orders_day(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, tz=_ORDERS_TZ).date().isoformat()


def _late_since_ms(due_iso: str | None) -> int | None:
    """Instante en que un pedido agendado para ``due_iso`` pasa a retrasado:
    la medianoche (hora de Colombia) del día siguiente."""
    if not due_iso:
        return None
    try:
        next_day = date.fromisoformat(due_iso) + timedelta(days=1)
    except ValueError:
        return None
    start = datetime(next_day.year, next_day.month, next_day.day, tzinfo=_ORDERS_TZ)
    return int(start.timestamp() * 1000)


def _vault_sessions(vault_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    if not vault_dir.exists():
        return []
    store = FilesystemMetadataStore(vault_dir)
    return [
        (entry, store.read(entry))
        for entry in sorted(os.listdir(vault_dir))
        if is_vault_session_id(entry) and (vault_dir / entry).is_dir()
    ]


async def _facts_for(deps: MobileDeps, order_ids: set[str]) -> Any:
    """`OrderFacts` de todos los pedidos en UNA lectura (Medusa caído →
    todos `unresolved`: la bandeja cae a las marcas del chat)."""
    if not order_ids:
        return OrderFactsSnapshot()
    try:
        return await deps.order_facts.get_facts(order_ids)
    except Exception as exc:  # noqa: BLE001 — la bandeja nunca 500-ea por Medusa
        logger.warning("[chats.mobile] OrderFacts no disponible: {}", exc)
        return OrderFactsSnapshot(unresolved=frozenset(order_ids), stale=True)


def _chat_fire_facts(deps: MobileDeps, session_id: str, metadata: dict[str, Any]) -> ChatFireFacts | None:
    if metadata.get("active_route") != ROUTE_HUMANO:
        return None
    events = _read_events(deps.vault_dir, session_id)
    count, since = unanswered_since(events)
    if not count:
        return None
    last_inbound = metadata.get("last_inbound_at_ms")
    reason = metadata.get("escalation_reason")
    return ChatFireFacts(
        session_id=session_id,
        name=display_name((metadata.get("profile") or {}).get("name")),
        in_human=True,
        escalation_reason=reason if isinstance(reason, str) else None,
        unanswered_count=count,
        waiting_since_ms=since,
        last_inbound_ms=last_inbound if isinstance(last_inbound, int) else since,
    )


def _order_fire_facts(
    session_id: str, metadata: dict[str, Any], order_id: str, snapshot: Any
) -> OrderFireFacts | None:
    fact = snapshot.facts.get(order_id)
    if fact is None and order_id not in snapshot.unresolved:
        return None  # confirmado inexistente en Medusa
    if fact is not None and fact.is_test:
        return None  # pedido de prueba: no es un cliente real
    registered = metadata.get("registered_order") if isinstance(metadata.get("registered_order"), dict) else {}
    pending = (
        registered.get("order_id") == order_id
        and _compute_pending_payment_order_id(metadata, snapshot) == order_id
    )
    registered_at = registered.get("registered_at_ms")
    return OrderFireFacts(
        session_id=session_id,
        order_id=order_id,
        display_id=fact.display_id if fact else None,
        name=display_name((metadata.get("profile") or {}).get("name"))
        or (display_name(fact.customer) if fact else None),
        total_cop=fact.total_cop if fact else None,
        stage=fact.stage if fact else "unknown",
        due_iso=fact.due_iso if fact else None,
        overdue_since_ms=_late_since_ms(fact.due_iso) if fact else None,
        payment_pending=pending,
        pending_since_ms=(fact.created_at_ms or None) if fact else (
            registered_at if isinstance(registered_at, int) else None
        ),
    )


@router.get("/mobile/fires")
async def fires(deps: Deps) -> dict[str, Any]:
    """Bandeja priorizada: chats esperando a un humano + pedidos con problemas."""
    now_ms = deps.now_ms()
    sessions = _vault_sessions(deps.vault_dir)
    chats = [c for sid, md in sessions if (c := _chat_fire_facts(deps, sid, md)) is not None]
    links = [(sid, md, oid) for sid, md in sessions for oid in sorted(_order_ref_candidate_ids(md))]
    snapshot = await _facts_for(deps, {oid for _sid, _md, oid in links})
    orders = [o for sid, md, oid in links if (o := _order_fire_facts(sid, md, oid, snapshot)) is not None]
    cards = detect_fires(chats, orders, now_ms=now_ms, today_iso=_orders_day(now_ms))
    used_jev = False
    if deps.decisions is not None:
        # El motor clasifica los incendios de chat (capacidad `incendio`) sin hacer esperar a la app.
        cards, used_jev = await deps.decisions.fires(
            cards, chats={c.session_id: c for c in chats},
            events_for=lambda sid: _read_events(deps.vault_dir, sid),
        )
    return {"decided_by": "jev" if used_jev else "rules", "fires": cards}


# ── GET /mobile/hot ──────────────────────────────────────────────────────────


def _hot_item(item: dict[str, Any], products: list[Any]) -> HotItem:
    product = find_product(products, item.get("producto"))
    return HotItem(
        name=product.title if product is not None else str(item.get("producto") or "").strip(),
        variant=" ".join(str(item[k]).strip() for k in ("aroma", "color") if item.get(k)) or None,
        quantity=parse_leading_quantity(str(item.get("cantidad") or "")),
        unit_price_cop=unit_price_cop(product) if product is not None else None,
    )


def _hot_facts(session_id: str, metadata: dict[str, Any], products: list[Any]) -> HotFacts:
    episode = get_active_episode(metadata) or {}
    draft = episode.get("order_draft") if isinstance(episode.get("order_draft"), dict) else {}
    last_inbound = metadata.get("last_inbound_at_ms")
    return HotFacts(
        session_id=session_id,
        name=display_name((metadata.get("profile") or {}).get("name")),
        stage=resolve_funnel_stage(metadata),
        in_human=metadata.get("active_route") == ROUTE_HUMANO,
        episode_has_order=bool(episode.get("order_id")),
        last_inbound_ms=last_inbound if isinstance(last_inbound, int) else None,
        items=tuple(_hot_item(i, products) for i in draft_items(draft)),
        customer_deferred=is_current_inbound_deferral(metadata),
    )


@router.get("/mobile/hot")
async def hot(deps: Deps) -> dict[str, Any]:
    """Ventas que el bot está por cerrar (widget del celular)."""
    products = await _catalog_products(deps) or []
    candidates = [_hot_facts(sid, md, products) for sid, md in _vault_sessions(deps.vault_dir)]
    return {"hot": hot_sales(candidates, now_ms=deps.now_ms())}


# ── POST/DELETE /mobile/devices ──────────────────────────────────────────────
#
# Registro de tokens FCM por operador (el envío de pushes todavía no existe:
# falta el proyecto de Firebase). Vive en `<vault>/_mobile/devices.json` — el
# `_` lo deja fuera de todo lo que recorre sesiones (`wa_*`). Solo el token y la
# versión de la app: nada del teléfono ni del cliente.


class DeviceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=4096)
    platform: Literal["android"]
    app_version: str = Field(min_length=1, max_length=64)


def _registry_path(vault_dir: Path) -> Path:
    return vault_dir / "_mobile" / "devices.json"


def _update_registry(vault_dir: Path, mutate: Callable[[dict[str, list[dict[str, Any]]]], None]) -> None:
    """Read-modify-write bajo flock (dos celulares registrándose a la vez no
    se pisan) + escritura atómica."""
    path = _registry_path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / f"{path.name}.lock", "w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            operators = data.get("operators") if isinstance(data, dict) else None
            operators = operators if isinstance(operators, dict) else {}
            mutate(operators)
            atomic_write_json(path, {"operators": operators})
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@router.post("/mobile/devices", status_code=204)
async def register_device(body: DeviceBody, request: Request, deps: Deps) -> Response:
    """Idempotente: el mismo token se actualiza, no se duplica. Un token queda
    del ÚLTIMO operador que lo registró (el celular cambió de manos)."""
    actor, now_ms = current_actor(request), deps.now_ms()

    def _mutate(operators: dict[str, list[dict[str, Any]]]) -> None:
        previous: dict[str, Any] | None = None
        for owner, devices in operators.items():
            for device in list(devices):
                if isinstance(device, dict) and device.get("token") == body.token:
                    devices.remove(device)
                    previous = previous or (device if owner == actor else None)
        operators.setdefault(actor, []).append({
            "token": body.token,
            "platform": body.platform,
            "app_version": body.app_version,
            "registered_at_ms": int((previous or {}).get("registered_at_ms") or now_ms),
            "updated_at_ms": now_ms,
        })

    _update_registry(deps.vault_dir, _mutate)
    return Response(status_code=204)


@router.delete("/mobile/devices/{token}", status_code=204)
async def unregister_device(
    token: Annotated[str, PathParam(min_length=1, max_length=4096)], request: Request, deps: Deps
) -> Response:
    """Borra el token del operador que llama (idempotente)."""
    actor = current_actor(request)

    def _mutate(operators: dict[str, list[dict[str, Any]]]) -> None:
        devices = operators.get(actor) or []
        operators[actor] = [d for d in devices if not (isinstance(d, dict) and d.get("token") == token)]

    _update_registry(deps.vault_dir, _mutate)
    return Response(status_code=204)


# ── GET /catalog ─────────────────────────────────────────────────────────────
#
# El selector de productos de la app. P-22: chats no llama el endpoint de
# catálogo de marketing — lee el MISMO snapshot que usan las tools del bot
# (visibilidad ya filtrada por el pull: publicados + allowlist de vitrinas).


def _designs(product: Any) -> list[str]:
    """Diseños que acepta `present_product_detail(design=...)`: los labels de
    las fotos, en orden (mismo criterio que la tool)."""
    designs: list[str] = []
    for img in product.images or []:
        label = derive_image_label(img.url)
        if label and label not in designs:
            designs.append(label)
    return designs


def _catalog_entry(product: Any) -> dict[str, Any]:
    attrs = parse_variant_tags(product.tags)
    images = [img.url for img in (product.images or []) if getattr(img, "url", None)]
    return {
        "handle": product.handle,
        "title": product.title,
        "price_cop": unit_price_cop(product),
        "thumbnail_url": product.thumbnail or (images[0] if images else None),
        "aromas": list(attrs.aromas),
        "colors": list(attrs.colors),
        "designs": _designs(product),
    }


@router.get("/catalog")
async def catalog(deps: Deps) -> Any:
    products = await _catalog_products(deps)
    if products is None:
        return _error(503, "catalog_unavailable")
    return {"products": [_catalog_entry(p) for p in products]}
