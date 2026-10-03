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
DATA = "[dato]"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Candidato a teléfono: dígitos con espacios, puntos, guiones o paréntesis.
# Se confirma contando dígitos (7 a 15) y descartando precios (`$`).
_PHONE_CANDIDATE_RE = re.compile(r"\+?\d[\d\s().-]{5,}\d")
# Vía + número + (`#`/`No.` opcional) + número + (`-` o espacio) + número:
# "Calle 45 # 12-30", "Cra 7 No. 32-16", y como lo escribe mucha gente,
# "cra 7 45-12" o "Cra 7 12 34".
_ADDRESS_RE = re.compile(
    r"\b(?:calle|cll|cl|carrera|cra|kra|kr|cr|avenida|av|diagonal|dg|transversal|trans|tv)\.?\s*"
    r"\d+\s*[a-z]?\b[^,\n]{0,25}?(?:(?:#|no\.?|n°|nro\.?)\s*)?\d+\s*[a-z]?\s*(?:-|\s)\s*\d+",
    re.IGNORECASE,
)
# Nombres que el cliente dice de sí mismo o de quien recibe: la frase que los
# anuncia (sin distinguir mayúsculas) y de 1 a 4 palabras con mayúscula inicial.
# "soy …" no cuenta: casi siempre es una ciudad ("soy de Medellín").
_NAME_WORD = r"[A-ZÁÉÍÓÚÑ][a-záéíóúüñ]+"
_NAMED_RE = re.compile(
    r"(?P<lead>\b(?i:me llamo|mi nombre es|a nombre de|(?:quien\s+)?(?:(?:lo|la|los|las)\s+)?recibe(?:\s+es)?))"
    rf"\s+{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}}"
)
# Campos personales de un formulario (`k=v; k=v`, p. ej. el Flow de envío):
# se tapa el valor; la ciudad y el resto quedan.
_PERSONAL_FIELD_RE = re.compile(
    r"(?P<key>\b\w{0,30}?(?:nombre|apellido|direcci[oó]n|barrio|tel[eé]fono|celular|c[eé]dula|documento|correo|email|recibe)\w{0,30})"
    r"=[^;\n]*",
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
    """Texto sin teléfonos, correos, direcciones, campos personales de
    formularios, nombres anunciados ("me llamo …") ni los nombres de `redact`."""
    out = _PERSONAL_FIELD_RE.sub(lambda m: f"{m.group('key')}={DATA}", text or "")
    out = _EMAIL_RE.sub(EMAIL, out)
    out = _ADDRESS_RE.sub(ADDRESS, out)
    out = _NAMED_RE.sub(lambda m: f"{m.group('lead')} {NAME}", out)
    out = _PHONE_CANDIDATE_RE.sub(_mask_phone, out)
    for name in sorted({n.strip() for n in redact if n and n.strip()}, key=len, reverse=True):
        out = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", NAME, out, flags=re.IGNORECASE)
    return out
