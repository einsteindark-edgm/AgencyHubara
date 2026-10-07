"""Los mensajes que arma el CÓDIGO para el cliente con una tarjeta.

Dos tarjetas llevan un texto que no redacta el LLM:

* el formulario de envío (`request_shipping_details`): qué está pidiendo el
  cliente (producto, variantes del borrador, cantidad), el subtotal en
  productos y qué hacer. Incidente 2026-10-06 (bot V2, turno 9): salía «Para
  enviarte *2× …* necesito unos datos. Toca el botón para completar el
  formulario — toma 30 segundos.», sin aroma, color ni subtotal y con guion
  largo. Sin el Flow, el flush pide los datos con la lista de campos
  (`shipping_fields_text`);
* el resumen del pedido (`present_order_confirmation`), el que va con los
  botones Confirmar / Modificar / Cancelar. Lo que se registra de él lleva la
  dirección tapada (`order_card_record`).

Una sola función por texto para el flush (que lo envía), su marcador del
historial y el envelope de la tool (`customer_text`): la calificación, la
verificación ③ y Calidad LLM leen lo mismo que el cliente. Puro y sin
Temporal: lo importan las tools (contrato `tools-no-temporal`).
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from src.plugins.chats.agent.sales.config.payments import (
    PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA,
    PAYMENT_LINK_SURCHARGE_OTHER_BANKS,
)
from src.plugins.chats.agent.sales.config.shipping import (
    ORDER_SUMMARY_SHIPPING_LINE,
    ORDER_SUMMARY_SHIPPING_NOTE,
    cash_on_delivery_available,
)
from src.plugins.chats.agent.sales.pricing import format_cop
from src.plugins.chats.shared.draft_items import product_key
from src.sdk.textkit import looks_like_admin_leak

#: Máximo del cuerpo de un mensaje interactivo de WhatsApp (botones, Flow).
MAX_CARD_BODY = 1024

#: El botón que abre el formulario de envío (`flow_cta` del intent).
SHIPPING_FORM_CTA = "Completar datos"

#: Las variantes de una línea del borrador, en el orden en que se nombran.
_VARIANT_FIELDS = ("color", "aroma", "diseno")

#: Un valor del borrador que va en el mensaje (color, aroma, diseño): lo
#: guarda `set_order_slot` como lo escribió el LLM y el mensaje del formulario
#: ya no pasa por el saneador. Corto, sin corchetes, enlaces ni números largos.
_MAX_VALUE_CHARS = 40
_UNSAFE_VALUE_RE = re.compile(
    r"[\[\]{}<>]|https?://|www\.|\.(?:com|co|net|org|shop|store)\b|\d(?:[ .-]?\d){5,}", re.IGNORECASE
)

#: Un renglón de producto del mensaje del formulario: «• *2× Título* (variantes)».
_FORM_ROW_RE = re.compile(r"^•\s*\*\d+×\s*(?P<title>[^*\n]+?)\*(?:\s*\((?P<variants>[^)\n]*)\))?", re.MULTILINE)
_LINE_COUNT_RE = re.compile(r"^\d+×\s*")


# ── Formulario de envío ─────────────────────────────────────────────────────


def shipping_form_text(lines: Sequence[Mapping[str, Any]], draft: Sequence[Mapping[str, Any]]) -> str:
    """El mensaje con el que sale el formulario de envío.

    `lines`: lo que `request_shipping_details` resolvió en el catálogo, una
    por ítem pedido (`handle`, `title`, `quantity`, `subtotal_cop`). `draft`:
    los ítems del borrador del pedido (`draft_items`); de ahí salen color,
    aroma y diseño, SOLO de las líneas del mismo producto (por título o
    handle). Los montos son del catálogo, nunca del LLM.

    Tuteo, sin guion largo, un solo emoji y dentro de `MAX_CARD_BODY`: con
    muchos productos los últimos se resumen («y N productos más») para que el
    subtotal y la instrucción del botón sigan en el mensaje.
    """
    products = _by_product(lines)
    rows = [_product_row(p, draft, with_amount=len(products) > 1) for p in products]
    total = sum(p["subtotal_cop"] for p in products)
    head = "Para enviarte tu pedido necesito unos datos 🤍"
    subtotal = f"Subtotal en productos: {format_cop(total)}"
    # El formulario sale ANTES de elegir el pago: la frase vale con contra
    # entrega (el resumen dice «Por confirmar») y con pago anticipado (tarifa
    # mínima, no definitiva; regla del operador 2026-09-07).
    closing = (
        "El envío va aparte (lo calcula la transportadora). "
        f"Toca «{SHIPPING_FORM_CTA}» para llenar el formulario (toma 30 segundos)."
    )

    def compose(shown: list[str]) -> str:
        hidden = len(rows) - len(shown)
        more = [f"• y {hidden} {'producto' if hidden == 1 else 'productos'} más"] if hidden else []
        return "\n".join([head, "", *shown, *more, subtotal, "", closing])

    shown = list(rows)
    body = compose(shown)
    while len(body) > MAX_CARD_BODY and len(shown) > 1:
        shown.pop()
        body = compose(shown)
    return body if len(body) <= MAX_CARD_BODY else body[: MAX_CARD_BODY - 1] + "…"


def _by_product(lines: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Las líneas juntas por producto, en orden: el mismo handle en dos líneas
    es un solo producto (sus variantes salen del borrador una vez)."""
    products: dict[str, dict[str, Any]] = {}
    for line in lines:
        key = str(line.get("handle") or line.get("title") or "")
        product = products.setdefault(
            key, {"handle": line.get("handle"), "title": line.get("title"), "quantity": 0, "subtotal_cop": 0}
        )
        product["quantity"] += int(line.get("quantity") or 0)
        product["subtotal_cop"] += int(line.get("subtotal_cop") or 0)
    return list(products.values())


