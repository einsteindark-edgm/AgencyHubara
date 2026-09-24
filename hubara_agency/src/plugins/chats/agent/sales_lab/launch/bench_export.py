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
  numero_interno    números del equipo (`LAB_INTERNAL_NUMBERS`, Terraform:
                    `tenants.<t>.lab.internal_numbers`)
  pedido_de_prueba  un pedido de la conversación está marcado "prueba" en
                    Órdenes (`exclude_test_orders`, con OrderFacts; si no
                    responde, no se excluye y el manifiesto lleva la nota)
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.sdk.connectorkit import OrderFactsSnapshot
from src.sdk.labkit import LabStorePort

_SESSION_FILES = ("metadata.json", "evals/turn_traces.jsonl")
_CATALOG_SKIP_PREFIX = ".meta_state"


@dataclass(frozen=True)
class BenchFile:
    key: str
    path: Path


@dataclass(frozen=True)
class BenchConversation:
    """Una conversación del banco: sus archivos, sus turnos del cliente y los
    pedidos que el vault le vincula (`episodes[].order_id`, solo el vínculo)."""

    session_id: str
    files: tuple[BenchFile, ...]
    customer_turns: int
    order_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchPlan:
    bench_id: str
    since_ms: int
    now_ms: int
    conversations: tuple[BenchConversation, ...]
    shared_files: tuple[BenchFile, ...]  # scorecards y catálogo
    exclusions: tuple[tuple[str, str], ...]
    notes: tuple[str, ...] = ()

    @property
    def files(self) -> tuple[BenchFile, ...]:
        return tuple(f for c in self.conversations for f in c.files) + self.shared_files

    @property
    def sessions(self) -> tuple[str, ...]:
        return tuple(c.session_id for c in self.conversations)

    @property
    def customer_turns(self) -> int:
        return sum(c.customer_turns for c in self.conversations)

    @property
    def order_ids(self) -> frozenset[str]:
        return frozenset(o for c in self.conversations for o in c.order_ids)

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
            "notes": list(self.notes),
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


def _order_ids(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    ids = [
        ep["order_id"]
        for ep in metadata.get("episodes") or []
        if isinstance(ep, dict) and isinstance(ep.get("order_id"), str) and ep["order_id"]
    ]
    return tuple(dict.fromkeys(ids))


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
    conversations: list[BenchConversation] = []
    shared: list[BenchFile] = []
    exclusions: list[tuple[str, str]] = []
    session_dirs = sorted(p for p in vault_dir.iterdir() if p.is_dir() and p.name.startswith("wa_")) if vault_dir.is_dir() else []
    for sdir in session_dirs:
        sid = sdir.name
        history = sdir / "sessions" / f"{sid}.jsonl"
        if not _has_customer_message_since(history, since_ms):
            continue
        metadata = _read_json(sdir / "metadata.json")
        reason = _exclusion(sid, metadata, internal)
        if reason:
            exclusions.append((sid, reason))
            continue
        files = [BenchFile(f"{prefix}/vault/{sid}/sessions/{sid}.jsonl", history)]
        for rel in _SESSION_FILES:
            if (sdir / rel).is_file():
                files.append(BenchFile(f"{prefix}/vault/{sid}/{rel}", sdir / rel))
        if state_dir is not None and state_dir.is_dir():
            for workspace in sorted(p for p in state_dir.iterdir() if p.is_dir()):
                llm = workspace / "sessions" / f"{sid}.jsonl"
                if llm.is_file():
                    files.append(BenchFile(f"{prefix}/agent_state/{workspace.name}/sessions/{sid}.jsonl", llm))
        conversations.append(
            BenchConversation(
                session_id=sid,
                files=tuple(files),
                customer_turns=_customer_turns(sdir / "evals" / "turn_traces.jsonl", since_ms),
                order_ids=_order_ids(metadata),
            )
        )
    cards = vault_dir / "_evals" / "scorecards"
    since_day = datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).date()
    if cards.is_dir():
        for card in sorted(cards.glob("*.jsonl")):
            day = _scorecard_date(card)
            if day is not None and day >= since_day:
                shared.append(BenchFile(f"{prefix}/scorecards/{card.name}", card))
    if catalog_dir is not None and catalog_dir.is_dir():
        for item in sorted(p for p in catalog_dir.rglob("*") if p.is_file()):
            rel = item.relative_to(catalog_dir).as_posix()
            if rel.startswith(_CATALOG_SKIP_PREFIX):
                continue
            shared.append(BenchFile(f"{prefix}/catalog/{rel}", item))
    return BenchPlan(
        bench_id=bench_id,
        since_ms=since_ms,
        now_ms=now_ms,
        conversations=tuple(conversations),
        shared_files=tuple(shared),
        exclusions=tuple(exclusions),
    )


def _unverified_note(count: int) -> str:
    if count == 1:
        return "1 conversación con pedido quedó en el banco sin verificar si el pedido es de prueba (OrderFacts no respondió)"
    return f"{count} conversaciones con pedido quedaron en el banco sin verificar si el pedido es de prueba (OrderFacts no respondió)"


def exclude_test_orders(plan: BenchPlan, facts: OrderFactsSnapshot) -> BenchPlan:
    """Saca del banco las conversaciones con un pedido marcado "prueba" en
    Órdenes. La marca vive en Medusa (`hubara_test_order`) y la lee OrderFacts
    (`is_test`), nunca una copia del vault.

    Si OrderFacts no pudo leer Medusa (`unresolved` o `stale`), la conversación
    se queda y el manifiesto lo anota: sin la marca no se adivina."""

    def is_test(order_id: str) -> bool:
        fact = facts.facts.get(order_id)
        return fact is not None and fact.is_test

    kept: list[BenchConversation] = []
    tested: list[str] = []
    unverified = 0
    for conv in plan.conversations:
        if any(is_test(o) for o in conv.order_ids):
            tested.append(conv.session_id)
            continue
        kept.append(conv)
        if conv.order_ids and (facts.stale or not facts.unresolved.isdisjoint(conv.order_ids)):
            unverified += 1
    return replace(
        plan,
        conversations=tuple(kept),
        exclusions=plan.exclusions + tuple((sid, "pedido_de_prueba") for sid in tested),
        notes=plan.notes + ((_unverified_note(unverified),) if unverified else ()),
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
