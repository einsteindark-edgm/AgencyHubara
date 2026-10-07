"""Avisos push a la App Operador: el corrientazo que la despierta aunque esté cerrada.

El backend ya sabe cuándo cambia algo: el muestreador del vault publica
`chats` y `orders` en el bus del dashboard. Con cada cambio —y cada minuto,
porque un chat se vuelve grave solo por esperar— el despachador mira lo mismo
que la app (los incendios de `/mobile/fires` y las ventas calientes de
`/mobile/hot`) y despierta a TODOS los teléfonos registrados:

* un incendio GRAVE que no había avisado → push urgente (Android HIGH:
  despierta un teléfono en reposo);
* cambiaron las ventas calientes del widget → push normal, como mucho uno
  cada `HOT_MIN_GAP_MS`.

El push no lleva datos de clientes —pasa por Google—: solo
`{"type": "sync", "reason": "fire" | "hot"}`. La app lee su backend con
sesión, actualiza el widget y arma el aviso. Si un push se pierde, el vigía de
la app (cada 15 min) lo cubre; si llega de más, la app no repite avisos
(`notified_fires`). Un token que Firebase ya no reconoce se borra del registro.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from loguru import logger

from src.plugins.chats.api.mobile_devices import forget_tokens, registered_tokens
from src.sdk.connectorkit import PushMessage, PushOutcome
from src.sdk.dashboardkit import DashboardEvent

#: Junta la ráfaga de cambios de un turno (varios eventos seguidos) en una sola mirada.
DEBOUNCE_S = 3.0
#: Lo menos entre dos miradas: cada una lee todas las conversaciones del vault.
MIN_TICK_GAP_S = 10.0
#: Sin cambios, mira igual cada minuto: un chat pasa a grave solo por esperar.
PERIODIC_S = 60.0
#: Lo menos entre dos pushes del widget.
HOT_MIN_GAP_MS = 120_000

FIRE_MESSAGE = PushMessage(data={"type": "sync", "reason": "fire"}, urgent=True, collapse_key="fires", ttl_s=600)
HOT_MESSAGE = PushMessage(data={"type": "sync", "reason": "hot"}, urgent=False, collapse_key="hot", ttl_s=3600)
TEST_MESSAGE = PushMessage(data={"type": "test"}, urgent=True, ttl_s=300)

#: Los dominios del bus que pueden cambiar incendios o ventas calientes.
WAKE_DOMAINS = frozenset({"chats", "orders"})

#: Lo que el widget muestra de cada venta (sin `updated_ms`: el paso del tiempo no es un cambio).
_HOT_FIELDS = ("session_id", "name", "stage", "product", "cart_value_cop", "risk")


def _hot_signature(hot: Sequence[dict[str, Any]]) -> str:
    return json.dumps([[sale.get(k) for k in _HOT_FIELDS] for sale in hot], ensure_ascii=False, default=str)


class PushDispatcher:
    """Uno por proceso del API (lo arranca el startup de `mobile.py` si hay Firebase)."""

    def __init__(
        self,
        *,
        port: Any,
        vault_dir: Path,
        fires: Callable[[], Awaitable[list[dict[str, Any]]]],
        hot: Callable[[], Awaitable[list[dict[str, Any]]]],
        now_ms: Callable[[], int],
    ) -> None:
        self._port = port
        self._vault = vault_dir
        self._fires = fires
        self._hot = hot
        self._now_ms = now_ms
        #: Los graves que ya despertaron a los teléfonos. Si uno sale y vuelve, avisa otra vez.
        self._graves: frozenset[str] = frozenset()
        self._hot_sent = _hot_signature([])
        self._hot_sent_ms: int | None = None
        self._hot_pending = False
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        #: Llegó un cambio que todavía no se miró.
        self.woken = False

    # ── el bus ──

    def on_event(self, event: DashboardEvent) -> None:
        """Listener síncrono del bus del dashboard: solo anota que hay que mirar."""
        if event.domain not in WAKE_DOMAINS:
            return
        self.woken = True
        if self._wake is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    # ── una mirada ──

    async def tick(self) -> None:
        self.woken = False
        tokens = registered_tokens(self._vault)
        if not tokens:
            # Nadie a quien despertar: ni se leen los incendios, y el que se registre después recibe lo abierto.
            self._graves = frozenset()
            return
        cards = await self._fires()
        graves = frozenset(str(c.get("fire_id")) for c in cards if c.get("severity") == "grave")
        fresh, self._graves = graves - self._graves, graves
        if fresh:
            await self._send(tokens, FIRE_MESSAGE)
        signature = _hot_signature(await self._hot())
        if signature == self._hot_sent:
            self._hot_pending = False
        elif self._hot_sent_ms is not None and self._now_ms() - self._hot_sent_ms < HOT_MIN_GAP_MS:
            self._hot_pending = True  # sale apenas se cumpla el plazo (`seconds_until_due`)
        else:
            await self._send(tokens, HOT_MESSAGE)
            self._hot_sent, self._hot_sent_ms, self._hot_pending = signature, self._now_ms(), False

    def seconds_until_due(self) -> float:
        """Cuánto esperar la próxima mirada si no llega ningún cambio."""
        wait = PERIODIC_S
        if self._hot_pending and self._hot_sent_ms is not None:
            wait = min(wait, max(0.0, (self._hot_sent_ms + HOT_MIN_GAP_MS - self._now_ms()) / 1000))
        return wait

    async def _send(self, tokens: Iterable[str], message: PushMessage) -> None:
        dead = [t for t in tokens if await self._port.send(t, message) == PushOutcome.UNREGISTERED]
        if dead:
            forget_tokens(self._vault, dead)
            logger.info("[chats.push] {} token(s) que Firebase ya no reconoce: borrados", len(dead))

    # ── el ciclo ──

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        last_tick = 0.0
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.seconds_until_due())
                await asyncio.sleep(max(DEBOUNCE_S, last_tick + MIN_TICK_GAP_S - self._loop.time()))
            except TimeoutError:
                pass
            self._wake.clear()
            last_tick = self._loop.time()
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — una mirada mala (vault a medio escribir, Medusa caído) no lo mata
                logger.exception("[chats.push] la mirada falló")

    def start(self) -> asyncio.Task[None]:
        """Arranca el ciclo una vez (la referencia fuerte queda aquí: L-7)."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())
        return self._task
