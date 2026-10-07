"""Avisos push de mentira: para pruebas y para el backend del emulador.

Guarda lo que mandaría (`sent`) y, con `outbox`, deja una línea JSON por aviso
en un archivo: el arnés del emulador la reenvía a la app como si fuera Firebase
(`android_operator/e2e`, escenario S20).
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path

from src.platform.push.ports import FirebaseClientOptions, PushMessage, PushOutcome


class FakePushAdapter:
    name = "fake"
    configured = True

    def __init__(
        self,
        *,
        options: FirebaseClientOptions | None = None,
        dead_tokens: Iterable[str] = (),
        fail: bool = False,
        outbox: Path | None = None,
    ) -> None:
        self._options = options
        self._dead = frozenset(dead_tokens)
        self._fail = fail
        self._outbox = outbox
        self.sent: list[tuple[str, PushMessage]] = []

    def client_options(self) -> FirebaseClientOptions | None:
        return self._options

    async def send(self, token: str, message: PushMessage) -> PushOutcome:
        if self._fail:
            return PushOutcome.FAILED
        if token in self._dead:
            return PushOutcome.UNREGISTERED
        self.sent.append((token, message))
        if self._outbox is not None:
            line = {
                "token": token,
                "data": dict(message.data),
                "urgent": message.urgent,
                "collapse_key": message.collapse_key,
                "ttl_s": message.ttl_s,
                "at_ms": int(time.time() * 1000),
            }
            self._outbox.parent.mkdir(parents=True, exist_ok=True)
            with self._outbox.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return PushOutcome.SENT
