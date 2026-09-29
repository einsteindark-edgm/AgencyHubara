"""Métricas de cada decisión de una capacidad del motor (diseño v2 §08).

Una línea por decisión de una capacidad en sombra o en jev: si Jev contestó a
tiempo (`ok`), cuánto tardó y si coincidió con la regla de hoy. De acá sale la
vara de producción para encender una capacidad (días en sombra, decisiones
medidas, caídas < 1 %, p95). Es UNA medición para todo el sistema: la llenan
el motor de decisiones de ventas y el Order Sentinel (F8), y los plugins la
toman de `src.sdk.connectorkit`.

Vive en `<vault>/_decisions/metrics/<AAAA-MM-DD>.jsonl` (directorio `_*`: los
recorredores de sesiones lo ignoran). Sin Temporal.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DecisionMetrics:
    def __init__(self, vault_dir: Path) -> None:
        self._dir = Path(vault_dir) / "_decisions" / "metrics"

    def record(
        self,
        *,
        capability: str,
        provider: str,
        ok: bool,
        latency_ms: int,
        agree: bool | None,
        at_ms: int | None = None,
    ) -> None:
        at = int(at_ms if at_ms is not None else time.time() * 1000)
        day = datetime.fromtimestamp(at / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        path = self._dir / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"at_ms": at, "capability": capability, "provider": provider, "ok": ok, "latency_ms": latency_ms, "agree": agree}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def rows(self, capability: str, *, since_ms: int) -> list[dict[str, Any]]:
        # Solo los días de la ventana (el nombre del archivo es el día UTC).
        first_day = datetime.fromtimestamp(max(0, since_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        out: list[dict[str, Any]] = []
        for path in sorted(self._dir.glob("*.jsonl")):
            if path.stem < first_day:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                at = row.get("at_ms") if isinstance(row, dict) else None
                if row.get("capability") == capability and isinstance(at, int) and at >= since_ms:
                    out.append(row)
        return out
