"""Resolución `variant_label` (texto libre del LLM) → variante(s) de Medusa.

Lógica pura (sin IO) usada por `MedusaOrderRegistration._resolve_items`.

Contexto (prod 2026-09-17): el único producto con 2+ variantes es
`duo-zodiacal` (opción "Signo", 12 valores). Aromas y colores son TAGS del
producto ("Aroma: Limoncillo"), no variantes. El LLM manda labels compuestos
como "Café, Sándalo · Leo" o "Capricornio morado, Capricornio verde". El
matching es por FRASE con límite de palabra, sin acentos: una variante matchea
si sus valores de opción (o su título) aparecen dentro del label.

Clases de resultado (`mismatch_kind`):
  * `None`                       — variante resuelta, nada sobra.
  * `"partial"`                  — variante resuelta, sobran tokens (aroma,
                                   color…). NO es mismatch: el operador sólo
                                   verifica los tokens sobrantes.
  * `"multi_variant_unresolved"` — el label nombra 2+ variantes y sus
                                   conteos no suman la cantidad → mismatch.
  * `"dimension_unresolved"`     — producto con 2+ opciones y alguna no
                                   aparece en el label → mismatch.
  * `"fallback_first_variant"`   — ninguna variante aparece → `variants[0]`,
                                   mismatch fuerte.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

PARTIAL = "partial"
MULTI_VARIANT_UNRESOLVED = "multi_variant_unresolved"
DIMENSION_UNRESOLVED = "dimension_unresolved"
FALLBACK_FIRST_VARIANT = "fallback_first_variant"

_MISMATCH_KINDS = frozenset(
    {MULTI_VARIANT_UNRESOLVED, DIMENSION_UNRESOLVED, FALLBACK_FIRST_VARIANT}
)

# Conectores que no cuentan como token sin resolver ("Leo y Aries").
_FILLER_WORDS = frozenset({"y", "e", "o", "con", "de", "del", "el", "la", "los", "las", "x"})

# Separadores que el LLM usa para combinar valores en una sola string. Se
# aplican TODOS a la vez ("Café, Sándalo · Leo" → 3 tokens). El guion sólo
# separa con espacios alrededor para no romper palabras compuestas.
LABEL_SEPARATORS = (" - ", " – ", " · ", "·", " | ", "|", "/", ",", ";")
_SPLIT_RE = re.compile("|".join(re.escape(sep) for sep in LABEL_SEPARATORS))


@dataclass(frozen=True)
class VariantLine:
    variant: Any
    quantity: int


@dataclass(frozen=True)
class VariantResolution:
    lines: tuple[VariantLine, ...]
    mismatch_kind: str | None = None
    unresolved_tokens: tuple[str, ...] = ()
    unresolved_tag_kinds: tuple[str, ...] = ()

    @property
    def is_mismatch(self) -> bool:
        return self.mismatch_kind in _MISMATCH_KINDS


def split_label(label: str) -> list[str]:
    return [p.strip() for p in _SPLIT_RE.split(label or "") if p.strip()]


def normalize(text: str) -> str:
    """lower + sin acentos + sólo alfanuméricos separados por un espacio."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", stripped.lower()))


def count_phrase(phrase: str, text: str) -> int:
    """Ocurrencias de `phrase` en `text` (ambos normalizados) como palabras
    completas — "leo" no matchea dentro de "leopardo"."""
    if not phrase or not text:
        return 0
    return len(_phrase_re(phrase).findall(text))


def _phrase_re(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\S){re.escape(phrase)}(?!\S)")


def variant_values(variant: Any) -> list[str]:
    """Valores normalizados que identifican la variante: sus option values;
    si no tiene, el título."""
    values = [
        normalize(getattr(opt, "value", "") or "")
        for opt in (getattr(variant, "options", None) or [])
    ]
    values = [v for v in values if v]
    if values:
        return values
    title = normalize(getattr(variant, "title", "") or "")
    return [title] if title else []


