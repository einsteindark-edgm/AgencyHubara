"""Lo que el texto del bot promete hacer AHORA, y si lo hizo.

Incidente del 2026-10-09 (bot V2, cliente sin teléfono): con el producto, la
ciudad y la forma de pago elegidos, el bot cerró dos turnos seguidos con «Te
paso el formulario para los datos de envío» y nunca llamó
`request_shipping_details`. El texto estaba bien; faltó la acción. Lo mismo
puede pasar con cualquier componente que el bot anuncia: las tarifas, el
resumen, el catálogo, las fotos, los aromas o colores, o decir que el pedido
quedó registrado sin registrarlo.

Este módulo es PURO (sin I/O ni reloj): lo leen la segunda puerta del turno en
el workflow V2 (`workflows/promises_v2.py`: una ronda más que nombra la tool,
ANTES de que el texto salga), la red de la activity
(`ensure_promised_handoff_activity`: el formulario, si aun así no salió) y la
calificación.

Qué NO es una promesa de ahora: una pregunta («¿te paso el formulario?»), una
oferta («si quieres te envío las fotos»), un condicional («cuando me confirmes
te paso el resumen»), un pasado («ya te envié el formulario») o una negación.
Es una regla de palabras (sin tildes ni mayúsculas): una paráfrasis que no
conoce no la ve, y la red y la calificación siguen ahí.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Promise:
    """Un componente que el texto promete, las tools que lo cumplen, los
    componentes de la cola del turno (`pending_ui_intents[].kind`) que lo
    cumplen y la nota que se le da al LLM si no hizo ninguno."""

    kind: str
    tools: tuple[str, ...]
    nudge: str
    intents: tuple[str, ...] = ()


# A quién y cómo: «te paso / envío / mando / comparto / dejo / muestro»,
# «te voy a enviar», con relleno opcional («te paso ya mismo el formulario»).
_TO = r"(?:te|le|les)"
_NOW = r"(?:paso|envio|mando|comparto|dejo|adjunto|muestro|hago\s+llegar)"
_SOON = r"(?:voy|vamos)\s+a\s+(?:pasar|enviar|mandar|compartir|dejar|mostrar|adjuntar)"
_FILL = r"(?:\s+(?:ya|ahora|ahorita|enseguida|mismo|entonces|de\s+una|rapidito))*"
_SAY = rf"\b{_TO}\s+(?:{_NOW}|{_SOON}){_FILL}\s+"
_HERE = r"\b(?:aqui|ahi|aca)\s+(?:te\s+)?(?:va|van|dejo|envio|mando|paso|comparto)\s+"


def _promise_re(objects: str) -> re.Pattern[str]:
    return re.compile(rf"(?:{_SAY}|{_HERE})(?:{objects})")


#: kind → (patrón, tools que la cumplen, nota para el LLM). El orden es el de
#: la nota cuando el texto promete varias cosas.
_PROMISES: tuple[tuple[Promise, re.Pattern[str]], ...] = (
    (
        Promise(
            "formulario",
            ("request_shipping_details",),
            "Le dijiste que le pasas el formulario de envío y no lo mandaste: llama request_shipping_details "
            "con los items del pedido en esta misma respuesta (tu texto va con send_reply).",
            ("shipping_flow",),
        ),
        _promise_re(r"(?:(?:el|un|nuestro|este)\s+)?(?:(?:link|enlace)\s+(?:del|de)\s+)?formulario\b"),
    ),
    (
        Promise(
            "tarifas",
            ("send_shipping_rates",),
            "Le dijiste que le pasas las tarifas de envío y no las mandaste: llama send_shipping_rates en esta "
            "misma respuesta.",
            ("shipping_rates",),
        ),
        _promise_re(
            r"(?:(?:las|nuestras)\s+)?tarifas\b"
            r"|(?:(?:el|los)\s+)?(?:costos?|valor(?:es)?|precios?)\s+(?:del|de\s+los|de)\s+(?:envios?|domicilios?)\b"
        ),
    ),
    (
        Promise(
            "resumen",
            ("present_order_confirmation",),
            "Le dijiste que le pasas el resumen del pedido y no lo mandaste: llama verify_order_for_checkout y "
            "present_order_confirmation en esta misma respuesta; si todavía faltan datos, no lo prometas.",
            ("order_confirmation",),
        ),
        _promise_re(r"(?:(?:el|un)\s+)?resumen\b|(?:la\s+)?confirmacion\s+del\s+pedido\b"),
    ),
    (
        Promise(
            "catalogo",
            ("present_products", "list_categories", "present_product_gallery"),
            "Le dijiste que le muestras el catálogo y no lo mandaste: llama present_products (o list_categories) "
            "en esta misma respuesta.",
            ("products_list", "categories", "product_gallery"),
        ),
        _promise_re(r"(?:(?:el|nuestro|todo\s+el)\s+)?catalogo\b|(?:(?:las|nuestras)\s+)?categorias\b"),
    ),
    (
        Promise(
            "fotos",
            ("present_product_detail", "present_product_gallery", "present_products"),
            "Le dijiste que le envías fotos y no las mandaste: llama present_product_detail (o "
            "present_product_gallery) con el producto en esta misma respuesta.",
            ("product_detail", "product_gallery", "products_list"),
        ),
        _promise_re(r"(?:(?:la|las|una|unas|mas|otras|algunas)\s+)?(?:fotos?|imagen(?:es)?)\b"),
    ),
    (
        Promise(
            "opciones",
            ("present_variant_picker",),
            "Le dijiste que le muestras los aromas o colores y no los mandaste: llama present_variant_picker con "
            "el producto (una llamada por atributo) en esta misma respuesta.",
            ("variant_picker",),
        ),
        _promise_re(
            r"(?:(?:los|las|todos\s+los|todas\s+las|nuestros|nuestras)\s+)?"
            r"(?:aromas|colores|disenos|signos|opciones\s+de\s+(?:aromas?|colou?r(?:es)?|disenos?))\b"
        ),
    ),
)

#: Afirmar que el pedido quedó registrado o confirmado (no es una promesa:
#: es un hecho que solo crea `register_order`; calificación CON-03).
_REGISTERED = Promise(
    "registro",
    ("register_order",),
    "Le dijiste que el pedido quedó registrado o confirmado y no llamaste register_order: el pedido solo existe "
    "con register_order (después de que el cliente toca ✅ Confirmar). Si no lo registraste, no lo afirmes.",
)
_REGISTERED_RE = re.compile(
    r"\b(?:pedido|compra|orden)\b[^.?!\n]{0,40}?(?<!no )\b(?:quedo|esta|fue|ya\s+esta)\s+"
    r"(?:confirmad[oa]|registrad[oa])\b"
)

# Lo que convierte la frase en oferta o condicional si va ANTES de la promesa.
_CONDITIONAL_RE = re.compile(
    r"\bsi\s+(?:quieres|gustas|deseas|prefieres|te\s+parece|necesitas|me\b)"
    r"|\bquieres\s+que\b|\bte\s+gustaria\b|\bcuando\b|\bapenas\b|\ben\s+cuanto\b|\buna\s+vez\s+que\b"
    r"|\b(?:luego|despues)\s+de\b|\bno\s+$"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\n])|(?=¿)")


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFD", text or "")
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).lower().split())


def _sentences(text: str) -> list[str]:
    """Las oraciones que afirman (las preguntas no prometen)."""
    out: list[str] = []
    for raw in _SENTENCE_SPLIT_RE.split(text or ""):
        if not raw.strip() or "?" in raw or "¿" in raw:
            continue
        out.append(_plain(raw))
    return out


def _promises_in(sentence: str, pattern: re.Pattern[str], *, inline_list: bool = False) -> bool:
    for match in pattern.finditer(sentence):
        if _CONDITIONAL_RE.search(sentence[: match.start()] + " "):
            continue
        if inline_list and ":" in sentence[match.end():]:
            # «Te muestro los aromas: canela y vainilla» ya es la lista.
            continue
        return True
    return False


def promised_kinds(text: str | None) -> set[str]:
    """Los componentes que el texto promete AHORA (y `registro` si afirma
    que el pedido quedó registrado)."""
    kinds: set[str] = set()
    for sentence in _sentences(text or ""):
        for promise, pattern in _PROMISES:
            if _promises_in(sentence, pattern, inline_list=promise.kind == "opciones"):
                kinds.add(promise.kind)
        if _REGISTERED_RE.search(sentence):
            kinds.add(_REGISTERED.kind)
    return kinds


def broken_promises(text: str | None, tools_used: Iterable[str]) -> list[Promise]:
    """Lo que el texto promete y ninguna de sus tools se usó en el turno, en el
    orden de `_PROMISES` (una vez cada una)."""
    kinds = promised_kinds(text)
    if not kinds:
        return []
    used = set(tools_used)
    catalog = [p for p, _ in _PROMISES] + [_REGISTERED]
    return [p for p in catalog if p.kind in kinds and not used & set(p.tools)]


def broken_promises_in_queue(text: str | None, queued: Iterable[str], *, registered: bool) -> list[Promise]:
    """Lo que el texto promete y la cola del turno no trae (`queued`: los
    `kind` de `pending_ui_intents`, lo que encolaron las tools de este turno
    antes del flush). `registered`: hay una orden registrada (afirmar que el
    pedido quedó registrado solo vale con ella)."""
    kinds = promised_kinds(text)
    if not kinds:
        return []
    have = set(queued)
    catalog = [p for p, _ in _PROMISES] + [_REGISTERED]
    return [
        p
        for p in catalog
        if p.kind in kinds and not (registered if p is _REGISTERED else have & set(p.intents))
    ]


def promise_note(promises: Iterable[Promise]) -> str | None:
    """La nota de la segunda puerta para las promesas rotas (None si no hay)."""
    nudges = [p.nudge for p in promises]
    if not nudges:
        return None
    return "[LO QUE PROMETISTE] Tu respuesta todavía no salió. " + " ".join(nudges)
