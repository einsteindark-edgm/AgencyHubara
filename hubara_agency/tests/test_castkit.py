"""Tests del Canal-3 castkit (`src.sdk.castkit`) — el helper de cast HTTP
cross-plugin con propagación de identidad.

Guard del incidente (run 2026-06-23): los casts loopback plugin→plugin
(`agents_admin→chats`, `chats→orders`) hacían `httpx` a la MISMA app FastAPI
SIN reenviar el header `Authorization`. Post auth JWT/Cognito (require_auth
colgado en todos los routers), el 2º hop daba 401 ``{"detail":"Falta el bearer
token"}`` y el cast lo re-envolvía con ``resp.text`` → ``detail`` doble-anidado.

Comportamiento exigido al helper centralizado:

* **Propaga** el ``Authorization`` del request entrante al hop del provider
  (el token ya fue validado en el edge; el 2º hop es la MISMA identidad).
* **Tolera** la ausencia de token (dev/tests con auth no-op): no manda header.
* **Desanida** el ``detail`` del upstream (no re-envuelve el JSON crudo).
* **Semántica honesta de fallos (L-1, generalizada a TODO cast)**: connect →
  502 "NO se aplicó"; timeout → 504 "PUEDE haberse aplicado"; HTTP del
  provider (4xx/5xx) → passthrough del status con el detail desanidado.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.sdk import castkit

_BASE = "http://127.0.0.1:8000"


def _request(headers: dict[str, str] | None = None) -> Request:
    """Request ASGI mínimo con los headers dados (para leer Authorization).

    ``query_string`` siempre presente (ASGI lo garantiza en prod) — el helper lo
    incluye para que ``request.query_params`` no rompa.
    """
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {"type": "http", "method": "GET", "headers": raw, "query_string": b""}
    )


class _FakeAsyncClient:
    """Reemplaza httpx.AsyncClient: captura la request y devuelve/lanza fijo."""

    def __init__(
        self,
        *,
        result: httpx.Response | None = None,
        exc: Exception | None = None,
        capture: dict[str, Any] | None = None,
    ) -> None:
        self._result = result
        self._exc = exc
        self._capture = capture

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: object = None,
        json: object = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if self._capture is not None:
            self._capture.update(
                method=method, url=url, params=params, json=json, headers=headers
            )
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


def _install(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> None:
    monkeypatch.setattr(
        castkit.httpx, "AsyncClient",
        lambda **_ignored: _FakeAsyncClient(**kwargs),  # type: ignore[arg-type]
    )


async def _forward(request: Request, **over: Any) -> dict[str, Any]:
    kw: dict[str, Any] = dict(
        method="GET", path="/api/chats/evals/history", base_url=_BASE,
        timeout=15.0, cast_label="test→provider",
    )
    kw.update(over)
    return await castkit.forward(request, **kw)


# --- propagación de identidad (el bug principal) ----------------------------


async def test_forward_propagates_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El Bearer del request entrante viaja al hop del provider."""
    cap: dict[str, Any] = {}
    _install(monkeypatch, capture=cap, result=httpx.Response(200, json={"ok": True}))
    await _forward(_request({"Authorization": "Bearer tok-123"}))
    assert (cap["headers"] or {}).get("Authorization") == "Bearer tok-123"


async def test_forward_without_token_sends_no_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin sesión (dev/tests, auth no-op) no se inventa header."""
    cap: dict[str, Any] = {}
    _install(monkeypatch, capture=cap, result=httpx.Response(200, json={"ok": True}))
    await _forward(_request())
    assert "Authorization" not in (cap["headers"] or {})


# --- desanidado del detail (el doble-detail) --------------------------------


async def test_forward_unwraps_upstream_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El 401 del provider ``{"detail": "X"}`` NO se re-anida: detail == "X"."""
    _install(
        monkeypatch,
        result=httpx.Response(401, json={"detail": "Falta el bearer token"}),
    )
    with pytest.raises(HTTPException) as exc:
        await _forward(_request())
    assert exc.value.status_code == 401
    assert exc.value.detail == "Falta el bearer token"
    # Y nunca el JSON crudo re-anidado.
    assert "{" not in str(exc.value.detail)


