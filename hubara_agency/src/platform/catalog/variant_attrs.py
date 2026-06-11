"""Atributos de variante (aromas/colores) derivados de los tags del producto.

En Medusa los productos Hubara tienen UNA variante "Unico"; los aromas y
colores reales viven como tags con prefijo (`"Aroma: Drakar"`, `"Color: gris"`).
Ese formato es facil de recitar mal: el caso ep_010 (run fa1eb974) mostro al
LLM inventando "Melocotón"/"Crema" e "14 aromas y 10 colores" AUN habiendo
llamado `search_products` en el mismo episodio. Este modulo convierte los tags
en listas cerradas y da el matching normalizado (case/acentos-insensible) que
usan los choke points deterministas (variant picker, order draft) y el juez
del eval.

Solo stdlib (R-DIP de `platform/catalog`): funciones puras, testeables sin IO.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_AROMA_PREFIX = "aroma:"
_COLOR_PREFIX = "color:"

# Separadores aceptados cuando un slot trae varias elecciones en un string
# ("Drakar, Café" / "Drakar y Café" / "Drakar / Café").
_MULTI_SPLIT_RE = re.compile(r"\s*(?:,|;|/| y | e )\s*", re.IGNORECASE)


def normalize_label(raw: str) -> str:
    """Normaliza para comparar: sin acentos, casefold, espacios colapsados.

    "Melocotón" -> "melocoton"; "  Verde  Menta " -> "verde menta".
    """
    text = unicodedata.normalize("NFKD", str(raw))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


@dataclass(frozen=True)
class VariantAttrs:
    """Listas cerradas de un producto, con el casing canonico del catalogo."""

    aromas: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)


def parse_variant_tags(tags: list[str] | None) -> VariantAttrs:
    """`["Aroma: Drakar", "Color: gris", ...]` -> listas cerradas dedupeadas.

    Conserva el casing original del tag (es lo que se muestra al cliente) y el
    orden de aparicion; dedupea por forma normalizada. Tags sin prefijo
    reconocido se ignoran (no son atributos de variante).
    """
    aromas: list[str] = []
    colors: list[str] = []
    seen_a: set[str] = set()
    seen_c: set[str] = set()
    for tag in tags or []:
        text = str(tag).strip()
        lowered = normalize_label(text)
        if lowered.startswith(_AROMA_PREFIX):
            value = text.split(":", 1)[1].strip()
            key = normalize_label(value)
            if value and key not in seen_a:
                seen_a.add(key)
                aromas.append(value)
        elif lowered.startswith(_COLOR_PREFIX):
            value = text.split(":", 1)[1].strip()
            key = normalize_label(value)
            if value and key not in seen_c:
                seen_c.add(key)
                colors.append(value)
    return VariantAttrs(aromas=aromas, colors=colors)


def split_multi_label(raw: str) -> list[str]:
    """Divide una eleccion multiple en tokens: "Drakar, Café" -> [Drakar, Café]."""
    return [part for part in (_MULTI_SPLIT_RE.split(str(raw))) if part.strip()]


def match_option(raw: str, valid: list[str]) -> str | None:
    """Matchea una eleccion contra la lista cerrada (case/acentos-insensible).

    Devuelve el label CANONICO del catalogo (para que lo que se persista tenga
    el casing real) o None si la opcion no existe.
    """
    key = normalize_label(raw)
    if not key:
        return None
    for option in valid:
        if normalize_label(option) == key:
            return option
    return None
