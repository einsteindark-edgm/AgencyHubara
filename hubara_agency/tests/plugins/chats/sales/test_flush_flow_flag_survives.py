"""Run 01a0a0f1 (2026-09-14): el flush escribía `shipping_flow_awaiting_reply_since_ms`
al mandar el Flow nativo y un segundo después lo pisaba con su copia vieja de
metadata.json → `read_idle_timeout_seconds` devolvía 300 y el ghosting cerró a
los 5 min mientras el cliente aún podía estar llenando el formulario."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

SESSION = "wa_573001234567"
NOW_MS = int(time.time() * 1000)


@pytest.fixture
def flow_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from src.platform import config
    from src.platform.whatsapp import client as wa_client
    from src.plugins.chats.agent.sales.activities import bootstrap_session

    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    monkeypatch.setattr(config, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setattr(bootstrap_session, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setattr(flush_ui_intents, "WORKSPACE_VAULT_DIR", tmp_path, raising=False)
    monkeypatch.setenv("META_FLOW_ID_SHIPPING", "951293630651590")
    monkeypatch.setattr(wa_client, "send_flow", AsyncMock(return_value=SimpleNamespace(ok=True, wa_message_id="wamid.flow", error=None)))
    monkeypatch.setattr(wa_client, "send_text", AsyncMock(return_value=SimpleNamespace(ok=True, wa_message_id="wamid.txt", error=None)))
    path = tmp_path / SESSION / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "phone_number_id": "pnid-1",
        "episodes": [{"episode_id": "ep_001", "started_at_ms": NOW_MS - 5000, "closed_at_ms": None}],
        "pending_ui_intents": [{
            "id": "shipping-1", "kind": "shipping_flow", "queued_at_ms": NOW_MS, "analytics": {},
            "params": {"flow_id": "FLOW_ID_SHIPPING_PLACEHOLDER", "flow_token": "shipping_x", "flow_cta": "Completar datos",
                       "header_text": "Datos de envío", "body": "Para enviarte necesito unos datos.", "order_total_cop": 21000,
                       "items_summary": "1× Cubo Love", "flow_action": "navigate", "flow_action_screen": "SHIPPING",
                       "flow_action_data": {"order_total_cop": 21000, "items_summary": "1× Cubo Love"}},
        }],
    }), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_native_flow_flag_survives_the_flush_and_extends_the_idle_timeout(flow_env: Path) -> None:
    from src.plugins.chats.agent.sales.activities import flush_ui_intents
    from src.plugins.chats.agent.sales.activities.bootstrap_session import read_idle_timeout_seconds_activity

    assert await flush_ui_intents.flush_pending_ui_intents(SESSION) == 1
    md = json.loads(flow_env.read_text(encoding="utf-8"))
    assert md.get("pending_ui_intents") == []
    assert isinstance(md.get("shipping_flow_awaiting_reply_since_ms"), int), md.keys()
    timeout = await read_idle_timeout_seconds_activity(SESSION)
    assert timeout > 300, timeout
    assert timeout <= 600
