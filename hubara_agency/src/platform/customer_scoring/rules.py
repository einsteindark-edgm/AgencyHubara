"""Motor de rules — aplica un YAML doc parseado a `CustomerFeatures`.

Diseño:
  * `RulesDoc` es un `@dataclass(frozen=True)` validado, parseado desde el
    YAML por `parse_rules_doc()`. Si el YAML tiene shape inválido, levanta
    `InvalidRulesDocError` con mensaje claro.
  * `apply_rules(features, doc) -> CustomerScore` es PURO — sin I/O.
  * Operadores soportados en `when:`: `>=`, `<=`, `>`, `<`, `==`, `!=`.
  * Comparaciones con None: si la feature es None, una condición que la usa
    se evalúa como False (conservador: no aplicar la regla si dato falta).
  * Tags: primera que matchea wins; si ninguna, devuelve "Estándar".
  * Score: suma de bins (uno por feature) → letter via score_letter ordenado
    descendente por `min`.
  * `reason_hints` opcional: el primero que matchea sobreescribe la reason
    default del letter (útil para mensajes contextuales).

R-DET: completamente puro, sin acceso a now, random, env.
R-JSON: input/output son frozen dataclasses JSON-serializable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.platform.customer_scoring.port import (
    CustomerFeatures,
    CustomerScore,
    ScoreBreakdownItem,
)

log = logging.getLogger(__name__)


# Operadores soportados en `when:` y `bins:`.
_OPS: dict[str, Any] = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">":  lambda a, b: a > b,
    "<":  lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


class InvalidRulesDocError(ValueError):
    """El YAML no matchea el shape esperado o tiene valores inválidos."""


@dataclass(frozen=True)
class _TagRule:
    name: str
    when: dict[str, dict[str, float]]  # feature → {op: value}


@dataclass(frozen=True)
class _ScoreBin:
    upper: float | None  # None significa "default / cap" (sin upper bound)
    points: int


@dataclass(frozen=True)
class _ScoreWeight:
    feature: str
    bins: tuple[_ScoreBin, ...]


@dataclass(frozen=True)
class _LetterRule:
    min_score: int
    letter: str
    reason: str


@dataclass(frozen=True)
class _ReasonHint:
    when: dict[str, dict[str, float]]
    message: str


@dataclass(frozen=True)
class RulesDoc:
    """YAML rules ya parseado y validado.

    Versión semántica para auditar: cada `CustomerScore` persiste
    `rules_version` para poder comparar histórico.
    """
    version: int
    description: str
    tags: tuple[_TagRule, ...]
    score_weights: tuple[_ScoreWeight, ...]
    score_letter: tuple[_LetterRule, ...]
    reason_hints: tuple[_ReasonHint, ...]


# ----------------------------------------------------------------------
# Parser — YAML dict → RulesDoc validado
# ----------------------------------------------------------------------


def parse_rules_doc(raw: dict[str, Any]) -> RulesDoc:
    """Parsea + valida un dict (típicamente cargado de YAML) → RulesDoc.

    Raises `InvalidRulesDocError` con mensaje claro si el shape no aplica.
    """
    if not isinstance(raw, dict):
        raise InvalidRulesDocError(
            f"rules doc debe ser un dict en el top-level, no {type(raw).__name__}"
        )

    version_raw = raw.get("version")
    if not isinstance(version_raw, int) or version_raw < 1:
        raise InvalidRulesDocError(
            f"`version` debe ser un int >= 1, recibido {version_raw!r}"
        )

    description = raw.get("description") or ""
    if not isinstance(description, str):
        raise InvalidRulesDocError("`description` debe ser string")

    tags_raw = raw.get("tags") or []
    if not isinstance(tags_raw, list):
        raise InvalidRulesDocError("`tags` debe ser una lista")
    tags = tuple(_parse_tag_rule(t, idx) for idx, t in enumerate(tags_raw))

    weights_raw = raw.get("score_weights") or []
    if not isinstance(weights_raw, list):
        raise InvalidRulesDocError("`score_weights` debe ser una lista")
    score_weights = tuple(
        _parse_score_weight(w, idx) for idx, w in enumerate(weights_raw)
    )

    letter_raw = raw.get("score_letter") or []
    if not isinstance(letter_raw, list) or not letter_raw:
        raise InvalidRulesDocError(
            "`score_letter` debe ser una lista no vacía"
        )
    score_letter = tuple(
        _parse_letter_rule(le, idx) for idx, le in enumerate(letter_raw)
    )
    # Ordenamos por min_score DESC — el primero que aplica wins.
    score_letter = tuple(
        sorted(score_letter, key=lambda l: l.min_score, reverse=True)
    )

    hints_raw = raw.get("reason_hints") or []
    if not isinstance(hints_raw, list):
        raise InvalidRulesDocError("`reason_hints` debe ser una lista")
    reason_hints = tuple(
        _parse_reason_hint(h, idx) for idx, h in enumerate(hints_raw)
    )

    return RulesDoc(
        version=version_raw,
        description=description,
        tags=tags,
        score_weights=score_weights,
        score_letter=score_letter,
        reason_hints=reason_hints,
    )


def _parse_tag_rule(raw: Any, idx: int) -> _TagRule:
    if not isinstance(raw, dict):
        raise InvalidRulesDocError(
            f"tags[{idx}] debe ser dict, no {type(raw).__name__}"
        )
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise InvalidRulesDocError(f"tags[{idx}].name debe ser string no vacío")
    when = raw.get("when") or {}
    return _TagRule(name=name, when=_parse_when(when, f"tags[{idx}].when"))


def _parse_when(
    raw: Any, ctx: str
) -> dict[str, dict[str, float]]:
    """Parsea un dict `feature → {op: value}`. Cada feature condiciona
    via uno o varios operadores. Todas las condiciones de un `when` deben
    matchear (AND implícito)."""
    if not isinstance(raw, dict):
        raise InvalidRulesDocError(f"{ctx} debe ser dict")
    out: dict[str, dict[str, float]] = {}
    for feat, cond in raw.items():
        if not isinstance(feat, str):
            raise InvalidRulesDocError(f"{ctx}: feature debe ser string")
        if not isinstance(cond, dict):
            raise InvalidRulesDocError(
                f"{ctx}.{feat}: debe ser dict {{op: value}}"
            )
        ops_dict: dict[str, float] = {}
        for op, val in cond.items():
            if op not in _OPS:
                raise InvalidRulesDocError(
                    f"{ctx}.{feat}: operador {op!r} desconocido. "
                    f"Soportados: {sorted(_OPS)}"
                )
            if not isinstance(val, (int, float)):
                raise InvalidRulesDocError(
                    f"{ctx}.{feat}.{op}: value debe ser numérico"
                )
            ops_dict[op] = float(val)
        out[feat] = ops_dict
    return out


def _parse_score_weight(raw: Any, idx: int) -> _ScoreWeight:
    if not isinstance(raw, dict):
        raise InvalidRulesDocError(f"score_weights[{idx}] debe ser dict")
    feature = raw.get("feature")
    if not isinstance(feature, str) or not feature:
        raise InvalidRulesDocError(
            f"score_weights[{idx}].feature debe ser string"
        )
    bins_raw = raw.get("bins") or []
    if not isinstance(bins_raw, list) or not bins_raw:
        raise InvalidRulesDocError(
            f"score_weights[{idx}].bins debe ser lista no vacía"
        )
    bins = tuple(_parse_bin(b, f"score_weights[{idx}].bins[{i}]")
                 for i, b in enumerate(bins_raw))
    return _ScoreWeight(feature=feature, bins=bins)


def _parse_bin(raw: Any, ctx: str) -> _ScoreBin:
    if not isinstance(raw, dict):
        raise InvalidRulesDocError(f"{ctx} debe ser dict")
    points = raw.get("points")
    if not isinstance(points, int):
        raise InvalidRulesDocError(f"{ctx}.points debe ser int")
    upper = raw.get("upper")
    if upper is not None and not isinstance(upper, (int, float)):
        raise InvalidRulesDocError(f"{ctx}.upper debe ser numérico o ausente")
    return _ScoreBin(upper=float(upper) if upper is not None else None, points=points)


def _parse_letter_rule(raw: Any, idx: int) -> _LetterRule:
    if not isinstance(raw, dict):
        raise InvalidRulesDocError(f"score_letter[{idx}] debe ser dict")
    min_score = raw.get("min")
    letter = raw.get("letter")
    reason = raw.get("reason") or ""
    if not isinstance(min_score, int):
        raise InvalidRulesDocError(
            f"score_letter[{idx}].min debe ser int"
        )
    if not isinstance(letter, str) or not letter:
        raise InvalidRulesDocError(
            f"score_letter[{idx}].letter debe ser string"
        )
    if not isinstance(reason, str):
        raise InvalidRulesDocError(
            f"score_letter[{idx}].reason debe ser string"
        )
    return _LetterRule(min_score=min_score, letter=letter, reason=reason)


def _parse_reason_hint(raw: Any, idx: int) -> _ReasonHint:
    if not isinstance(raw, dict):
        raise InvalidRulesDocError(f"reason_hints[{idx}] debe ser dict")
    when = raw.get("when") or {}
    message = raw.get("message")
    if not isinstance(message, str) or not message:
        raise InvalidRulesDocError(
            f"reason_hints[{idx}].message debe ser string"
        )
    return _ReasonHint(
        when=_parse_when(when, f"reason_hints[{idx}].when"),
        message=message,
    )


# ----------------------------------------------------------------------
# Evaluation — features + doc → CustomerScore
# ----------------------------------------------------------------------


def apply_rules(features: CustomerFeatures, doc: RulesDoc) -> CustomerScore:
    """Aplica el rules doc a las features y devuelve el `CustomerScore`.

    Algoritmo:
      1. Determinar `tag`: la primera regla en `doc.tags` cuyo `when` matcha.
         Sino "Estándar".
      2. Computar `score_value`: para cada `score_weights[]`, encontrar el
         primer bin cuyo `upper` cubre el feature value. Si todos los bins
         tienen upper definido y el value los supera, usar el último (= default).
         Sumar los puntos. Si la feature es None, el bin con `upper=None`
         (default) aplica con su `points`.
      3. Mapear `score_value → letter` via `doc.score_letter` (ordenado desc).
      4. Construir `reason`: primer `reason_hints[]` que matcha, sino la
         `reason` del letter rule.

    Returns:
      `CustomerScore` con tag + letter + value + reason + breakdown +
      monetary + last_purchase + rules_version.
    """
    # Tag.
    tag = "Estándar"
    for tr in doc.tags:
        if _matches_when(features, tr.when):
            tag = tr.name
            break

    # Score breakdown + total.
    breakdown: list[ScoreBreakdownItem] = []
    score_value = 0
    for w in doc.score_weights:
        value = _get_feature_value(features, w.feature)
        bin_points = _bin_points(w.bins, value)
        score_value += bin_points
        # NaN → usar 0 en el breakdown (UI no debería ver NaN crudo).
        feature_value_safe = (
            float(value) if isinstance(value, (int, float)) else 0.0
        )
        breakdown.append(
            ScoreBreakdownItem(
                feature=w.feature,
                feature_value=feature_value_safe,
                points=bin_points,
            )
        )

    # Letter mapping. doc.score_letter ya está ordenado desc por min_score.
    letter_rule = _pick_letter(score_value, doc.score_letter)
    score_letter = letter_rule.letter if letter_rule else "—"

    # Reason: primero hints que apliquen, sino letter.reason.
    reason = letter_rule.reason if letter_rule else ""
    for hint in doc.reason_hints:
        if _matches_when(features, hint.when):
            reason = hint.message
            break

    return CustomerScore(
        tag=tag,
        score_letter=score_letter,
        score_value=score_value,
        score_reason=reason,
        breakdown=breakdown,
        monetary_cop=features.monetary_cop,
        last_purchase_at_ms=features.last_purchase_at_ms,
        frequency_total=features.frequency_total,
        episodes_total=features.episodes_total,
        rules_version=doc.version,
    )


def _matches_when(
    features: CustomerFeatures, when: dict[str, dict[str, float]]
) -> bool:
    """True si todas las condiciones del `when` matchean. AND implícito."""
    if not when:
        return False  # un `when` vacío no debe matchear nunca (defensivo)
    for feat, cond in when.items():
        value = _get_feature_value(features, feat)
        if value is None:
            # Feature missing → conservador: condición no aplica → no matchea.
            return False
        for op, threshold in cond.items():
            if not _OPS[op](value, threshold):
                return False
    return True


def _get_feature_value(
    features: CustomerFeatures, name: str
) -> float | int | None:
    """Acceso seguro a una feature por nombre. Devuelve None si la feature
    no existe en el dataclass — para evitar typos de YAML que silenciosamente
    pasan."""
    value = getattr(features, name, _MISSING)
    if value is _MISSING:
        log.warning(
            "customer_scoring: feature %r usada en rules pero no existe "
            "en CustomerFeatures — la regla NO matcheará. Revisar typo en YAML.",
            name,
        )
        return None
    return value  # type: ignore[no-any-return]


_MISSING = object()


def _bin_points(bins: tuple[_ScoreBin, ...], value: float | int | None) -> int:
    """Encuentra el primer bin cuyo upper cubre `value`. Si todos tienen
    upper y `value` los supera, usa el último bin (que debería tener `upper=None`
    = default/cap)."""
    if value is None:
        # Feature missing — usar el bin default (upper=None) si existe, sino 0.
        for b in bins:
            if b.upper is None:
                return b.points
        return 0
    for b in bins:
        if b.upper is None:
            return b.points  # default reached
        if value <= b.upper:
            return b.points
    return 0  # bins definidos pero ninguno aplicó (shouldn't happen si hay default)


def _pick_letter(
    score: int, letter_rules: tuple[_LetterRule, ...]
) -> _LetterRule | None:
    """Devuelve la primera regla en `letter_rules` (ordenadas DESC por min_score)
    cuyo `min` es <= score."""
    for r in letter_rules:
        if score >= r.min_score:
            return r
    return None
