"""Frecuencia del remarketing — cuántos toques máximo hace el bot por negocio.

Lo elige el operador en el dashboard (Agents → Remarketing → Frecuencia); es
SOLO la cantidad (los huecos 2h/2h/4h/6h/6h de `reengagement_ladder` no se
tocan). El TECHO lo declara Terraform por tenant (`REMARKETING_MAX_TOUCHES`,
módulo `scheduler-config`): el dashboard elige dentro del techo y nunca lo
sube. Sin nada guardado manda el techo (= el comportamiento de siempre).

Aplica desde ya: lo leen la central de envío, el ciclo (snapshot y etiqueta
`SIN_RESPUESTA`) y el contexto del remarketing en cada decisión, sin cache.

Vive en el vault (`_rollout/remarketing_frequency.json`, escritura atómica):
es config operativa del negocio, no del turno. Un archivo ilegible cae al
techo — nunca rompe un envío ni lo deja en 0 por accidente.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.platform.whatsapp.reengagement_ladder import LADDER_GAPS_MS, clamp_max_steps

CEILING_ENV = "REMARKETING_MAX_TOUCHES"

REASON_ABOVE_CEILING = "above_ceiling"
REASON_INVALID = "invalid_value"


@dataclass(frozen=True)
class FrequencyState:
    """Lo guardado por el dashboard. `max_touches=None` = nunca se tocó."""

    max_touches: int | None = None
    updated_at_ms: int | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class Outcome:
    applied: bool
    reason: str = ""


def _path(vault_dir: Path) -> Path:
    return Path(vault_dir) / "_rollout" / "remarketing_frequency.json"


def ceiling() -> int:
    """Techo de Terraform; ausente o ilegible = la escalera completa."""
    raw = os.environ.get(CEILING_ENV, "").strip()
    try:
        return clamp_max_steps(int(raw))
    except ValueError:
        return len(LADDER_GAPS_MS)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def read_state(vault_dir: Path) -> FrequencyState:
    try:
        raw = json.loads(_path(vault_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FrequencyState()
    if not isinstance(raw, dict) or not _is_int(raw.get("max_touches")):
        return FrequencyState()
    at = raw.get("updated_at_ms")
    by = raw.get("updated_by")
    return FrequencyState(
        max_touches=raw["max_touches"],
        updated_at_ms=at if _is_int(at) else None,
        updated_by=by if isinstance(by, str) else None,
    )


def effective_max_touches(vault_dir: Path) -> int:
    """El tope que corre de verdad: lo guardado, dentro del techo."""
    top = ceiling()
    saved = read_state(vault_dir).max_touches
    if saved is None:
        return top
    return min(max(saved, 0), top)


def set_max_touches(vault_dir: Path, value: Any, *, actor: str, now_ms: int) -> Outcome:
    """Guarda la cantidad. Rechaza lo que no sea un entero ≥ 0 o pase el techo."""
    if not _is_int(value) or value < 0:
        return Outcome(applied=False, reason=REASON_INVALID)
    if value > ceiling():
        return Outcome(applied=False, reason=REASON_ABOVE_CEILING)
    path = _path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(
            {"max_touches": value, "updated_at_ms": now_ms, "updated_by": actor},
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return Outcome(applied=True)
