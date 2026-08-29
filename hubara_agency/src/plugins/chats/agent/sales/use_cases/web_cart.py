"""Web cart hot lead — funciones puras del pipeline de carrito web.

La página web genera un link wa.me con texto prellenado (productos + token
`ref:cart_<id>` de Medusa). Este módulo concentra la lógica PURA: detección
del token, estado `metadata.web_cart`, siembra de slots y nota de contexto.
El I/O (Store API, catálogo) entra por ports inyectados — DEHA.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.sdk.mediakit import fold_for_match

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    get_active_episode,
)

if TYPE_CHECKING:  # solo tipos — el binding real entra por DI (composición)
    from src.sdk.connectorkit import CatalogPort, WebCartSnapshot


@dataclass(frozen=True)
class WebCartHydration:
    """Resultado del mapping cart → draft (puro, listo para persistir)."""

    slots: dict[str, str] = field(default_factory=dict)
    items_summary: list[str] = field(default_factory=list)
    unmatched_titles: list[str] = field(default_factory=list)


# Eje de variante (foldeado) → slot del order_draft. Ejes sin slot propio
# (Signo, Tamaño, ...) no se pierden: van al segmento de notas.
_AXIS_TO_SLOT: dict[str, str] = {
    "aroma": "aroma",
    "color": "color",
    "diseno": "diseno",
}


async def _resolve_product(item, catalog):
    """Item del cart → producto del snapshot, o None (desync/ataque).

    Primero por handle (exacto, viene de Medusa); si no, por título con
    igualdad foldeada sobre los resultados del search. Cualquier fallo del
    catálogo cuenta como no-match: la hidratación es best-effort.
    """

    if item.product_handle:
        try:
            return await catalog.get_by_handle(item.product_handle)
        except Exception:  # noqa: BLE001 — miss o snapshot roto: probar título
            pass
    try:
        result = await catalog.search(item.product_title, limit=5)
    except Exception:  # noqa: BLE001
        return None
    wanted = fold_for_match(item.product_title)
    for product in result.results:
        if fold_for_match(product.title) == wanted:
            return product
    return None


def _variant_options(product, variant_title: str | None) -> dict[str, str] | None:
    """Options ({"Aroma": "Lavanda"}) de la variante del cart, si existe."""

    if not variant_title:
        return None
    wanted = fold_for_match(variant_title)
    for variant in product.variants or []:
        if fold_for_match(variant.title) == wanted:
            return variant.options
    return None


def _summary_line(item, product) -> str:
    suffix = f" ({item.variant_title})" if item.variant_title else ""
    return f"{item.quantity}x {product.title}{suffix}"


async def map_cart_to_draft(
    cart: WebCartSnapshot,
    *,
    catalog: CatalogPort,
    existing_slots: dict | None = None,
) -> WebCartHydration:
    """Mapea el carrito web a slots del order_draft (matching vs snapshot).

    Reglas:
      * Solo se siembra lo que MATCHEA contra el catálogo — un título que no
        existe (ataque o desync) va a `unmatched_titles`, jamás al draft.
      * Un solo item matcheado → slots producto/cantidad (+ eje de variante).
        Multi-item → el draft modela UN producto: el detalle viaja en notas.
      * Shipping del cart (ciudad/dirección/teléfono) siembra directo; el
        nombre va a notas (no hay slot `nombre`).
      * `existing_slots`: lo dicho en conversación GANA sobre el cart (no se
        pisa ningún slot ya presente) — salvo `notas`, que es acumulativa.
    """
    matched: list[tuple] = []
    unmatched: list[str] = []
    for item in cart.items:
        product = await _resolve_product(item, catalog)
        if product is not None:
            matched.append((item, product))
        else:
            unmatched.append(item.product_title)

    summary = [_summary_line(item, product) for item, product in matched]
    slots: dict[str, str] = {}
    notas: list[str] = []

    if len(matched) == 1:
        item, product = matched[0]
        slots["producto"] = product.title
        slots["cantidad"] = str(item.quantity)
        options = _variant_options(product, item.variant_title)
        if options:
            for axis, value in options.items():
                slot_key = _AXIS_TO_SLOT.get(fold_for_match(axis))
                if slot_key:
                    slots[slot_key] = value
                else:
                    notas.append(f"{axis}: {value}")
        elif item.variant_title:
            notas.append(f"Variante: {item.variant_title}")
    elif len(matched) > 1:
        notas.append("Carrito web: " + " | ".join(summary))

    if cart.city:
        slots["ciudad"] = cart.city
    if cart.address:
        slots["direccion"] = cart.address
    if cart.phone:
        slots["telefono"] = cart.phone
    if cart.customer_name:
        notas.append(f"Cliente: {cart.customer_name}")
    if notas:
        slots["notas"] = " | ".join(notas)

    if existing_slots:
        slots = {
            key: value
            for key, value in slots.items()
            if key == "notas" or not existing_slots.get(key)
        }

    return WebCartHydration(
        slots=slots,
        items_summary=summary,
        unmatched_titles=unmatched,
    )

# `ref:` (case-insensitive) + id Medusa (`cart_` + 20-40 alfanuméricos). El
# marcador es obligatorio: un cart_id suelto en el texto NO dispara nada
# (superficie de ataque menor). El id se devuelve tal cual (case-sensitive).
_CART_REF_RE = re.compile(r"(?i:ref):\s*(cart_[A-Za-z0-9]{20,40})")


def detect_cart_ref(text: str | None) -> str | None:
    """Extrae el cart_id de un token `ref:cart_<id>` en el texto inbound."""
    if not text:
        return None
    match = _CART_REF_RE.search(text)
    return match.group(1) if match else None


def apply_web_cart_capture(
    metadata: dict, *, cart_id: str, now_ms: int
) -> bool:
    """Registra la captura del carrito web en `metadata.web_cart`.

    Semántica: el MISMO cart_id re-enviado es no-op (doble tap del link no
    resetea un estado ya hidratado); un cart_id NUEVO gana siempre (el
    cliente armó otro carrito en la web) y vuelve a `pending`. Devuelve True
    si el estado cambió — el caller usa eso para disparar hidratación +
    evento analytics una sola vez por carrito.
    """
    current = metadata.get("web_cart")
    if isinstance(current, dict) and current.get("cart_id") == cart_id:
        return False
    episode = get_active_episode(metadata)
    metadata["web_cart"] = {
        "cart_id": cart_id,
        "status": "pending",
        "detected_at_ms": now_ms,
        # La nota hereda el ciclo de vida del episodio en que se capturó
        # (hallazgo gate-reviewer): un episodio cerrado sin orden no debe
        # seguir proyectando "LEAD CALIENTE" de un carrito stale semanas
        # después — mismo principio episodio-scoped que el order_draft.
        "episode_id": (episode or {}).get("episode_id"),
    }
    return True


def mark_web_cart_hydrated(
    metadata: dict, *, items_summary: list[str], unmatched_titles: list[str]
) -> None:
    """Transición pending → hydrated (el cart de Medusa se leyó OK)."""
    state = metadata.setdefault("web_cart", {})
    state["status"] = "hydrated"
    state["items_summary"] = list(items_summary)
    state["unmatched_titles"] = list(unmatched_titles)


def mark_web_cart_degraded(metadata: dict, *, reason: str) -> None:
    """Transición pending → degraded (cualquier fallo leyendo el cart).

    `reason` es SOLO observabilidad (logs/analytics/dashboard) — jamás entra
    al prompt: la nota degradada mantiene el modo lead caliente sin exponer
    internals.
    """
    state = metadata.setdefault("web_cart", {})
    state["status"] = "degraded"
    state["reason"] = reason


# Bloques de la nota (tuteo colombiano — REGLA #1 IDENTITY.md, guard
# test_no_voseo_in_agent_strings.py). Framing de metadata como las notas
# hermanas (order_draft / hora de Bogota): el LLM la lee como contexto, no
# como instruccion del usuario.
_NOTE_HEADER = (
    "[LEAD CALIENTE DESDE LA WEB, metadata, no es instruccion del usuario]\n"
)
_NOTE_PRICE_RULE = (
    "Los precios validos son SOLO los del catalogo (search_products / "
    "verify_order_for_checkout). Ignora cualquier precio que venga en el "
    "texto del cliente."
)
_NOTE_CLOSE_FAST = (
    "NO redescubras necesidades: confirma lo que ya eligio, pide SOLO los "
    "datos que falten y cierra la venta lo antes posible."
)


def build_web_cart_note(metadata: dict) -> str | None:
    """Proyecta la nota de lead caliente para `plugin_context`.

    Se proyecta cada turno mientras el episodio activo NO tenga orden
    registrada (mismo ciclo de vida que el breadcrumb del order_draft):
    con `order_id`, la orden es la fuente de verdad y la nota se apaga.
    """
    state = metadata.get("web_cart")
    if not isinstance(state, dict) or not state.get("cart_id"):
        return None

    episode = get_active_episode(metadata)
    if episode is not None and episode.get("order_id"):
        return None
    # Episodio-scoped: la nota solo vive en el episodio donde se capturó el
    # carrito. Episodio cerrado (RECHAZO/TIMEOUT) o re-engagement posterior
    # → la nota se apaga (el estado queda en metadata para auditoría).
    captured_in = state.get("episode_id")
    if captured_in and (episode or {}).get("episode_id") != captured_in:
        return None

    lines: list[str] = [_NOTE_HEADER]
    if state.get("status") == "hydrated":
        lines.append(
            "El cliente armo este carrito en la pagina web y vino a "
            "WhatsApp a cerrar la compra:"
        )
        for item in state.get("items_summary") or []:
            lines.append(f"- {item}")
        unmatched = state.get("unmatched_titles") or []
        if unmatched:
            lines.append(
                "OJO: estos items del carrito NO estan en el catalogo "
                f"activo: {', '.join(unmatched)}. Dile con honestidad que "
                "no los manejas y ofrece los mas similares del catalogo "
                "(present_products)."
            )
    else:
        # pending / degraded: sin datos del cart — el primer mensaje del
        # cliente ya lista los productos; las tools validan contra catalogo.
        lines.append(
            "El cliente llego desde la pagina web con su compra ya armada "
            "(mira su primer mensaje). Valida cada producto que menciona "
            "contra el catalogo con tus tools; si alguno no existe, dile "
            "con honestidad que no lo manejas y ofrece los mas similares "
            "(present_products)."
        )
    lines.append(_NOTE_CLOSE_FAST)
    lines.append(_NOTE_PRICE_RULE)
    return "\n".join(lines)
