"""Webhook signature verification para WhatsApp Cloud API.

HU-WA24H-001 pre-mortem F9.2: Meta firma cada webhook POST con HMAC-SHA256
usando el App Secret de la app Meta. El header viene en
``X-Hub-Signature-256: sha256=<hex_digest>``. Sin verificar esto, cualquier
atacante puede POST al endpoint y:

  * Inyectar fake delivery statuses → corrompe cost metrics.
  * Inyectar fake messages → arranca workflows fantasma + consume LLM tokens.

Implementación: comparación constant-time vía ``hmac.compare_digest``.

R-STATELESS: módulo sin module-level cache. El ``app_secret`` se inyecta
por argumento desde el handler (que lo lee del config en cada request).
Permite tests sin mockear env vars globales y rotación del secret
post-deploy sin restart.

Referencias:
  * https://developers.facebook.com/docs/messenger-platform/webhooks/#security
  * Meta WhatsApp Webhooks signature docs.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

log = logging.getLogger(__name__)


#: Prefijo obligatorio del header X-Hub-Signature-256. Meta siempre lo
#: incluye con este formato. Si no aparece → header malformed.
_SIGNATURE_PREFIX: str = "sha256="


def verify_meta_signature(
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str,
) -> bool:
    """Verifica que ``raw_body`` esté firmado por Meta con ``app_secret``.

    Args:
        raw_body: el body crudo del request HTTP (BYTES, NO el dict parseado —
            JSON parsing introduce normalizaciones que rompen el hash).
        signature_header: valor del header ``X-Hub-Signature-256`` o None si
            el request no lo trajo.
        app_secret: el App Secret de la app Meta (de Meta Business Manager).

    Returns:
        True si la signature es válida.
        False si:
          * ``app_secret`` está vacío (modo dev/local — el caller decide
            si bypasear o rechazar; este helper NO toma esa decisión).
          * ``signature_header`` es None o no empieza con ``sha256=``.
          * El hex digest no matchea.

    NOTA: el caller (handler HTTP) decide qué hacer con un False:
      * Pre-launch: rechazar con 403.
      * Dev local con ``app_secret=""``: bypasear con warning + log.

    Constant-time comparison via ``hmac.compare_digest`` para resistir
    timing attacks (atacante que enumera signatures válidas observando el
    tiempo de respuesta).
    """
    if not app_secret:
        log.warning(
            "webhook_signature_verification_skipped",
            extra={"reason": "WHATSAPP_APP_SECRET not configured"},
        )
        return False

    if not signature_header:
        log.warning(
            "webhook_signature_missing",
            extra={"reason": "X-Hub-Signature-256 header absent"},
        )
        return False

    if not signature_header.startswith(_SIGNATURE_PREFIX):
        log.warning(
            "webhook_signature_malformed",
            extra={"header_value": signature_header[:30]},
        )
        return False

    received_digest = signature_header[len(_SIGNATURE_PREFIX):]

    expected_digest = hmac.new(
        key=app_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(received_digest, expected_digest):
        log.warning(
            "webhook_signature_invalid",
            extra={
                "received_prefix": received_digest[:8],
                "expected_prefix": expected_digest[:8],
            },
        )
        return False

    return True
