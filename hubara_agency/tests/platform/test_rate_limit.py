"""SEC-13: rate limiter in-memory (sliding window por clave, reloj inyectable)."""
from __future__ import annotations

from src.platform.rate_limit import SlidingWindowRateLimiter


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_allows_up_to_limit_then_blocks() -> None:
    clock = _Clock()
    rl = SlidingWindowRateLimiter(max_requests=3, window_seconds=60, clock=clock)
    assert rl.allow("ip-1") is True
    assert rl.allow("ip-1") is True
    assert rl.allow("ip-1") is True
    assert rl.allow("ip-1") is False  # 4to en la ventana → bloqueado


def test_window_slides_and_frees_capacity() -> None:
    clock = _Clock()
    rl = SlidingWindowRateLimiter(max_requests=2, window_seconds=60, clock=clock)
    assert rl.allow("ip-1") is True
    assert rl.allow("ip-1") is True
    assert rl.allow("ip-1") is False
    clock.t = 61  # pasó la ventana → los timestamps viejos se podan
    assert rl.allow("ip-1") is True


def test_keys_are_independent() -> None:
    clock = _Clock()
    rl = SlidingWindowRateLimiter(max_requests=1, window_seconds=60, clock=clock)
    assert rl.allow("ip-1") is True
    assert rl.allow("ip-2") is True  # otra clave, cupo propio
    assert rl.allow("ip-1") is False


def test_zero_or_negative_limit_always_allows() -> None:
    rl = SlidingWindowRateLimiter(max_requests=0, window_seconds=60)
    assert all(rl.allow("ip-1") for _ in range(100)) is True


def _app_with_limiter(limiter):
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    from src.platform.rate_limit import install_rate_limit_middleware

    app = FastAPI()
    install_rate_limit_middleware(
        app, limiter, is_exempt=lambda path: path.endswith("/webhook")
    )

    @app.get("/api/thing")
    def thing():  # noqa: ANN202
        return PlainTextResponse("ok")

    @app.get("/api/webhook")
    def webhook():  # noqa: ANN202
        return PlainTextResponse("ok")

    return app


def test_middleware_returns_429_over_limit() -> None:
    from fastapi.testclient import TestClient

    rl = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
    client = TestClient(_app_with_limiter(rl))
    assert client.get("/api/thing").status_code == 200
    assert client.get("/api/thing").status_code == 200
    assert client.get("/api/thing").status_code == 429  # 3ro excede


def test_middleware_exempts_webhook() -> None:
    from fastapi.testclient import TestClient

    rl = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    client = TestClient(_app_with_limiter(rl))
    # el webhook nunca es limitado (Meta manda ráfagas legítimas + HMAC)
    for _ in range(5):
        assert client.get("/api/webhook").status_code == 200
