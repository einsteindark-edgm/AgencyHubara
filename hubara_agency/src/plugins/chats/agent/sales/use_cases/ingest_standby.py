"""D1.4 — ``IngestStandby``: el oído de Hubara cuando Meta Business Agent
controla el hilo.

Mientras MBA es el respondedor, Meta nos manda el webhook ``standby`` (ver
``parsers.parse_whatsapp_standby``): lo que escribe el cliente
(``standby.messages``), lo que MBA le respondió con el body exacto
(``standby.message_echoes``) y los recibos con ``pricing``
(``standby.statuses``). Este use case persiste lo primero y lo segundo al
vault de la sesión para que nada se pierda:

* **Historial de la sesión** (``<vault>/<session>/sessions/<session>.jsonl``,
  el que lee el dashboard de chats y las evals): el inbound como turno
  ``user`` y el eco como turno ``assistant`` con ``sender="mba"``; ambos con
  ``wamid``.
* **metadata.json**: con cada inbound, episodio activo (``ensure_active_episode``:
  D1.10 — el primer inbound tras un episodio cerrado abre uno nuevo, con el
  referral del anuncio si lo trae), ventana de servicio 24h,
  ``last_inbound_message_id`` y ``ctwa_referrals``; con cada eco, un
  ``OutboundLogEntry`` PENDIENTE de pricing en el episodio activo +
  ``last_outbound`` (``record_outbound_in_active_episode``, el mismo log que
  un send propio) — el status ``standby`` con ``pricing`` lo materializa
  después el ``IngestDeliveryStatus`` de siempre, así el costo de MBA cae en
  el ``cost_summary`` del episodio.
* **Dedupe por wamid** (``standby_seen_wamids``, acotado): Meta puede
  reentregar; un duplicado no reescribe nada.

Lo que NO hace, por diseño:

* **No despacha a Temporal ni emite eventos.** Con MBA al frente no hay turno
  del bot ni watchdog que arrancar; el módulo no importa nada que llegue a
  un workflow (test lo verifica).
* **No escribe el historial LLM de exoclaw** (``EXOCLAW_STATE_DIR``): el API
  no tiene ese volumen (amnesia del PR #183) y con MBA al frente el LLM de
  Hubara no corre. Cuando Hubara retome el hilo (D1.6) el historial LLM se
  siembra desde este JSONL, que ahora es la copia completa.
* Con un humano en el hilo (``active_route=humano``) NO toca el episodio ni
  el tag (misma regla que el ingest regular); el mensaje igual se persiste.

Concurrencia: cada mutación (metadata + append al JSONL) va por
``FilesystemMetadataStore.update`` (flock por sesión), porque la sesión la
escriben en paralelo las connector tools de MBA (``api/session_actions``) y el
``IngestDeliveryStatus`` (que desde D1.4 también escribe por ``update``).

P-28: archivo NUEVO de plugin → importa solo ``src.sdk`` + módulos de chats.
El literal de la ruta humana se verifica contra platform en el test.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from src.plugins.chats.agent.sales.parsers import (
    StandbyEcho,
    StandbyEvent,
    WhatsAppMessage,
    echo_display_text,
    inbound_display_text,
)
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    count_session_jsonl_lines,
    ensure_active_episode,
)
from src.sdk.messagingkit import (
    OutboundLogEntry,
    compute_service_window_expiry,
    record_outbound_in_active_episode,
)

__all__ = ["IngestStandby", "StandbyIngestResult", "SEEN_WAMIDS_CAP"]

#: Mismo valor que ``src.platform.constants.ROUTE_HUMANO`` (test lo verifica).
ROUTE_HUMANO = "humano"
SESSION_PREFIX = "wa_"
#: Últimos wamids vistos por sesión (Meta reentrega; la lista no crece sin tope).
SEEN_WAMIDS_CAP = 500
_SOURCE = "standby"


@dataclass(frozen=True)
class StandbyIngestResult:
    messages_persisted: int = 0
    echoes_persisted: int = 0
    duplicates: int = 0
    sessions: tuple[str, ...] = ()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _seen(data: dict[str, Any], wamid: str) -> bool:
    seen = data.get("standby_seen_wamids")
    return isinstance(seen, list) and wamid in seen


def _mark_seen(data: dict[str, Any], wamid: str) -> None:
    seen = data.get("standby_seen_wamids")
    if not isinstance(seen, list):
        seen = []
    seen.append(wamid)
    data["standby_seen_wamids"] = seen[-SEEN_WAMIDS_CAP:]


def _touch_stats(data: dict[str, Any], key: str, now_ms: int) -> None:
    stats = data.get("mba_standby")
    if not isinstance(stats, dict):
        stats = {}
    stats[f"{key}_count"] = int(stats.get(f"{key}_count") or 0) + 1
    stats[f"last_{key}_at_ms"] = now_ms
    data["mba_standby"] = stats


def _record_referral(data: dict[str, Any], referral: dict[str, Any], wamid: str, now_ms: int) -> None:
    """Espejo mínimo de ``IngestInboundMessage._handle_referral`` (sin
    analytics): CAPI atribuye por ``ctwa_referrals[-1]``."""
    clid = referral.get("ctwa_clid")
    seen = list(data.get("ctwa_clids_seen") or [])
    if clid and clid in seen:
        return
    referrals = list(data.get("ctwa_referrals") or [])
    referrals.append({**referral, "captured_at_ms": now_ms, "inbound_message_id": wamid, "source": _SOURCE})
    if clid:
        seen.append(clid)
    data["ctwa_referrals"] = referrals
    data["ctwa_clids_seen"] = seen


class IngestStandby:
    def __init__(
        self,
        *,
        metadata_store: Any,  # FilesystemMetadataStore (read/update con flock)
        history_store: Any,  # FilesystemMessageHistoryStore
        vault_dir: Path,
        now_ms: Callable[[], int] = _now_ms,
    ) -> None:
        self._metadata_store = metadata_store
        self._history_store = history_store
        self._vault_dir = Path(vault_dir)
        self._now_ms = now_ms

    async def execute(self, event: StandbyEvent) -> StandbyIngestResult:
        messages = echoes = duplicates = 0
        sessions: list[str] = []
        for msg in event.messages:
            session = f"{SESSION_PREFIX}{msg.from_number}"
            if self._ingest_inbound(session, msg, event.phone_number_id):
                messages += 1
            else:
                duplicates += 1
            if session not in sessions:
                sessions.append(session)
        for echo in event.echoes:
            session = f"{SESSION_PREFIX}{echo.to}"
            if self._ingest_echo(session, echo):
                echoes += 1
            else:
                duplicates += 1
            if session not in sessions:
                sessions.append(session)
        logger.info(
            "[chats.standby] ingest sessions={} messages={} echoes={} duplicates={} statuses={}",
            sessions, messages, echoes, duplicates, len(event.statuses),
        )
        return StandbyIngestResult(
            messages_persisted=messages, echoes_persisted=echoes, duplicates=duplicates, sessions=tuple(sessions)
        )

    # ── inbound del cliente (MBA responde; nosotros escuchamos) ──────────────

    def _ingest_inbound(self, session: str, msg: WhatsAppMessage, phone_number_id: str) -> bool:
        now_ms = self._now_ms()
        (self._vault_dir / session).mkdir(parents=True, exist_ok=True)
        applied: dict[str, bool] = {}

        def _mutate(data: dict[str, Any]) -> dict[str, Any] | None:
            if _seen(data, msg.message_id):
                return None
            if phone_number_id and not data.get("phone_number_id"):
                data["phone_number_id"] = phone_number_id
            if data.get("active_route") != ROUTE_HUMANO:
                # D1.10: el primer inbound tras un episodio cerrado abre uno nuevo
                # (resetea tag/draft); con humano en el hilo no se rota.
                ensure_active_episode(
                    data,
                    now_ms=now_ms,
                    inbound_message_id=msg.message_id,
                    referral_snapshot=dict(msg.referral) if msg.referral else None,
                    # Bajo el flock: el conteo previo al append de ESTE turno.
                    msgs_count_at_start=count_session_jsonl_lines(self._vault_dir, session),
                )
            if msg.referral and msg.referral.get("ctwa_clid"):
                # Contrato HU-002 (mismo que el ingest regular): solo los touches
                # con click id entran a `ctwa_referrals` — CAPI atribuye por el
                # último; un referral web/direct no debe pisar al anuncio.
                _record_referral(data, msg.referral, msg.message_id, now_ms)
            data["last_inbound_at_ms"] = now_ms
            data["service_window_expires_at_ms"] = compute_service_window_expiry(now_ms)
            data["last_inbound_message_id"] = msg.message_id
            _touch_stats(data, "inbound", now_ms)
            # El turno va al JSONL DENTRO del read-modify-write: si el append
            # falla, el mutador aborta sin escribir y el wamid NO queda visto —
            # la reentrega de Meta vuelve a intentar en vez de perderse.
            self._history_store.append_user_event(session, inbound_display_text(msg), wamid=msg.message_id)
            _mark_seen(data, msg.message_id)
            applied["ok"] = True
            return data

        self._metadata_store.update(session, _mutate)
        return bool(applied.get("ok"))

    # ── eco de lo que MBA envió ──────────────────────────────────────────────

    def _ingest_echo(self, session: str, echo: StandbyEcho) -> bool:
        now_ms = self._now_ms()
        (self._vault_dir / session).mkdir(parents=True, exist_ok=True)
        sent_at_ms = int(echo.timestamp) * 1000 if echo.timestamp.isdigit() else now_ms
        entry = OutboundLogEntry(
            sent_at_ms=sent_at_ms,
            wa_message_id=echo.wamid,
            kind=f"mba_{echo.msg_type}",
            template_name=echo.template_name,
            pricing=None,
            cost_usd_micros=None,
            rate_card_version=None,
        )
        applied: dict[str, bool] = {}

        def _mutate(data: dict[str, Any]) -> dict[str, Any] | None:
            if _seen(data, echo.wamid):
                return None
            record_outbound_in_active_episode(data, entry)
            _touch_stats(data, "echo", now_ms)
            self._history_store.append_assistant_event(
                session, echo_display_text(echo), sender="mba", wamid=echo.wamid
            )
            _mark_seen(data, echo.wamid)
            applied["ok"] = True
            return data

        self._metadata_store.update(session, _mutate)
        return bool(applied.get("ok"))
