"""SEC-02: el POST ``/api/webhook`` debe FAIL-CLOSED en producción.

El handler verifica el HMAC ``X-Hub-Signature-256`` de Meta sólo si
``WHATSAPP_APP_SECRET`` está seteado; si falta, hoy loguea un warning y procesa
igual (fail-OPEN). En prod eso deja al webhook abierto a inyección de
mensajes/statuses falsos (SECURITY_AUDIT_fable SEC-02). El comportamiento
exigido: en prod, secret ausente → RECHAZAR (403); en dev/local (sin app real
detrás) sigue procesando con warning para no romper el desarrollo con webhooks
simulados.

Los tests golpean el gate de firma ANTES del parsing/composición: un 403 corta
en seco, así que no necesitan que el use case real corra.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def main_app():
    from src.main import app

    return app


def test_webhook_rejects_when_secret_missing_in_production(
    main_app, monkeypatch
) -> None:
    """Prod + WHATSAPP_APP_SECRET vacío → 403 (no procesa sin verificar)."""
    from src.platform import config

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "production")
    client = TestClient(main_app)
    resp = client.post(
        "/api/webhook", json={"object": "whatsapp_business_account", "entry": []}
    )
    assert resp.status_code == 403


def test_webhook_processes_when_secret_missing_in_dev(
    main_app, monkeypatch
) -> None:
    """Dev/local + secret vacío → NO rechaza (procesa con warning)."""
    from src.platform import config

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "dev")
    client = TestClient(main_app)
    resp = client.post(
        "/api/webhook", json={"object": "whatsapp_business_account", "entry": []}
    )
    assert resp.status_code != 403


def test_placeholder_app_secret_treated_as_unconfigured_in_dev(
    main_app, monkeypatch
) -> None:
    """El placeholder de Terraform NO es un secreto real: debe tratarse como
    AUSENTE, no verificar el HMAC contra él. En dev → procesa (no 403 por un
    mismatch contra el placeholder). En prod iría al fail-closed (403 vía elif).
    """
    from src.platform import config

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", config.SSM_PLACEHOLDER)
    monkeypatch.setattr(config, "HUBARA_ENV", "dev")
    client = TestClient(main_app)
    resp = client.post(
        "/api/webhook",
        json={"object": "whatsapp_business_account", "entry": []},
        headers={"X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert resp.status_code != 403


def test_get_verify_empty_token_does_not_bypass(main_app, monkeypatch) -> None:
    """SEC-15: el GET de suscripción compara `hub.verify_token`. Con `==`, un
    verify token vacío + `hub.verify_token=""` pasaba (`"" == ""`). Debe rechazar.
    """
    from src.platform import config

    monkeypatch.setattr(config, "WHATSAPP_VERIFY_TOKEN", "")
    client = TestClient(main_app)
    resp = client.get(
        "/api/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "123"},
    )
    assert resp.status_code == 403


def test_get_verify_returns_challenge_on_correct_token(
    main_app, monkeypatch
) -> None:
    """Token correcto → devuelve el challenge (comportamiento preservado)."""
    from src.platform import config

    monkeypatch.setattr(config, "WHATSAPP_VERIFY_TOKEN", "el-verify-token")
    client = TestClient(main_app)
    resp = client.get(
        "/api/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "el-verify-token",
            "hub.challenge": "424242",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == 424242


def test_webhook_rejects_bad_signature_when_secret_present(
    main_app, monkeypatch
) -> None:
    """Con secret configurado, firma inválida → 403 (comportamiento existente,
    guard de que el nuevo branch no lo altera)."""
    from src.platform import config

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "the-app-secret")
    monkeypatch.setattr(config, "HUBARA_ENV", "production")
    client = TestClient(main_app)
    resp = client.post(
        "/api/webhook",
        json={"object": "whatsapp_business_account", "entry": []},
        headers={"X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert resp.status_code == 403
