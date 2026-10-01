"""Shared helpers for the local sandbox (stdlib only — seed.py / inject.py run
with any python3, no repo imports).

Layout of the sandbox data dir (default ``<sandbox>/data``)::

    data/vault/<session>/metadata.json            same layout the real vault uses
    data/vault/<session>/sessions/<session>.jsonl
    data/vault/<session>/media/<file>
    data/catalog/snapshot.json + manifest.json    LocalSnapshotCatalogClient input
    data/static/catalog/*.png                     product photos (served by the launcher)
    data/medusa/store.json                        backing store of the FAKE Medusa REST API
    data/temporal/workflows.json                  "running" workflow ids of the FAKE Temporal client
    data/seed_info.json                           what the seed created (ids, names, times)

Every JSON write is atomic (temp + os.replace) and metadata/store mutations take
the same ``<file>.lock`` flock the repo's ``FilesystemMetadataStore.update`` uses,
so a mutation from inject.py serializes with the running API.
"""
from __future__ import annotations

import fcntl
import json
import os
import struct
import tempfile
import time
import zlib
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SANDBOX_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SANDBOX_DATA_DIR") or (SANDBOX_DIR / "data")).resolve()
VAULT_DIR = DATA_DIR / "vault"
CATALOG_DIR = DATA_DIR / "catalog"
STATIC_DIR = DATA_DIR / "static"
MEDUSA_STORE = DATA_DIR / "medusa" / "store.json"
TEMPORAL_STORE = DATA_DIR / "temporal" / "workflows.json"
SEED_INFO = DATA_DIR / "seed_info.json"
MOBILE_CONFIG = DATA_DIR / "mobile_config.json"  # lo que inject.py cambia de la configuración de la app

PORT = 8010
#: Catalog photo URLs. The REAL WhatsApp outbound builder only accepts
#: ``https://`` image links (``outbound._validate_media_source``), so the default
#: is an https host under the reserved ``.invalid`` TLD: image tools pass the real
#: validation, nothing can ever resolve it (the app just shows a placeholder).
DEFAULT_IMAGE_BASE = "https://cdn.sandbox.invalid/catalog"
#: Alternative that renders thumbnails in the emulator (served by the launcher at
#: /__sandbox/static) — but then photo tools are rejected by that same validation.
EMULATOR_IMAGE_BASE = f"http://10.0.2.2:{PORT}/__sandbox/static/catalog"

#: Metadata ``phone_number_id`` (Meta's id of OUR business number — not a phone).
PHONE_NUMBER_ID = "sandbox-phone-number-id"

MIN_MS = 60_000
HOUR_MS = 60 * MIN_MS
DAY_MS = 24 * HOUR_MS
#: Colombia has no DST — UTC-5 is exact (same fallback the repo uses).
BOGOTA = timezone(timedelta(hours=-5))


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_utc(ms: int) -> str:
    """ISO-8601 UTC with offset — the shape the repo writes in the JSONL."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def iso_z(ms: int) -> str:
    """Medusa-style timestamp (``2026-09-29T14:00:00.000Z``)."""
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def bogota_day(ms: int, delta_days: int = 0) -> str:
    """Calendar day in Colombia (YYYY-MM-DD), shifted by ``delta_days``."""
    day = datetime.fromtimestamp(ms / 1000, tz=BOGOTA).date() + timedelta(days=delta_days)
    return day.isoformat()


def atomic_write_json(path: Path, data: Any, *, indent: int | None = 2) -> None:
    """Same contract as ``src.platform.state.atomic_write_json``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=indent, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def locked_update(path: Path, mutator: Callable[[Any], Any], *, default: Any = None) -> Any:
    """Read-modify-write under ``<path>.lock`` (the lock file the repo's
    ``FilesystemMetadataStore.update`` uses for ``metadata.json``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / f"{path.name}.lock", "w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            current = read_json(path, default)
            result = mutator(current)
            if result is not None:
                atomic_write_json(path, result)
            return result
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def session_paths(session_id: str) -> tuple[Path, Path, Path]:
    """(metadata.json, history jsonl, media dir) of a vault session."""
    base = VAULT_DIR / session_id
    return base / "metadata.json", base / "sessions" / f"{session_id}.jsonl", base / "media"


# ── tiny PNG writer (no Pillow needed) ──────────────────────────────────────


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png_from_rows(width: int, height: int, pixel: Callable[[int, int], tuple[int, int, int]]) -> bytes:
    """RGB PNG where ``pixel(x, y) -> (r, g, b)``."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        for x in range(width):
            raw.extend(pixel(x, y))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b"")


def product_png(base: tuple[int, int, int], accent: tuple[int, int, int], size: int = 240) -> bytes:
    """A candle-ish placeholder: background + a centered 'candle' + a 'flame'."""
    cx = size // 2

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        if size * 0.18 < y < size * 0.30 and abs(x - cx) < size * 0.05:
            return (250, 190, 60)  # flame
        if size * 0.32 < y < size * 0.88 and abs(x - cx) < size * 0.22:
            return accent  # candle body
        return base

    return png_from_rows(size, size, pixel)


def receipt_png(width: int = 240, height: int = 360) -> bytes:
    """A fake transfer receipt: white card, grey 'text' bars, green check block."""

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        if y < 60:
            return (218, 0, 116)  # header band
        if 80 < y < 150 and 80 < x < 160:
            return (46, 160, 67)  # "approved" block
        if y > 170 and (y // 18) % 2 == 0 and 24 < x < width - 24 - (y % 50):
            return (200, 200, 205)  # text lines
        return (255, 255, 255)

    return png_from_rows(width, height, pixel)
