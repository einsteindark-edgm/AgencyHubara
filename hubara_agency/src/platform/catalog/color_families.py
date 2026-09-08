"""Familias de color — tolerancia entre el TONO que pide el cliente y el
COLOR que ofrece el catálogo.

Caso del operador (2026-09-08): "¿lo tienen en azul clarito / azul mar?"
con el catálogo ofreciendo "Azul" terminaba en un "ese color no lo manejo"
cortante, porque `match_option` es exacto. Acá el tono se resuelve a la
familia del catálogo:

    "azul clarito" / "azul mar" / "celeste"  →  "Azul"
    "fucsia"                                  →  "Rosado"
    "lavanda"                                 →  "Lila"

La tabla de familias NO vive en código: es un YAML configurable por tenant
(`config/color_families/families.yaml`, loader en `color_families_loader.py`).
Este módulo solo sabe parsear ese documento y matchear contra él.

Reglas de matcheo:
  * Sin acentos, sin mayúsculas, tolerante a género/número ("rojas" = rojo,
    "azules" = azul) — mismo espíritu que `variant_colors._stem_tokens`.
  * Un nombre (label o shade) matchea un texto si TODOS sus tokens están en
    el texto ("azul mar" ⊆ "un azul mar bonito").
  * Gana el nombre MÁS ESPECÍFICO (más tokens): "morado clarito" declarado
    en `lila` le gana al genérico "morado" de la familia `morado`.
  * Si el texto cae en DOS familias distintas con la misma especificidad
    ("azul o verde") no se adivina: resolución ambigua con candidatos.

Solo stdlib (R-DIP de `platform/catalog`): funciones puras, testeables sin IO.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.platform.catalog.variant_attrs import match_option, normalize_label


class InvalidColorFamiliesError(ValueError):
    """El documento de familias no cumple el schema."""


@dataclass(frozen=True)
class ColorFamily:
    """Una familia: id estable, label citable y los tonos que la componen."""

    id: str
    label: str
    shades: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        """Label + shades: todo lo que se reconoce como esta familia."""
        return (self.label, *self.shades)


@dataclass(frozen=True)
class ColorFamilies:
    """Documento parseado. `modifiers` son palabras que NO cambian la familia
    (claro, oscuro, pastel…) pero delatan que el cliente pidió un tono."""

    version: int = 0
    modifiers: frozenset[str] = field(default_factory=frozenset)
    families: tuple[ColorFamily, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.families


EMPTY_COLOR_FAMILIES = ColorFamilies()


@dataclass(frozen=True)
class ColorFamilyResolution:
    """Resultado de resolver lo que pidió el cliente contra el catálogo.

    - `families`: labels de las familias detectadas en el pedido.
    - `canonical`: el color del catálogo a persistir (solo si `resolved`).
    - `candidates`: colores del catálogo dentro de esas familias.
    - `shade_requested`: el cliente pidió un tono específico (no solo la
      familia con otro género/número) → el bot debe confirmarlo.
    """

    requested: str
    families: tuple[str, ...]
    canonical: str | None
    candidates: tuple[str, ...]
    shade_requested: bool

    @property
    def status(self) -> str:
        if self.canonical is not None:
            return "resolved"
        if self.candidates or len(self.families) > 1:
            return "ambiguous"
        return "not_offered"


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def parse_color_families(doc: Any) -> ColorFamilies:
    """`{"version": 1, "modifiers": [...], "families": {id: {label, shades}}}`
    → `ColorFamilies`. Valida el schema; orden de declaración preservado."""
    if not isinstance(doc, dict):
        raise InvalidColorFamiliesError("el documento debe ser un mapping")
    raw_families = doc.get("families")
    if not isinstance(raw_families, dict) or not raw_families:
        raise InvalidColorFamiliesError("`families` debe ser un mapping no vacío")

    version = doc.get("version", 1)
    if not isinstance(version, int):
        raise InvalidColorFamiliesError("`version` debe ser entero")

    raw_modifiers = doc.get("modifiers") or []
    if not isinstance(raw_modifiers, list):
        raise InvalidColorFamiliesError("`modifiers` debe ser una lista")
    modifiers = frozenset(
        normalize_label(m) for m in raw_modifiers if isinstance(m, str) and m.strip()
    )

    families: list[ColorFamily] = []
    for fam_id, spec in raw_families.items():
        if not isinstance(fam_id, str) or not fam_id.strip():
            raise InvalidColorFamiliesError("id de familia inválido")
        if not isinstance(spec, dict):
            raise InvalidColorFamiliesError(f"familia {fam_id!r}: debe ser un mapping")
        label = spec.get("label")
        if not isinstance(label, str) or not label.strip():
            raise InvalidColorFamiliesError(f"familia {fam_id!r}: falta `label`")
        raw_shades = spec.get("shades") or []
        if not isinstance(raw_shades, list):
            raise InvalidColorFamiliesError(f"familia {fam_id!r}: `shades` debe ser lista")
        shades: list[str] = []
        seen: set[str] = set()
        for shade in raw_shades:
            if not isinstance(shade, str) or not shade.strip():
                raise InvalidColorFamiliesError(f"familia {fam_id!r}: shade inválido")
            key = normalize_label(shade)
            if key not in seen:
                seen.add(key)
                shades.append(shade.strip())
        families.append(
            ColorFamily(id=fam_id.strip(), label=label.strip(), shades=tuple(shades))
        )
    return ColorFamilies(
        version=version, modifiers=modifiers, families=tuple(families)
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _stem(token: str) -> str:
    """Stem liviano de género/número castellano.

    "azules" → "azul", "rojas" → "roj", "celeste(s)" → "celest",
    "clarito"/"clarita" → "clarit". Corta el plural (`es`/`s`) y UNA vocal
    final (a/o/e). Palabras cortas (≤3) quedan intactas ("mar", "oro").
    """
    if len(token) > 4 and token.endswith("es"):
        token = token[:-2]
    elif len(token) > 3 and token.endswith("s"):
        token = token[:-1]
    if len(token) > 3 and token[-1] in "aoe":
        token = token[:-1]
    return token


def _stem_tokens(text: str) -> frozenset[str]:
    return frozenset(_stem(t) for t in normalize_label(text).split())


def _detect(text: str, families: ColorFamilies) -> list[ColorFamily]:
    """Familias cuyo nombre más específico matchea `text`.

    Devuelve solo las familias que alcanzan la especificidad MÁXIMA
    encontrada (en orden de declaración, sin duplicados).
    """
    wanted = _stem_tokens(text)
    if not wanted:
        return []
    best = 0
    hits: list[tuple[int, ColorFamily]] = []
    for family in families.families:
        for name in family.names:
            name_tokens = _stem_tokens(name)
            if name_tokens and name_tokens <= wanted:
                hits.append((len(name_tokens), family))
                best = max(best, len(name_tokens))
    result: list[ColorFamily] = []
    for size, family in hits:
        if size == best and family not in result:
            result.append(family)
    return result


def family_of_color(label: str, families: ColorFamilies) -> ColorFamily | None:
    """Familia a la que pertenece un color del catálogo ("Azul marino" →
    azul; "Lila" → lila). None si no está declarado en ninguna."""
    detected = _detect(label, families)
    return detected[0] if detected else None


def resolve_color_family(
    requested: str, valid_colors: list[str], families: ColorFamilies
) -> ColorFamilyResolution | None:
    """Resuelve lo que pidió el cliente contra los colores REALES del producto.

    None → el texto no cae en ninguna familia conocida (el caller conserva
    su rechazo legacy). Si el texto es exactamente un color del catálogo se
    devuelve resuelto sin ceremonia.
    """
    if families.is_empty or not normalize_label(requested):
        return None

    exact = match_option(requested, valid_colors)
    if exact is not None:
        fam = family_of_color(exact, families)
        return ColorFamilyResolution(
            requested=requested,
            families=(fam.label if fam else exact,),
            canonical=exact,
            candidates=(exact,),
            shade_requested=False,
        )

    detected = _detect(requested, families)
    if not detected:
        return None
    detected_ids = {f.id for f in detected}
    candidates = tuple(
        color
        for color in valid_colors
        if (fam := family_of_color(color, families)) is not None
        and fam.id in detected_ids
    )
    labels = tuple(f.label for f in detected)
    if len(detected) == 1 and len(candidates) == 1:
        family = detected[0]
        shade_requested = _stem_tokens(requested) != _stem_tokens(family.label)
        return ColorFamilyResolution(
            requested=requested,
            families=labels,
            canonical=candidates[0],
            candidates=candidates,
            shade_requested=shade_requested,
        )
    return ColorFamilyResolution(
        requested=requested,
        families=labels,
        canonical=None,
        candidates=candidates,
        shade_requested=True,
    )
