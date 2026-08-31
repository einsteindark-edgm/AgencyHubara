"""Mapeo declarativo color↔option-value para productos multi-variante.

Caso: Duo Zodiacal — cada signo (option value) viene en UN color fijo, pero
ese mapeo no existe estructurado en Medusa (ni options, ni SKU, ni metadata
de variante; solo se ve en las fotos). El operador lo declara en
`product.metadata["colores"]` con un formato hand-editable:

    "Aries: rojo; Tauro: azul petróleo, verde azulado"

- Entradas separadas por `;` o newline. Cada entrada: `<option value>: <color>`
  (también se acepta `=`).
- Un value puede tener varios aliases de color separados por coma
  ("azul claro, celeste") — el primero es el nombre canónico a citar.

Solo stdlib (R-DIP de `platform/catalog`): funciones puras, testeables sin IO.
"""
from __future__ import annotations

import re

from src.platform.catalog.variant_attrs import normalize_label

COLORS_METADATA_KEY = "colores"

_ENTRY_SPLIT_RE = re.compile(r"[;\n]+")
_KEY_VALUE_SPLIT_RE = re.compile(r"[:=]")


def parse_variant_colors(
    metadata: dict[str, str] | None,
) -> dict[str, list[str]]:
    """`metadata["colores"]` → {option_value: [alias, ...]} en orden declarado."""
    raw = (metadata or {}).get(COLORS_METADATA_KEY) or ""
    mapping: dict[str, list[str]] = {}
    for entry in _ENTRY_SPLIT_RE.split(raw):
        if _KEY_VALUE_SPLIT_RE.search(entry) is None:
            continue  # entrada vacía o sin separador clave/valor
        value, colors_raw = _KEY_VALUE_SPLIT_RE.split(entry, maxsplit=1)
        aliases = [c.strip() for c in colors_raw.split(",") if c.strip()]
        if value.strip() and aliases:
            mapping[value.strip()] = aliases
    return mapping


def colors_for_value(
    mapping: dict[str, list[str]], value: str
) -> list[str]:
    """Aliases de color del option value (match case/acentos-insensible)."""
    key = normalize_label(value)
    if not key:
        return []
    for declared, aliases in mapping.items():
        if normalize_label(declared) == key:
            return list(aliases)
    return []


def values_for_color(
    mapping: dict[str, list[str]], requested: str
) -> list[str]:
    """Option values cuyo color matchea lo pedido (tolerante a género/número).

    Dos pasadas: primero los aliases al menos tan específicos como el pedido
    ("azul" encuentra "azul", "azul claro" y "azul petróleo"; "azul petróleo"
    encuentra SOLO "azul petróleo"). Si ninguno, cae al genérico (alias
    contenido en el pedido) para no dejar al cliente sin oferta.
    """
    wanted = _stem_tokens(requested)
    if not wanted:
        return []
    specific = [
        value
        for value, aliases in mapping.items()
        if any(wanted <= _stem_tokens(alias) for alias in aliases)
    ]
    if specific:
        return specific
    return [
        value
        for value, aliases in mapping.items()
        if any(
            _stem_tokens(alias) and _stem_tokens(alias) <= wanted
            for alias in aliases
        )
    ]


def matching_color_alias(
    mapping: dict[str, list[str]], requested: str
) -> str | None:
    """Alias del catálogo que matchea lo pedido (para persistirlo canónico).

    Mismo criterio de dos pasadas que `values_for_color`: gana el alias al
    menos tan específico como el pedido; el genérico solo como fallback.
    """
    wanted = _stem_tokens(requested)
    if not wanted:
        return None
    generic: str | None = None
    for aliases in mapping.values():
        for alias in aliases:
            alias_tokens = _stem_tokens(alias)
            if not alias_tokens:
                continue
            if wanted <= alias_tokens:
                return alias
            if generic is None and alias_tokens <= wanted:
                generic = alias
    return generic


def primary_colors(mapping: dict[str, list[str]]) -> list[str]:
    """Paleta citable: el primer alias de cada value, dedupeada en orden."""
    palette: list[str] = []
    seen: set[str] = set()
    for aliases in mapping.values():
        key = normalize_label(aliases[0])
        if key not in seen:
            seen.add(key)
            palette.append(aliases[0])
    return palette


def _stem_tokens(text: str) -> frozenset[str]:
    """Tokens normalizados con stem de género/número castellano liviano.

    "ROJAS" → {"roj"}; "azul claro" → {"azul", "clar"}. Solo recorta la `s`
    final y UNA `a`/`o` final (nunca `e`: "verde"/"celeste" quedan enteros) —
    suficiente para rojo/roja/rojos/rojas sin diccionario.
    """
    tokens = set()
    for token in normalize_label(text).split():
        if len(token) > 3 and token.endswith("s"):
            token = token[:-1]
        if len(token) > 3 and token[-1] in "ao":
            token = token[:-1]
        tokens.add(token)
    return frozenset(tokens)
