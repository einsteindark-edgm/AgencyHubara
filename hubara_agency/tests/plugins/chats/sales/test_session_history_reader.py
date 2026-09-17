"""`composition.build_session_history_reader` — el lector que
`verify_order_for_checkout` usa para auditar lo que el bot escribió (run
ebbc203d). Lee el JSONL del vault; sin log → lista vacía; línea corrupta se
saltea."""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.chats.agent.sales.composition import (
    build_session_history_reader,
    build_session_metadata_store,
)


def test_reader_returns_session_events_in_order(_isolate_vault_dir: Path) -> None:
    key = "wa_reader_test"
    log = _isolate_vault_dir / key / "sessions" / f"{key}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"role": "user", "content": "hola"}) + "\n"
        + "{corrupta\n"
        + json.dumps({"role": "assistant", "content": "¡Buenas tardes!"}) + "\n",
        encoding="utf-8",
    )
    events = build_session_history_reader()(key)
    assert [e["role"] for e in events] == ["user", "assistant"]


def test_reader_without_log_is_empty(_isolate_vault_dir: Path) -> None:
    assert build_session_history_reader()("wa_sin_log") == []


def test_metadata_store_round_trips_the_isolated_vault(_isolate_vault_dir: Path) -> None:
    store = build_session_metadata_store()
    store.write("wa_store_test", {"checkout_verification": {"items": {}}})
    assert (_isolate_vault_dir / "wa_store_test" / "metadata.json").exists()
    assert store.read("wa_store_test")["checkout_verification"] == {"items": {}}
