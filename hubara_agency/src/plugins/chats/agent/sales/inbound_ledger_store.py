"""Adapter filesystem del ledger de inbound: JSONL append-only, un archivo por
día UTC, bajo el vault (volumen durable — sobrevive a deploys, a diferencia de
los logs del container)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

_DAY_MS = 86_400_000


def _day(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


class FilesystemInboundLedger:
    def __init__(self, root: Path) -> None:
        self._root = root

    def append(self, records: list[dict[str, Any]]) -> None:
        """Best-effort: el ledger es observabilidad — un disco lleno o un path
        roto se loguea y NUNCA llega al webhook (Meta reintentaría el POST y
        duplicaría el inbound)."""
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            by_day: dict[str, list[str]] = {}
            for record in records:
                line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                by_day.setdefault(_day(int(record["at_ms"])), []).append(line)
            for day, lines in by_day.items():
                # Un solo write por archivo: con O_APPEND las líneas de un POST
                # no se intercalan con las de otro request concurrente.
                with (self._root / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
        except Exception as exc:  # noqa: BLE001 — jamás tumbar el webhook
            logger.error("inbound_ledger_append_failed", error=str(exc), records=len(records))

    def read(self, *, since_ms: int, until_ms: int) -> list[dict[str, Any]]:
        """Registros con ``since_ms <= at_ms < until_ms``, en orden de escritura.
        Una línea corrupta se saltea (un crash a mitad de write no invalida el día)."""
        out: list[dict[str, Any]] = []
        try:
            files = sorted(self._root.glob("*.jsonl"))
        except OSError:
            return out
        lo, hi = _day(max(since_ms, 0)), _day(max(until_ms - 1, 0))
        for path in files:
            if not (lo <= path.stem <= hi):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                at_ms = record.get("at_ms") if isinstance(record, dict) else None
                if isinstance(at_ms, int) and since_ms <= at_ms < until_ms:
                    out.append(record)
        return out
