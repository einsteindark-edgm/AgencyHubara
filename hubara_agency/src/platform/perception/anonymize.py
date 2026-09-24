"""Anonimización de una ráfaga antes de enviarla a un clasificador externo.

Decisión 2 del plan del laboratorio: las ráfagas salen a proveedores nuevos
(OpenRouter y TypeSafe) sin datos de contacto. Se tapan teléfonos, correos,
direcciones colombianas y los nombres que el llamador indique (el nombre del
perfil de WhatsApp, el de quien recibe el pedido). Lo que el clasificador
necesita para entender el asunto queda igual: productos, precios, fechas y
ciudades.

Puro y determinista (sin I/O): lo usan los adaptadores y el laboratorio.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

PHONE = "[teléfono]"
EMAIL = "[correo]"
ADDRESS = "[dirección]"
NAME = "[nombre]"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Candidato a teléfono: dígitos con espacios, puntos, guiones o paréntesis.
# Se confirma contando dígitos (7 a 15) y descartando precios (`$`).
_PHONE_CANDIDATE_RE = re.compile(r"\+?\d[\d\s().-]{5,}\d")
_ADDRESS_RE = re.compile(
    r"\b(?:calle|cll|cl|carrera|cra|kra|kr|cr|avenida|av|diagonal|dg|transversal|trans|tv)\.?\s*"
    r"\d+\s*[a-z]?\b[^,\n]{0,25}?(?:#|no\.?|n°|nro\.?)\s*\d+\s*[a-z]?\s*-\s*\d+",
    re.IGNORECASE,
)


def _mask_phone(match: re.Match[str]) -> str:
    raw = match.group(0)
    digits = sum(ch.isdigit() for ch in raw)
    before = match.string[max(0, match.start() - 2) : match.start()]
    if not 7 <= digits <= 15 or "$" in before:
        return raw
    return PHONE


def anonymize_text(text: str, *, redact: Sequence[str] = ()) -> str:
    """Texto sin teléfonos, correos, direcciones ni los nombres de `redact`."""
    out = _EMAIL_RE.sub(EMAIL, text or "")
    out = _ADDRESS_RE.sub(ADDRESS, out)
    out = _PHONE_CANDIDATE_RE.sub(_mask_phone, out)
    for name in sorted({n.strip() for n in redact if n and n.strip()}, key=len, reverse=True):
        out = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", NAME, out, flags=re.IGNORECASE)
    return out
