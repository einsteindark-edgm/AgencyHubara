"""Índice compacto de un JSONL que solo crece (Calidad LLM sin 504, 2026-11-03).

Calidad LLM agrega por ventana archivos que solo crecen —los scorecards de
cada día y las trazas de cada sesión— pero de cada línea usa unas pocas
columnas. Parsear todo en cada pedido tumbaba la pestaña: cuatro listas a la
vez sobre 56 días y el cast de Agents cortaba a los 15 s (504).

`indexed_lines(source, index, project, version=…)` devuelve la proyección de
cada línea válida de `source` y la guarda en `index` (un JSON al lado):

  * `source` no cambió (mismo inodo, tamaño y fecha): se lee solo el índice;
  * creció y lo ya indexado sigue igual (cabeza y cola de lo cubierto): se
    parsea solo lo nuevo;
  * si no (reescrito, truncado, otra `version` de la proyección): se rearma.

Una última línea a medias (el escritor va por la mitad) no se indexa hasta
que termina. El índice es derivado: si no se puede escribir se devuelve igual
lo calculado, y si se borra se rearma. `project(record, at, length)` recibe
dónde está la línea en `source`, para que quien necesite el registro completo
lea solo esa línea (`read_line`). Dentro de un proceso, dos pedidos que
rearman el mismo índice no repiten el trabajo: el segundo espera al primero.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

Projection = Callable[[dict[str, Any], int, int], dict[str, Any] | None]

#: Bytes de cabeza y de cola de lo indexado que se comparan para saber que
#: `source` es el mismo archivo y solo creció.
_PROBE_BYTES = 256

_guard = threading.Lock()
_building: dict[str, threading.Lock] = {}


def _lock_for(index: Path) -> threading.Lock:
    with _guard:
        return _building.setdefault(str(index), threading.Lock())


def _probe(f: Any, covered: int) -> str:
    f.seek(0)
    head = f.read(min(_PROBE_BYTES, covered))
    tail_from = max(0, covered - _PROBE_BYTES)
    f.seek(tail_from)
    tail = f.read(covered - tail_from)
    return hashlib.blake2b(head + b"|" + tail, digest_size=16).hexdigest()


def _load(index: Path, version: int) -> dict[str, Any] | None:
    try:
        data = json.loads(index.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != version or not isinstance(data.get("rows"), list):
        return None
    return data


def _save(index: Path, data: dict[str, Any]) -> None:
    tmp = index.with_name(f".{index.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        index.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, index)
    except OSError:
        tmp.unlink(missing_ok=True)


def _parse(chunk: bytes, start: int, project: Projection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    at = start
    for line in chunk.split(b"\n"):
        length = len(line)
        stripped = line.strip()
        if stripped:
            try:
                record = json.loads(stripped.decode("utf-8", errors="replace"))
            except ValueError:
                record = None
            if isinstance(record, dict):
                row = project(record, at, length)
                if row is not None:
                    rows.append(row)
        at += length + 1
    return rows


def indexed_lines(source: Path, index: Path, project: Projection, *, version: int) -> list[dict[str, Any]]:
    """La proyección de cada línea válida de `source`, en orden (ver el módulo)."""
    try:
        st = source.stat()
    except OSError:
        return []
    with _lock_for(index):
        cached = _load(index, version)
        if cached is not None and (cached.get("ino"), cached.get("size"), cached.get("mtime_ns")) == (
            st.st_ino, st.st_size, st.st_mtime_ns,
        ):
            return list(cached["rows"])
        try:
            with source.open("rb") as f:
                start, rows = 0, []
                covered = cached.get("covered") if cached is not None else None
                if (
                    cached is not None
                    and cached.get("ino") == st.st_ino
                    and isinstance(covered, int)
                    and 0 <= covered <= st.st_size
                    and _probe(f, covered) == cached.get("probe")
                ):
                    start, rows = covered, list(cached["rows"])
                f.seek(start)
                chunk = f.read(st.st_size - start)
                consumed = chunk.rfind(b"\n") + 1  # una última línea a medias espera
                rows.extend(_parse(chunk[:consumed - 1] if consumed else b"", start, project))
                covered = start + consumed
                probe = _probe(f, covered)
        except OSError:
            return []
        _save(index, {
            "v": version, "ino": st.st_ino, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "covered": covered, "probe": probe, "rows": rows,
        })
        return rows


def read_line(source: Path, at: int, length: int) -> dict[str, Any] | None:
    """El registro de la línea que empieza en `at` (o None si ya no está ahí)."""
    try:
        with source.open("rb") as f:
            f.seek(at)
            raw = f.read(length)
        record = json.loads(raw.decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None
