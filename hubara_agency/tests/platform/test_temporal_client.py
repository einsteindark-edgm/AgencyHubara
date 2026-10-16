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


# ── Un cliente por proceso (auditoría Temporal, Ola 0) ───────────────────────
#
# `Client.connect` abre un canal gRPC + handshake TLS + GetSystemInfo: hacerlo en
# CADA llamada (34 call sites, cada webhook entrante incluido) es una conexión
# nueva por request. El cliente de temporalio es seguro de compartir.


async def test_two_calls_share_one_connection(monkeypatch):
    from src.platform.temporal import client

    connect = AsyncMock(side_effect=lambda *a, **kw: object())
    monkeypatch.setattr(client.Client, "connect", connect)

    first = await client.get_temporal_client()
    second = await client.get_temporal_client()

    assert connect.await_count == 1, (
        f"dos llamadas deben reusar UN cliente; Client.connect corrió {connect.await_count} veces"
    )
    assert first is second


async def test_concurrent_first_calls_connect_once(monkeypatch):
    """Dos requests que llegan juntos con la caché vacía no abren dos conexiones."""
    import asyncio

    from src.platform.temporal import client

    async def slow_connect(*args, **kwargs):
        await asyncio.sleep(0.01)  # el handshake real tarda: los demás esperan
        return object()

    connect = AsyncMock(side_effect=slow_connect)
    monkeypatch.setattr(client.Client, "connect", connect)

    clients = await asyncio.gather(*(client.get_temporal_client() for _ in range(5)))

    assert connect.await_count == 1, (
        f"5 llamadas concurrentes deben conectar UNA vez; conectaron {connect.await_count}"
    )
    assert all(c is clients[0] for c in clients)


def test_each_event_loop_gets_its_own_client(monkeypatch):
    """El cliente (y su candado) son del event loop que los creó: un proceso que
    corre `asyncio.run` dos veces (scripts, tests con loops distintos) no recibe
    en el segundo loop un cliente atado al primero, ya cerrado."""
    import asyncio

    from src.platform.temporal import client

    async def slow_connect(*args, **kwargs):
        await asyncio.sleep(0.01)
        return object()

    connect = AsyncMock(side_effect=slow_connect)
    monkeypatch.setattr(client.Client, "connect", connect)

    async def two_concurrent_calls():
        a, b = await asyncio.gather(client.get_temporal_client(), client.get_temporal_client())
        assert a is b
        return a

    from_first_loop = asyncio.run(two_concurrent_calls())
    from_second_loop = asyncio.run(two_concurrent_calls())

    assert connect.await_count == 2, (
        f"un cliente por event loop: esperaba 2 conexiones, hubo {connect.await_count}"
    )
    assert from_first_loop is not from_second_loop


async def test_failed_connect_is_not_cached(monkeypatch):
    """Temporal caído en el arranque no deja el proceso sin cliente para siempre:
    la próxima llamada vuelve a intentar."""
    from src.platform.temporal import client

    connect = AsyncMock(side_effect=[ConnectionRefusedError("temporal caído"), "CLIENT"])
    monkeypatch.setattr(client.Client, "connect", connect)

    with pytest.raises(ConnectionRefusedError):
        await client.get_temporal_client()
    assert await client.get_temporal_client() == "CLIENT"
    assert connect.await_count == 2


# ── Falla cerrado: mTLS pedido pero imposible → error, nunca sin TLS ─────────


def _local_mode(monkeypatch, client, *, cert: str, key: str) -> AsyncMock:
    """Sin API key, con los paths de mTLS dados; `Client.connect` espiado."""
    monkeypatch.setattr(client, "TEMPORAL_API_KEY", "")
    monkeypatch.setattr(client, "TEMPORAL_TLS_CERT_PATH", cert)
    monkeypatch.setattr(client, "TEMPORAL_TLS_KEY_PATH", key)
    connect = AsyncMock(return_value="CLIENT")
    monkeypatch.setattr(client.Client, "connect", connect)
    return connect


async def test_missing_tls_files_fail_closed(monkeypatch, tmp_path):
    """Certs configurados pero ausentes (secret sin montar, path mal escrito):
    antes conectaba SIN TLS con un warning. Ahora es un error claro y no hay
    conexión."""
    from src.platform.temporal import client

    connect = _local_mode(
        monkeypatch, client,
        cert=str(tmp_path / "no-existe.pem"), key=str(tmp_path / "no-existe.key"),
    )

    with pytest.raises(RuntimeError, match="TEMPORAL_TLS_CERT_PATH"):
        await client.get_temporal_client()
    connect.assert_not_awaited()


async def test_half_tls_config_fails_closed(monkeypatch, tmp_path):
    """Solo uno de los dos paths definido también es mTLS a medio configurar."""
    from src.platform.temporal import client

    cert = tmp_path / "temporal.pem"
    cert.write_bytes(b"CERT")
    connect = _local_mode(monkeypatch, client, cert=str(cert), key="")

    with pytest.raises(RuntimeError, match="TEMPORAL_TLS_KEY_PATH"):
        await client.get_temporal_client()
    connect.assert_not_awaited()


async def test_mtls_with_existing_files_uses_tls_config(monkeypatch, tmp_path):
    from src.platform.temporal import client

    cert, key = tmp_path / "temporal.pem", tmp_path / "temporal.key"
    cert.write_bytes(b"CERT")
    key.write_bytes(b"KEY")
    connect = _local_mode(monkeypatch, client, cert=str(cert), key=str(key))

    await client.get_temporal_client()

    tls = connect.call_args.kwargs["tls"]
    assert isinstance(tls, client.TLSConfig)
    assert (tls.client_cert, tls.client_private_key) == (b"CERT", b"KEY")


async def test_local_without_api_key_nor_certs_stays_plaintext(monkeypatch):
    """El stack local/docker (sin API key ni certs) sigue conectando sin TLS."""
    from src.platform.temporal import client

    connect = _local_mode(monkeypatch, client, cert="", key="")

    await client.get_temporal_client()

    assert connect.call_args.args[0] == client.TEMPORAL_URL
    assert connect.call_args.kwargs.get("tls") is False


# ── Dentro de una activity: el cliente del worker ────────────────────────────


async def test_activity_client_inside_an_activity_is_the_workers(monkeypatch):
    from temporalio.testing import ActivityEnvironment

    from src.platform.temporal import client

    connect = AsyncMock(return_value="PROCESS")
    monkeypatch.setattr(client.Client, "connect", connect)
    worker_client = object()

    async def _activity() -> object:
        return await client.get_activity_temporal_client()

    got = await ActivityEnvironment(client=worker_client).run(_activity)  # type: ignore[arg-type]

    assert got is worker_client
    connect.assert_not_awaited()


async def test_activity_client_falls_back_to_the_process_client(monkeypatch):
    """Fuera de una activity (HTTP) o en una ActivityEnvironment sin cliente:
    el cliente del proceso — o el `fallback` que pase el call site."""
    from temporalio.testing import ActivityEnvironment

    from src.platform.temporal import client

    monkeypatch.setattr(client.Client, "connect", AsyncMock(return_value="PROCESS"))

    async def _activity() -> object:
        return await client.get_activity_temporal_client()

    assert await client.get_activity_temporal_client() == "PROCESS"
    assert await ActivityEnvironment().run(_activity) == "PROCESS"

    async def _own_factory():
        return "CALL_SITE"

    assert await client.get_activity_temporal_client(fallback=_own_factory) == "CALL_SITE"
