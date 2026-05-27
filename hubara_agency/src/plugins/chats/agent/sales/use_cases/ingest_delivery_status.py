"""Use case `IngestDeliveryStatus` — procesa el webhook `statuses[]` de
WhatsApp Cloud API y materializa el costo del outbound correspondiente.

Meta envía un webhook `message_status` por cada outbound nuestro con shape:

    {
      "id": "wamid.HBg...",
      "status": "sent" | "delivered" | "read" | "failed",
      "timestamp": "...",
      "recipient_id": "+57...",
      "pricing": {"billable": true, "pricing_type": "regular", "category": "marketing"}
    }

Algoritmo:
  1. Construir `PricingSnapshot` del dict (si pricing está presente — puede
     faltar en `status=failed`).
  2. Localizar el episodio con `outbound_messages[*].wa_message_id ==
     wa_message_id` escaneando el vault.
  3. Si no se encuentra → retry con backoff (race: el outbound activity
     puede no haber persistido todavía al momento del webhook). Max 3
     reintentos en ~3.5s.
  4. Si persiste sin encontrarlo → dead-letter a
     `<vault>/_orphan_delivery_statuses.jsonl`.
  5. Si el episodio está cerrado → dead-letter (no mutar summary).
  6. Si la entry ya está materializada (cost_cents_usd != None) → emitir
     analytic event pero NO duplicar el delta en summary (defensa contra
     webhook duplicado de Meta).
  7. Path feliz: computar cost via `compute_message_cost_cents`, reemplazar
     el entry en `outbound_messages[]` y llamar
     `materialize_pending_in_summary` para reflejar el cost.
  8. Emitir `wa_delivery_status` event.

R-DET: no `datetime.now()` en lógica core. Timing del retry vía DI de un
`sleeper` callable (default `asyncio.sleep`, override en tests).

R-DIP: importa de `platform/whatsapp/*` y `platform/analytics` (OK, ambos
en platform/). NO importa siblings (`agent.remarketing`).

R-STATELESS: la `RateCard` se inyecta como instancia desde el composition
root. No hay module-globals.

Ver HU-WA24H-001 §4.7 y §3.4 para contrato completo.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.platform.analytics import EventBus, make_delivery_status
from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.cost import (
    CategoryCostBreakdown,
    EpisodeCostSummary,
    OutboundLogEntry,
    PricingSnapshot,
    RateCard,
    compute_message_cost_cents,
    empty_episode_cost_summary,
    materialize_pending_in_summary,
)


log = logging.getLogger(__name__)


#: Backoff entre reintentos del lookup. El outbound activity persiste su
#: OutboundLogEntry ANTES de retornar (atomicidad), pero el dispatcher
#: webhook puede llegar carrera-fast (Meta confirma el `sent` en <100ms).
#: Total max ~3.5s — dentro del budget conservador de 5s del refinement.
_RETRY_DELAYS_SECONDS: tuple[float, ...] = (0.5, 1.0, 2.0)


Sleeper = Callable[[float], Awaitable[None]]


class IngestDeliveryStatus:
    """Use case puro invocado desde el webhook handler (FastAPI bg task).

    Dependencias:
      * ``metadata_store`` — read/write per-session metadata.
      * ``rate_card`` — RateCard vigente (singleton del composition root,
        inyectado como instancia → R-STATELESS).
      * ``event_bus`` — opcional, para emitir ``wa_delivery_status``.
      * ``vault_dir`` — base path para escanear sesiones por ``wa_message_id``.
      * ``dead_letter_path`` — JSONL append-only. Default
        ``vault_dir/_orphan_delivery_statuses.jsonl``.
      * ``tenant_id`` — para correlation del analytic event.
      * ``sleeper`` — async sleep DI (default ``asyncio.sleep``).
      * ``retry_delays`` — backoff schedule (override en tests para acelerar).
    """

    def __init__(
        self,
        metadata_store: FilesystemMetadataStore,
        rate_card: RateCard,
        event_bus: EventBus | None,
        vault_dir: Path,
        *,
        dead_letter_path: Path | None = None,
        tenant_id: str | None = None,
        sleeper: Sleeper | None = None,
        retry_delays: tuple[float, ...] = _RETRY_DELAYS_SECONDS,
    ) -> None:
        self._metadata_store = metadata_store
        self._rate_card = rate_card
        self._event_bus = event_bus
        self._vault_dir = vault_dir
        self._dead_letter_path = dead_letter_path or (
            vault_dir / "_orphan_delivery_statuses.jsonl"
        )
        self._tenant_id = tenant_id
        self._sleeper = sleeper or asyncio.sleep
        self._retry_delays = retry_delays

    async def execute(
        self,
        wa_message_id: str,
        status: str,
        pricing: dict[str, Any] | None,
    ) -> None:
        """Procesa UN status update.

        ``wa_message_id``: id del mensaje outbound nuestro.
        ``status``: ``"sent" | "delivered" | "read" | "failed"``.
        ``pricing``: dict ``{billable, pricing_type, category}`` o ``None``
        (status=failed puede no traerlo).
        """
        snapshot = _pricing_snapshot_from_dict(pricing)

        # 1. Buscar el episodio con retry (race: outbound puede no haber
        # persistido cuando llega el primer webhook 'sent').
        located = await self._locate_with_retry(wa_message_id)

        if located is None:
            self._dead_letter(
                wa_message_id=wa_message_id,
                status=status,
                pricing=pricing,
                reason="not_found",
            )
            await self._emit_event(
                session_id=None,
                wa_message_id=wa_message_id,
                status=status,
                snapshot=snapshot,
                cost_cents_usd=None,
            )
            return

        session_id, episode_idx, log_entry_idx = located

        # Re-leer metadata fresca antes de mutar (el retry pudo haber
        # capturado una versión ligeramente vieja).
        metadata = self._metadata_store.read(session_id)
        episodes = metadata.get("episodes") or []

        if episode_idx >= len(episodes):
            self._dead_letter(
                wa_message_id=wa_message_id,
                status=status,
                pricing=pricing,
                reason="episode_disappeared",
                session_id=session_id,
            )
            return

        episode = episodes[episode_idx]

        # 2. Episodio cerrado → dead-letter, NO mutar summary.
        # El summary del episodio quedó congelado al close_episode; los
        # webhooks tardíos NO deben recalcular el agregado histórico.
        if episode.get("closed_at_ms") is not None:
            self._dead_letter(
                wa_message_id=wa_message_id,
                status=status,
                pricing=pricing,
                reason="episode_closed",
                session_id=session_id,
                episode_id=episode.get("episode_id"),
            )
            await self._emit_event(
                session_id=session_id,
                wa_message_id=wa_message_id,
                status=status,
                snapshot=snapshot,
                cost_cents_usd=None,
            )
            return

        outbound_messages = list(episode.get("outbound_messages") or [])
        if log_entry_idx >= len(outbound_messages):
            self._dead_letter(
                wa_message_id=wa_message_id,
                status=status,
                pricing=pricing,
                reason="entry_disappeared",
                session_id=session_id,
            )
            return

        existing_entry = _outbound_log_entry_from_dict(
            outbound_messages[log_entry_idx]
        )

        # 3. Sin pricing (status=failed sin pricing object): emitir el
        # status pero NO tocar cost. El entry queda con cost_cents_usd=None
        # (sigue en pending_count). Defensa contra dataset incompleto de Meta.
        if snapshot is None:
            await self._emit_event(
                session_id=session_id,
                wa_message_id=wa_message_id,
                status=status,
                snapshot=None,
                cost_cents_usd=existing_entry.cost_cents_usd,
            )
            return

        # 4. Idempotencia: si la entry YA tiene cost materializado, este
        # webhook es duplicado. NO llamar materialize_pending_in_summary
        # (el helper acumularía total/by_category/by_pricing_type otra vez,
        # rompiendo invariantes — solo el pending_count está clamped a 0).
        already_materialized = existing_entry.cost_cents_usd is not None

        cost = compute_message_cost_cents(snapshot, self._rate_card)
        new_entry = replace(
            existing_entry,
            pricing=snapshot,
            cost_cents_usd=cost,
            rate_card_version=self._rate_card.version,
        )
        outbound_messages[log_entry_idx] = _outbound_log_entry_to_dict(new_entry)
        episode["outbound_messages"] = outbound_messages

        if not already_materialized:
            current_summary = _summary_from_episode(episode)
            new_summary = materialize_pending_in_summary(current_summary, new_entry)
            episode["cost_summary"] = _summary_to_dict(new_summary)

        # 5. Persistir
        try:
            self._metadata_store.write(session_id, metadata)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "ingest_delivery_status_write_failed",
                extra={
                    "session_id": session_id,
                    "wa_message_id": wa_message_id,
                    "error": str(e),
                },
            )

        # 6. Analytics — siempre, incluso en duplicado (audit trail).
        await self._emit_event(
            session_id=session_id,
            wa_message_id=wa_message_id,
            status=status,
            snapshot=snapshot,
            cost_cents_usd=cost,
        )

    # =====================================================================
    # Lookup with retry
    # =====================================================================

    async def _locate_with_retry(
        self, wa_message_id: str
    ) -> tuple[str, int, int] | None:
        """Busca (session_id, episode_idx, log_entry_idx) con backoff.

        Total reintentos = 1 inicial + len(retry_delays). Cada delay es
        sleep async (no bloquea el event loop).
        """
        result = self._locate(wa_message_id)
        if result is not None:
            return result
        for delay in self._retry_delays:
            await self._sleeper(delay)
            result = self._locate(wa_message_id)
            if result is not None:
                return result
        return None

    def _locate(self, wa_message_id: str) -> tuple[str, int, int] | None:
        """Scan del vault: O(sessions × episodes × outbound_messages).

        Aceptable pre-launch (<10K sessions). Si crece, agregar índice
        secundario ``wa_message_id → (session_id, episode_idx)`` como
        follow-up.
        """
        if not self._vault_dir.exists():
            return None
        for session_dir in self._vault_dir.iterdir():
            if not session_dir.is_dir() or not session_dir.name.startswith("wa_"):
                continue
            session_id = session_dir.name
            try:
                metadata = self._metadata_store.read(session_id)
            except Exception:  # noqa: BLE001
                continue
            episodes = metadata.get("episodes") or []
            for ep_idx, episode in enumerate(episodes):
                outbound_messages = episode.get("outbound_messages") or []
                for log_idx, entry in enumerate(outbound_messages):
                    if (
                        isinstance(entry, dict)
                        and entry.get("wa_message_id") == wa_message_id
                    ):
                        return (session_id, ep_idx, log_idx)
        return None

    # =====================================================================
    # Dead letter
    # =====================================================================

    def _dead_letter(
        self,
        *,
        wa_message_id: str,
        status: str,
        pricing: dict[str, Any] | None,
        reason: str,
        session_id: str | None = None,
        episode_id: str | None = None,
    ) -> None:
        """Appendea un orphan delivery status al JSONL para análisis manual.

        Reasons: ``not_found`` (sin retry hit), ``episode_disappeared``,
        ``entry_disappeared``, ``episode_closed``.
        """
        record = {
            "wa_message_id": wa_message_id,
            "status": status,
            "pricing": pricing,
            "reason": reason,
            "session_id": session_id,
            "episode_id": episode_id,
        }
        try:
            self._dead_letter_path.parent.mkdir(parents=True, exist_ok=True)
            with self._dead_letter_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as e:
            log.warning(
                "ingest_delivery_status_dead_letter_failed",
                extra={
                    "wa_message_id": wa_message_id,
                    "reason": reason,
                    "error": str(e),
                },
            )

    # =====================================================================
    # Analytics
    # =====================================================================

    async def _emit_event(
        self,
        *,
        session_id: str | None,
        wa_message_id: str,
        status: str,
        snapshot: PricingSnapshot | None,
        cost_cents_usd: int | None,
    ) -> None:
        if self._event_bus is None:
            return
        event = make_delivery_status(
            session_id=session_id,
            tenant_id=self._tenant_id,
            wa_message_id=wa_message_id,
            status=status,
            pricing_type=snapshot.pricing_type if snapshot else None,
            category=snapshot.category if snapshot else None,
            billable=snapshot.billable if snapshot else None,
            cost_cents_usd=cost_cents_usd,
        )
        try:
            await self._event_bus.record(event)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "ingest_delivery_status_event_emit_failed",
                extra={"wa_message_id": wa_message_id, "error": str(e)},
            )


# =============================================================================
# Helpers de conversión dict ↔ DTO
# =============================================================================


def _pricing_snapshot_from_dict(d: dict[str, Any] | None) -> PricingSnapshot | None:
    """Construye un PricingSnapshot desde el dict Meta o None si falta data.

    Meta puede mandar pricing parcial / ausente en status=failed; defensivo
    contra estructura inesperada.
    """
    if not isinstance(d, dict):
        return None
    pricing_type = d.get("pricing_type")
    category = d.get("category")
    if not isinstance(pricing_type, str) or not isinstance(category, str):
        return None
    return PricingSnapshot(
        billable=bool(d.get("billable", False)),
        pricing_type=pricing_type,
        category=category,
    )


def _outbound_log_entry_from_dict(d: dict[str, Any]) -> OutboundLogEntry:
    """Reconstruye el dataclass frozen desde el dict persistido."""
    pricing_d = d.get("pricing")
    pricing: PricingSnapshot | None = None
    if isinstance(pricing_d, dict):
        pricing = PricingSnapshot(
            billable=bool(pricing_d.get("billable", False)),
            pricing_type=str(pricing_d.get("pricing_type", "")),
            category=str(pricing_d.get("category", "")),
        )
    return OutboundLogEntry(
        sent_at_ms=int(d.get("sent_at_ms", 0)),
        wa_message_id=str(d.get("wa_message_id", "")),
        kind=str(d.get("kind", "")),
        template_name=d.get("template_name"),
        pricing=pricing,
        cost_cents_usd=d.get("cost_cents_usd"),
        rate_card_version=d.get("rate_card_version"),
    )


def _outbound_log_entry_to_dict(entry: OutboundLogEntry) -> dict[str, Any]:
    """Serializa el dataclass frozen a dict JSON-safe para persistencia."""
    pricing_dict = asdict(entry.pricing) if entry.pricing is not None else None
    return {
        "sent_at_ms": entry.sent_at_ms,
        "wa_message_id": entry.wa_message_id,
        "kind": entry.kind,
        "template_name": entry.template_name,
        "pricing": pricing_dict,
        "cost_cents_usd": entry.cost_cents_usd,
        "rate_card_version": entry.rate_card_version,
    }


def _summary_from_episode(episode: dict[str, Any]) -> EpisodeCostSummary:
    """Reconstruye EpisodeCostSummary desde el dict persistido en episode."""
    raw = episode.get("cost_summary")
    if not isinstance(raw, dict):
        return empty_episode_cost_summary()
    by_category = {
        k: CategoryCostBreakdown(
            count=int(v.get("count", 0)),
            cents_usd=int(v.get("cents_usd", 0)),
        )
        for k, v in (raw.get("by_category") or {}).items()
        if isinstance(v, dict)
    }
    by_pricing_type = {
        k: CategoryCostBreakdown(
            count=int(v.get("count", 0)),
            cents_usd=int(v.get("cents_usd", 0)),
        )
        for k, v in (raw.get("by_pricing_type") or {}).items()
        if isinstance(v, dict)
    }
    return EpisodeCostSummary(
        total_cents_usd=int(raw.get("total_cents_usd", 0)),
        messages_count=int(raw.get("messages_count", 0)),
        messages_billable_count=int(raw.get("messages_billable_count", 0)),
        messages_free_count=int(raw.get("messages_free_count", 0)),
        messages_pending_count=int(raw.get("messages_pending_count", 0)),
        by_category=by_category,
        by_pricing_type=by_pricing_type,
    )


def _summary_to_dict(summary: EpisodeCostSummary) -> dict[str, Any]:
    """Serializa EpisodeCostSummary a dict JSON-safe."""
    return {
        "total_cents_usd": summary.total_cents_usd,
        "messages_count": summary.messages_count,
        "messages_billable_count": summary.messages_billable_count,
        "messages_free_count": summary.messages_free_count,
        "messages_pending_count": summary.messages_pending_count,
        "by_category": {
            k: {"count": v.count, "cents_usd": v.cents_usd}
            for k, v in summary.by_category.items()
        },
        "by_pricing_type": {
            k: {"count": v.count, "cents_usd": v.cents_usd}
            for k, v in summary.by_pricing_type.items()
        },
    }