async def test_forward_non_json_error_falls_back_to_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un error no-JSON (p.ej. 502 de un proxy) cae a resp.text sin romper."""
    _install(monkeypatch, result=httpx.Response(502, text="Bad Gateway"))
    with pytest.raises(HTTPException) as exc:
        await _forward(_request())
    assert exc.value.status_code == 502
    assert "Bad Gateway" in str(exc.value.detail)


# --- contrato de éxito + semántica honesta de fallos (L-1 generalizada) -----


async def test_forward_success_returns_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, result=httpx.Response(200, json={"days": 30, "series": []}))
    data = await _forward(_request())
    assert data == {"days": 30, "series": []}


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError("refused"), httpx.ConnectTimeout("connect")],
)
async def test_forward_connect_failure_is_502_not_applied(
    monkeypatch: pytest.MonkeyPatch, exc: Exception,
) -> None:
    """Connect-error → garantizado no-aplicación → 502 honesto."""
    _install(monkeypatch, exc=exc)
    with pytest.raises(HTTPException) as info:
        await _forward(_request())
    assert info.value.status_code == 502
    assert "NO se aplicó" in info.value.detail


@pytest.mark.parametrize(
    "exc", [httpx.ReadTimeout("read"), httpx.WriteTimeout("write"),
            httpx.PoolTimeout("pool")],
)
async def test_forward_timeout_is_504_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch, exc: Exception,
) -> None:
    """Timeout → resultado DESCONOCIDO → 504 (nunca 'falló'). L-1."""
    _install(monkeypatch, exc=exc)
    with pytest.raises(HTTPException) as info:
        await _forward(_request())
    assert info.value.status_code == 504
    assert "PUEDE haberse aplicado" in info.value.detail


async def test_forward_sends_method_path_and_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El método, la URL (base+path) y los params llegan al provider."""
    cap: dict[str, Any] = {}
    _install(monkeypatch, capture=cap, result=httpx.Response(200, json={"ok": True}))
    await _forward(
        _request(), method="POST", path="/api/chats/evals/candidates/c1/approve",
        params={"days": 7}, body={"expected_outcome": "x"},
    )
    assert cap["method"] == "POST"
    assert cap["url"] == f"{_BASE}/api/chats/evals/candidates/c1/approve"
    assert cap["params"] == {"days": 7}
    assert cap["json"] == {"expected_outcome": "x"}


# --- premortem: robustez ante body no-JSON y token por query (SSE) -----------


async def test_forward_success_non_json_is_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 con body no-JSON (proxy intermedio / otro servicio) → 502 claro, no 500."""
    _install(monkeypatch, result=httpx.Response(200, text="<html>OK</html>"))
    with pytest.raises(HTTPException) as exc:
        await _forward(_request())
    assert exc.value.status_code == 502
    assert "no-JSON" in str(exc.value.detail)


async def test_forward_propagates_token_from_query_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSE-origin: el token viaja por ?access_token= → portado como Bearer al hop.

    El EventSource del browser no manda header `Authorization`; el backend
    (`_extract_token`) acepta el token por query. El kit debe portar la identidad
    sin importar el transporte, o un cast desde un endpoint SSE daría 401.
    """
    cap: dict[str, Any] = {}
    _install(monkeypatch, capture=cap, result=httpx.Response(200, json={"ok": True}))
    req = Request(
        {
            "type": "http",
            "method": "GET",
            "headers": [],
            "query_string": b"access_token=qtok-9",
        }
    )
    await castkit.forward(
        req, "GET", "/api/chats/evals/history",
        base_url=_BASE, timeout=15.0, cast_label="t->p",
    )
    assert (cap["headers"] or {}).get("Authorization") == "Bearer qtok-9"


async def test_forward_header_wins_over_query_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si vienen ambos, el header tiene precedencia (igual que _extract_token)."""
    cap: dict[str, Any] = {}
    _install(monkeypatch, capture=cap, result=httpx.Response(200, json={"ok": True}))
    req = Request(
        {
            "type": "http",
            "method": "GET",
            "headers": [(b"authorization", b"Bearer hdr")],
            "query_string": b"access_token=qry",
        }
    )
    await castkit.forward(
        req, "GET", "/x", base_url=_BASE, timeout=15.0, cast_label="t->p",
    )
    assert (cap["headers"] or {}).get("Authorization") == "Bearer hdr"