def _product_row(product: Mapping[str, Any], draft: Sequence[Mapping[str, Any]], *, with_amount: bool) -> str:
    variant = _variant_label(product, draft)
    row = f"• *{product['quantity']}× {product['title']}*" + (f" ({variant})" if variant else "")
    return row + (f": {format_cop(product['subtotal_cop'])}" if with_amount else "")


def _variant_label(product: Mapping[str, Any], draft: Sequence[Mapping[str, Any]]) -> str:
    """Las variantes del borrador para `product`, separadas por coma como en
    el guion («2× Velón Koala (Sándalo, Café)»): las de su línea o, si el
    producto va repartido y sus cantidades suman las del pedido, una por
    variante («1× Lila, Lavanda; 1× Azul, Lavanda»). Si no cuadran, nada: el
    mensaje no dice algo que contradiga la cantidad."""
    keys = {product_key(product.get("title")), product_key(product.get("handle"))} - {""}
    own = [item for item in draft if product_key(item.get("producto")) in keys]
    if len(own) == 1:
        return _variant(own[0])
    quantities = [_quantity(item.get("cantidad")) for item in own]
    labels = [_variant(item) for item in own]
    if own and all(quantities) and all(labels) and sum(q or 0 for q in quantities) == product["quantity"]:
        return "; ".join(f"{q}× {label}" for q, label in zip(quantities, labels, strict=True))
    return ""


def _variant(item: Mapping[str, Any]) -> str:
    values = (_variant_value(item.get(field)) for field in _VARIANT_FIELDS)
    return ", ".join(value for value in values if value)


def _variant_value(raw: Any) -> str:
    """Un valor del borrador apto para el mensaje, o "" si no lo es: más de
    `_MAX_VALUE_CHARS`, corchetes, un enlace, 6 o más dígitos seguidos (un
    teléfono) o texto que huele a interno (`looks_like_admin_leak`)."""
    value = " ".join(str(raw or "").split())
    if not value or len(value) > _MAX_VALUE_CHARS or _UNSAFE_VALUE_RE.search(value):
        return ""
    return "" if looks_like_admin_leak(value) else value


def named_in_form(text: str) -> tuple[str, ...]:
    """Los productos y las variantes que nombra un mensaje de formulario
    (`shipping_form_text`), en orden y sin repetir: lo que ENV-02 cuenta como
    cubierto si el cliente lo nombra. Un texto sin renglones de producto (la
    lista de campos, otra tarjeta) no nombra nada."""
    named: list[str] = []
    for row in _FORM_ROW_RE.finditer(text or ""):
        parts = [row["title"], *re.split(r"[;,]", row["variants"] or "")]
        for part in parts:
            value = _LINE_COUNT_RE.sub("", part.strip()).strip()
            if value and value not in named:
                named.append(value)
    return tuple(named)


