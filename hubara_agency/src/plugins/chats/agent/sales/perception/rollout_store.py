"""Estado del encendido del bot nuevo y métricas de la sombra (plan del
laboratorio §4.4, PR 16).

El estado vive en `<vault>/_rollout/perception.json`: un directorio `_*`
que los recorredores de sesiones (reengagement, Order Sentinel, barridos)
ignoran. Lo escribe el control del dashboard; lo lee el ingest en cada
mensaje (un archivo chico). Nunca pasa el techo de Terraform: eso lo aplica
`rollout.effective_mode` al leer.

Las métricas de la sombra salen de las trazas de los turnos
(`<vault>/wa_*/evals/turn_traces.jsonl`) con modo activo: días desde la
primera, turnos, caídas a "turno como hoy" (`perception.fallback`) y p95 de
la duración de la percepción.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from src.plugins.chats.agent.sales.perception.rollout import RolloutState

_DAY_MS = 86_400_000
_LAYER_MODES = ("shadow", "canary", "on")


def _path(vault_dir: Path) -> Path:
    return Path(vault_dir) / "_rollout" / "perception.json"


def read_state(vault_dir: Path) -> RolloutState:
    try:
        raw = json.loads(_path(vault_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return RolloutState()
    if not isinstance(raw, dict):
        return RolloutState()
    numbers = raw.get("test_numbers") or []
    percent = raw.get("canary_percent")
    return RolloutState(
        mode=str(raw.get("mode") or "off"),
        canary_percent=int(percent) if isinstance(percent, (int, float)) else 0,
        test_numbers=tuple(str(n) for n in numbers if isinstance(n, str)),
        updated_at_ms=raw.get("updated_at_ms") if isinstance(raw.get("updated_at_ms"), int) else None,
        updated_by=raw.get("updated_by") if isinstance(raw.get("updated_by"), str) else None,
    )


def write_state(vault_dir: Path, state: RolloutState) -> None:
    path = _path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({**asdict(state), "test_numbers": list(state.test_numbers)}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class ShadowMetrics:
    days: int
    turns: int
    fallback_rate: float | None
    p95_ms: int | None


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


def shadow_metrics(vault_dir: Path, *, now_ms: int) -> ShadowMetrics:
    first: int | None = None
    turns = 0
    fallbacks = 0
    durations: list[int] = []
    root = Path(vault_dir)
    for sdir in sorted(root.glob("wa_*")):
        path = sdir / "evals" / "turn_traces.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                trace = json.loads(line)
            except ValueError:
                continue
            if not isinstance(trace, dict) or trace.get("mode") not in _LAYER_MODES:
                continue
            step = next((s for s in trace.get("steps") or [] if isinstance(s, dict) and s.get("kind") == "perception"), None)
            if step is None:
                continue
            turns += 1
            if step.get("fallback"):
                fallbacks += 1
            if isinstance(step.get("dur_ms"), (int, float)):
                durations.append(int(step["dur_ms"]))
            at = trace.get("turn_started_ms")
            if isinstance(at, (int, float)):
                first = int(at) if first is None else min(first, int(at))
    return ShadowMetrics(
        days=max(0, (now_ms - first) // _DAY_MS) if first is not None else 0,
        turns=turns,
        fallback_rate=(fallbacks / turns) if turns else None,
        p95_ms=_p95(durations),
    )
