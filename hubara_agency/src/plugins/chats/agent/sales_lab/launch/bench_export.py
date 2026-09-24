"""Exportador del banco del laboratorio (plan §3.4 y §3.7).

`plan_bench_export` decide QUÉ archivo del vault va a QUÉ clave del bucket;
`upload_bench` los sube de a uno (streaming; la caja de producción tiene poca
RAM libre) y escribe el manifiesto al FINAL: un banco sin manifiesto está
incompleto y la caja no lo usa.

Solo lee el vault. Lo que viaja, por conversación con mensajes del cliente
desde el corte: `metadata.json`, el historial del dashboard
(`sessions/<sid>.jsonl`), las trazas (`evals/turn_traces.jsonl`) y el
historial del LLM de cada agente (`agent_state/<workspace>/sessions/<sid>.jsonl`).
Además los scorecards desde el corte y el snapshot del catálogo. NO viajan las
fotos (`media/`), los locks, los respaldos de rescate ni el estado de Meta del
catálogo.

Exclusiones con motivo (las de nivel turno, como los mensajes de una persona
del equipo, las aplica el armado de casos en la caja):
  golden            sesiones `wa_golden_*` que la suite escribió en el vault (#338)
  sesion_de_prueba  `seeded_test: true` (datos sembrados para probar Ads)
  numero_interno    números del equipo (`LAB_INTERNAL_NUMBERS`)
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.sdk.labkit import LabStorePort

_SESSION_FILES = ("metadata.json", "evals/turn_traces.jsonl")
_CATALOG_SKIP_PREFIX = ".meta_state"


@dataclass(frozen=True)
class BenchFile:
    key: str
    path: Path


@dataclass(frozen=True)
class BenchPlan:
    bench_id: str
    since_ms: int
    now_ms: int
    files: tuple[BenchFile, ...]
    sessions: tuple[str, ...]
    exclusions: tuple[tuple[str, str], ...]
    customer_turns: int

    @property
    def prefix(self) -> str:
        return f"bench/{self.bench_id}"

    def manifest(self, *, extra_files: int = 0) -> dict[str, Any]:
        return {
            "bench_id": self.bench_id,
            "since_ms": self.since_ms,
            "exported_at_ms": self.now_ms,
            "counts": {
                "sessions": len(self.sessions),
                "customer_turns": self.customer_turns,
                "files": len(self.files) + extra_files,
            },
            "sessions": list(self.sessions),
            "exclusions": [{"session_id": s, "reason": r} for s, r in sorted(self.exclusions)],
        }


@dataclass(frozen=True)
class UploadResult:
    files: int
    bytes_uploaded: int
    manifest: dict[str, Any] = field(default_factory=dict)


def _iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _has_customer_message_since(history: Path, since_ms: int) -> bool:
    try:
        with history.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("role") == "user":
                    ts = _iso_ms(ev.get("timestamp"))
                    if ts is not None and ts >= since_ms:
                        return True
    except OSError:
        return False
    return False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _exclusion(session_id: str, metadata: Mapping[str, Any], internal_numbers: Iterable[str]) -> str | None:
    if session_id.startswith("wa_golden"):
        return "golden"
    if metadata.get("seeded_test") is True:
        return "sesion_de_prueba"
    digits = "".join(ch for ch in session_id if ch.isdigit())
    if digits and digits in {"".join(ch for ch in n if ch.isdigit()) for n in internal_numbers}:
        return "numero_interno"
    return None


def _customer_turns(traces: Path, since_ms: int) -> int:
    count = 0
    try:
        with traces.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    t = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(t, dict) or (t.get("trigger") or "customer") != "customer":
                    continue
                started = t.get("turn_started_ms") or t.get("recorded_at_ms")
                if not isinstance(started, (int, float)) or started >= since_ms:
                    count += 1
    except OSError:
        return 0
    return count


def _scorecard_date(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None


def plan_bench_export(
    vault_dir: Path,
    *,
    state_dir: Path | None,
    catalog_dir: Path | None,
    bench_id: str,
    since_ms: int,
    now_ms: int,
    internal_numbers: Iterable[str] = (),
) -> BenchPlan:
    prefix = f"bench/{bench_id}"
    internal = tuple(internal_numbers)
    files: list[BenchFile] = []
    sessions: list[str] = []
    exclusions: list[tuple[str, str]] = []
    turns = 0
    session_dirs = sorted(p for p in vault_dir.iterdir() if p.is_dir() and p.name.startswith("wa_")) if vault_dir.is_dir() else []
    for sdir in session_dirs:
        sid = sdir.name
        history = sdir / "sessions" / f"{sid}.jsonl"
        if not _has_customer_message_since(history, since_ms):
            continue
        reason = _exclusion(sid, _read_json(sdir / "metadata.json"), internal)
        if reason:
            exclusions.append((sid, reason))
            continue
        sessions.append(sid)
        files.append(BenchFile(f"{prefix}/vault/{sid}/sessions/{sid}.jsonl", history))
        for rel in _SESSION_FILES:
            if (sdir / rel).is_file():
                files.append(BenchFile(f"{prefix}/vault/{sid}/{rel}", sdir / rel))
        turns += _customer_turns(sdir / "evals" / "turn_traces.jsonl", since_ms)
        if state_dir is not None and state_dir.is_dir():
            for workspace in sorted(p for p in state_dir.iterdir() if p.is_dir()):
                llm = workspace / "sessions" / f"{sid}.jsonl"
                if llm.is_file():
                    files.append(BenchFile(f"{prefix}/agent_state/{workspace.name}/sessions/{sid}.jsonl", llm))
    cards = vault_dir / "_evals" / "scorecards"
    since_day = datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).date()
    if cards.is_dir():
        for card in sorted(cards.glob("*.jsonl")):
            day = _scorecard_date(card)
            if day is not None and day >= since_day:
                files.append(BenchFile(f"{prefix}/scorecards/{card.name}", card))
    if catalog_dir is not None and catalog_dir.is_dir():
        for item in sorted(p for p in catalog_dir.rglob("*") if p.is_file()):
            rel = item.relative_to(catalog_dir).as_posix()
            if rel.startswith(_CATALOG_SKIP_PREFIX):
                continue
            files.append(BenchFile(f"{prefix}/catalog/{rel}", item))
    return BenchPlan(
        bench_id=bench_id,
        since_ms=since_ms,
        now_ms=now_ms,
        files=tuple(files),
        sessions=tuple(sessions),
        exclusions=tuple(exclusions),
        customer_turns=turns,
    )


def upload_bench(
    plan: BenchPlan,
    store: LabStorePort,
    *,
    extra: Mapping[str, bytes] | None = None,
    on_file: Callable[[str], None] | None = None,
) -> UploadResult:
    """Sube el banco archivo por archivo; `extra` son archivos generados
    (promociones, order facts). El manifiesto va último."""
    total = 0
    for f in plan.files:
        store.put_file(f.key, f.path)
        total += f.path.stat().st_size
        if on_file:
            on_file(f.key)
    for name, data in (extra or {}).items():
        key = f"{plan.prefix}/{name}"
        store.put_bytes(key, data)
        total += len(data)
        if on_file:
            on_file(key)
    manifest = plan.manifest(extra_files=len(extra or {}))
    key = f"{plan.prefix}/manifest.json"
    store.put_bytes(key, json.dumps(manifest, ensure_ascii=False, indent=1).encode())
    if on_file:
        on_file(key)
    return UploadResult(files=len(plan.files) + len(extra or {}), bytes_uploaded=total, manifest=manifest)
