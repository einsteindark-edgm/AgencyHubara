"""Adapter filesystem: qué conversación es de cada id de Meta (BSUID).

Un cliente con nombre de usuario llega primero SIN teléfono (conversación
`wa_CO1502…`) y, una vez que le contestamos, Meta empieza a mandar también su
teléfono. Sin recordar el BSUID, ese segundo mensaje abriría `wa_57…` y el bot
perdería el hilo. Al revés también: un cliente con teléfono que más adelante
llega solo con el BSUID sigue en su `wa_57…`.

Un archivo por BSUID (`<root>/<dirección del BSUID>` → la dirección de su
conversación) dentro del vault: sobrevive a deploys y `_identity/` no empieza
con `wa_`, así que ningún scanner de sesiones lo toma por conversación. El
primero que escribe gana (creación exclusiva): dos mensajes simultáneos del
mismo cliente terminan en la misma conversación. La creación es atómica
(archivo temporal con el contenido + `os.link`): nunca queda un archivo a
medio escribir, y un fallo de disco no tumba el webhook (revisión del PR:
ENOSPC dejaba un archivo vacío y el BSUID no se volvía a anclar).
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import structlog

from src.sdk.identitykit import is_customer_session_id

logger = structlog.get_logger()


class FilesystemSenderIdentity:
    def __init__(self, root: Path) -> None:
        self._root = root

    def known_address(self, user_address: str) -> str | None:
        """La dirección de la conversación de este BSUID, o `None` si nunca lo
        vimos, no se puede leer o lo leído no es una dirección de conversación
        (termina siendo un path del vault: se revalida). El mensaje igual entra."""
        try:
            value = (self._root / user_address).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value if is_customer_session_id(f"wa_{value}") else None

    def remember(self, user_address: str, address: str) -> str:
        """Fija la conversación de este BSUID si no tenía una, y devuelve la
        que quedó (la propia o la de quien escribió primero). Best-effort: un
        fallo de disco se loguea y el mensaje sigue con `address`."""
        known = self.known_address(user_address)
        if known is not None:
            return known
        final = self._root / user_address
        tmp = self._root / f".{user_address}.{uuid.uuid4().hex}.tmp"
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            tmp.write_text(address, encoding="utf-8")
            try:
                os.link(tmp, final)
            except FileExistsError:
                # Otro proceso ganó, o quedó un archivo inservible (vacío por un
                # crash de la versión anterior): manda el que sirva; si ninguno
                # sirve, este lo reemplaza.
                winner = self.known_address(user_address)
                if winner is not None:
                    return winner
                os.replace(tmp, final)
            return address
        except OSError as exc:
            logger.error("sender_identity_write_failed", error=str(exc))
            return address
        finally:
            tmp.unlink(missing_ok=True)
