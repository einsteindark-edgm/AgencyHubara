"""Veracidad de producto frente al catálogo (puro, sin I/O).

Compartido por los dos agentes que le hablan al cliente (acá y no en uno de
ellos: Ventas y remarketing son independientes por contrato de import-linter):
Ventas nombra en la nota del turno lo que el cliente pidió y no existe
(`sales/use_cases/catalog_gap.py`); el remarketing lo usa en el trigger y en
la guarda de salida del gancho.

Incidente 2026-09-23/25: el cliente mandó la foto de una vela de dragón y
preguntó «¿y en vaso también?» — nada de eso existe en el catálogo y Ventas
nunca lo aclaró. El agente de remarketing (sin catálogo) lo convirtió en hechos:
«las de vaso son las más pedidas», «la del dragón es de las más lindas», «el
Cubo Love también viene en vaso». El A/B con el modelo de prod mostró que la
regla en el prompt sola no alcanza (el LLM copia sus ganchos anteriores), así
que la veracidad se cierra en dos lados deterministas:

  * `unavailable_terms`: lo que el cliente pidió o mostró y NO aparece en el
    catálogo — el trigger se lo nombra al LLM como inexistente.
  * `invented_product_claim`: la guarda de salida — el gancho que lo menciona
    igual, o que inventa popularidad sin datos de ventas, no se envía.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

#: «en vaso», «las de vaso», «con diseño de dragón», «tipo difusor»: lo que el
#: cliente pide va pegado a una preposición. Solo la palabra INMEDIATA (sin
#: saltar artículos): «en el catálogo» no es un pedido de producto.
_ASKED_AFTER = re.compile(r"\b(?:en|de|con|tipo)\s+([^\W\d_]{4,})", re.UNICODE)

#: anotaciones del sistema en el transcript que NO son pedidos del cliente
#: («[el cliente vino desde un anuncio …]»). La de la foto sí lo es: describe
#: lo que el cliente mostró.
_ANNOTATION = re.compile(r"\[([^\]]*)\]")
_PHOTO_ANNOTATION = "el cliente envio una foto"

#: vocabulario de la tienda/charla que no es un producto aunque no esté en las
#: descripciones del catálogo (evita bloquear ganchos legítimos).
_SHOP_WORDS = frozenset({
    "catalogo", "precio", "precios", "envio", "envios", "pedido", "pedidos",
    "pago", "pagos", "regalo", "regalos", "casa", "sala", "cuarto", "habitacion",
    "aroma", "aromas", "color", "colores", "vela", "velas", "foto", "fotos",
    "tienda", "anuncio", "mensaje", "whatsapp", "transferencia", "efectivo",
    "entrega", "domicilio", "ciudad", "bogota", "verdad", "nuevo", "nueva",
    "todas", "todos", "esas", "esos", "estas", "estos", "otra", "otro", "otras",
    "otros", "cual", "cuales", "alguna", "alguno", "ella", "ellas", "ellos",
    "nada", "mucho", "mucha", "algo", "forma", "diseno", "texto", "tamano",
    # frases de la charla de Ventas («en cuanto pueda», «con nequi»): la nota
    # de Ventas no puede acusar de inexistente lo que no es un producto.
    "cuanto", "acuerdo", "serio", "pronto", "momento", "gusto", "total",
    "tarjeta", "nequi", "daviplata", "bancolombia", "cuenta", "dinero", "plata",
    "contraentrega", "tarde", "manana", "noche", "semana", "cumpleanos",
})

#: popularidad/valoración sin datos de ventas («las más pedidas», «de las más
#: lindas», «son los favoritos»). «¿cuál es tu favorito?» es pregunta, no claim.
_POPULARITY = (
    re.compile(r"\bmas (?:pedid|vendid|popular|buscad|solicitad)\w*"),
    re.compile(r"\bde l[oa]s mas (?:lind|bonit|hermos)\w*"),
    re.compile(r"\b(?:son|es) (?:l[oa]s?|el) (?:favorit|preferid)\w*"),
)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _singular(word: str) -> str:
    if word.endswith("es") and len(word) > 5:
        return word[:-2]
    if word.endswith("s") and len(word) > 4:
        return word[:-1]
    return word


def _customer_asks(text: str) -> str:
    def keep_photo(match: re.Match[str]) -> str:
        inner = match.group(1)
        return inner if _fold(inner).startswith(_PHOTO_ANNOTATION) else " "

    return _ANNOTATION.sub(keep_photo, text)


def _catalog_haystack(products: list[Any]) -> set[str]:
    words: set[str] = set()
    for product in products:
        parts = [product.title or "", product.description or "", *(product.tags or [])]
        for axis, values in (getattr(product, "options", None) or {}).items():
            parts.extend([axis, *(values or [])])
        for word in re.findall(r"[^\W\d_]+", _fold(" ".join(parts)), re.UNICODE):
            words.add(_singular(word))
    return words


def unavailable_terms(customer_text: str, products: list[Any]) -> list[str]:
    """Lo que el cliente pidió o mostró y NO existe en el catálogo.

    `customer_text` = lo que escribió el cliente en el episodio (+ el motivo
    que anotó Ventas). Sin catálogo → [] (no se puede saber qué no existe).
    Devuelve la palabra como la escribió el cliente (minúsculas), sin repetir.
    """
    if not products:
        return []
    haystack = _catalog_haystack(products)
    out: list[str] = []
    seen: set[str] = set()
    for match in _ASKED_AFTER.finditer(_customer_asks(customer_text)):
        word = match.group(1).lower()
        key = _singular(_fold(word))
        if key in seen or key in haystack or _fold(word) in _SHOP_WORDS or key in _SHOP_WORDS:
            continue
        seen.add(key)
        out.append(word)
    return out


def invented_product_claim(text: str, terms: list[str]) -> str | None:
    """El dato inventado que trae el gancho, o None si es veraz.

    Palabra completa, sin tildes y tolerante a plural: «vasos» ≡ «vaso»,
    «dragon» ≡ «dragón»; «envaso» no es «vaso».
    """
    folded = _fold(text)
    words = {_singular(w) for w in re.findall(r"[^\W\d_]+", folded, re.UNICODE)}
    for term in terms:
        if _singular(_fold(term)) in words:
            return term
    for pattern in _POPULARITY:
        match = pattern.search(folded)
        if match:
            return match.group(0)
    return None
