"""Tests del cast chats→orders (`order_actions`) — guard de la lección L-1.

L-1 (2026-06-10, validación en vivo): el cast abortaba a los 15s comandos que
SÍ se aplicaban server-side (el provider habla con Medusa cloud: 30s/request ×
3 retries) y reportaba 502 "no respondió" — la UI mentía.

Invariantes que este archivo protege:

* **Timeout = resultado DESCONOCIDO** → 504 con mensaje honesto ("PUEDE
  haberse aplicado"), nunca un error que afirme que el comando no pasó.
* **Solo connect-error garantiza no-aplicación** → 502 ("NO se aplicó").
* El timeout default cubre la cadena completa del provider (Medusa
  30s × 3 retries ≈ 95s peor caso de UNA llamada).
* Errores HTTP del provider (422, 404...) pasan through con su status.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from src.plugins.chats.api import order_actions


class _FakeAsyncClient:
    """Reemplaza httpx.AsyncClient: devuelve una Response fija o lanza."""

    def __init__(self, *, result: httpx.Response | None = None,
                 exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def patch(self, url: str, json: object = None) -> httpx.Response:
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


def _install(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> None:
    monkeypatch.setattr(
        order_actions.httpx, "AsyncClient",
        lambda **_ignored: _FakeAsyncClient(**kwargs),  # type: ignore[arg-type]
    )


async def test_success_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, result=httpx.Response(
        200, json={"success": True, "current_stage": "preparing"}))
    data = await order_actions._forward_patch("/api/orders/orders/o1/schedule", {})
    assert data == {"success": True, "current_stage": "preparing"}


async def test_provider_http_error_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, result=httpx.Response(422, text="delivery_iso requerido"))
    with pytest.raises(HTTPException) as exc_info:
        await order_actions._forward_patch("/api/orders/orders/o1/schedule", {})
    assert exc_info.value.status_code == 422
    assert "delivery_iso" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    "exc", [httpx.ReadTimeout("read"), httpx.WriteTimeout("write"),
            httpx.PoolTimeout("pool")],
)
async def test_timeout_is_504_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch, exc: Exception,
) -> None:
    """L-1: timeout NUNCA se reporta como 'el comando falló'."""
    _install(monkeypatch, exc=exc)
    with pytest.raises(HTTPException) as exc_info:
        await order_actions._forward_patch("/api/orders/orders/o1/schedule", {})
    assert exc_info.value.status_code == 504
    assert "PUEDE haberse aplicado" in exc_info.value.detail


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError("refused"), httpx.ConnectTimeout("connect")],
)
async def test_connect_failure_is_502_not_applied(
    monkeypatch: pytest.MonkeyPatch, exc: Exception,
) -> None:
    _install(monkeypatch, exc=exc)
    with pytest.raises(HTTPException) as exc_info:
        await order_actions._forward_patch("/api/orders/orders/o1/schedule", {})
    assert exc_info.value.status_code == 502
    assert "NO se aplicó" in exc_info.value.detail


def test_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORDERS_CAST_TIMEOUT_S", "33.5")
    assert order_actions._timeout_s() == 33.5


def test_default_timeout_covers_medusa_retry_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """30s/request × 3 retries tenacity del HttpMedusaClient ≈ 95s peor caso."""
    monkeypatch.delenv("ORDERS_CAST_TIMEOUT_S", raising=False)
    assert order_actions._timeout_s() >= 95.0
