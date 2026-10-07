"""Sin Firebase configurado: no manda nada y el teléfono no arranca Firebase."""
from __future__ import annotations

from src.platform.push.ports import FirebaseClientOptions, PushMessage, PushOutcome


class NullPushAdapter:
    name = "null"
    configured = False

    def client_options(self) -> FirebaseClientOptions | None:
        return None

    async def send(self, token: str, message: PushMessage) -> PushOutcome:
        return PushOutcome.NOT_CONFIGURED
