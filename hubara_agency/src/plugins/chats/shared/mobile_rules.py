"""App móvil del operador — reglas PURAS (sin red, sin disco, sin reloj).

Las rutas de ``api/mobile.py`` juntan los hechos (metadata del vault,
catálogo, ``OrderFacts``) y estas funciones deciden. Todo lo que decide la app
es determinista (``decided_by: "rules"``): nada de LLM acá.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class DraftItemFacts:
    """Un producto del borrador del pedido, ya cruzado con el catálogo.

    ``handle`` None = el nombre del borrador no resolvió a un producto del
    catálogo. ``missing``: atributos ("aroma", "color") que el cliente no eligió
    Y que el producto ofrece con ≥2 opciones (el picker necesita 2).
    """

    handle: str | None = None
    quantity: int | None = None
    missing: tuple[str, ...] = ()
    image_count: int = 0
    unit_price_cop: int | None = None


@dataclass(frozen=True)
class OperatorMove:
    """Una acción que el operador ejecutó desde la app (entrada del ledger
    ``operator_tool_actions`` de ``POST .../tools/{tool}``)."""

    action: str
    at_ms: int
    #: Los args con que la envió. ``None`` = entrada vieja del ledger (sin
    #: args): cuenta solo el nombre de la acción.
    args: dict[str, Any] | None = None


@dataclass(frozen=True)
class SuggestionFacts:
    """Lo que la ruta sabe de UNA conversación para proponer burbujas."""

    session_id: str
    version: int
    stage: str
    window_open: bool
    in_control: str
    catalog_available: bool = False
    payment_methods_available: bool = False
    items: tuple[DraftItemFacts, ...] = field(default_factory=tuple)
    #: El cliente dijo que sí a la compra en este episodio (o ya hay orden).
    purchase_confirmed: bool = False
    #: El ÚLTIMO inbound fue un aplazamiento ("luego te escribo").
    customer_deferred: bool = False
    #: El borrador trae ciudad + dirección + un medio de pago reconocible:
    #: alcanza para armar `present_order_confirmation` sin preguntar nada.
    shipping_ready: bool = False
    #: Post-cierre: el pedido del episodio sigue sin pago confirmado (según
    #: `OrderFacts`) y no es contra entrega.
    payment_pending: bool = False
    #: La última acción que salió en la conversación (del bot o del operador,
    #: ver `last_sent_action`): no se vuelve a sugerir. Si fue una jugada del
    #: operador desde el último mensaje del cliente, decide `operator_moves`
    #: (acción + args).
    last_action: str | None = None
    #: Lo que el operador ejecutó desde la app (ledger `operator_tool_actions`).
    operator_moves: tuple[OperatorMove, ...] = ()
    #: Último mensaje del cliente (epoch ms): lo que el operador ejecutó
    #: después no se vuelve a sugerir hasta que el cliente escriba de nuevo.
    last_inbound_ms: int | None = None


#: Etiqueta visible y si el operador puede retocar la acción antes de enviarla.
_ACTION_UI: dict[str, tuple[str, bool]] = {
    "present_products": ("Enviar productos", True),
    "send_shipping_rates": ("Tarifas de envío", False),
    "send_payment_methods": ("Medios de pago", False),
    "present_variant_picker": ("Enviar aromas", True),
    "present_product_gallery": ("Más fotos", True),
    "request_shipping_details": ("Pedir datos de envío", False),
    "present_order_confirmation": ("Resumen para confirmar", False),
}

_STAGE_ACTIONS: dict[str, tuple[str, ...]] = {
    "etapa_descubrimiento": ("present_products", "send_shipping_rates", "send_payment_methods"),
    "etapa_variantes": ("present_variant_picker", "present_product_gallery", "request_shipping_details"),
    "etapa_datos_envio": ("request_shipping_details", "send_shipping_rates", "send_payment_methods"),
    "etapa_cierre": ("present_order_confirmation", "send_payment_methods"),
    "etapa_postcierre": ("send_payment_methods",),
}

#: Tope de burbujas que la app muestra.
MAX_SUGGESTIONS = 4

#: Atributo del borrador → etiqueta del picker (el orden es el del guion).
_PICKER_LABELS: dict[str, str] = {"aroma": "Enviar aromas", "color": "Enviar colores"}


def _label(name: str, args: dict[str, Any]) -> str:
    if name == "present_variant_picker":
        return _PICKER_LABELS[args["attribute"]]
    return _ACTION_UI[name][0]


def _suggestion(name: str, args: dict[str, Any], *, primary: bool) -> dict[str, Any]:
    label, editable = _label(name, args), _ACTION_UI[name][1]
    return {
        "id": name,
        "label": label,
        "prominence": "primary" if primary else "normal",
        "editable": editable,
        "action": {"name": name, "args": args},
    }


def _legal_args(name: str, facts: SuggestionFacts) -> list[dict[str, Any]]:
    """Los args con que la acción es "jugada legal", en orden de preferencia
    (vacía si sus precondiciones no se cumplen). Son los que acepta
    ``POST .../tools/{tool}`` tal cual; se sugiere la primera opción que el
    operador todavía no jugó."""
    if name == "present_products":
        return [{}] if facts.catalog_available else []
    if name == "send_payment_methods":
        if facts.stage == "etapa_postcierre" and not facts.payment_pending:
            return []  # pagado (o contra entrega): no hay nada que cobrar
        return [{}] if facts.payment_methods_available else []
    if name == "present_variant_picker":
        # Cada producto del pedido con cada atributo elegible que le falta.
        return [
            {"product": item.handle, "attribute": attr}
            for item in facts.items
            if item.handle
            for attr in _PICKER_LABELS
            if attr in item.missing
        ]
    if name == "present_product_gallery":
        # `skip_first` (default de la tool): hace falta una foto además de la portada.
        return [{"handle": i.handle} for i in facts.items if i.handle and i.image_count >= 2]
    if name == "request_shipping_details":
        # Misma guarda que la tool del bot (sin "sí" o con aplazamiento, no
        # sale) + los ítems {handle, quantity} tienen que poder armarse del
        # borrador: la ruta de tools los deriva de ahí cuando args viene vacío.
        ready = facts.purchase_confirmed and not facts.customer_deferred
        return [{}] if ready and _items_buildable(facts.items) else []
    if name == "present_order_confirmation":
        # La ruta de tools arma items/envío/pago desde el borrador + catálogo.
        priced = all(i.unit_price_cop is not None for i in facts.items)
        return [{}] if facts.shipping_ready and priced and _items_buildable(facts.items) else []
    return [{}]


def _items_buildable(items: tuple[DraftItemFacts, ...]) -> bool:
    return bool(items) and all(i.handle and i.quantity and i.quantity >= 1 for i in items)


def _moves_since_last_inbound(facts: SuggestionFacts) -> tuple[OperatorMove, ...]:
    """Lo que el operador ejecutó DESPUÉS del último mensaje del cliente.

    Con un humano al mando nada actualiza el borrador, así que la jugada del
    operador vuelve al estado por acá: lo que ya mandó y el cliente todavía no
    contestó no se vuelve a sugerir. Sin fecha del último mensaje, toda jugada
    cuenta como posterior.
    """
    since = facts.last_inbound_ms
    return tuple(m for m in facts.operator_moves if since is None or m.at_ms >= since)


def _is_move(move: OperatorMove, name: str, args: dict[str, Any]) -> bool:
    """¿La jugada ``move`` del operador ES la sugerencia ``(name, args)``?

    Identidad = la acción + los args de la sugerencia («Enviar aromas» y
    «Enviar colores» son la misma acción con otro ``attribute``), comparados
    en las claves que el ledger también registró: lo que el operador retocó de
    más (opciones, intro) no cuenta. Sin claves comparables (entrada vieja del
    ledger, o args nativos de la tool) cuenta solo el nombre.
    """
    if move.action != name:
        return False
    recorded = move.args or {}
    return all(recorded[key] == value for key, value in args.items() if key in recorded)


def _unplayed_args(
    name: str, facts: SuggestionFacts, done: tuple[OperatorMove, ...]
) -> dict[str, Any] | None:
    """La primera opción legal de la acción que el operador no jugó desde el
    último mensaje del cliente, o None."""
    return next(
        (args for args in _legal_args(name, facts) if not any(_is_move(m, name, args) for m in done)),
        None,
    )


def _blocked_as_last_sent(name: str, facts: SuggestionFacts, done: tuple[OperatorMove, ...]) -> bool:
    """La última acción que salió no se vuelve a sugerir — por nombre, porque
    el turno del bot no deja con qué args. Si fue una jugada del operador
    posterior al último mensaje del cliente, decide su identidad completa
    (acción + args, ver `_is_move`)."""
    return name == facts.last_action and all(m.action != name for m in done)


def suggest_actions(facts: SuggestionFacts) -> dict[str, Any]:
    # Ventana 24h cerrada: ningún mensaje libre sale — la app ofrece la plantilla.
    names = _STAGE_ACTIONS.get(facts.stage, ()) if facts.window_open else ()
    done = _moves_since_last_inbound(facts)
    legal = [
        (n, args)
        for n in names
        if not _blocked_as_last_sent(n, facts, done) and (args := _unplayed_args(n, facts, done)) is not None
    ][:MAX_SUGGESTIONS]
    return {
        "session_id": facts.session_id,
        "version": facts.version,
        "decided_by": "rules",
        "stage": facts.stage,
        "window_open": facts.window_open,
        "in_control": facts.in_control,
        "suggestions": [
            _suggestion(n, args, primary=i == 0) for i, (n, args) in enumerate(legal)
        ],
    }


#: `component_kind` del marker que deja el flush en el JSONL → acción.
_COMPONENT_ACTIONS: dict[str, str] = {
    "products_list": "present_products",
    "product_detail": "present_product_detail",
    "product_gallery": "present_product_gallery",
    "shipping_flow": "request_shipping_details",
    "order_confirmation": "present_order_confirmation",
    "quick_replies": "send_quick_replies",
    "variant_picker": "present_variant_picker",
    "payment_instructions": "send_payment_methods",
    "shipping_rates": "send_shipping_rates",
}

#: Tools del bot que mandan algo al cliente → acción. `register_order` encola
#: las instrucciones de pago (lo mismo que manda `send_payment_methods`).
_TOOL_ACTIONS: dict[str, str] = {
    **{action: action for action in _COMPONENT_ACTIONS.values()},
    "register_order": "send_payment_methods",
}

#: Cuántos eventos hacia atrás se mira (el JSONL de un chat largo crece mucho).
_LAST_ACTION_LOOKBACK = 200


def last_sent_action(events: list[dict[str, Any]]) -> str | None:
    """La acción que salió MÁS RECIENTE en la conversación, o ``None``.

    Tres huellas en el JSONL, la primera que aparezca desde el final gana:

    * ``operator_tool`` — el marker que deja una acción del operador (app).
    * ``component_kind`` — el marker del flush para envíos no textuales.
    * ``tools_used`` — el turno del bot: el picker de variantes, las tarifas y
      las instrucciones de pago salen como TEXTO (sin marker propio), así que
      lo que los delata es la tool que el turno ejecutó.

    Los mensajes del cliente en el medio no la borran.
    """
    for event in reversed(events[-_LAST_ACTION_LOOKBACK:]):
        if not isinstance(event, dict):
            continue
        marker = event.get("operator_tool")
        if isinstance(marker, str) and marker:
            return marker
        component = _COMPONENT_ACTIONS.get(str(event.get("component_kind") or ""))
        if component:
            return component
        tools = event.get("tools_used")
        if isinstance(tools, list):
            for tool in reversed(tools):
                action = _TOOL_ACTIONS.get(str(tool))
                if action:
                    return action
    return None


# ── Incendios (bandeja priorizada) ───────────────────────────────────────────


@dataclass(frozen=True)
class ChatFireFacts:
    """Una conversación, vista para la bandeja de incendios."""

    session_id: str
    name: str | None
    in_human: bool
    escalation_reason: str | None
    unanswered_count: int
    #: Primer mensaje del cliente que sigue sin respuesta (epoch ms).
    waiting_since_ms: int | None
    last_inbound_ms: int | None


@dataclass(frozen=True)
class OrderFireFacts:
    """Un pedido vinculado a una conversación (datos de ``OrderFacts``)."""

    session_id: str
    order_id: str
    display_id: str | None
    name: str | None
    total_cop: int | None
    stage: str
    due_iso: str | None
    overdue_since_ms: int | None
    payment_pending: bool
    pending_since_ms: int | None


_GRAVE_WAIT_MS = 10 * 60_000
_TODAY_WAIT_MS = 2 * 60_000

#: `escalation_reason` → (kind del incendio, título con `{name}`).
_REASON_KINDS: dict[str, tuple[str, str]] = {
    "EXPLICIT_REQUEST": ("wants_human", "{name} pide un humano"),
    "HEALTH_SAFETY": ("health", "{name}: tema de salud"),
    "POST_SALE_ISSUE": ("order_problem", "{name} tiene un problema con su pedido"),
    "SHIPPING_ISSUE": ("order_problem", "{name} tiene un problema con el envío"),
    "PAYMENT_VERIFICATION_PENDING": ("payment_proof", "{name} espera que verifiquen su pago"),
    "ORDER_REGISTRATION_FAILED": ("order_problem", "No se pudo registrar el pedido de {name}"),
    "CHECKOUT_VERIFY_FAILED": ("bot_stuck", "El bot no pudo verificar el pedido de {name}"),
}
_DEFAULT_REASON_KIND = ("other", "{name} espera respuesta")

#: Motivos que son graves aunque el cliente lleve segundos esperando.
_ALWAYS_GRAVE = frozenset({"HEALTH_SAFETY", "ORDER_REGISTRATION_FAILED"})

#: "Sigue escribiendo ahora": otro mensaje sin respuesta en estos últimos ms.
_STILL_WRITING_MS = 5 * 60_000


def _first_name(name: str | None) -> str:
    words = (name or "").split()
    return words[0] if words else "Cliente"


def _wait_text(wait_ms: int) -> str:
    minutes = max(0, wait_ms) // 60_000
    if minutes < 1:
        return "menos de 1 min"
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h"
    return f"{hours // 24} d"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _chat_fire(chat: ChatFireFacts, now_ms: int) -> tuple[dict[str, Any], int] | None:
    """(tarjeta, ms esperando) o None."""
    if not chat.in_human or chat.unanswered_count <= 0:
        return None
    since = chat.waiting_since_ms or chat.last_inbound_ms or now_ms
    wait_ms = now_ms - since
    if wait_ms >= _GRAVE_WAIT_MS or chat.escalation_reason in _ALWAYS_GRAVE:
        severity = "grave"
    elif wait_ms >= _TODAY_WAIT_MS:
        severity = "hoy"
    else:
        severity = "espera"
    kind, title = _REASON_KINDS.get(chat.escalation_reason or "", _DEFAULT_REASON_KIND)
    card = {
        "fire_id": f"chat:{chat.session_id}",
        "subject": {"kind": "chat", "session_id": chat.session_id, "order_id": None},
        "severity": severity,
        "kind": kind,
        # Empeora = el cliente insiste AHORA sin respuesta (sin historial de
        # la tarjeta, es lo único que se puede afirmar sin estado).
        "getting_worse": chat.unanswered_count >= 2
        and chat.last_inbound_ms is not None
        and now_ms - chat.last_inbound_ms <= _STILL_WRITING_MS,
        "title": title.format(name=_first_name(chat.name)),
        "subtitle": f"{_wait_text(wait_ms)} sin respuesta · {_plural(chat.unanswered_count, 'mensaje')}",
        "primary_action": {"name": "open_chat", "args": {"session_id": chat.session_id}},
        "updated_ms": chat.last_inbound_ms or since,
    }
    return card, wait_ms


#: Etapas en las que un pedido ya no puede ir "retrasado".
_TERMINAL_STAGES = frozenset({"delivered", "cancelled"})
#: Más de estos días de retraso = grave.
_GRAVE_LATE_DAYS = 3
_DAY_MS = 24 * 60 * 60_000

#: Orden de la bandeja (y de "más grave" al fusionar tarjetas de un cliente).
_SEVERITY_RANK = {"grave": 0, "hoy": 1, "espera": 2}


def _days_late(due_iso: str, today_iso: str) -> int:
    try:
        return (date.fromisoformat(today_iso) - date.fromisoformat(due_iso)).days
    except ValueError:
        return 0


def _order_ref(order: OrderFireFacts) -> str:
    ref = (order.display_id or "").strip()
    if not ref:
        return "sin número"
    return ref if ref.startswith("#") else f"#{ref}"


def _cop(amount: int) -> str:
    return "$" + f"{amount:,}".replace(",", ".")


def _order_card(order: OrderFireFacts, *, severity: str, kind: str, title: str,
                subtitle: str, updated_ms: int | None) -> dict[str, Any]:
    return {
        "fire_id": f"order:{order.order_id}",
        "subject": {"kind": "order", "session_id": order.session_id, "order_id": order.order_id},
        "severity": severity,
        "kind": kind,
        "getting_worse": False,
        "title": title,
        "subtitle": subtitle,
        "primary_action": {
            "name": "open_order",
            "args": {"order_id": order.order_id, "session_id": order.session_id},
        },
        "updated_ms": updated_ms or 0,
    }


def _since(now_ms: int, since_ms: int | None, fallback_ms: int = 0) -> int:
    return now_ms - since_ms if since_ms else fallback_ms


def _order_fires(order: OrderFireFacts, now_ms: int, today_iso: str) -> list[tuple[dict[str, Any], int]]:
    """[(tarjeta, ms esperando)] del pedido (0, 1 o 2 motivos)."""
    name, ref = _first_name(order.name), _order_ref(order)
    cards: list[tuple[dict[str, Any], int]] = []
    if order.stage not in _TERMINAL_STAGES and order.due_iso and order.due_iso < today_iso:
        days = _days_late(order.due_iso, today_iso)
        if days >= 1:
            cards.append((_order_card(
                order,
                severity="grave" if days > _GRAVE_LATE_DAYS else "hoy",
                kind="delayed",
                title=f"El pedido {ref} de {name} va retrasado",
                subtitle=f"{_plural(days, 'día')} de retraso",
                updated_ms=order.overdue_since_ms,
            ), _since(now_ms, order.overdue_since_ms, days * _DAY_MS)))
    if order.payment_pending and order.stage != "cancelled":
        amount = f" · {_cop(order.total_cop)}" if order.total_cop else ""
        cards.append((_order_card(
            order,
            severity="hoy",
            kind="payment_proof",
            title=f"Verificar el pago de {name}",
            subtitle=f"Pedido {ref}{amount}",
            updated_ms=order.pending_since_ms,
        ), _since(now_ms, order.pending_since_ms)))
    return cards


def detect_fires(
    chats: list[ChatFireFacts],
    orders: list[OrderFireFacts],
    *,
    now_ms: int,
    today_iso: str,
) -> list[dict[str, Any]]:
    """Incendios de la bandeja: UNA tarjeta por cliente, lo más grave primero.

    Por cliente (sesión) queda la tarjeta más grave; a igual gravedad, la que
    lleva más tiempo esperando, y a igualdad total la del chat. Orden final:
    grave → hoy → espera; dentro de cada una, la espera más vieja primero.
    """
    candidates = [fire for chat in chats if (fire := _chat_fire(chat, now_ms)) is not None]
    for order in orders:
        candidates.extend(_order_fires(order, now_ms, today_iso))

    def urgency(item: tuple[dict[str, Any], int]) -> tuple[int, int, int]:
        card, wait_ms = item
        return (_SEVERITY_RANK[card["severity"]], -wait_ms, 0 if card["subject"]["kind"] == "chat" else 1)

    best: dict[str, tuple[dict[str, Any], int]] = {}
    for item in candidates:
        session_id = item[0]["subject"]["session_id"]
        if session_id not in best or urgency(item) < urgency(best[session_id]):
            best[session_id] = item
    ranked = sorted(best.values(), key=lambda item: (*urgency(item)[:2], item[0]["fire_id"]))
    return [card for card, _wait in ranked]


def _is_reply(event: dict[str, Any]) -> bool:
    """Lo que el cliente VIO como respuesta: texto del bot/operador o un envío
    no textual (`ui_component`). Un turno con `tool_calls` no (su texto no sale).
    Es el criterio del «sin respuesta» del incendio; la bandeja cuenta no leídos (#384)."""
    if event.get("role") != "assistant" or event.get("tool_calls"):
        return False
    return bool(event.get("content")) or event.get("kind") == "ui_component"


def _event_ms(event: dict[str, Any]) -> int | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return int(datetime.fromisoformat(raw).timestamp() * 1000)
    except ValueError:
        return None


def unanswered_since(events: list[dict[str, Any]]) -> tuple[int, int | None]:
    """(mensajes del cliente sin respuesta, epoch ms del PRIMERO de ellos).

    Se recorre desde el final hasta la última respuesta que el cliente vio.
    Un inbound legacy sin timestamp cuenta pero no fecha la espera.
    """
    count = 0
    since: int | None = None
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if event.get("role") == "user":
            count += 1
            since = _event_ms(event) or since
        elif _is_reply(event):
            break
    return count, since


#: Nombres de relleno con los que Medusa crea el cliente de WhatsApp.
_PLACEHOLDER_NAMES = frozenset({"cliente whatsapp", "cliente sin nombre", "cliente"})


def display_name(raw: Any) -> str | None:
    """Primer nombre mostrable, o None. Nunca un correo ni un teléfono (la
    bandeja se ve en un celular: nada de datos de contacto en los títulos)."""
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.split())
    if not text or text.casefold() in _PLACEHOLDER_NAMES:
        return None
    if "@" in text or any(ch.isdigit() for ch in text):
        return None
    return text.split()[0]


