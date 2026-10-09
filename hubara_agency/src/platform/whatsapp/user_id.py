"""Clientes de WhatsApp SIN teléfono: el id de Meta (BSUID) como dirección.

Cuando un cliente activa su nombre de usuario de WhatsApp, Meta omite su
teléfono en el webhook (`messages[].from`, `contacts[].wa_id`) mientras el
negocio no haya hablado con él en los últimos 30 días — justo el caso de un
lead nuevo de anuncio. Solo llega `from_user_id` (el *business-scoped user
ID*: `CO.9990000000000002` = país ISO, punto, hasta 128 alfanuméricos), y para
contestarle se usa el campo `recipient` en vez de `to`.

Una conversación de WhatsApp es `wa_<dirección>` y la dirección nombra un
directorio del vault, donde un `.` no se admite (`is_vault_session_id`). Así
que la dirección de estos clientes es el BSUID SIN el punto
(`CO9990000000000002`): empieza con letras, nunca se confunde con un
teléfono (solo dígitos) y se reconstruye exacto (el país son siempre 2 letras).

Ledger 2026-09-25 y 2026-10-09: el parser exigía `from` y estos clientes
nunca llegaban al bot (Halloween 07–08 oct: 3 de 8 conversaciones de Meta).
"""
from __future__ import annotations

import re
from typing import Any

from src.platform.constants import WHATSAPP_SESSION_PREFIX

_USER_ID_RE = re.compile(r"([A-Z]{2})\.([A-Za-z0-9]{1,128})")
_USER_ID_ADDRESS_RE = re.compile(r"[A-Z]{2}[A-Za-z0-9]{1,128}")
_PHONE_RE = re.compile(r"\d{8,15}")


def address_from_user_id(user_id: Any) -> str | None:
    """`CO.9990000000000002` → `CO9990000000000002`; `None` si no es un BSUID
    de quien escribe (un id "padre" `CO.ENT.…`, un traversal, etc.)."""
    if not isinstance(user_id, str):
        return None
    match = _USER_ID_RE.fullmatch(user_id)
    return f"{match.group(1)}{match.group(2)}" if match else None


def is_user_id_address(address: str) -> bool:
    """La dirección es un BSUID (no un teléfono)."""
    return _USER_ID_ADDRESS_RE.fullmatch(address) is not None


def meta_recipient(address: str) -> dict[str, str]:
    """El campo de destinatario de la Cloud API para una dirección: `to` con
    el teléfono, `recipient` con el BSUID (con su punto)."""
    if is_user_id_address(address):
        return {"recipient": f"{address[:2]}.{address[2:]}"}
    return {"to": address}


def is_customer_session_id(session_id: str) -> bool:
    """`wa_<teléfono>` o `wa_<BSUID sin punto>`: una conversación de un
    cliente real (no un id de prueba ni otro directorio del vault)."""
    if not session_id.startswith(WHATSAPP_SESSION_PREFIX):
        return False
    address = session_id[len(WHATSAPP_SESSION_PREFIX):]
    return _PHONE_RE.fullmatch(address) is not None or is_user_id_address(address)
