"""Redacción de PII — puro, sin dependencias de deepeval.

Las conversaciones son de clientes reales (teléfonos, emails, cédulas). Antes de
(a) mandarlas al LLM-juez y (b) escribir un candidato a golden en el repo (git),
se redactan los identificadores obvios. Es best-effort + determinista; el curador
humano hace la redacción final antes de promover un candidato a `curated.json`.

NO intenta NER de nombres (requiere modelo); cubre lo mecánico y de alta señal:
teléfonos, emails, secuencias largas de dígitos (cédula/NIT), URLs con query.
"""
from __future__ import annotations

import re

# Email: razonablemente conservador.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Teléfono Colombia y genéricos: +57..., con separadores. Exige >= 7 dígitos
# reales para no comerse precios cortos. El lookahead cuenta dígitos.
_PHONE_RE = re.compile(
    r"(?<![\w$])\+?(?:\d[\s().\-]?){7,15}\d(?![\w])"
)

# Secuencia de >= 7 dígitos seguidos (cédula/NIT/orden) que no es precio con
# separador de miles. Se aplica DESPUÉS de teléfono.
_LONG_DIGITS_RE = re.compile(r"(?<![\w.,$])\d{7,}(?![\w])")

# URL con querystring (puede llevar tokens/PII). Solo redacta el query.
_URL_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?[^\s]+")


def redact_pii(text: str) -> str:
    """Devuelve `text` con PII obvia reemplazada por placeholders estables.

    Idempotente: re-redactar un texto ya redactado no cambia nada (los
    placeholders `<...>` no matchean los patrones).
    """
    if not text:
        return text
    text = _EMAIL_RE.sub("<EMAIL>", text)
    text = _URL_QUERY_RE.sub(r"\1?<REDACTED>", text)
    text = _PHONE_RE.sub("<PHONE>", text)
    text = _LONG_DIGITS_RE.sub("<ID>", text)
    return text


def redact_turn_content(role: str, content: str) -> str:
    """Redacta el `content` de un turno. El `role` no lleva PII; se pasa por
    simetría con el reconstructor (y por si en el futuro se redacta distinto
    según quién habla)."""
    return redact_pii(content)
