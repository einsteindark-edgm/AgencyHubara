"""Store de trazas por turno (HU-SC-0) — lectura y escritura por sesión.

Archivo propio por sesión: `<vault>/<session_id>/evals/turn_traces.jsonl`.
NO va en el JSONL del dashboard (`sessions/<id>.jsonl`): el corte por episodio
del evaluador cuenta líneas de ese archivo (`msgs_count_at_start/close`) y el
dashboard pinta cada evento como burbuja. Lo escribe el worker de ventas
(`persist_turn_trace`) y lo leen el scorecard y la API de evals (mismo plugin).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TAIL_BYTES = 64_000


def trace_path(vault_dir: Path, session_id: str) -> Path:
    return vault_dir / session_id / "evals" / "turn_traces.jsonl"


def append_trace(vault_dir: Path, session_id: str, record: dict[str, Any]) -> None:
    path = trace_path(vault_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _parse_lines(lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def read_traces(vault_dir: Path, session_id: str) -> list[dict[str, Any]]:
    path = trace_path(vault_dir, session_id)
    try:
        return _parse_lines(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return []


def last_trace(vault_dir: Path, session_id: str) -> dict[str, Any] | None:
    """Última traza válida leyendo solo la cola del archivo."""
    path = trace_path(vault_dir, session_id)
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    parsed = _parse_lines(tail.splitlines())
    return parsed[-1] if parsed else None


def traces_for_episode(
    vault_dir: Path, session_id: str, episode_id: str
) -> list[dict[str, Any]]:
    return [
        t for t in read_traces(vault_dir, session_id) if t.get("episode_id") == episode_id
    ]
