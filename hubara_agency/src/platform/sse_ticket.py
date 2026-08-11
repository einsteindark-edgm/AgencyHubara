"""Ticket SSE firmado, de corta vida — SEC-06.

El stream `/api/dashboard/events` no puede mandar el header Authorization (el
`EventSource` del browser no lo permite), así que antes se pasaba el access-token
de Cognito por `?access_token=` → quedaba en access logs / proxies / referrer.

En vez de eso el frontend pide un TICKET (POST autenticado por header) y lo pasa
por query. El ticket:
  * es opaco y de corta vida (~30s) → aunque se loguee, expira enseguida y NO
    sirve como bearer contra el resto de la API;
  * es stateless (HMAC firmado) → sin store compartido, multi-worker safe.

Formato: ``<expiry_epoch>.<hmac_hex>`` con hmac = HMAC-SHA256(secret, str(expiry)).
"""
from __future__ import annotations

import hashlib
import hmac


def _sign(secret: str, expiry: int) -> str:
    return hmac.new(
        secret.encode("utf-8"), str(expiry).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def mint(secret: str, ttl_seconds: int, now: float) -> str:
    """Firma un ticket que vence en ``now + ttl_seconds``."""
    expiry = int(now) + int(ttl_seconds)
    return f"{expiry}.{_sign(secret, expiry)}"


def verify(secret: str, ticket: str, now: float) -> bool:
    """True si el ticket está bien firmado y no venció. Secret vacío nunca verifica."""
    if not secret or not ticket:
        return False
    expiry_str, sep, sig = ticket.partition(".")
    if not sep or not sig or not expiry_str.isdigit():
        return False
    expiry = int(expiry_str)
    if now > expiry:
        return False
    return hmac.compare_digest(sig, _sign(secret, expiry))