def _quantity(raw: Any) -> int | None:
    text = str(raw if raw is not None else "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def shipping_fields_text(order_total_cop: int, nequi: str) -> str:
    """Los datos de envío pedidos por TEXTO, cuando no sale el Flow (sin
    `META_FLOW_ID_SHIPPING` o si Meta lo rechaza): los campos, uno por línea, y
    las tres formas de pago con sus condiciones (requisito 2026-08-31; contra
    entrega solo desde el mínimo en productos). Sin botones: la opción nativa
    de ubicación hacía abandonar (sesión adc6400c). `nequi`: la llave vigente
    (`get_nequi_number()`); vacía, sin número."""
    payment_lines = []
    if cash_on_delivery_available(order_total_cop):
        payment_lines.append(
            "  • Contra entrega — el valor se calcula con la "
            "transportadora"
        )
    payment_lines.append(
        f"  • Pago anticipado — Nequi o llave {nequi}"
        if nequi
        else "  • Pago anticipado (Nequi)"
    )
    payment_lines.append(
        "  • Link de pago — recargo adicional de "
        f"{PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA} con Nequi o "
        f"Bancolombia, {PAYMENT_LINK_SURCHARGE_OTHER_BANKS} con otros "
        "bancos"
    )
    payment_block = "\n".join(payment_lines)
    return (
        "Para coordinar el envío necesito estos datos, puedes "
        "enviármelos en un solo mensaje o uno por uno:\n\n"
        "🏙️ *Ciudad*\n"
        "📍 *Barrio*\n"
        "🏠 *Dirección* (calle, número, apartamento)\n"
        "📞 *Teléfono* de contacto\n"
        "🙋 *Nombre de quien recibe* el pedido\n"
        "🪪 *Cédula* de quien recibe (opcional)\n"
        "💳 *Método de pago*, elige entre:\n"
        f"{payment_block}"
    )


# ── Resumen del pedido ──────────────────────────────────────────────────────


def payment_label(code: str | None) -> str:
    """La forma de pago como la lee el cliente. `card` es legacy (pedidos
    anteriores al requisito 2026-08-31); las tres vigentes son transfer /
    payment_link / cash_on_delivery."""
    return {
        "card": "Tarjeta",
        "transfer": "Pago anticipado (Nequi)",
        "payment_link": "Link de pago",
        "cash_on_delivery": "Contra entrega",
    }.get(code or "", code or "Por confirmar")


def _amount(amount: int, currency: str) -> str:
    return f"{format_cop(amount)} {currency}"


def order_card_text(params: Mapping[str, Any]) -> str:
    """El resumen del pedido que el cliente lee con los botones (los `params`
    del intent `order_confirmation`). Fallback transparente de A.12: el
    `interactive.order_details` nativo requiere Meta Catalog + gateway.

    * La variante va cuando el producto se repite en varias líneas
      («1× Velón Gorrión (Lila · Lavanda)»); si no, la línea de siempre.
    * Contra entrega (operador 2026-09-07, `config/shipping.py`): el envío se
      paga al recibir y la transportadora lo recalcula antes de despachar →
      sin valor de envío ni total; «Por confirmar» y la nota al pie.
    * Pago anticipado / link: el cliente paga ahora la tarifa mínima (la que
      valida SEC-07 en `register_order`) → se aclara que es mínima y va el
      total. Envío 0 = «sin costo»: nunca se inventa un reparto.
    * Cupo por unidad (premortem B4): qué unidades llevan el descuento, o por
      qué no, al final y solo si cabe en `MAX_CARD_BODY` (la tarjeta termina
      el turno del bot: el cliente lo lee acá).
    """
    items = params.get("items") or []
    lines = [
        (
            f"• {it.get('quantity', 1)}× {it.get('title') or it.get('handle')}"
            + (f" ({it['variant']})" if it.get("variant") else "")
            + f" — ${it.get('unit_price_cop', 0):,}"
        ).replace(",", ".")
        for it in items
    ]
    subtotal = int(params.get("subtotal_cop", 0))
    shipping = int(params.get("shipping_cop", 0))
    total = int(params.get("total_cop", 0))
    currency = params.get("currency", "COP")
    payment_method = params.get("payment_method")
    body_lines = [
        "*Resumen de tu pedido*",
        "\n".join(lines),
        "",
        f"Subtotal productos: {_amount(subtotal, currency)}",
    ]
    discount_cop = params.get("discount_cop")
    if isinstance(discount_cop, int) and discount_cop > 0:
        coupon = params.get("coupon_code")
        body_lines.append(f"Descuento{f' ({coupon})' if coupon else ''}: −{_amount(discount_cop, currency)}")
    if payment_method == "cash_on_delivery":
        body_lines.extend(["", ORDER_SUMMARY_SHIPPING_LINE])
    else:
        shipping_line = (
            f"Envío (tarifa mínima): {_amount(shipping, currency)}" if shipping > 0 else "Envío: sin costo"
        )
        body_lines.extend([shipping_line, f"Total: {_amount(total, currency)}"])
    body_lines.extend([
        "",
        f"📍 Dirección: {params.get('shipping_address_summary', '')}",
        "",
        f"💳 Medio de pago: {payment_label(payment_method)}",
    ])
    if payment_method == "cash_on_delivery":
        body_lines.extend(["", ORDER_SUMMARY_SHIPPING_NOTE])
    body = "\n".join(body_lines)
    note = params.get("coupon_note")
    if isinstance(note, str) and note.strip():
        room = MAX_CARD_BODY - len(body) - len("\n\n🎟️ ")
        if room >= 40:
            text = note.strip()
            body += "\n\n🎟️ " + (text if len(text) <= room else text[: room - 1].rstrip() + "…")
    return body


def order_card_record(params: Mapping[str, Any]) -> str:
    """El resumen del pedido como queda en los REGISTROS (envelope que lee el
    LLM, traza, verificación ③ hacia Jev, marcador del historial): el mismo
    que leyó el cliente SIN la línea de su dirección. El anonimizador no
    reconoce direcciones como «Calle 59b sur 38», y un texto que la tape
    podría copiarlo un LLM que lee el historial. Lo que recibe el cliente no
    cambia (`order_card_text`)."""
    line = f"📍 Dirección: {params.get('shipping_address_summary', '')}"
    return order_card_text(params).replace(f"\n\n{line}", "", 1)
