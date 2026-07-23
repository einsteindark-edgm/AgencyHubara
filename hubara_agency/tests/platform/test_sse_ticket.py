"""SEC-06: ticket SSE firmado y de corta vida (reemplaza el access-token en la URL)."""
from __future__ import annotations

from src.platform import sse_ticket


def test_mint_then_verify_ok() -> None:
    t = sse_ticket.mint("secreto", ttl_seconds=30, now=1000.0)
    assert sse_ticket.verify("secreto", t, now=1000.0) is True
    assert sse_ticket.verify("secreto", t, now=1029.0) is True  # dentro de la ventana


def test_expired_ticket_rejected() -> None:
    t = sse_ticket.mint("secreto", ttl_seconds=30, now=1000.0)
    assert sse_ticket.verify("secreto", t, now=1031.0) is False  # venció


def test_wrong_secret_rejected() -> None:
    t = sse_ticket.mint("secreto", ttl_seconds=30, now=1000.0)
    assert sse_ticket.verify("otro-secreto", t, now=1000.0) is False


def test_tampered_ticket_rejected() -> None:
    t = sse_ticket.mint("secreto", ttl_seconds=30, now=1000.0)
    # alterar el payload (adelantar el expiry) sin re-firmar → HMAC no matchea
    exp, _, sig = t.partition(".")
    forged = f"{int(exp) + 99999}.{sig}"
    assert sse_ticket.verify("secreto", forged, now=1000.0) is False


def test_garbage_ticket_rejected() -> None:
    for bad in ["", "sinpunto", "abc.def", "..", "999999"]:
        assert sse_ticket.verify("secreto", bad, now=1000.0) is False


def test_empty_secret_never_verifies() -> None:
    t = sse_ticket.mint("secreto", ttl_seconds=30, now=1000.0)
    assert sse_ticket.verify("", t, now=1000.0) is False
