"""Tests del cast chats→orders (`order_actions`) — guard de L-1 + propagación auth.

L-1 (2026-06-10, validación en vivo): el cast abortaba a los 15s comandos que
SÍ se aplicaban server-side (el provider habla con Medusa cloud: 30s/request ×
3 retries) y reportaba 502 "no respondió" — la UI mentía.

Post 2026-06-23 el cast delega en ``src.sdk.castkit`` (Canal 3), que centraliza
la semántica de fallos honesta Y añade la propagación del ``Authorization`` (el
2º hop atraviesa ``require_auth``). Este archivo mantiene el guard L-1
end-to-end DEL CAST REAL (que delega bien, con su timeout) + el guard del fix de
auth. La semántica genérica del helper vive en ``tests/test_castkit.py``.

Invariantes que este archivo protege:

* **Timeout = resultado DESCONOCIDO** → 504 con mensaje honesto ("PUEDE
  haberse aplicado"), nunca un error que afirme que el comando no pasó.
* **Solo connect-error garantiza no-aplicación** → 502 ("NO se aplicó").
* El timeout default cubre la cadena completa del provider (Medusa
  30s × 3 retries ≈ 95s peor caso de UNA llamada).
* El ``Authorization`` del operador viaja al provider (sin esto: 401 en el 2º hop).
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.plugins.chats.api import order_actions
from src.sdk import castkit


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {"type": "http", "method": "PATCH", "headers": raw, "query_string": b""}
    )


class _FakeAsyncClient:
    """Reemplaza httpx.AsyncClient (el que vive en castkit): captura o lanza."""

    def __init__(self, *, result=None, exc=None, capture=None) -> None:
        self._result = result
        self._exc = exc
        self._capture = capture

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def request(self, method, url, *, params=None, json=None, headers=None):
        if self._capture is not None:
            self._capture.update(method=method, url=url, json=json, headers=headers)
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


def _install(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> None:
    # El AsyncClient del cast vive ahora en castkit (order_actions delega).
    monkeypatch.setattr(
        castkit.httpx, "AsyncClient",
        lambda **_ignored: _FakeAsyncClient(**kwargs),  # type: ignore[arg-type]
    )


async def test_success_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, result=httpx.Response(
        200, json={"success": True, "current_stage": "preparing"}))
    data = await order_actions._forward_patch(
        _request(), "/api/orders/orders/o1/schedule", {})
    assert data == {"success": True, "current_stage": "preparing"}


async def test_provider_http_error_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, result=httpx.Response(
        422, json={"detail": "delivery_iso requerido"}))
    with pytest.raises(HTTPException) as exc_info:
        await order_actions._forward_patch(
            _request(), "/api/orders/orders/o1/schedule", {})
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
        await order_actions._forward_patch(
            _request(), "/api/orders/orders/o1/schedule", {})
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
        await order_actions._forward_patch(
            _request(), "/api/orders/orders/o1/schedule", {})
    assert exc_info.value.status_code == 502
    assert "NO se aplicó" in exc_info.value.detail


async def test_forward_patch_propagates_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 2026-06-23: el Bearer del operador viaja al provider (PATCH incluido)."""
    cap: dict[str, object] = {}
    _install(monkeypatch, capture=cap, result=httpx.Response(200, json={"success": True}))
    await order_actions._forward_patch(
        _request({"Authorization": "Bearer op-token"}),
        "/api/orders/orders/o1/confirm-payment", {})
    assert (cap["headers"] or {}).get("Authorization") == "Bearer op-token"
    assert cap["method"] == "PATCH"


def test_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORDERS_CAST_TIMEOUT_S", "33.5")
    assert order_actions._timeout_s() == 33.5


def test_default_timeout_covers_medusa_retry_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """30s/request × 3 retries tenacity del HttpMedusaClient ≈ 95s peor caso."""
    monkeypatch.delenv("ORDERS_CAST_TIMEOUT_S", raising=False)
    assert order_actions._timeout_s() >= 95.0


def test_get_order_detail_route_forwards_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read-side del cast: el chat necesita saber si el pedido YA tiene fecha
    asignada (`summary.due_iso`) para que "Confirmar pago" NO re-agende por
    encima de una fecha puesta por el operador (botón "Asignar fecha").

    GET /order-actions/{id} → GET /api/orders/orders/{id} (order@v1 read-side).
    """
    cap: dict[str, object] = {}
    _install(monkeypatch, capture=cap, result=httpx.Response(
        200, json={"summary": {"id": "o1", "due_iso": "2026-07-15"}}))
    app = FastAPI()
    app.include_router(order_actions.router, prefix="/api/chats")
    resp = TestClient(app).get("/api/chats/order-actions/o1")
    assert resp.status_code == 200
    assert resp.json()["summary"]["due_iso"] == "2026-07-15"
    assert cap["method"] == "GET"
    assert str(cap["url"]).endswith("/api/orders/orders/o1")
    assert cap["json"] is None  # read puro, sin body


def test_get_order_detail_propagates_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El Bearer del operador viaja también en el hop de lectura."""
    cap: dict[str, object] = {}
    _install(monkeypatch, capture=cap, result=httpx.Response(200, json={}))
    app = FastAPI()
    app.include_router(order_actions.router, prefix="/api/chats")
    TestClient(app).get(
        "/api/chats/order-actions/o1",
        headers={"Authorization": "Bearer op-token"},
    )
    assert (cap["headers"] or {}).get("Authorization") == "Bearer op-token"


# ── Panel de pedidos del cliente (mobile): listar + cambiar estado ──────────


def test_by_session_route_forwards_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El panel móvil lista los pedidos DE ESE cliente: GET .../by-session/{id}
    → GET /api/orders/orders/by-session/{id} (el vínculo sesión→órdenes lo
    resuelve orders desde el vault)."""
    cap: dict[str, object] = {}
    _install(monkeypatch, capture=cap, result=httpx.Response(
        200, json={"orders": [{"id": "order_01HX", "status": "preparing"}], "count": 1}))
    app = FastAPI()
    app.include_router(order_actions.router, prefix="/api/chats")
    resp = TestClient(app).get("/api/chats/order-actions/by-session/wa_573001")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    assert cap["method"] == "GET"
    assert str(cap["url"]).endswith("/api/orders/orders/by-session/wa_573001")
    assert cap["json"] is None  # read puro


def test_stage_route_forwards_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cambio de estado manual desde el chat: PATCH .../{id}/stage →
    PATCH /api/orders/orders/{id}/stage (el provider valida la transición DAG)."""
    cap: dict[str, object] = {}
    _install(monkeypatch, capture=cap, result=httpx.Response(
        200, json={"success": True, "current_stage": "ready"}))
    app = FastAPI()
    app.include_router(order_actions.router, prefix="/api/chats")
    resp = TestClient(app).patch(
        "/api/chats/order-actions/order_01HX/stage", json={"stage": "ready"})
    assert resp.status_code == 200
    assert resp.json()["current_stage"] == "ready"
    assert cap["method"] == "PATCH"
    assert str(cap["url"]).endswith("/api/orders/orders/order_01HX/stage")
    assert cap["json"] == {"stage": "ready"}


def test_by_session_route_propagates_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap: dict[str, object] = {}
    _install(monkeypatch, capture=cap, result=httpx.Response(200, json={"orders": [], "count": 0}))
    app = FastAPI()
    app.include_router(order_actions.router, prefix="/api/chats")
    TestClient(app).get(
        "/api/chats/order-actions/by-session/wa_1",
        headers={"Authorization": "Bearer op-token"},
    )
    assert (cap["headers"] or {}).get("Authorization") == "Bearer op-token"
