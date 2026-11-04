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

from src.plugins.chats.shared import jsonl_index

# Cola leída para `last_trace`: una traza con muchas tools (24 × args + extracto)
# puede pasar los 64 KB; si la cola no cubre la última línea entera, `previous`
# se pierde y la numeración de turnos reinicia.
_TAIL_BYTES = 256_000


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
    """Todas las trazas válidas de la sesión. Una escritura cortada a mitad
    (disco lleno) puede dejar bytes que no son UTF-8: se reemplazan y la línea
    rota se descarta, nunca tumba a quien lee (chat, Calidad LLM, encendido)."""
    path = trace_path(vault_dir, session_id)
    try:
        # Un corte a mitad de escritura o un byte raro no tumba a quien lee.
        return _parse_lines(path.read_text(encoding="utf-8", errors="replace").splitlines())
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


# ── El resumen (Calidad LLM sin 504, 2026-11-03) ───────────────────────────
#: Lo que la lista de Calidad LLM y el filtro por bot usan de cada traza
#: (episodio, turno, quién lo disparó, cuándo empezó y qué workflow respondió).
SUMMARY_FIELDS = ("episode_id", "turn", "trigger", "turn_started_ms", "workflow")
#: Subirla si cambia lo que guarda el resumen: los guardados se rearman.
_SUMMARY_VERSION = 1


def summary_path(vault_dir: Path, session_id: str) -> Path:
    return trace_path(vault_dir, session_id).with_name("turn_traces.summary.json")


def _summary(trace: dict[str, Any], _at: int, _length: int) -> dict[str, Any]:
    row = {k: trace[k] for k in SUMMARY_FIELDS if k in trace}
    if "egress" in trace:
        # Las trazas de antes de `workflow`: el workflow nuevo deja la salida
        # de Jev y el actual nunca (`scorecard.bot.turn_workflow`).
        row["egress"] = True
    return row


def read_summaries(vault_dir: Path, session_id: str) -> list[dict[str, Any]]:
    """De cada traza de la sesión, solo `SUMMARY_FIELDS` (y `egress: True` si
    trajo la salida de Jev): sin las tools, sus argumentos ni sus extractos.
    Se guarda al lado de las trazas y se pone al día leyendo solo los turnos
    nuevos (`jsonl_index`); una sesión sin turnos nuevos no se vuelve a leer."""
    return jsonl_index.indexed_lines(
        trace_path(vault_dir, session_id), summary_path(vault_dir, session_id), _summary, version=_SUMMARY_VERSION
    )
