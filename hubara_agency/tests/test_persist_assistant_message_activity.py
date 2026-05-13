"""Tests de `persist_assistant_message_activity`.

Cubierto:
  * Happy path: append crea el JSONL con role=assistant y content correctos.
  * Empty content: early return, no crea archivo.
  * Multiple calls: appendea sin sobrescribir.
"""
from __future__ import annotations

import json

import pytest
from temporalio.testing import ActivityEnvironment

import src.platform.session_history.activities as mod


pytestmark = pytest.mark.asyncio


async def test_persist_writes_assistant_event(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "WORKSPACE_VAULT_DIR", tmp_path)

    env = ActivityEnvironment()
    await env.run(
        mod.persist_assistant_message_activity, "wa_test", "hola desde agente"
    )

    log = tmp_path / "wa_test" / "sessions" / "wa_test.jsonl"
    assert log.exists()
    parsed = json.loads(log.read_text(encoding="utf-8").strip())
    assert parsed["role"] == "assistant"
    assert parsed["content"] == "hola desde agente"
    assert "timestamp" in parsed


async def test_persist_skips_empty_content(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "WORKSPACE_VAULT_DIR", tmp_path)

    env = ActivityEnvironment()
    await env.run(mod.persist_assistant_message_activity, "wa_test", "")

    log = tmp_path / "wa_test" / "sessions" / "wa_test.jsonl"
    assert not log.exists()


async def test_persist_appends_to_existing_log(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "WORKSPACE_VAULT_DIR", tmp_path)

    # Simula que el user_event ya esta en el log
    log_dir = tmp_path / "wa_test" / "sessions"
    log_dir.mkdir(parents=True)
    log = log_dir / "wa_test.jsonl"
    log.write_text(
        json.dumps({"role": "user", "content": "hola"}) + "\n",
        encoding="utf-8",
    )

    env = ActivityEnvironment()
    await env.run(mod.persist_assistant_message_activity, "wa_test", "respondiendo")

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["role"] == "user"
    assert json.loads(lines[1])["role"] == "assistant"
    assert json.loads(lines[1])["content"] == "respondiendo"
