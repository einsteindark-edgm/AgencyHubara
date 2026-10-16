"""Diario de efectos de la entrada durable (ADR 2026-10-15 §6).

El ingest de un mensaje corre en una activity con reintentos: si el worker muere
a mitad, el mensaje se ingiere de nuevo en el worker que lo reemplaza. Lo que ese
reintento NO puede repetir — la línea del cliente en el historial, el mensaje a
la sesión de ventas, un envío directo al cliente — queda marcado acá por
``wamid``: un archivo vacío por (mensaje, efecto), creado con ``O_EXCL``, bajo el
vault (sobrevive al proceso y al deploy).

Un directorio por día UTC. Un reintento se mide en minutos, así que se consultan
el día de hoy y el de ayer; los días más viejos se borran al abrir uno nuevo. La
re-entrega de Meta días después la frena antes ``REJECT_DUPLICATE`` en Temporal.

Best-effort, como el ledger: un disco roto se loguea y el ingest sigue (en el
peor caso un reintento repite el efecto; nunca se pierde el mensaje).
"""
from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger()

_DAY_MS = 86_400_000
#: Días que se conservan (hoy incluido). Ayer basta para un reintento que cruza
#: la medianoche UTC; el resto es margen para investigar.
_KEEP_DAYS = 3


def _day(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _marker_name(wamid: str, effect: str) -> str:
    return hashlib.sha256(f"{wamid}\x00{effect}".encode()).hexdigest()[:40]


class FilesystemInboundEffects:
    def __init__(self, root: Path, *, now_ms: Callable[[], int]) -> None:
        self._root = root
        self._now_ms = now_ms

    def claim(self, wamid: str, effect: str) -> bool:
        """Reclama el efecto ANTES de hacerlo: ``True`` solo la primera vez
        (lo hace quien reclama). Sin ``wamid`` no hay con qué recordar: siempre
        ``True``."""
        if not wamid:
            return True
        if self.done(wamid, effect):
            return False
        return self._create(wamid, effect)

    def done(self, wamid: str, effect: str) -> bool:
        """¿Ya se hizo (o se reclamó) este efecto para este mensaje?"""
        if not wamid:
            return False
        name = _marker_name(wamid, effect)
        now = self._now_ms()
        try:
            return any((self._root / _day(now - back * _DAY_MS) / name).exists() for back in (0, 1))
        except OSError as exc:
            logger.error("inbound_effects_read_failed", effect=effect, error=str(exc))
            return False

    def mark(self, wamid: str, effect: str) -> None:
        """Deja constancia DESPUÉS de hacer el efecto."""
        if wamid and not self.done(wamid, effect):
            self._create(wamid, effect)

    def _create(self, wamid: str, effect: str) -> bool:
        today = self._root / _day(self._now_ms())
        try:
            if not today.is_dir():
                today.mkdir(parents=True, exist_ok=True)
                self._prune(keep=today.name)
            fd = os.open(today / _marker_name(wamid, effect), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            return True
        except FileExistsError:
            return False
        except OSError as exc:
            logger.error("inbound_effects_write_failed", effect=effect, error=str(exc))
            return True

    def _prune(self, *, keep: str) -> None:
        oldest = _day(self._now_ms() - (_KEEP_DAYS - 1) * _DAY_MS)
        for day_dir in self._root.iterdir():
            if day_dir.is_dir() and day_dir.name < oldest and day_dir.name != keep:
                shutil.rmtree(day_dir, ignore_errors=True)