# ── Ventas calientes (widget) ────────────────────────────────────────────────


@dataclass(frozen=True)
class HotItem:
    """Un producto del borrador para la línea del widget."""

    name: str
    variant: str | None = None
    quantity: int | None = None
    unit_price_cop: int | None = None


@dataclass(frozen=True)
class HotFacts:
    session_id: str
    name: str | None
    stage: str
    in_human: bool
    episode_has_order: bool
    last_inbound_ms: int | None
    items: tuple[HotItem, ...] = ()
    customer_deferred: bool = False


def _product_label(items: tuple[HotItem, ...]) -> str | None:
    parts = []
    for item in items:
        text = " ".join(p for p in (item.name, item.variant) if p)
        if item.quantity:
            text += f" × {item.quantity}"
        parts.append(text)
    return " + ".join(parts) or None


def _cart_value(items: tuple[HotItem, ...]) -> int | None:
    if not items or any(i.unit_price_cop is None or not i.quantity for i in items):
        return None
    return sum(int(i.unit_price_cop or 0) * int(i.quantity or 0) for i in items)


#: Etapas "calientes": el cliente ya eligió y está dando datos o cerrando.
_HOT_STAGES = ("etapa_cierre", "etapa_datos_envio")  # en orden de prioridad
_HOT_RECENT_MS = 30 * 60_000
MAX_HOT = 10


def _is_hot(c: HotFacts, now_ms: int) -> bool:
    return (
        not c.in_human
        and c.stage in _HOT_STAGES
        and not c.episode_has_order
        and c.last_inbound_ms is not None
        and now_ms - c.last_inbound_ms <= _HOT_RECENT_MS
    )


def hot_sales(candidates: list[HotFacts], *, now_ms: int) -> list[dict[str, Any]]:
    """Ventas que el BOT está por cerrar: cierre primero, luego la más reciente."""
    hot = sorted(
        (c for c in candidates if _is_hot(c, now_ms)),
        key=lambda c: (_HOT_STAGES.index(c.stage), -(c.last_inbound_ms or 0), c.session_id),
    )[:MAX_HOT]
    return [
        {
            "session_id": c.session_id,
            "name": _first_name(c.name),
            "stage": c.stage,
            "product": _product_label(c.items),
            "cart_value_cop": _cart_value(c.items),
            "risk": c.customer_deferred,
            "updated_ms": c.last_inbound_ms,
        }
        for c in hot
    ]
