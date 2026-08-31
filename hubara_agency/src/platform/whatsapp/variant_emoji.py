"""Mapeos closed-list de variantes → emoji para componentes interactivos.

Contexto (HU-002, sesión 71f479f7): el LLM, al listar aromas o colores en
texto, repetía siempre el mismo emoji 🌿 — quedaba pobre y poco premium.
Mover esos listados a `interactive.list` con un emoji por variante mejora
la UI; pero **no podemos dejar que el LLM invente el emoji** (rompe
closed-list y agrega ruido).

Esta tabla es la fuente de la verdad. Si el catálogo agrega un aroma
nuevo y no está mapeado, devolvemos un emoji genérico (`🕯️` para aromas,
`⚪` para colores). Mantenerla mínima — más vale 0 emoji que uno genérico
mal usado.

Convenciones:
  * Las keys están **normalizadas**: lower-case, sin tildes, sin emojis
    incrustados (la normalización vive en `normalize_variant_key`).
  * Los valores son emojis unicode únicos (Meta acepta cualquiera en row
    titles, dentro del cap de 24 chars).
  * Aromas agrupados conceptualmente en `SCENT_GROUPS` para construir
    secciones interactivas más coherentes.
"""
from __future__ import annotations

import unicodedata

# =============================================================================
# Aromas — diccionario curado por Hubara
# =============================================================================

# Categorías para sections del interactive.list. El orden importa: Meta
# muestra las sections en este orden, y queremos que las "frescas" abran
# la lista (sensaciones livianas primero, dulces después).
SCENT_GROUPS = (
    ("Frescos", ("lavanda", "verde menta", "coco cremoso")),
    ("Cítricos y frutales", ("limoncillo", "frutos rojos", "ylan ylang")),
    ("Cálidos y dulces", ("cafe", "sandalo")),
    (
        "Notas perfumadas",
        ("caballero de la noche", "drakar", "chanel"),
    ),
)

SCENT_EMOJI: dict[str, str] = {
    "lavanda": "💜",
    "sandalo": "🪵",
    "cafe": "☕",
    "limoncillo": "🍋",
    "coco cremoso": "🥥",
    "frutos rojos": "🍓",
    "verde menta": "🌿",
    "ylan ylang": "🌸",
    "caballero de la noche": "🌙",
    "drakar": "🖤",
    "chanel": "✨",
}

# =============================================================================
# Colores — círculos de color (Meta + iOS + Android los renderizan idénticos)
# =============================================================================

# Categorías para sections del color picker — separación visual entre
# tonos claros y oscuros ayuda al cliente a decidir más rápido.
COLOR_GROUPS = (
    ("Claros y suaves", ("blanco", "rosado", "rosa", "lila", "amarillo")),
    ("Vibrantes", ("verde", "naranja", "azul", "rojo")),
    ("Profundos", ("morado", "gris", "marron", "cafe", "negro")),
)

COLOR_EMOJI: dict[str, str] = {
    "blanco": "⚪",
    # Corazón gris (U+1FA76) — Emoji 15.0, soportado en iOS 16.4+, Android 14+,
    # WhatsApp Web. ANTES era `⬛` (cuadrado negro) que el cliente leía como
    # "negro" en lugar de "gris" (post-mortem run bc54cb93, 2026-05-25).
    "gris": "🩶",
    "amarillo": "🟡",
    "verde": "🟢",
    "naranja": "🟠",
    "morado": "🟣",
    "azul": "🔵",
    # Rojo: usamos círculo rojo dedicado.
    "rojo": "🔴",
    # Negro: cuadrado negro (única opción no-ambigua).
    "negro": "⬛",
    # Cafe/marrón: corazón marrón (cliente vio "Marrón" como variante).
    "marron": "🤎",
    "cafe": "🤎",
    # No existe round-circle rosa pálido en unicode; usamos flor rosa que es
    # universalmente legible como "rosa/rosado".
    "rosado": "🌸",
    "rosa": "🌸",
    # Lila no tiene círculo dedicado — usamos corazón violeta (distinto de
    # morado para no chocar visualmente).
    "lila": "💜",
}

# =============================================================================
# Fallbacks
# =============================================================================

GENERIC_SCENT_EMOJI = "🕯️"
GENERIC_COLOR_EMOJI = "⚪"


def normalize_variant_key(raw: str) -> str:
    """Normaliza el nombre de la variante para lookup en los diccionarios.

    Pasos:
      * lower-case
      * remover tildes (NFD + filtrar marks)
      * strip + colapsar whitespace

    Devuelve la key normalizada. No pierde caracteres: "Ylan-Ylang" →
    "ylan-ylang", "Sándalo" → "sandalo".
    """
    if not raw:
        return ""
    nfkd = unicodedata.normalize("NFD", raw.strip().lower())
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Colapsa espacios múltiples
    return " ".join(no_accents.split())


def scent_emoji(label: str) -> str:
    """Devuelve el emoji curado para un aroma, o `GENERIC_SCENT_EMOJI` si
    no está mapeado. Match case-insensitive y sin tildes."""
    key = normalize_variant_key(label)
    return SCENT_EMOJI.get(key, GENERIC_SCENT_EMOJI)


def color_emoji(label: str) -> str:
    """Idem para colores."""
    key = normalize_variant_key(label)
    return COLOR_EMOJI.get(key, GENERIC_COLOR_EMOJI)


def group_scents(labels: list[str]) -> list[tuple[str, list[str]]]:
    """Agrupa una lista de aromas en las categorías de `SCENT_GROUPS`,
    manteniendo el orden de las categorías. Los que no calzan en ninguna
    van a una sección final 'Otros'.

    No muta `labels`. Devuelve `[(section_title, [labels_in_order]), ...]`.
    """
    return _group_by(labels, SCENT_GROUPS)


def group_colors(labels: list[str]) -> list[tuple[str, list[str]]]:
    return _group_by(labels, COLOR_GROUPS)


def _group_by(
    labels: list[str],
    groups: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[tuple[str, list[str]]]:
    norm = {normalize_variant_key(lbl): lbl for lbl in labels}
    grouped: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for section_title, keys in groups:
        bucket: list[str] = []
        for k in keys:
            if k in norm and k not in seen:
                bucket.append(norm[k])
                seen.add(k)
        if bucket:
            grouped.append((section_title, bucket))
    leftovers = [norm[k] for k in norm if k not in seen]
    if leftovers:
        grouped.append(("Otros", leftovers))
    return grouped
