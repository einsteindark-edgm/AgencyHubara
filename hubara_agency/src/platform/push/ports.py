"""Puerto de avisos push (Firebase Cloud Messaging) hacia la App Operador.

Un push es el «corrientazo»: despierta el teléfono para que la app lea su
backend (con sesión) y se ponga al día. NO lleva datos de clientes —pasa por
los servidores de Google—, solo qué hacer (`data = {"type": "sync", ...}`).

Contrato (lo prueba `tests/platform/push/test_push_contract.py` contra el
falso y contra FCM):

* `send` NUNCA lanza: devuelve un `PushOutcome`.
* `UNREGISTERED` = el token ya no sirve (app desinstalada, token rotado, otro
  proyecto de Firebase): quien llama lo borra del registro.
* `FAILED` = transitorio (Google no respondió, cuota): el siguiente aviso
  vuelve a intentar; nunca se borra un token por esto.
* `client_options()` = las opciones con las que el TELÉFONO arranca Firebase
  (las cuatro de `google-services.json`); None si no hay.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True)
class PushMessage:
    """Un aviso solo de datos. `data`: textos cortos, sin nada de clientes."""

    data: Mapping[str, str]
    #: Android HIGH: despierta un teléfono en reposo. Solo para lo que no puede esperar (un incendio grave).
    urgent: bool = False
    #: Con el teléfono apagado, FCM guarda solo el último aviso de cada clave.
    collapse_key: str | None = None
    #: Cuánto lo guarda FCM si el teléfono no está conectado.
    ttl_s: int = 3600


class PushOutcome(StrEnum):
    SENT = "sent"
    UNREGISTERED = "unregistered"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class FirebaseClientOptions:
    """Lo que el teléfono necesita para arrancar Firebase (no es secreto, pero no va en el repo: es público)."""

    project_id: str
    application_id: str
    api_key: str
    gcm_sender_id: str


class PushPort(Protocol):
    name: str

    @property
    def configured(self) -> bool:
        """True si puede mandar avisos (hay credenciales válidas)."""
        ...

    def client_options(self) -> FirebaseClientOptions | None: ...

    async def send(self, token: str, message: PushMessage) -> PushOutcome:
        """Manda un aviso a un teléfono. NUNCA lanza."""
        ...


__all__ = ["FirebaseClientOptions", "PushMessage", "PushOutcome", "PushPort"]
