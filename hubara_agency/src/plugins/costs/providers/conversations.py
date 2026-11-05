"""Costs already recorded in each conversation — DeepSeek, Jev and Gemini.

Every WhatsApp conversation of the production vault keeps, per episode, what
its calls cost (``<vault>/wa_*/metadata.json`` → ``episodes[]``):

* ``llm_usage.cost_usd`` — the bot's own LLM (DeepSeek), sales and
  remarketing turns (written by ``record_episode_llm_usage``);
* ``jev_usage`` — the questions to Jev, the decision engine (OpenRouter's
  ``usage.cost`` of each call);
* ``vision_usage`` / ``audio_usage`` — Gemini reading the customer's photos and
  transcribing voice notes.

``jev_usage``/``vision_usage``/``audio_usage`` are ``{calls, cost_usd_micros}``
(see ``platform/observability/episode_usage.py``). Nothing carries a per-call
timestamp, so a conversation's whole cost lands on the Bogotá day it started.

This reads the vault the way the production API already sees it: raw JSON,
one session at a time (only the sums stay in memory — the Ads scan that kept
every parsed session froze the 4 GB box once), skipping sessions whose
metadata was not written since the range started (writing an episode always
rewrites the file). Local labs and simulations never land here, so these are
production numbers by construction.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from src.plugins.costs.domain.period import BOGOTA, DateRange
from src.plugins.costs.domain.report import CostLine, ProviderReport, Source
from src.sdk.runtime import BoundedTTLCache, is_vault_session_id

#: Cost kinds read from each episode.
KINDS = ("llm", "jev", "vision", "audio")
_MICROS_FIELDS = {"jev": "jev_usage", "vision": "vision_usage", "audio": "audio_usage"}


@dataclass
class ConversationCosts:
    """Sums of the recorded costs of the conversations that started in a range."""

    #: kind → Bogotá day → USD micros.
    daily: dict[str, dict[date, int]] = field(default_factory=lambda: {k: {} for k in KINDS})
    #: kind → tokens (llm) or calls (the rest).
    quantity: dict[str, int] = field(default_factory=lambda: {k: 0 for k in KINDS})
    conversations: int = 0
    sessions_read: int = 0
    skipped_files: int = 0
    vault_found: bool = True

    def add(self, kind: str, day: date, micros: int, quantity: int) -> None:
        if micros > 0:
            by_day = self.daily[kind]
            by_day[day] = by_day.get(day, 0) + micros
        self.quantity[kind] += max(quantity, 0)


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _usd_micros(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        micros = (Decimal(str(value)) * 1_000_000).to_integral_value()
    except InvalidOperation:
        return 0
    return max(0, int(micros))


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _add_episode(costs: ConversationCosts, episode: dict[str, Any], day: date) -> None:
    llm = episode.get("llm_usage")
    if isinstance(llm, dict):
        costs.add("llm", day, _usd_micros(llm.get("cost_usd")), _count(llm.get("total_tokens")))
    for kind, key in _MICROS_FIELDS.items():
        usage = episode.get(key)
        if isinstance(usage, dict):
            costs.add(kind, day, _count(usage.get("cost_usd_micros")), _count(usage.get("calls")))


def scan_conversation_costs(vault_dir: Path, rng: DateRange) -> ConversationCosts:
    """Blocking (file I/O): run it in a thread from async code."""
    costs = ConversationCosts()
    if not vault_dir.is_dir():
        costs.vault_found = False
        return costs
    start_ms = int(rng.utc_start.timestamp() * 1000)
    end_ms = int(rng.utc_end_exclusive.timestamp() * 1000)
    for entry in vault_dir.iterdir():
        if not entry.name.startswith("wa_") or not is_vault_session_id(entry.name):
            continue
        path = entry / "metadata.json"
        try:
            if int(path.stat().st_mtime * 1000) < start_ms:
                continue
        except OSError:
            continue
        metadata = _read(path)
        if metadata is None:
            costs.skipped_files += 1
            continue
        costs.sessions_read += 1
        for episode in metadata.get("episodes") or []:
            if not isinstance(episode, dict):
                continue
            started = episode.get("started_at_ms")
            if isinstance(started, bool) or not isinstance(started, int):
                continue
            if not start_ms <= started < end_ms:
                continue
            costs.conversations += 1
            day = datetime.fromtimestamp(started / 1000, BOGOTA).date()
            _add_episode(costs, episode, day)
    return costs



# ── The shared read and the three cards ──────────────────────────────────────

Scan = Callable[[Path, DateRange], ConversationCosts]

#: The three cards of one page load ask for the same range within seconds.
_SHARE_TTL_S = 60.0

_NO_VAULT = (
    "Este entorno no tiene conversaciones guardadas (la carpeta del vault no existe), "
    "así que no hay costos por conversación que sumar."
)


class ConversationCostsReader:
    """One vault read per range, shared by the DeepSeek, Jev and Gemini cards."""

    def __init__(self, vault_dir: Path, *, scan: Scan = scan_conversation_costs) -> None:
        self._vault_dir = vault_dir
        self._scan = scan
        self._cache: BoundedTTLCache = BoundedTTLCache(ttl_s=_SHARE_TTL_S, max_entries=16)
        self._locks: dict[tuple[date, date], asyncio.Lock] = {}

    async def costs(self, rng: DateRange) -> ConversationCosts:
        key = (rng.start, rng.end)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(key)
            if hit is None:
                hit = await asyncio.to_thread(self._scan, self._vault_dir, rng)
                self._cache.put(key, hit)
        if not lock.locked():
            self._locks.pop(key, None)
        return hit


@dataclass(frozen=True)
class _LineSpec:
    kind: str
    label: str
    unit: str


class ConversationCostProvider:
    """A card whose lines are kinds of cost recorded per conversation."""

    connected = True

    def __init__(
        self,
        *,
        provider_id: str,
        label: str,
        lines: tuple[_LineSpec, ...],
        note: str,
        reader: ConversationCostsReader,
    ) -> None:
        self.id = provider_id
        self.label = label
        self._lines = lines
        self._note = note
        self._reader = reader

    async def fetch(
        self, client: httpx.AsyncClient, rng: DateRange, now: datetime
    ) -> ProviderReport:
        costs = await self._reader.costs(rng)
        if not costs.vault_found:
            return ProviderReport.not_connected(self.id, rng, how_to_connect=_NO_VAULT)
        lines = [
            CostLine(
                key=spec.kind,
                label=spec.label,
                currency="USD",
                daily=dict(costs.daily[spec.kind]),
                quantity=costs.quantity[spec.kind],
                unit=spec.unit,
            )
            for spec in self._lines
            if costs.daily[spec.kind] or costs.quantity[spec.kind]
        ]
        return ProviderReport.ok(
            self.id,
            rng,
            Source.CONVERSATIONS,
            lines,
            notes=[
                self._note,
                f"Suma de {costs.conversations} conversaciones que empezaron en el rango; "
                "el costo de cada conversación queda en el día en que empezó.",
            ],
        )


def conversation_providers(reader: ConversationCostsReader) -> list[ConversationCostProvider]:
    return [
        ConversationCostProvider(
            provider_id="deepseek",
            label="DeepSeek (el bot)",
            lines=(_LineSpec("llm", "Turnos del bot (ventas y remarketing)", "tokens"),),
            note=(
                "Lo que costó el modelo del bot en cada conversación, con la tarifa de "
                "DeepSeek de la hora del turno (si DeepSeek falla y responde el respaldo "
                "de Gemini, se cuenta como DeepSeek)."
            ),
            reader=reader,
        ),
        ConversationCostProvider(
            provider_id="openrouter",
            label="Jev (OpenRouter)",
            lines=(_LineSpec("jev", "Preguntas al motor de decisiones", "preguntas"),),
            note="Lo que OpenRouter cobró por cada pregunta a Jev, guardado en la conversación.",
            reader=reader,
        ),
        ConversationCostProvider(
            provider_id="gemini",
            label="Gemini (fotos y audios)",
            lines=(
                _LineSpec("vision", "Leer las fotos del cliente", "llamadas"),
                _LineSpec("audio", "Transcribir notas de voz", "notas"),
            ),
            note="Lo que costó Gemini al leer fotos y transcribir notas de voz de los clientes.",
            reader=reader,
        ),
    ]
