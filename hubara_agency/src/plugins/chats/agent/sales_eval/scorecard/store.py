"""Store del scorecard (HU-SC-1 / SC-4) — JSONL bajo el vault.

  * Scorecards: `<vault>/_evals/scorecards/<YYYY-MM-DD>.jsonl`, un registro por
    evaluación (un episodio puede re-evaluarse; la vista usa el último).
  * Etiquetas humanas: `<vault>/_evals/labels/labels.jsonl`, append-only (la
    última etiqueta de un check en un episodio gana).

Worker (escribe) y API (lee) comparten el volumen del vault, igual que el
histórico legado de `evals/history.py`.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERDICT_RANK = {"FALLA": 0, "ALERTA": 1, "PASA": 2, "SIN_DATOS": 3}


def scorecards_dir(vault_dir: Path) -> Path:
    return vault_dir / "_evals" / "scorecards"


def labels_path(vault_dir: Path) -> Path:
    return vault_dir / "_evals" / "labels" / "labels.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def append_scorecard(directory: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Agrega una evaluación. Completa `ts` y `date` si faltan."""
    rec = dict(record)
    rec.setdefault("ts", _now_iso())
    rec.setdefault("date", str(rec["ts"])[:10])
    _append(directory / f"{rec['date']}.jsonl", rec)
    return rec


def read_scorecards(directory: Path, *, dates: Iterable[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for date in sorted(set(dates)):
        records.extend(_read_jsonl(directory / f"{date}.jsonl"))
    return records


def latest_by_unit(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for rec in records:
        key = (str(rec.get("session_id") or ""), str(rec.get("episode_id") or ""))
        current = latest.get(key)
        if current is None or str(rec.get("ts") or "") >= str(current.get("ts") or ""):
            latest[key] = rec
    return list(latest.values())


def to_row(record: dict[str, Any]) -> dict[str, Any]:
    """Fila de lista/matriz: el registro sin `results`, con el mapa check → veredicto."""
    row = {k: v for k, v in record.items() if k != "results"}
    row["checks"] = {
        str(r.get("check_id")): r.get("verdict")
        for r in record.get("results") or []
        if isinstance(r, dict)
    }
    return row


def order_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ts = sorted(rows, key=lambda r: str(r.get("ts") or ""), reverse=True)
    return sorted(by_ts, key=lambda r: VERDICT_RANK.get(str(r.get("verdict")), 9))


def list_scorecards(
    directory: Path, *, dates: Iterable[str], episode_since: str | None = None
) -> list[dict[str, Any]]:
    """Último registro por episodio de los archivos de `dates` (fecha de
    evaluación). `episode_since` (ISO) recorta además por la fecha del episodio:
    un backfill califica HOY episodios cerrados hace meses, y la vista de
    "últimos N días" es por cuándo ocurrió la conversación, no cuándo se
    calificó. Registros sin `episode_date` usan `date`."""
    rows: Iterable[dict[str, Any]] = (to_row(r) for r in latest_by_unit(read_scorecards(directory, dates=dates)))
    if episode_since:
        rows = (r for r in rows if str(r.get("episode_date") or r.get("date") or "") >= episode_since)
    return order_rows(rows)


def find_latest(
    directory: Path, session_id: str, episode_id: str, *, max_files: int = 120
) -> dict[str, Any] | None:
    """Última evaluación completa del episodio (recorre los días más recientes)."""
    try:
        files = sorted(directory.glob("*.jsonl"), reverse=True)[:max_files]
    except OSError:
        return None
    for path in files:
        matches = [
            r for r in _read_jsonl(path)
            if r.get("session_id") == session_id and r.get("episode_id") == episode_id
        ]
        if matches:
            return max(matches, key=lambda r: str(r.get("ts") or ""))
    return None


def append_label(path: Path, label: dict[str, Any]) -> dict[str, Any]:
    rec = dict(label)
    rec.setdefault("labeled_at", _now_iso())
    _append(path, rec)
    return rec


def read_labels(
    path: Path, *, session_id: str | None = None, episode_id: str | None = None
) -> list[dict[str, Any]]:
    labels = _read_jsonl(path)
    if session_id is not None:
        labels = [lab for lab in labels if lab.get("session_id") == session_id]
    if episode_id is not None:
        labels = [lab for lab in labels if lab.get("episode_id") == episode_id]
    return labels


def latest_labels(labels: Iterable[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for lab in labels:
        key = (str(lab.get("session_id")), str(lab.get("episode_id")), str(lab.get("check_id")))
        out[key] = lab
    return out
