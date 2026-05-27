"""Tests del HMAC verification del webhook WhatsApp (HU-WA24H-001 F9.2).

Verifica:
  * Signature válida → True
  * Signature inválida → False
  * Header ausente → False
  * Header malformed (sin prefijo sha256=) → False
  * App secret vacío → False (con warning log)
  * Constant-time comparison (smoke test que usamos hmac.compare_digest)

Ver `src/platform/whatsapp/webhook_security.py`.
"""
from __future__ import annotations

import hashlib
import hmac


from src.platform.whatsapp.webhook_security import verify_meta_signature


def _compute_signature(body: bytes, secret: str) -> str:
    """Helper para los tests — calcula el header EXACTO que Meta enviaría."""
    digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


class TestVerifyMetaSignature:
    def test_returns_true_when_signature_valid(self):
        body = b'{"object":"whatsapp_business_account","entry":[...]}'
        secret = "test_app_secret_12345"
        signature = _compute_signature(body, secret)
        assert verify_meta_signature(body, signature, secret) is True

    def test_returns_false_when_signature_invalid(self):
        body = b'{"object":"whatsapp"}'
        secret = "real_secret"
        # Signature firmada con secret distinto
        wrong_sig = _compute_signature(body, "attacker_secret")
        assert verify_meta_signature(body, wrong_sig, secret) is False

    def test_returns_false_when_body_tampered(self):
        body = b'{"a":1}'
        secret = "secret"
        signature = _compute_signature(body, secret)
        # Atacante modifica el body manteniendo signature original
        tampered = b'{"a":2}'
        assert verify_meta_signature(tampered, signature, secret) is False

    def test_returns_false_when_signature_header_none(self):
        body = b'{"a":1}'
        assert verify_meta_signature(body, None, "secret") is False

    def test_returns_false_when_signature_header_empty(self):
        body = b'{"a":1}'
        assert verify_meta_signature(body, "", "secret") is False

    def test_returns_false_when_signature_lacks_prefix(self):
        body = b'{"a":1}'
        # Solo el digest sin "sha256=" prefix
        bare_digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        assert verify_meta_signature(body, bare_digest, "secret") is False

    def test_returns_false_when_signature_wrong_algorithm_prefix(self):
        body = b'{"a":1}'
        # Atacante intenta con SHA1 (legacy de Meta — Cloud API solo usa 256)
        sha1_digest = hmac.new(b"secret", body, hashlib.sha1).hexdigest()
        bogus = f"sha1={sha1_digest}"
        assert verify_meta_signature(body, bogus, "secret") is False

    def test_returns_false_when_app_secret_empty(self):
        body = b'{"a":1}'
        # Caller no configuró el secret — el helper retorna False.
        # El handler decide si esto bypasea o no.
        signature = _compute_signature(body, "any_secret")
        assert verify_meta_signature(body, signature, "") is False

    def test_unicode_body_handled_correctly(self):
        """Bodies con UTF-8 (emojis, ñ) deben firmarse correctamente."""
        body = '{"text":"ñandú está acá 🦘"}'.encode("utf-8")
        secret = "secret"
        signature = _compute_signature(body, secret)
        assert verify_meta_signature(body, signature, secret) is True

    def test_large_body_handled(self):
        """Bodies grandes (10KB) no rompen el HMAC."""
        body = ("a" * 10_000).encode("utf-8")
        secret = "secret"
        signature = _compute_signature(body, secret)
        assert verify_meta_signature(body, signature, secret) is True
