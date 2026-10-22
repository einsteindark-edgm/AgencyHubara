"""La pasada que marca el perfil «interesado sin compra» en las conversaciones
de antes (operador, 2026-10-20: «luego hacemos una pasada y etiquetamos todos
los que no alcanzaron a estar etiquetados antes del desarrollo»).

Sin `--apply` solo cuenta (lo corre el operador en la caja: devuelve números,
nunca teléfonos). Con `--apply` escribe con el store (candado y atómico): lo
que otro escritor puso mientras tanto no se pisa.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.shared import customer_profiles as cp


def _session(vault: Path, sid: str, metadata: dict, *, decisions: list[dict] = (), traces: list[dict] = ()) -> None:
    FilesystemMetadataStore(vault).write(sid, metadata)
    evals = vault / sid / "evals"
    evals.mkdir(parents=True, exist_ok=True)
    if decisions:
        (evals / "decisions.jsonl").write_text("\n".join(json.dumps(d) for d in decisions), encoding="utf-8")
    if traces:
        (evals / "turn_traces.jsonl").write_text("\n".join(json.dumps(t) for t in traces), encoding="utf-8")


def _row(choice: str, at_ms: int = 2_000) -> dict:
    return {"at_ms": at_ms, "stage": "ingest", "capability": "compra", "by": "jev", "provider": "jev",
            "value": [None, "text"], "answers": [{"q": "compra.que_hace", "choice": choice, "confidence": 0.9}]}


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    ep = {"episode_id": "ep_001", "started_at_ms": 1_000, "sales_campaign": "velitas-7-dic"}
    _session(vault, "wa_10000000001", {"episodes": [dict(ep)]}, decisions=[_row("elige")])
    _session(vault, "wa_10000000002", {"episodes": [dict(ep)]}, traces=[{"episode_id": "ep_001", "stage_out": "datos_envio"}])
    _session(vault, "wa_10000000003", {"episodes": [{**ep, "order_id": "order_1"}]}, decisions=[_row("elige")])
    _session(vault, "wa_10000000004", {"episodes": [dict(ep)]}, decisions=[_row("pregunta")])
    already = {"episodes": [dict(ep)], cp.PROFILE_KEY: {cp.INTERESADO_SIN_COMPRA: {"at_ms": 1, "episode_id": "ep_001"}}}
    _session(vault, "wa_10000000005", already, decisions=[_row("aplaza")])
    return vault


def test_the_dry_run_only_counts(tmp_path: Path) -> None:
    from scripts.backfill_interest_profile import run

    vault = _vault(tmp_path)
    before = {p: p.read_bytes() for p in vault.glob("wa_*/metadata.json")}

    summary = run(vault, apply=False, now_ms=10_000)

    assert summary["sessions"] == 5
    assert summary["to_tag"] == {"jev": 1, "etapa": 1}
    assert summary["already_tagged"] == 1
    assert summary["by_campaign"] == {"velitas-7-dic": 2}
    assert {p: p.read_bytes() for p in vault.glob("wa_*/metadata.json")} == before


def test_apply_tags_who_wanted_to_buy_and_did_not(tmp_path: Path) -> None:
    from scripts.backfill_interest_profile import run

    vault = _vault(tmp_path)
    store = FilesystemMetadataStore(vault)

    run(vault, apply=True, now_ms=10_000)

    mark = store.read("wa_10000000001")[cp.PROFILE_KEY][cp.INTERESADO_SIN_COMPRA]
    assert mark["source"] == "pasada:jev" and mark["episode_id"] == "ep_001" and mark["campaign"] == "velitas-7-dic"
    assert store.read("wa_10000000002")[cp.PROFILE_KEY][cp.INTERESADO_SIN_COMPRA]["source"] == "pasada:etapa"
    assert cp.PROFILE_KEY not in store.read("wa_10000000003")  # compró
    assert cp.PROFILE_KEY not in store.read("wa_10000000004")  # solo preguntó
    # El que ya tenía el perfil (lo marcó el ingest en vivo) no se toca.
    assert store.read("wa_10000000005")[cp.PROFILE_KEY][cp.INTERESADO_SIN_COMPRA] == {"at_ms": 1, "episode_id": "ep_001"}


def _meanwhile(monkeypatch, write) -> None:
    """Otro escritor escribe entre la lectura de la pasada y su escritura: la
    primera decisión (`history_interest`) corre después de leer y antes del
    candado de `update`."""
    real = cp.history_interest
    calls: list[int] = []

    def deciding(metadata, **kw):
        if not calls:
            write()
        calls.append(1)
        return real(metadata, **kw)

    monkeypatch.setattr(cp, "history_interest", deciding)


def test_apply_keeps_what_another_writer_wrote_meanwhile(tmp_path: Path, monkeypatch) -> None:
    from scripts.backfill_interest_profile import run

    vault = _vault(tmp_path)
    store = FilesystemMetadataStore(vault)
    _meanwhile(monkeypatch, lambda: store.update("wa_10000000001", lambda d: {**d, "last_inbound_message_id": "wamid.nuevo"}))

    run(vault, apply=True, now_ms=10_000)

    doc = store.read("wa_10000000001")
    assert doc["last_inbound_message_id"] == "wamid.nuevo" and cp.PROFILE_KEY in doc


def test_a_live_mark_written_meanwhile_is_kept_and_not_counted(tmp_path: Path, monkeypatch) -> None:
    from scripts.backfill_interest_profile import run

    vault = _vault(tmp_path)
    store = FilesystemMetadataStore(vault)
    live = {"first_at_ms": 9_000, "at_ms": 9_000, "episode_id": "ep_001", "source": "jev", "campaign": None}
    _meanwhile(monkeypatch, lambda: store.update(
        "wa_10000000001", lambda d: {**d, cp.PROFILE_KEY: {cp.INTERESADO_SIN_COMPRA: dict(live)}}))

    summary = run(vault, apply=True, now_ms=10_000)

    assert store.read("wa_10000000001")[cp.PROFILE_KEY][cp.INTERESADO_SIN_COMPRA] == live
    assert summary["written"] == 1  # solo wa_10000000002


def test_the_mark_says_when_the_customer_showed_the_intent(tmp_path: Path) -> None:
    from scripts.backfill_interest_profile import run

    vault = _vault(tmp_path)

    run(vault, apply=True, now_ms=10_000)

    mark = FilesystemMetadataStore(vault).read("wa_10000000001")[cp.PROFILE_KEY][cp.INTERESADO_SIN_COMPRA]
    assert (mark["first_at_ms"], mark["at_ms"], mark["stamped_at_ms"]) == (2_000, 2_000, 10_000)


def test_a_broken_session_does_not_stop_the_pass(tmp_path: Path, monkeypatch) -> None:
    from scripts.backfill_interest_profile import run

    vault = _vault(tmp_path)
    _session(vault, "wa_10000000000", {"roto": True, "episodes": [{"episode_id": "ep_001"}]})
    real = cp.history_interest

    def breaks(metadata, **kw):
        if metadata.get("roto"):
            raise ValueError("sesión rota")
        return real(metadata, **kw)

    monkeypatch.setattr(cp, "history_interest", breaks)

    summary = run(vault, apply=True, now_ms=10_000)

    assert summary["errors"] == {"ValueError": 1}
    assert cp.PROFILE_KEY in FilesystemMetadataStore(vault).read("wa_10000000001")


def test_the_pass_reads_only_what_it_needs_from_long_traces(tmp_path: Path) -> None:
    """Las trazas de un cliente con meses de turnos pesan megas (y la caja tiene
    4 GB): una línea rota o sin etapa no tumba la lectura."""
    from scripts.backfill_interest_profile import run

    vault = tmp_path / "vault"
    _session(vault, "wa_10000000001", {"episodes": [{"episode_id": "ep_001", "started_at_ms": 1_000}]})
    (vault / "wa_10000000001" / "evals" / "turn_traces.jsonl").write_bytes(
        b'{"episode_id": "ep_001", "steps": "' + b"x" * 200_000 + b'"}\n'
        b"{roto\n"
        b'{"episode_id": "ep_001", "stage_out": "variantes", "recorded_at_ms": 4000, "steps": []}\n'
    )

    summary = run(vault, apply=False, now_ms=10_000)

    assert summary["to_tag"] == {"etapa": 1}
