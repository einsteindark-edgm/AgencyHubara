"""get_temporal_client: auth por API key (Temporal Cloud) vs fallback mTLS/local.

Decisión INFRASTRUCTURE.md §6: API key auth (sin certs que rotar). El conector
debe PREFERIR API key cuando `TEMPORAL_API_KEY` está seteada, usando el endpoint
regional `TEMPORAL_ADDRESS` con `tls=True`; sino conserva el camino actual
(mTLS/insecure sobre `TEMPORAL_URL`) para dev local.
"""
import importlib
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_prefers_api_key_when_set(monkeypatch):
    monkeypatch.setenv("TEMPORAL_API_KEY", "sk-test-key")
    monkeypatch.setenv("TEMPORAL_ADDRESS", "us-east-1.aws.api.temporal.io:7233")
    monkeypatch.setenv("TEMPORAL_NAMESPACE", "hubara.acct")

    from src.platform import config
    from src.platform.temporal import client
    try:
        importlib.reload(config)
        importlib.reload(client)
        with patch.object(client.Client, "connect", new=AsyncMock(return_value="CLIENT")) as connect:
            result = await client.get_temporal_client()

        assert result == "CLIENT"
        connect.assert_awaited_once()
        assert connect.call_args.args[0] == "us-east-1.aws.api.temporal.io:7233"
        assert connect.call_args.kwargs.get("api_key") == "sk-test-key"
        assert connect.call_args.kwargs.get("tls") is True
        assert connect.call_args.kwargs.get("namespace") == "hubara.acct"
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(client)


@pytest.mark.asyncio
async def test_falls_back_to_url_without_api_key(monkeypatch):
    monkeypatch.delenv("TEMPORAL_API_KEY", raising=False)
    monkeypatch.setenv("TEMPORAL_URL", "localhost:7233")
    monkeypatch.delenv("TEMPORAL_TLS_CERT_PATH", raising=False)
    monkeypatch.delenv("TEMPORAL_TLS_KEY_PATH", raising=False)

    from src.platform import config
    from src.platform.temporal import client
    try:
        importlib.reload(config)
        importlib.reload(client)
        with patch.object(client.Client, "connect", new=AsyncMock(return_value="CLIENT")) as connect:
            await client.get_temporal_client()

        # sin API key → conecta a TEMPORAL_URL, sin api_key, tls insecure (False)
        assert connect.call_args.args[0] == "localhost:7233"
        assert connect.call_args.kwargs.get("api_key") is None
        assert connect.call_args.kwargs.get("tls") is False
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(client)
