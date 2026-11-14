"""Ventana de tiempo de las tools (`window`) → query del backend.

Gramática (igual a `src/shared/window.ts` del plugin):
`7d | 30d | 90d | total | YYYY-MM-DD..YYYY-MM-DD` (rango inclusivo, día America/Bogota),
por defecto `30d`. La traducción espeja al dashboard (`AdsSection.tsx:72-79`):
preset → `days=N`, rango → `from`/`to`, `total` → nada, salvo en daily → `days=90`.

El backend degrada una fecha inválida a «todo el historial» sin avisar
(`_window` en `plugins/ads/api/__init__.py`), así que acá se valida antes de llamar.
"""

from datetime import date

DEFAULT_WINDOW = "30d"
_PRESET_DAYS: dict[str, int | None] = {"7d": 7, "30d": 30, "90d": 90, "total": None}
_RANGE_SEP = ".."


class WindowError(ValueError):
    pass


def _parse_day(raw: str) -> date:
    if len(raw) != 10:
        raise WindowError(f"invalid window date {raw!r} (expected YYYY-MM-DD)")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise WindowError(f"invalid window date {raw!r} (expected YYYY-MM-DD)") from exc


def parse_window(raw: str | None) -> str:
    """Normaliza `raw` a una ventana válida o lanza `WindowError`."""
    if not raw:
        return DEFAULT_WINDOW
    if raw in _PRESET_DAYS:
        return raw
    start, sep, end = raw.partition(_RANGE_SEP)
    if not sep:
        raise WindowError(f"invalid window {raw!r} (expected 7d|30d|90d|total|YYYY-MM-DD..YYYY-MM-DD)")
    a, b = sorted((_parse_day(start), _parse_day(end)))
    return f"{a.isoformat()}{_RANGE_SEP}{b.isoformat()}"


def window_query(window: str, *, daily: bool = False) -> dict[str, str]:
    """Query del backend para una ventana ya normalizada por `parse_window`."""
    if _RANGE_SEP in window:
        start, _, end = window.partition(_RANGE_SEP)
        return {"from": start, "to": end}
    days = _PRESET_DAYS[window]
    if days is not None:
        return {"days": str(days)}
    # El gráfico diario del dashboard pide 90 días cuando la ventana es «total».
    return {"days": "90"} if daily else {}
