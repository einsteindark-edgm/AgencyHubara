"""Guard del router público: token bucket por (IP, tool) con reloj inyectado."""
from __future__ import annotations

from src.plugins.mba.domain.guard import MAX_BODY_BYTES, RateLimiter


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_bucket_allows_burst_then_blocks_until_refill() -> None:
    clock = _Clock()
    rl = RateLimiter(capacity=3, refill_per_s=1.0, clock=clock)
    assert [rl.allow("ip:search") for _ in range(3)] == [True, True, True]
    assert rl.allow("ip:search") is False
    clock.t += 1.0  # un token repuesto
    assert rl.allow("ip:search") is True
    assert rl.allow("ip:search") is False
    clock.t += 60.0  # nunca más que la capacidad
    assert [rl.allow("ip:search") for _ in range(4)] == [True, True, True, False]


def test_keys_are_independent_and_memory_is_bounded() -> None:
    clock = _Clock()
    rl = RateLimiter(capacity=1, refill_per_s=1.0, clock=clock, max_keys=2)
    assert rl.allow("a") and rl.allow("b")
    assert rl.allow("a") is False
    rl.allow("c")  # tercera clave: la menos reciente (b) se descarta
    assert rl.size == 2
    assert rl.allow("b") is True  # b vuelve con bucket lleno (y desplaza a la siguiente)


def test_body_cap_is_small_enough_for_a_public_endpoint() -> None:
    assert 8 * 1024 <= MAX_BODY_BYTES <= 128 * 1024


def test_remaining_reads_without_consuming() -> None:
    clock = _Clock()
    rl = RateLimiter(capacity=2, refill_per_s=1.0, clock=clock)
    assert rl.remaining("k") == 2 and rl.remaining("k") == 2
    rl.allow("k")
    assert rl.remaining("k") == 1
    clock.t += 5
    assert rl.remaining("k") == 2
