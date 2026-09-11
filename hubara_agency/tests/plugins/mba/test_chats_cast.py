"""El cast `mba→chats` (canal 3) usa castkit en modo service y el path del contrato."""

from __future__ import annotations

from typing import Any

import pytest

from src.plugins.mba.api import chats_cast


async def test_session_action_forwards_with_the_service_token_to_the_contract_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_forward(
        request: Any, method: str, path: str, **kw: Any
    ) -> dict[str, Any]:
        captured.update(request=request, method=method, path=path, **kw)
        return {"ok": True}

    monkeypatch.setattr(chats_cast.castkit, "forward", fake_forward)
    monkeypatch.setenv("CHATS_API_BASE", "http://127.0.0.1:8000/")
    monkeypatch.setenv("CHATS_CAST_TIMEOUT_S", "12.5")
    out = await chats_cast.session_action(
        "REQ", "wa_573001234567", "draft", {"aroma": "Lavanda"}
    )
    assert out == {"ok": True}
    assert captured["request"] == "REQ" and captured["method"] == "POST"
    assert captured["path"] == "/api/chats/session-actions/wa_573001234567/draft"
    assert (
        captured["auth"] == "service"
        and captured["base_url"] == "http://127.0.0.1:8000"
    )
    assert captured["timeout"] == 12.5 and captured["cast_label"] == "mba→chats"
    assert captured["body"] == {"aroma": "Lavanda"}


async def test_session_key_is_percent_encoded_in_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_forward(
        request: Any, method: str, path: str, **kw: Any
    ) -> dict[str, Any]:
        captured["path"] = path
        return {}

    monkeypatch.setattr(chats_cast.castkit, "forward", fake_forward)
    await chats_cast.session_action(None, "wa_1/2?x", "tag", {})
    assert captured["path"] == "/api/chats/session-actions/wa_1%2F2%3Fx/tag"
