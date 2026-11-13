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
# "cra 7 45-12" o "Cra 7 12 34"; también "Carrer 7 #12_34" (visto en producción).
_ADDRESS_RE = re.compile(
    r"\b(?:calle|cll|cl|carrera|carrer|cra|kra|kr|cr|avenida|av|diagonal|dg|transversal|trans|tv)\.?\s*"
    r"\d+\s*[a-z]?\b[^,\n]{0,25}?(?:(?:#|no\.?|n°|nro\.?)\s*)?\d+\s*[a-z]?\s*(?:[-_]|\s)\s*\d+",
    re.IGNORECASE,
)
# Nombres que el cliente dice de sí mismo o de quien recibe: la frase que los
# anuncia (sin distinguir mayúsculas) y de 1 a 4 palabras con mayúscula inicial
# o escritas todas en mayúsculas ("a nombre de ANDRÉS MEJÍA").
# "soy …" no cuenta: casi siempre es una ciudad ("soy de Medellín").
_NAME_WORD = r"[A-ZÁÉÍÓÚÑ](?:[a-záéíóúüñ]+|[A-ZÁÉÍÓÚÜÑ]+)"
_NAMED_RE = re.compile(
    r"(?P<lead>\b(?i:me llamo|mi nombre es|a nombre de|(?:quien\s+)?(?:(?:lo|la|los|las)\s+)?recibe(?:\s+es)?))"
    rf"\s+{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}}"
)
# Campos personales de un formulario (`k=v; k=v`, p. ej. el Flow de envío):
# se tapa el valor; la ciudad y el resto quedan. El Flow de WhatsApp responde
# con sus propias llaves en inglés (`receiver_name`, `address`, `phone`,
# `national_id`, `neighborhood`), no con las del borrador: también se tapan.
_PERSONAL_FIELD_RE = re.compile(
    r"(?P<key>\b(?:\w{0,30}?(?:nombre|apellido|direcci[oó]n|barrio|tel[eé]fono|celular|c[eé]dula|documento|correo|email|recibe)\w{0,30}"
    r"|(?:receiver|recipient|customer|contact|full|first|last)_name|name|address\w{0,10}|neighbou?rhood"
    r"|phone\w{0,10}|mobile|national_id|id_number|document\w{0,10}))"
    r"=[^;\n]*",
    re.IGNORECASE,
)
# Fechas y horas ISO ("2026-11-05T12:44:30.123+00:00", "2026-11-05"): no son
# teléfonos. Se apartan antes de tapar y vuelven al final.
_ISO_DATETIME_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?(?!\d)"
)
# Marca de reemplazo de una fecha apartada: un carácter de uso privado, sin
# dígitos ni letras, para que ninguna regla de arriba la toque.
_SLOT_BASE = 0xE000
_SLOT_COUNT = 0x1900
_HAS_SLOT_RE = re.compile("[\ue000-\uf8ff]")


def _mask_phone(match: re.Match[str]) -> str:
    raw = match.group(0)
    digits = sum(ch.isdigit() for ch in raw)
    before = match.string[max(0, match.start() - 2) : match.start()]
    if not 7 <= digits <= 15 or "$" in before:
        return raw
    return PHONE


def anonymize_text(text: str, *, redact: Sequence[str] = ()) -> str:
    """Texto sin teléfonos, correos, direcciones, campos personales de
    formularios, nombres anunciados ("me llamo …") ni los nombres de `redact`.
    Las fechas y horas ISO quedan como estaban."""
    out = text or ""
    dates: list[str] = []

    def _set_aside(match: re.Match[str]) -> str:
        if len(dates) >= _SLOT_COUNT:
            return match.group(0)
        dates.append(match.group(0))
        return chr(_SLOT_BASE + len(dates) - 1)

    if not _HAS_SLOT_RE.search(out):
        out = _ISO_DATETIME_RE.sub(_set_aside, out)
    out = _PERSONAL_FIELD_RE.sub(lambda m: f"{m.group('key')}={DATA}", out)
    out = _EMAIL_RE.sub(EMAIL, out)
    out = _ADDRESS_RE.sub(ADDRESS, out)
    out = _NAMED_RE.sub(lambda m: f"{m.group('lead')} {NAME}", out)
    out = _PHONE_CANDIDATE_RE.sub(_mask_phone, out)
    for name in sorted({n.strip() for n in redact if n and n.strip()}, key=len, reverse=True):
        out = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", NAME, out, flags=re.IGNORECASE)
    for i, date in enumerate(dates):
        out = out.replace(chr(_SLOT_BASE + i), date)
    return out
