"""Sink filesystem — JSONL append-only por día.

Layout:

    <WORKSPACE_VAULT_DIR>/_analytics/YYYY-MM-DD.jsonl

Cada línea es un `AnalyticsEvent` serializado a JSON. Diseñado para post-mortem
analysis local + ingesta por pipelines externos (ej: dump al data warehouse).

PREMORTEM #3: multi-process safety. POSIX garantiza `write()` atómico para
buffers < PIPE_BUF (4KB Linux). Como nuestras líneas suelen ser < 1KB no
hay corrupción en práctica — PERO si en el futuro alguien embebe payload
grande (texto transcrito completo, base64, etc.) podrían intercalarse.

Solución: lock OS-level con `fcntl.flock(LOCK_EX)` (Unix) o
`msvcrt.locking` (Windows). Esto serializa appends entre procesos del
mismo host. En k8s con N réplicas en hosts distintos esto no aplica —
cada host escribe a su propio path (asumimos PV per-replica) o se usa
una solución log-aggregator más arriba (Fluentd, Loki). Documentado.

Plus assertion defensiva: si una línea supera 4KB, loguea warning para
que el dev sepa que necesita refactor (truncate o sink alternativo).

R-JSON: AnalyticsEvent ya es JSON-safe (frozen dataclass de primitivos).
"""
from __future__ import annotations

import asyncio
import errno
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import structlog

from src.platform.analytics.events import AnalyticsEvent

logger = structlog.get_logger()

# POSIX guaranteed atomic for write < PIPE_BUF (Linux=4096, macOS=512).
# Usamos 4KB como cap defensivo; si superamos, loguamos warning.
_LINE_SIZE_WARN = 4096


class FilesystemAnalyticsSink:
    name = "filesystem"

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._lock = asyncio.Lock()  # serializa intra-process; OS lock serializa cross-process
        self._base_dir.mkdir(parents=True, exist_ok=True)

    async def write(self, event: AnalyticsEvent) -> None:
        # Path por día UTC para rotación simple.
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        file = self._base_dir / f"{day}.jsonl"
        line = json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":"))
        if len(line.encode("utf-8")) > _LINE_SIZE_WARN:
            logger.warning(
                "analytics_line_oversized",
                bytes=len(line.encode("utf-8")),
                event_kind=event.kind,
                hint="POSIX atomic append guarantee at risk — consider sink_split",
            )
        async with self._lock:
            # asyncio.to_thread mantiene el event loop libre durante el write
            await asyncio.to_thread(_append_line_locked, file, line)


def _append_line_locked(path: Path, line: str) -> None:
    """Append con lock OS-level cross-process.

    Unix: `fcntl.flock(LOCK_EX)` serializa appends entre procesos del mismo
    host. Windows: `msvcrt.locking` (best-effort). Si el módulo no está
    disponible (rare edge), fallback a append sin lock — degradado pero no
    bloqueante.
    """
    if sys.platform == "win32":
        _append_line_win32(path, line)
        return
    # Unix path
    try:
        import fcntl
    except ImportError:
        # Plataforma exótica sin fcntl — fallback inseguro pero no bloquea.
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
        return

    with path.open("a", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except OSError as e:
            if e.errno not in (errno.ENOLCK, errno.ENOSYS):
                raise
            # fcntl no soportado en el FS (tmpfs raro, NFS sin lockd) →
            # degradado: append sin lock. Loguea solo la primera vez.
            logger.warning(
                "analytics_filesystem_lock_unavailable",
                path=str(path),
                errno=e.errno,
            )
        try:
            f.write(line)
            f.write("\n")
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _append_line_win32(path: Path, line: str) -> None:
    import msvcrt
    with path.open("a", encoding="utf-8") as f:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        except OSError:
            pass
        try:
            f.write(line)
            f.write("\n")
        finally:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
