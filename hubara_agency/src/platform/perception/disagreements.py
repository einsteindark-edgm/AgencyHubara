"""Cola de desacuerdos regla ↔ Jev y sus etiquetas (diseño v2 §07, paso 2).

Mientras una capacidad corre en `sombra` (o en `jev`, para seguir midiendo),
cada vez que la regla de hoy y Jev no coinciden queda un registro. Claude Code
los califica (decisión del operador, 2026-09-28): cuando coinciden no hay nada
que revisar, así que el trabajo es chico. La vara para que una capacidad
actúe incluye que Jev gane en los desacuerdos.

Es UNA cola para todo el sistema: la llenan el motor de decisiones de ventas
y el Order Sentinel (F8), y los plugins la toman de `src.sdk.connectorkit`.

Vive en `<vault>/_decisions/` (directorio `_*`: los recorredores de sesiones
lo ignoran) y nunca sale de la caja de producción:
  disagreements.jsonl  {id, at_ms, capability, session_id, state, rule, jev, answers, model}
  labels.jsonl         {id, label, note, at_ms}
El `state` se guarda anonimizado (teléfonos, correos, direcciones, nombres).
Sin Temporal: lo usan activities, el ingest y las tools.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


class DisagreementLog:
    def __init__(self, vault_dir: Path) -> None:
        self._dir = Path(vault_dir) / "_decisions"

    @property
    def _items_path(self) -> Path:
        return self._dir / "disagreements.jsonl"

    @property
    def _labels_path(self) -> Path:
        return self._dir / "labels.jsonl"

    def record(
        self,
        *,
        capability: str,
        state: str,
        rule: Any,
        jev: Any,
        model: str,
        answers: Sequence[dict[str, Any]],
        session_id: str | None = None,
        redact: Sequence[str] = (),
    ) -> str:
        from src.platform.perception.anonymize import anonymize_text

        at_ms = _now_ms()
        item_id = hashlib.sha256(f"{capability}|{session_id}|{at_ms}|{state}".encode()).hexdigest()[:16]
        _append(
            self._items_path,
            {
                "id": item_id,
                "at_ms": at_ms,
                "capability": capability,
                "session_id": session_id,
                "state": anonymize_text(state, redact=redact),
                "rule": rule,
                "jev": jev,
                "answers": list(answers),
                "model": model,
            },
        )
        return item_id

    def items(self) -> list[dict[str, Any]]:
        return _read_jsonl(self._items_path)

    def _labels(self) -> dict[str, dict[str, Any]]:
        return {str(row.get("id")): row for row in _read_jsonl(self._labels_path) if row.get("id")}

    def pending(self, capability: str | None = None) -> list[dict[str, Any]]:
        labeled = self._labels()
        return [
            item for item in self.items()
            if item.get("id") not in labeled and (capability is None or item.get("capability") == capability)
        ]

    def label(self, item_id: str, value: Any, *, note: str = "") -> None:
        if not any(item.get("id") == item_id for item in self.items()):
            raise KeyError(item_id)
        _append(self._labels_path, {"id": item_id, "label": value, "note": note, "at_ms": _now_ms()})

    def score(self, capability: str) -> dict[str, int]:
        """Quién tenía razón en los desacuerdos calificados de la capacidad."""
        labels = self._labels()
        out = {"labeled": 0, "jev": 0, "rule": 0, "neither": 0}
        for item in self.items():
            if item.get("capability") != capability or item.get("id") not in labels:
                continue
            value = labels[item["id"]].get("label")
            out["labeled"] += 1
            if value == item.get("jev"):
                out["jev"] += 1
            elif value == item.get("rule"):
                out["rule"] += 1
            else:
                out["neither"] += 1
        return out
