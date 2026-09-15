"""Detección determinista de "enumeración de variantes" en el texto del agente.

Run 9bd495be (2026-09-14): el LLM respondió "Tenemos 11 aromas disponibles:
Caballero de la noche, Limoncillo, …" como texto plano en vez de llamar a
`present_variant_picker` (el formato curado con emojis y secciones). El guion
lo pide, el LLM no lo respeta de forma confiable → mecánica.

Funciones puras (sin I/O): reciben el texto final y las listas cerradas de
aromas/colores del catálogo, y devuelven qué tipo se enumeró y qué labels, en
orden de aparición. Umbral: 4+ labels del mismo tipo (el mismo criterio que
TOOLS.md usa para exigir el picker).
"""
from __future__ import annotations

import re
import unicodedata

MIN_ENUMERATED = 4

_DEFAULT_INTRO = {
    "scent": "Estos son los aromas que manejamos",
    "color": "Estos son los colores que manejamos",
}


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in stripped if not unicodedata.combining(c)).casefold()


def _find_labels(text_norm: str, labels: list[str]) -> list[tuple[int, str]]:
    """`(posición, label)` de cada label presente, ordenado por aparición.
    Labels más largos primero para que "Verde menta" gane a "verde"."""
    hits: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for label in sorted(labels, key=len, reverse=True):
        norm = _normalize(label)
        if not norm:
            continue
        for m in re.finditer(r"(?<!\w)" + re.escape(norm) + r"(?!\w)", text_norm):
            span = (m.start(), m.end())
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue
            taken.append(span)
            hits.append((m.start(), label))
            break  # un label cuenta una vez
    hits.sort()
    return hits


def find_enumerated_variants(
    text: str | None,
    *,
    aromas: list[str],
    colors: list[str],
    min_count: int = MIN_ENUMERATED,
) -> tuple[str, list[str]] | None:
    """`("scent"|"color", labels)` si el texto enumera `min_count`+ labels de
    un tipo; el tipo con más labels gana (empate → aromas). `None` si no."""
    if not text:
        return None
    norm = _normalize(text)
    scents = _find_labels(norm, aromas)
    cols = _find_labels(norm, colors)
    # Un label presente en ambas listas (ej. "Café") cuenta para el tipo que
    # domina: se resuelve después de contar.
    if len(cols) > len(scents):
        winner, labels = "color", cols
    else:
        winner, labels = "scent", scents
    if len(labels) < min_count:
        return None
    return winner, [label for _pos, label in labels]


def intro_before(text: str, labels: list[str]) -> str:
    """Texto que precede al primer label (sin ':' ni espacios finales)."""
    norm = _normalize(text)
    hits = _find_labels(norm, labels)
    if not hits:
        return text.strip()
    cut = hits[0][0]
    # `_normalize` conserva la longitud carácter a carácter (solo quita
    # marcas combinantes, que NFD separa como caracteres extra) → mapeamos
    # la posición sobre el texto original recorriendo ambos en paralelo.
    original_cut = _map_position(text, cut)
    return text[:original_cut].rstrip().rstrip(":;,-–— ").strip()


def _map_position(original: str, norm_pos: int) -> int:
    """Posición en `original` que corresponde a `norm_pos` en `_normalize(original)`."""
    count = 0
    for i, ch in enumerate(original):
        n = len("".join(c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c)))
        if count + n > norm_pos:
            return i
        count += n
    return len(original)


def default_intro(variant_type: str) -> str:
    return _DEFAULT_INTRO.get(variant_type, "Estas son las opciones")


__all__ = ["MIN_ENUMERATED", "default_intro", "find_enumerated_variants", "intro_before"]
