"""SEC-06: require_auth acepta un ticket SSE por query, SOLO en el path /events."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.platform import auth, config, sse_ticket


def _req(path: str, ticket: str | None = None, bearer: str | None = None):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    qp = {"ticket": ticket} if ticket is not None else {}
    return SimpleNamespace(
        headers=headers, query_params=qp, url=SimpleNamespace(path=path)
    )


@pytest.fixture
def _cognito_on(monkeypatch):
    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "pool-1")
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "client-1")
    monkeypatch.setattr(
        auth,
        "_verify_token",
        lambda t: (_ for _ in ()).throw(ValueError("jwt inválido")),
    )


def _mint(monkeypatch) -> str:
    monkeypatch.setattr(auth, "_sse_ticket_secret", lambda: "sse-secret")
    # require_auth verifica con time.time() real, así que minteamos con `now`
    # actual + ttl amplio para que no venza durante el test.
    return sse_ticket.mint("sse-secret", ttl_seconds=3600, now=time.time())


def test_valid_ticket_on_events_path_passes(_cognito_on, monkeypatch):
    ticket = _mint(monkeypatch)
    auth.require_auth(_req("/api/dashboard/events", ticket=ticket))  # no levanta


def test_valid_ticket_off_events_path_is_ignored(_cognito_on, monkeypatch):
    ticket = _mint(monkeypatch)
    # el ticket NO autentica rutas que no sean el stream de eventos
    with pytest.raises(HTTPException) as exc:
        auth.require_auth(_req("/api/dashboard/sessions", ticket=ticket))
    assert exc.value.status_code == 401


def test_invalid_ticket_on_events_path_falls_through(_cognito_on, monkeypatch):
    monkeypatch.setattr(auth, "_sse_ticket_secret", lambda: "sse-secret")
    with pytest.raises(HTTPException) as exc:
        auth.require_auth(_req("/api/dashboard/events", ticket="garbage.sig"))
    assert exc.value.status_code == 401
