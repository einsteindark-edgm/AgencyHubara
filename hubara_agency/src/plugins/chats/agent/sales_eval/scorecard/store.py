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

from src.plugins.chats.shared import jsonl_index

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
    """Fila de lista/matriz: el registro sin `results` (ni `by_turn`), con el
    mapa check → veredicto. En modo turno un check tiene un resultado por
    turno: queda el más fuerte (una falla en un turno no se esconde detrás de
    un pasa en otro). El índice de cada día la guarda: si cambia lo que sale
    de acá, subí `_INDEX_VERSION`."""
    row = {k: v for k, v in record.items() if k not in ("results", "by_turn")}
    results = [r for r in record.get("results") or [] if isinstance(r, dict)]
    if record.get("mode") == "turn" or "by_turn" in record:
        from src.plugins.chats.agent.sales_eval.scorecard.service import aggregate_checks

        row["checks"] = aggregate_checks(results)
    else:
        row["checks"] = {str(r.get("check_id")): r.get("verdict") for r in results}
    return row


def order_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ts = sorted(rows, key=lambda r: str(r.get("ts") or ""), reverse=True)
    return sorted(by_ts, key=lambda r: VERDICT_RANK.get(str(r.get("verdict")), 9))


# ── El índice de cada día (Calidad LLM sin 504, 2026-11-03) ────────────────
# En modo turno un registro trae todos los checks de cada turno: un día puede
# pesar decenas de MB. La lista, las gráficas y la búsqueda de un episodio
# leen el índice del día (la fila de `to_row` y dónde está la línea), que se
# pone al día leyendo solo lo nuevo (`chats/shared/jsonl_index`).

#: Subirla si cambia lo que `to_row` saca de un registro: los índices se rearman.
_INDEX_VERSION = 1


def _index_path(directory: Path, date: str) -> Path:
    return directory / "index" / f"{date}.json"


def _entry(record: dict[str, Any], at: int, length: int) -> dict[str, Any]:
    judge = [r for r in record.get("results") or [] if isinstance(r, dict) and r.get("source") == "judge"]
    return {"row": to_row(record), "judge": judge, "at": at, "len": length}


def _day_entries(directory: Path, date: str) -> list[dict[str, Any]]:
    return jsonl_index.indexed_lines(
        directory / f"{date}.jsonl", _index_path(directory, date), _entry, version=_INDEX_VERSION
    )


def day_index(
    directory: Path, date: str, name: str, project: jsonl_index.Projection, *, version: int
) -> list[dict[str, Any]]:
    """La proyección `project` de cada registro del día `date`, guardada en
    `index/<name>/<date>.json` y puesta al día como el índice de la lista: un
    lector que necesita otra vista de los registros (las bandas) arma la suya
    sin parsear los registros completos de un día que no cambió."""
    return jsonl_index.indexed_lines(
        directory / f"{date}.jsonl", directory / "index" / name / f"{date}.json", project, version=version
    )


def read_rows(directory: Path, *, dates: Iterable[str]) -> list[dict[str, Any]]:
    """Las filas (`to_row`) de cada registro de los días `dates`, sin parsear
    los registros completos de un día ya indexado."""
    return [e["row"] for date in sorted(set(dates)) for e in _day_entries(directory, date)]


def read_judged(directory: Path, *, dates: Iterable[str]) -> list[dict[str, Any]]:
    """Cada registro de los días `dates` con solo lo que dijo el juez en
    `results` (la cola de Calibración), sin parsear los checks de código."""
    return [
        {**e["row"], "results": e.get("judge") or []}
        for date in sorted(set(dates))
        for e in _day_entries(directory, date)
    ]


def _day_files(directory: Path, max_files: int) -> list[Path]:
    """Los días más recientes primero."""
    try:
        return sorted(directory.glob("*.jsonl"), reverse=True)[:max_files]
    except OSError:
        return []


def _full_record(path: Path, entry: dict[str, Any]) -> dict[str, Any] | None:
    """El registro completo de una fila del índice: solo su línea."""
    row = entry["row"]
    record = jsonl_index.read_line(path, int(entry.get("at") or 0), int(entry.get("len") or 0))
    if record is not None and (record.get("session_id"), record.get("episode_id"), record.get("ts")) == (
        row.get("session_id"), row.get("episode_id"), row.get("ts"),
    ):
        return record
    # El día cambió entre el índice y la lectura: se busca en el día entero.
    return next(
        (r for r in reversed(_read_jsonl(path))
         if (r.get("session_id"), r.get("episode_id"), r.get("ts")) == (row.get("session_id"), row.get("episode_id"), row.get("ts"))),
        None,
    )


def list_scorecards(
    directory: Path, *, dates: Iterable[str], episode_since: str | None = None
) -> list[dict[str, Any]]:
    """Último registro por episodio de los archivos de `dates` (fecha de
    evaluación). `episode_since` (ISO) recorta además por la fecha del episodio:
    un backfill califica HOY episodios cerrados hace meses, y la vista de
    "últimos N días" es por cuándo ocurrió la conversación, no cuándo se
    calificó. Registros sin `episode_date` usan `date`."""
    rows: Iterable[dict[str, Any]] = latest_by_unit(read_rows(directory, dates=dates))
    if episode_since:
        rows = (r for r in rows if str(r.get("episode_date") or r.get("date") or "") >= episode_since)
    return order_rows(rows)


def find_latest(
    directory: Path, session_id: str, episode_id: str, *, max_files: int = 120
) -> dict[str, Any] | None:
    """Última evaluación completa del episodio (recorre los días más recientes)."""
    for path in _day_files(directory, max_files):
        matches = [
            e for e in _day_entries(directory, path.stem)
            if e["row"].get("session_id") == session_id and e["row"].get("episode_id") == episode_id
        ]
        if matches:
            return _full_record(path, max(matches, key=lambda e: str(e["row"].get("ts") or "")))
    return None


def latest_for_session(
    directory: Path, session_id: str, *, max_files: int = 120
) -> dict[str, dict[str, Any]]:
    """La última evaluación completa de cada episodio de la conversación
    (recorre los días más recientes; de cada día lee solo sus registros)."""
    picked: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in reversed(_day_files(directory, max_files)):
        for entry in _day_entries(directory, path.stem):
            row = entry["row"]
            if row.get("session_id") != session_id:
                continue
            episode_id = str(row.get("episode_id"))
            current = picked.get(episode_id)
            if current is None or str(row.get("ts") or "") >= str(current[1]["row"].get("ts") or ""):
                picked[episode_id] = (path, entry)
    out: dict[str, dict[str, Any]] = {}
    for episode_id, (path, entry) in picked.items():
        record = _full_record(path, entry)
        if record is not None:
            out[episode_id] = record
    return out


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
