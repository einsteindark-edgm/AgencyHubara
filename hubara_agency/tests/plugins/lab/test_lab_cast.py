"""Cast lab→chats (`lab@v1` → vistas del laboratorio).

El frontend del plugin lab solo habla con `/api/lab/*`; este cast reenvía al
contrato `lab@v1` que publica chats (`/api/chats/lab/*`) con `castkit.forward`:
porta el `Authorization` del operador y traduce los fallos con honestidad
(L-1): connect-error = 502 "NO se aplicó"; timeout = 504 "PUEDE haberse
aplicado" (importa en "Nueva corrida": prende una caja que cuesta plata).
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.lab.api import router
from src.sdk import castkit

RUN = "run-20260923-1041-ab12"
SID = "wa_573001234567"


class _FakeAsyncClient:
    def __init__(self, *, result: httpx.Response | None = None, exc: Exception | None = None,
                 capture: dict[str, Any] | None = None, timeout: Any = None) -> None:
        self._result = result
        self._exc = exc
        self._capture = capture
        if capture is not None:
            capture["timeout"] = timeout

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def request(self, method, url, *, params=None, json=None, headers=None, files=None):
        if self._capture is not None:
            self._capture.update(method=method, url=url, params=params, json=json, headers=headers)
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


def _client(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> TestClient:
    monkeypatch.setenv("CHATS_API_BASE", "http://chats.internal:8000")
    monkeypatch.setattr(
        castkit.httpx, "AsyncClient",
        lambda **kw: _FakeAsyncClient(timeout=kw.get("timeout"), **kwargs),  # type: ignore[arg-type]
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/lab")
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "url", "provider_path", "params"),
    [
        ("GET", "/api/lab/estimate?arms=A1,B&reps=3&bench=new", "/api/chats/lab/estimate", {"arms": "A1,B", "reps": 3, "bench": "new"}),
        ("GET", "/api/lab/runs", "/api/chats/lab/runs", None),
        ("GET", "/api/lab/runs/active", "/api/chats/lab/runs/active", None),
        ("GET", f"/api/lab/runs/{RUN}/bench", f"/api/chats/lab/runs/{RUN}/bench", None),
        ("GET", f"/api/lab/runs/{RUN}/conversations", f"/api/chats/lab/runs/{RUN}/conversations", None),
        ("GET", f"/api/lab/runs/{RUN}/conversations/{SID}?episode=ep_2", f"/api/chats/lab/runs/{RUN}/conversations/{SID}", {"episode": "ep_2"}),
        ("GET", f"/api/lab/runs/{RUN}/conversations/{SID}/turns/trace?turn_key=run:abc/t:3&arm=B&rep=1",
         f"/api/chats/lab/runs/{RUN}/conversations/{SID}/turns/trace", {"turn_key": "run:abc/t:3", "arm": "B", "rep": 1}),
        ("GET", f"/api/lab/runs/{RUN}/conversations/{SID}/evaluations?arm=A1", f"/api/chats/lab/runs/{RUN}/conversations/{SID}/evaluations", {"arm": "A1", "rep": 0}),
        ("GET", f"/api/lab/runs/{RUN}/summary?arm=C", f"/api/chats/lab/runs/{RUN}/summary", {"arm": "C"}),
        ("GET", f"/api/lab/runs/{RUN}/report", f"/api/chats/lab/runs/{RUN}/report", None),
        ("GET", f"/api/lab/runs/{RUN}/diff?base=A1&cand=B", f"/api/chats/lab/runs/{RUN}/diff", {"base": "A1", "cand": "B"}),
    ],
)
def test_reads_forward_to_the_chats_contract(
    monkeypatch: pytest.MonkeyPatch, method: str, url: str, provider_path: str, params: dict[str, Any] | None,
) -> None:
    capture: dict[str, Any] = {}
    client = _client(monkeypatch, result=httpx.Response(200, json={"ok": True}), capture=capture)

    res = client.request(method, url, headers={"Authorization": "Bearer operador"})

    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True}
    assert capture["method"] == method
    assert capture["url"] == f"http://chats.internal:8000{provider_path}"
    assert (capture["params"] or None) == params
    assert capture["headers"]["Authorization"] == "Bearer operador"


def test_launch_forwards_the_body_and_keeps_202(monkeypatch: pytest.MonkeyPatch) -> None:
    capture: dict[str, Any] = {}
    client = _client(monkeypatch, result=httpx.Response(202, json={"run_id": RUN}), capture=capture)

    res = client.post("/api/lab/runs", json={"arms": ["A1", "B"], "reps": 1, "bench": "new"},
                      headers={"Authorization": "Bearer operador"})

    assert res.status_code == 202
    assert res.json() == {"run_id": RUN}
    assert capture["url"].endswith("/api/chats/lab/runs")
    assert capture["json"] == {"arms": ["A1", "B"], "reps": 1, "bench": "new"}
    assert capture["timeout"] is not None and capture["timeout"] >= 30


def test_cancel_forwards_and_keeps_202(monkeypatch: pytest.MonkeyPatch) -> None:
    capture: dict[str, Any] = {}
    client = _client(monkeypatch, result=httpx.Response(202, json={"cancel_requested": True}), capture=capture)

    res = client.post("/api/lab/runs/active/cancel")

    assert res.status_code == 202
    assert capture["method"] == "POST"
    assert capture["url"].endswith("/api/chats/lab/runs/active/cancel")


@pytest.mark.parametrize(
    "url",
    [
        "/api/lab/runs/..%2F..%2Fsecret/bench",
        "/api/lab/runs/RUN-X/summary",
        f"/api/lab/runs/{RUN}/conversations/not-a-session",
        f"/api/lab/runs/{RUN}/conversations/{SID}?episode=../x",
        f"/api/lab/runs/{RUN}/summary?arm=Z",
        f"/api/lab/runs/{RUN}/conversations/{SID}/turns/trace?turn_key=k&rep=7",
        "/api/lab/estimate?bench=../../x",
    ],
)
def test_invalid_segments_never_reach_the_provider(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    capture: dict[str, Any] = {}
    client = _client(monkeypatch, result=httpx.Response(200, json={}), capture=capture)

    res = client.get(url)

    assert res.status_code in (404, 422), res.text
    assert "url" not in capture


def test_provider_conflict_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, result=httpx.Response(
        409, json={"detail": {"message": "Ya hay una corrida en curso.", "active": {"run_id": RUN}}}))

    res = client.post("/api/lab/runs", json={"arms": ["A1"], "reps": 1})

    assert res.status_code == 409
    assert res.json()["detail"]["message"] == "Ya hay una corrida en curso."


def test_launch_timeout_is_504_outcome_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, exc=httpx.ReadTimeout("read"))

    res = client.post("/api/lab/runs", json={"arms": ["A1"], "reps": 1})

    assert res.status_code == 504
    assert "PUEDE haberse aplicado" in res.json()["detail"]


def test_provider_down_is_502_not_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, exc=httpx.ConnectError("refused"))

    res = client.get("/api/lab/runs")

    assert res.status_code == 502
    assert "NO se aplicó" in res.json()["detail"]
