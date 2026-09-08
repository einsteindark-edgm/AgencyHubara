"""D1.7 — cierre por silencio con MBA al frente: el watchdog (worker de
remarketing) NO puede importar la reconciliación de Sales (contrato
`agents-independent`), así que reusa el contrato HTTP `session-actions@v1`
de chats (`POST /tag` con INTERESADO: D1.3 reconcilia a CONFIRMADO_SIN_DATOS
+ escalación si hay datos de envío, descarta si hay orden) con identidad de
servicio, como hace el connector de mba.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi import HTTPException

from src.plugins.chats.agent.remarketing.activities.mba_silence_close import (
    SILENCE_MOTIVO,
    api_base_url,
    close_by_silence,
)

SESSION = "wa_573001234567"


def test_the_api_base_is_the_compose_service_name_unless_overridden(monkeypatch) -> None:
    monkeypatch.delenv("HUBARA_API_BASE_URL", raising=False)
    assert api_base_url() == "http://hubara-api:8000"
    monkeypatch.setenv("HUBARA_API_BASE_URL", "http://localhost:8000/")
    assert api_base_url() == "http://localhost:8000"


@respx.mock
async def test_close_by_silence_posts_an_interesado_proposal_with_the_service_identity(monkeypatch) -> None:
    from src.platform import config

    monkeypatch.setenv("HUBARA_API_BASE_URL", "http://api.test")
    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "svc-token")
    route = respx.post(f"http://api.test/api/chats/session-actions/{SESSION}/tag").mock(
        return_value=httpx.Response(200, json={"tag": "CONFIRMADO_SIN_DATOS", "proposed_tag": "INTERESADO",
                                               "applied": True, "reconciled": True,
                                               "reason": "shipping_data_without_order", "episode_closed": True,
                                               "escalated": True})
    )
    out = await close_by_silence(SESSION)
    assert out["tag"] == "CONFIRMADO_SIN_DATOS" and out["escalated"] is True
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer svc-token"
    assert json.loads(req.content) == {"tag": "INTERESADO", "motivo": SILENCE_MOTIVO}


@respx.mock
async def test_a_provider_error_surfaces_as_an_http_exception_for_the_caller_to_log(monkeypatch) -> None:
    monkeypatch.setenv("HUBARA_API_BASE_URL", "http://api.test")
    respx.post(f"http://api.test/api/chats/session-actions/{SESSION}/tag").mock(
        return_value=httpx.Response(409, json={"detail": {"error": "already_human"}})
    )
    with pytest.raises(HTTPException) as exc:
        await close_by_silence(SESSION)
    assert exc.value.status_code == 409