def resolve(
    variants: list[Any], tags: list[Any], label: str, quantity: int
) -> VariantResolution:
    """Resuelve `label` contra las variantes de un producto con 2+ variantes."""
    text = normalize(label)
    candidates: list[tuple[Any, list[str], list[str]]] = []
    for v in variants:
        values = variant_values(v)
        found = [val for val in values if count_phrase(val, text)]
        if found:
            candidates.append((v, values, found))

    if not candidates:
        return _with_leftovers(
            (VariantLine(variants[0], quantity),),
            FALLBACK_FIRST_VARIANT, label, consumed=[], tags=tags,
        )

    full = [c for c in candidates if len(c[2]) == len(c[1])]
    if not full:
        variant, _, found = max(candidates, key=lambda c: len(c[2]))
        return _with_leftovers(
            (VariantLine(variant, quantity),),
            DIMENSION_UNRESOLVED, label, consumed=found, tags=tags,
        )

    consumed = [val for _, values, _ in full for val in values]
    if len(full) == 1:
        return _with_leftovers(
            (VariantLine(full[0][0], quantity),), None, label,
            consumed=consumed, tags=tags,
        )

    # 2+ variantes nombradas ("Capricornio morado, Capricornio verde, Sagitario
    # azul" x3): una línea por variante, en orden de aparición, si los conteos
    # suman exactamente la cantidad. Si no, no adivinamos el reparto.
    counted = sorted(
        (
            (_first_position(values, text), variant,
             min(count_phrase(val, text) for val in values))
            for variant, values, _ in full
        ),
        key=lambda c: c[0],
    )
    if sum(n for _, _, n in counted) == quantity:
        lines = tuple(VariantLine(variant, n) for _, variant, n in counted)
        return _with_leftovers(lines, None, label, consumed=consumed, tags=tags)
    return _with_leftovers(
        (VariantLine(counted[0][1], quantity),), MULTI_VARIANT_UNRESOLVED, label,
        consumed=consumed, tags=tags,
    )


def _first_position(values: list[str], text: str) -> int:
    positions = [
        m.start()
        for val in values
        for m in [_phrase_re(val).search(text)]
        if m
    ]
    return min(positions) if positions else len(text)


def _with_leftovers(
    lines: tuple[VariantLine, ...],
    kind: str | None,
    label: str,
    *,
    consumed: list[str],
    tags: list[Any],
) -> VariantResolution:
    leftovers = _leftover_tokens(label, consumed)
    if kind is None and leftovers:
        kind = PARTIAL
    return VariantResolution(
        lines=lines,
        mismatch_kind=kind,
        unresolved_tokens=tuple(leftovers),
        unresolved_tag_kinds=tuple(_tag_kinds(leftovers, tags)),
    )


def _leftover_tokens(label: str, consumed: list[str]) -> list[str]:
    """Tokens del label (en su forma original) sin las frases ya resueltas.

    "Capricornio morado" con consumed=["capricornio"] → ["morado"].
    """
    phrases = [p.split() for p in consumed if p]
    leftovers: list[str] = []
    for token in split_label(label):
        words = token.split()
        # (índice de palabra original, pieza normalizada)
        pieces = [
            (i, piece) for i, w in enumerate(words) for piece in normalize(w).split()
        ]
        used: set[int] = set()
        for phrase in phrases:
            n = len(phrase)
            for start in range(len(pieces) - n + 1):
                if [p for _, p in pieces[start:start + n]] == phrase:
                    used.update(i for i, _ in pieces[start:start + n])
        rest = " ".join(
            w for i, w in enumerate(words)
            if i not in used and normalize(w) and normalize(w) not in _FILLER_WORDS
        )
        if rest:
            leftovers.append(rest)
    return leftovers


def _tag_kinds(leftovers: list[str], tags: list[Any]) -> list[str]:
    """Tipo de tag ("aroma", "color") de cada token sobrante, en orden y sin
    repetir. Los tags del producto tienen forma "Aroma: Limoncillo"."""
    tag_index: list[tuple[str, str]] = []
    for tag in tags or []:
        prefix, sep, value = (getattr(tag, "value", "") or "").partition(":")
        if sep and normalize(prefix) and normalize(value):
            tag_index.append((normalize(value), normalize(prefix)))

    kinds: list[str] = []
    for token in leftovers:
        text = normalize(token)
        for value, kind in tag_index:
            if count_phrase(value, text) and kind not in kinds:
                kinds.append(kind)
                break
    return kinds
