"""SEC-06 / SEC-13: la auth y el rate limit miran la ruta que enruta el router.

uvicorn decodifica la ruta antes de armar el scope (``/x/events%3Fy`` llega como
``scope["path"] == "/x/events?y"``) y Starlette 1.0 arma ``request.url`` pegándole
esa ruta ya decodificada: ``request.url.path`` corta en el ``?`` (o en el ``#``) y
queda ``/x/events``. El router, en cambio, enruta la ruta entera. Con
``request.url.path`` un ``%3F`` hacía que el ticket SSE autenticara cualquier ruta
con un parámetro (``/api/orders/orders/events%3Fy`` → ``order_id="events?y"``) y
que cualquier ruta así quedara exenta del rate limit como si fuera el webhook.

Los requests se arman a mano igual que uvicorn (``TestClient`` decodifica la ruta
a su manera) y entran directo a la app ASGI.
"""
from __future__ import annotations

import time
from urllib.parse import unquote

import pytest
from fastapi import APIRouter, Depends, FastAPI

from src.platform import auth, config, sse_ticket
from src.platform.rate_limit import (
    SlidingWindowRateLimiter,
    install_rate_limit_middleware,
)


def _uvicorn_scope(target: bytes) -> dict:
    """El scope que arma uvicorn (``h11_impl``) para ``GET <target>``."""
    raw_path, _, query_string = target.partition(b"?")
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "server": ("testserver", 80),
        "client": ("203.0.113.7", 50000),
        "scheme": "http",
        "method": "GET",
        "root_path": "",
        "path": unquote(raw_path.decode("ascii")),
        "raw_path": raw_path,
        "query_string": query_string,
        "headers": [(b"host", b"testserver")],
    }


async def _status(app, target: bytes) -> int:  # noqa: ANN001 — app ASGI
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(_uvicorn_scope(target), receive, send)
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


@pytest.fixture
def ticket(monkeypatch) -> bytes:
    """Cognito enforced (ningún bearer pasa) y un ticket SSE vigente."""
    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "pool-1")
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "client-1")
    monkeypatch.setattr(auth, "_sse_ticket_secret", lambda: "sse-secret")
    return sse_ticket.mint("sse-secret", ttl_seconds=3600, now=time.time()).encode()


def _app_like_main() -> FastAPI:
    """Mismo cableado que ``main.py``: el router del plugin cuelga de ``require_auth``."""
    router = APIRouter()

    @router.get("/dashboard/events")
    def stream() -> dict:
        return {"stream": True}

    @router.get("/orders/orders/{order_id}")
    def order(order_id: str) -> dict:
        return {"order_id": order_id}

    app = FastAPI()
    app.include_router(
        router, prefix="/api", dependencies=[Depends(auth.require_auth)]
    )
    return app


async def test_ticket_still_opens_the_stream(ticket: bytes) -> None:
    target = b"/api/dashboard/events?ticket=" + ticket
    assert await _status(_app_like_main(), target) == 200


@pytest.mark.parametrize("cut", [b"%3F", b"%23"])
async def test_ticket_does_not_open_a_route_whose_segment_hides_events(
    ticket: bytes, cut: bytes
) -> None:
    # El router enruta `order_id = "events?x"`; `request.url.path` daba
    # `/api/orders/orders/events` y el ticket pasaba (200).
    target = b"/api/orders/orders/events" + cut + b"x?ticket=" + ticket
    assert await _status(_app_like_main(), target) == 401


async def test_ticket_does_not_open_a_route_of_the_real_app(ticket: bytes) -> None:
    from src.main import app

    target = b"/api/dashboard/sessions/events%3Fx?ticket=" + ticket
    assert await _status(app, target) == 401


def _app_with_limit(limiter: SlidingWindowRateLimiter) -> FastAPI:
    app = FastAPI()
    # Misma exención que `main.py`.
    install_rate_limit_middleware(
        app, limiter, is_exempt=lambda path: path.endswith("/webhook")
    )

    @app.get("/api/webhook")
    def webhook() -> dict:
        return {}

    @app.get("/api/orders/orders/{order_id}")
    def order(order_id: str) -> dict:
        return {"order_id": order_id}

    return app


async def test_rate_limit_still_exempts_the_webhook() -> None:
    app = _app_with_limit(SlidingWindowRateLimiter(max_requests=1, window_seconds=60))
    assert [await _status(app, b"/api/webhook") for _ in range(3)] == [200, 200, 200]


@pytest.mark.parametrize("cut", [b"%3F", b"%23"])
async def test_rate_limit_does_not_exempt_a_route_whose_segment_hides_webhook(
    cut: bytes,
) -> None:
    app = _app_with_limit(SlidingWindowRateLimiter(max_requests=1, window_seconds=60))
    target = b"/api/orders/orders/webhook" + cut + b"x"
    # `request.url.path` daba `/api/orders/orders/webhook`: exenta, nunca 429.
    assert [await _status(app, target) for _ in range(2)] == [200, 429]
