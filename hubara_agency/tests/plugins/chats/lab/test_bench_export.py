"""Exportador del banco del laboratorio (plan del laboratorio, PR 7, §3.4).

Al pulsar "Nueva corrida" el worker `sales_eval` de producción arma el banco:
las conversaciones desde el 2026-09-10 con su historial, trazas, metadata,
historial del LLM, scorecards y el catálogo del día. Solo LEE el vault. Las
exclusiones van con motivo: sesiones golden (#338), sembradas de prueba,
números internos. Las fotos y los archivos auxiliares no viajan.

El plan es puro (qué archivo va a qué clave); la subida es otra función que
va archivo por archivo.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.chats.agent.sales_lab.launch.bench_export import plan_bench_export
from src.sdk.labkit import FilesystemLabStore
from src.plugins.chats.agent.sales_lab.launch.bench_export import upload_bench

SINCE_MS = 1_789_000_000_000  # ~2026-09-10
NOW_MS = SINCE_MS + 10 * 86_400_000
SALES = "app-hubara-agency-src-plugins-chats-agent-sales-workspace"


def _session(
    vault: Path, sid: str, *, started_ms: int, metadata_extra: dict | None = None,
    msg_ts: str = "2026-09-15T10:00:00+00:00",
) -> None:
    d = vault / sid
    (d / "sessions").mkdir(parents=True)
    (d / "evals").mkdir()
    (d / "media").mkdir()
    ep = {"episode_id": "ep_001", "started_at_ms": started_ms, "closed_at_ms": None}
    (d / "metadata.json").write_text(json.dumps({"episodes": [ep], **(metadata_extra or {})}), encoding="utf-8")
    events = [
        {"role": "user", "content": "hola", "timestamp": msg_ts},
        {"role": "assistant", "content": "¡Buenas!", "timestamp": msg_ts},
    ]
    (d / "sessions" / f"{sid}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    (d / "evals" / "turn_traces.jsonl").write_text('{"turn": 1, "trigger": "customer"}\n', encoding="utf-8")
    (d / "media" / "abc123.jpg").write_bytes(b"\xff\xd8")
    (d / "metadata.json.lock").write_text("", encoding="utf-8")
    (d / "metadata.json.bak-rescue-deadbeef").write_text("{}", encoding="utf-8")


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    _session(vault, "wa_573001234567", started_ms=SINCE_MS + 86_400_000)
    _session(vault, "wa_573007654321", started_ms=SINCE_MS - 30 * 86_400_000, msg_ts="2026-08-10T10:00:00+00:00")  # antes del corte
    _session(vault, "wa_golden_happy_path", started_ms=SINCE_MS + 86_400_000)
    _session(vault, "wa_573001112233", started_ms=SINCE_MS + 86_400_000, metadata_extra={"seeded_test": True})
    _session(vault, "wa_573000000099", started_ms=SINCE_MS + 86_400_000)  # número interno
    state = vault / "agent_state" / SALES / "sessions"
    state.mkdir(parents=True)
    (state / "wa_573001234567.jsonl").write_text('{"role": "user"}\n', encoding="utf-8")
    (state / "wa_573007654321.jsonl").write_text('{"role": "user"}\n', encoding="utf-8")
    cards = vault / "_evals" / "scorecards"
    cards.mkdir(parents=True)
    (cards / "2026-09-01.jsonl").write_text("{}\n", encoding="utf-8")
    (cards / "2026-09-15.jsonl").write_text("{}\n", encoding="utf-8")
    catalog = vault / "catalog"
    (catalog / "by_handle").mkdir(parents=True)
    (catalog / "snapshot.json").write_text("{}", encoding="utf-8")
    (catalog / "manifest.json").write_text("{}", encoding="utf-8")
    (catalog / "by_handle" / "cubo-love.json").write_text("{}", encoding="utf-8")
    (catalog / ".meta_state.json").write_text("{}", encoding="utf-8")
    return vault


def _plan(vault: Path):
    return plan_bench_export(
        vault,
        state_dir=vault / "agent_state",
        catalog_dir=vault / "catalog",
        bench_id="bench-run-20260923-a1b2",
        since_ms=SINCE_MS,
        now_ms=NOW_MS,
        internal_numbers={"573000000099"},
    )


def test_bench_takes_sessions_active_since_the_cut_with_their_history_traces_and_llm_state(tmp_path: Path) -> None:
    plan = _plan(_vault(tmp_path))

    keys = {f.key for f in plan.files}
    base = "bench/bench-run-20260923-a1b2"
    assert {
        f"{base}/vault/wa_573001234567/metadata.json",
        f"{base}/vault/wa_573001234567/sessions/wa_573001234567.jsonl",
        f"{base}/vault/wa_573001234567/evals/turn_traces.jsonl",
        f"{base}/agent_state/{SALES}/sessions/wa_573001234567.jsonl",
        f"{base}/scorecards/2026-09-15.jsonl",
        f"{base}/catalog/snapshot.json",
        f"{base}/catalog/manifest.json",
        f"{base}/catalog/by_handle/cubo-love.json",
    } <= keys
    assert plan.sessions == ("wa_573001234567",)


def test_bench_leaves_out_photos_locks_backups_and_meta_state(tmp_path: Path) -> None:
    keys = {f.key for f in _plan(_vault(tmp_path)).files}

    assert not any(k.endswith((".jpg", ".lock")) or ".bak-rescue-" in k or ".meta_state" in k for k in keys)


def test_exclusions_carry_their_reason(tmp_path: Path) -> None:
    plan = _plan(_vault(tmp_path))

    assert dict(plan.exclusions) == {
        "wa_golden_happy_path": "golden",
        "wa_573001112233": "sesion_de_prueba",
        "wa_573000000099": "numero_interno",
    }
    keys = {f.key for f in plan.files}
    assert not any("wa_573007654321" in k for k in keys)  # antes del corte: ni la sesión ni su estado
    assert not any("2026-09-01" in k for k in keys)


def test_manifest_describes_the_bench(tmp_path: Path) -> None:
    manifest = _plan(_vault(tmp_path)).manifest()

    assert manifest["bench_id"] == "bench-run-20260923-a1b2"
    assert manifest["since_ms"] == SINCE_MS
    assert manifest["counts"]["sessions"] == 1
    assert manifest["counts"]["customer_turns"] == 1
    assert manifest["exclusions"] == [
        {"session_id": "wa_573000000099", "reason": "numero_interno"},
        {"session_id": "wa_573001112233", "reason": "sesion_de_prueba"},
        {"session_id": "wa_golden_happy_path", "reason": "golden"},
    ]


def test_upload_goes_file_by_file_and_writes_the_manifest_last(tmp_path: Path) -> None:
    plan = _plan(_vault(tmp_path))
    store = FilesystemLabStore(tmp_path / "bucket")
    uploaded: list[str] = []

    result = upload_bench(plan, store, on_file=uploaded.append, extra={"promotions.json": b"[]"})

    assert uploaded[-1].endswith("/manifest.json")
    assert len(uploaded) == len(plan.files) + 2
    assert store.get_bytes("bench/bench-run-20260923-a1b2/promotions.json") == b"[]"
    manifest = json.loads(store.get_bytes("bench/bench-run-20260923-a1b2/manifest.json"))
    assert manifest["counts"]["files"] == len(plan.files) + 1
    assert result.bytes_uploaded > 0
