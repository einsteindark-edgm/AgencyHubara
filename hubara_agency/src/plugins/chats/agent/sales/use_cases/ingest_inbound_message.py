"""Use case top-level del webhook de WhatsApp.

Recibe un `WhatsAppMessage` ya parseado (la decision de 4xx vs 200 vive en el
parser, en ``api.py``) y orquesta:

1. Detectar y persistir referral CTWA si es la primera vez en la sesión.
2. Traducir el inbound a "texto efectivo" (texto natural para el LLM).
3. Si el mensaje requiere transcripción (audio), encolar la activity y NO
   delegar al agente hasta tener el texto.
4. Persistir el evento del usuario en el JSONL via
   ``FilesystemMessageHistoryStore``.
5. Emitir eventos analytics (referral_captured, wa_interaction).
6. Delegar a ``LoadOrStartSalesSession`` para resolver ruta + signal.

Backward-compat: el comportamiento para `text` solo permanece idéntico al
legacy — los tests existentes (`test_ingest_inbound_message.py`) pasan sin
cambios. Los campos nuevos (interactive, location, audio, referral) van por
la rama de la translator.

PR-E: el ``MessageHistoryStorePort`` Protocol intermedio desaparecio. El use
case ahora type-hints la concreta ``FilesystemMessageHistoryStore`` directo
(Python sigue siendo duck-typed, asi que los fakes en tests pasan sin
isinstance check).
"""
from __future__ import annotations

from typing import Any

import structlog

from src.platform.analytics import (
    EventBus,
    make_referral_captured,
    make_wa_interaction,
)
from src.platform.constants import WHATSAPP_SESSION_PREFIX
from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.translate import (
    EffectiveText,
    translate_to_effective_text,
)
from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.session_history import FilesystemMessageHistoryStore
from src.platform.whatsapp.window import (
    compute_ctwa_window_expiry,
    compute_service_window_expiry,
)
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    count_session_jsonl_lines,
    ensure_active_episode,
)
from src.plugins.chats.agent.sales.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)

logger = structlog.get_logger()


class IngestInboundMessage:
    """Procesa un `WhatsAppMessage` ya parseado: history + routing + signal."""

    def __init__(
        self,
        history_store: FilesystemMessageHistoryStore,
        load_session: LoadOrStartSalesSession,
        metadata_store: FilesystemMetadataStore,
        *,
        event_bus: EventBus | None = None,
        tenant_id: str | None = None,
    ) -> None:
        self._history_store = history_store
        self._load_session = load_session
        self._metadata_store = metadata_store
        self._event_bus = event_bus
        self._tenant_id = tenant_id

    async def execute(self, parsed: WhatsAppMessage) -> None:
        session_id = f"{WHATSAPP_SESSION_PREFIX}{parsed.from_number}"

        # --- 1. Read metadata UNA vez al principio (atribución + typing) ---
        try:
            metadata = self._metadata_store.read(session_id)
        except Exception:  # noqa: BLE001 — best-effort
            metadata = {}

        # --- 2a. Origin classification (4 buckets para reporting) ---
        # Sticky first-touch + last_touch updated cada inbound. Se persiste
        # ANTES del CTWA flow porque queremos clasificar incluso a clientes
        # `direct` (sin referral) o `web_referral` (referral sin ctwa_clid).
        self._handle_origin(
            session_id=session_id,
            metadata=metadata,
            referral=parsed.referral,
            inbound_message_id=parsed.message_id,
        )

        # --- 2b. Referral CTWA: detectar primer touch y persistir ---
        # Solo Meta-attributable touches (con ctwa_clid) van a ctwa_referrals
        # — feed de Conversions API. web_referral / direct NO contaminan
        # esta lista (mantenemos el contrato del HU-002).
        referral_already_seen = False
        if parsed.referral and parsed.referral.get("ctwa_clid"):
            referral_already_seen = self._handle_referral(
                session_id=session_id,
                metadata=metadata,
                referral=parsed.referral,
                inbound_message_id=parsed.message_id,
            )

        # --- 2c. Episode lifecycle: garantizar episodio activo ---
        # Cada inbound del cliente debe estar dentro de un episodio. Si el
        # último episodio del cliente está cerrado (COMPRA_EXITOSA / RECHAZO /
        # CONFIRMADO_SIN_DATOS / TIMEOUT), arrancamos uno nuevo automáticamente —
        # representa "el cliente volvió con una nueva intención".
        #
        # El snapshot del referral se enriquece con el `channel` resuelto
        # (ad / post / web_referral / direct) para que el listing use case
        # pueda asignar el episodio a una campaña por sí mismo, sin depender
        # del `origin` sticky de la sesión (FU2: re-atribución por episodio).
        #
        # `msgs_count_at_start` snapshotea el count del JSONL ANTES de
        # appendear el evento del usuario actual (FU3). Permite derivar
        # `msgs_in_episode` exacto downstream.
        msgs_count_at_start = count_session_jsonl_lines(
            WORKSPACE_VAULT_DIR, session_id
        )
        # HU-WA24H-001 F1.1: single now_ms para todo el bloque (episode
        # lifecycle + service window tracking) — más simple que llamar
        # _now_ms() en múltiples lugares y mantiene consistencia (los
        # timestamps de un mismo inbound son idénticos).
        now_ms = _now_ms()
        ensure_active_episode(
            metadata,
            now_ms=now_ms,
            inbound_message_id=parsed.message_id,
            referral_snapshot=_make_episode_snapshot(parsed.referral),
            msgs_count_at_start=msgs_count_at_start,
        )

        # HU-WA24H-001 F1.1: persistir timestamps de la ventana de servicio.
        # Cada inbound del cliente reabre la ventana 24h — esto es lo que
        # permite al watchdog (Sprint 2) saber cuándo está por cerrarse y
        # disparar un utility template legítimo.
        metadata["last_inbound_at_ms"] = now_ms
        metadata["service_window_expires_at_ms"] = compute_service_window_expiry(now_ms)

        # HU-WA24H-001 F1.3: ventana extendida 72h CTWA. Solo se setea la
        # PRIMERA vez que vemos ctwa_clid — la ventana CTWA NO se renueva
        # con inbounds subsecuentes. Defensivo: chequeamos presencia previa
        # del campo, no `referral_already_seen` (que es por ad_id, no por
        # ventana de pricing).
        if (
            parsed.referral
            and parsed.referral.get("ctwa_clid")
            and "ctwa_window_expires_at_ms" not in metadata
        ):
            metadata["ctwa_window_expires_at_ms"] = compute_ctwa_window_expiry(now_ms)

        self._safe_write_metadata(session_id, metadata)

        # --- 3. Traducir a texto efectivo (LLM-ready) ---
        # `catalog=None` por ahora — list_reply usa el title raw del cliente.
        # Wire del catalog port queda como follow-up cuando se exponga via
        # composition.
        effective: EffectiveText = await translate_to_effective_text(
            parsed,
            catalog=None,
            referral_already_seen=referral_already_seen,
        )

        # --- 4. Audio inbound: defer a la transcripción ---
        if effective.requires_transcription:
            logger.info(
                "audio_inbound_received_pending_transcription",
                session=session_id,
                media_id=effective.audio_media_id,
            )
            await self._emit_event(
                make_wa_interaction(
                    session_id=session_id,
                    tenant_id=self._tenant_id,
                    kind="audio_received",
                    component_id=effective.audio_media_id,
                    wa_message_id=parsed.message_id,
                    payload_extra={"voice": (parsed.audio or {}).get("voice", False)},
                )
            )
            # Persistimos el media_id en metadata para tracking + para que
            # el activity (si se usa el path Temporal) lo retome.
            metadata["pending_transcription"] = {
                "media_id": effective.audio_media_id,
                "inbound_message_id": parsed.message_id,
                "mime_type": (parsed.audio or {}).get("mime_type"),
                "voice": (parsed.audio or {}).get("voice", False),
            }
            self._safe_write_metadata(session_id, metadata)
            # HU-002 / A.5: spawn background transcription task. El HTTP layer
            # tiene permiso de I/O (ya llama Temporal client), así que la
            # transcripción puede correr ahí — más simple que un workflow
            # dedicado, y el media URL de Meta expira a los 5min así que no
            # podemos demorar.
            #
            # PREMORTEM #2: spawn safe — captura excepciones y las loguea.
            _spawn_safe(
                self._transcribe_and_reenter(parsed),
                label="audio.transcribe_and_reenter",
                session_id=session_id,
            )
            return

        # --- 5. Sin texto efectivo: ignorar pero loguear ---
        if not effective.text:
            logger.info(
                "Inbound without effective text ignored",
                message_id=parsed.message_id,
                msg_type=parsed.msg_type,
                tags=effective.debug_tags,
            )
            # Aún persistimos last_inbound_message_id para que el typing
            # indicator pueda referenciar este msg si el cliente reintenta.
            if parsed.message_id:
                metadata["last_inbound_message_id"] = parsed.message_id
                self._safe_write_metadata(session_id, metadata)
            return

        logger.info(
            "WhatsApp Message Received",
            message_text=effective.text[:200],
            from_number=parsed.from_number,
            msg_type=parsed.msg_type,
            tags=effective.debug_tags,
        )

        # --- 6. Persistir history (texto efectivo, NO el JSON raw) ---
        self._history_store.append_user_event(session_id, effective.text)

        # --- 6.5. Limpiar flag de Flow pendiente (sesión c4e3416f) ---
        # Si el cliente respondió (por texto, por nfm_reply, lo que sea),
        # ya NO estamos en modo "esperando Flow" — limpiamos el flag para
        # que el próximo loop del workflow vuelva al timeout normal de 60s.
        cleared_flow_flag = (
            metadata.pop("shipping_flow_awaiting_reply_since_ms", None)
            is not None
        )

        # --- 7. Persistir last_inbound_message_id para typing indicator ---
        if parsed.message_id:
            metadata["last_inbound_message_id"] = parsed.message_id
            self._safe_write_metadata(session_id, metadata)
        elif cleared_flow_flag:
            # No hay message_id PERO limpiamos el flag arriba — persistimos
            # el pop para que no quede zombie en metadata.
            self._safe_write_metadata(session_id, metadata)

        # --- 8. Analytics de interacciones (clicks) ---
        if effective.structured_payload:
            await self._emit_interaction_event(
                session_id=session_id,
                structured=effective.structured_payload,
                wa_message_id=parsed.message_id,
            )

        # --- 9. Resolver ruta + signal al workflow correspondiente ---
        await self._load_session.execute(
            session_id=session_id,
            message=effective.text,
            phone_number_id=parsed.phone_number_id,
        )

    # =========================================================================
    # Helpers
    # =========================================================================

    def _handle_origin(
        self,
        *,
        session_id: str,
        metadata: dict[str, Any],
        referral: dict[str, Any] | None,
        inbound_message_id: str | None,
    ) -> None:
        """Clasifica el origen del cliente en uno de 4 buckets y persiste
        `origin` (sticky first-touch) + `last_touch` (updated cada inbound).

        Buckets:
          - "ad" / "post" — referral con ctwa_clid (CTWA atribuible, según
            source_type del referral).
          - "web_referral" — referral SIN ctwa_clid (WhatsApp Web / cliente
            viejo). No atribuible vía Conversions API.
          - "direct" — sin referral en el payload (escribió al número,
            QR, link, etc.).

        State shape:
          metadata["origin"] = {
            "channel": str, "first_seen_ms": int,
            "first_inbound_message_id": str | None,
            "headline": str | None, "source_id": str | None,
          }
          metadata["last_touch"] = {
            "channel": str, "seen_at_ms": int,
            "inbound_message_id": str | None, "ctwa_clid": str | None,
            "headline": str | None, "source_id": str | None,
          }

        `origin` se setea UNA SOLA VEZ (primer inbound de la sesión).
        `last_touch` se actualiza en CADA inbound, incluso direct.

        NO toca `ctwa_referrals` / `ctwa_clids_seen` — eso lo hace
        `_handle_referral` (solo cuando hay clid).
        """
        channel = _classify_origin_channel(referral)
        now_ms = _now_ms()

        # last_touch: siempre actualizar
        metadata["last_touch"] = {
            "channel": channel,
            "seen_at_ms": now_ms,
            "inbound_message_id": inbound_message_id,
            "ctwa_clid": (referral or {}).get("ctwa_clid"),
            "headline": (referral or {}).get("headline"),
            "source_id": (referral or {}).get("source_id"),
        }

        # origin: sticky first-touch
        if not metadata.get("origin"):
            metadata["origin"] = {
                "channel": channel,
                "first_seen_ms": now_ms,
                "first_inbound_message_id": inbound_message_id,
                "headline": (referral or {}).get("headline"),
                "source_id": (referral or {}).get("source_id"),
            }
            logger.info(
                "session_origin_classified",
                session=session_id,
                channel=channel,
                source_id=(referral or {}).get("source_id"),
            )

        # Persistimos inmediatamente — el resto del flujo del ingest hará
        # más writes con `_safe_write_metadata`, pero queremos que
        # origin/last_touch queden grabados aunque el flujo posterior
        # crashee.
        self._safe_write_metadata(session_id, metadata)

    def _handle_referral(
        self,
        *,
        session_id: str,
        metadata: dict[str, Any],
        referral: dict[str, Any],
        inbound_message_id: str | None,
    ) -> bool:
        """Persiste el referral en metadata y emite analytics.

        Devuelve True si el clid ya fue visto antes (banner ya inyectado),
        False si es nuevo (banner debe inyectarse al texto efectivo).

        State shape:
          metadata["ctwa_referrals"] = list[ReferralRecord]
          metadata["ctwa_clids_seen"] = list[str]

        Multi-touch: cada referral nuevo se appendea. Solo el primero
        determina el "first touch" attribution para optimización Meta.
        """
        clid = referral.get("ctwa_clid")
        clids_seen: list[str] = list(metadata.get("ctwa_clids_seen") or [])
        if clid and clid in clids_seen:
            return True  # banner ya inyectado

        # Capturar el referral
        referrals: list[dict[str, Any]] = list(metadata.get("ctwa_referrals") or [])
        record = {
            **referral,
            "captured_at_ms": _now_ms(),
            "inbound_message_id": inbound_message_id,
        }
        referrals.append(record)
        if clid:
            clids_seen.append(clid)
        metadata["ctwa_referrals"] = referrals
        metadata["ctwa_clids_seen"] = clids_seen

        # Persistir inmediatamente (el bus es async y puede demorar)
        self._safe_write_metadata(session_id, metadata)

        # Emitir analytics. Fire-and-forget — si el bus falla, NO bloquea.
        # PREMORTEM #2: spawn safe.
        if self._event_bus is not None:
            event = make_referral_captured(
                session_id=session_id,
                tenant_id=self._tenant_id,
                referral=referral,
                inbound_message_id=inbound_message_id,
            )
            _spawn_safe(
                self._event_bus.record(event),
                label="analytics.referral_captured",
                session_id=session_id,
            )

        logger.info(
            "ctwa_referral_captured",
            session=session_id,
            ctwa_clid=clid,
            source_type=referral.get("source_type"),
            headline=referral.get("headline"),
        )
        return False  # primer touch, banner SE inyecta

    async def _emit_interaction_event(
        self,
        *,
        session_id: str,
        structured: dict[str, Any],
        wa_message_id: str | None,
    ) -> None:
        """Emite el evento wa_interaction según el tipo de structured payload."""
        if not self._event_bus:
            return

        kind_map = {
            "button_reply": "button_click",
            "list_reply": "list_select",
            "nfm_reply": "flow_submit",
            "location": "location_share",
            "order": "order_cart_submit",
            "contacts": "contact_received",
            "unknown_interactive": "unknown_interactive",
        }
        kind_raw = structured.get("kind", "unknown")
        kind = kind_map.get(kind_raw, kind_raw)
        component_id = (
            structured.get("id")
            or structured.get("name")
            or structured.get("catalog_id")
        )
        title = structured.get("title") or structured.get("resolved_product_title")

        event = make_wa_interaction(
            session_id=session_id,
            tenant_id=self._tenant_id,
            kind=kind,
            component_id=component_id,
            component_title=title,
            wa_message_id=wa_message_id,
            payload_extra={"structured": structured},
        )
        _spawn_safe(
            self._event_bus.record(event),
            label="analytics.wa_interaction",
            session_id=session_id,
        )

    async def _emit_event(self, event: Any) -> None:
        """Fire-and-forget de un evento. Tolerante a bus=None.

        PREMORTEM #2: spawn safe — captura excepciones y las loguea
        estructurado para que no se pierdan en "Task exception was never
        retrieved" warnings genéricos del stderr.
        """
        if not self._event_bus:
            return
        _spawn_safe(
            self._event_bus.record(event),
            label="analytics.record",
            session_id=None,
        )

    def _safe_write_metadata(self, session_id: str, data: dict[str, Any]) -> None:
        try:
            self._metadata_store.write(session_id, data)
        except Exception:  # noqa: BLE001 — best-effort
            logger.info("metadata_write_failed_ignored", session=session_id)

    async def _transcribe_and_reenter(self, parsed: WhatsAppMessage) -> None:
        """Transcribe el audio (Groq/OpenAI) y re-ejecuta el ingest con
        un mensaje text sintético. Background task — el HTTP webhook ya
        devolvió 200. Si falla, mandamos un texto pidiendo al cliente
        que escriba en lugar del audio.
        """
        from src.platform.audio.composition import get_audio_transcription_port
        from src.platform.audio.dtos import TranscriptionRequest
        from src.platform.whatsapp import client as wa_client

        audio_info = parsed.audio or {}
        media_id = audio_info.get("id")
        if not media_id:
            return

        session_id = f"{WHATSAPP_SESSION_PREFIX}{parsed.from_number}"
        port = get_audio_transcription_port()
        request = TranscriptionRequest(
            media_id=media_id,
            mime_type=audio_info.get("mime_type", "audio/ogg"),
            voice_note=bool(audio_info.get("voice", True)),
            language_hint="es",
            max_duration_seconds=60,
        )
        try:
            result = await port.transcribe(request)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "transcribe_and_reenter.exception",
                session=session_id,
                error=str(e),
            )
            return

        # Limpiar pending_transcription
        try:
            metadata = self._metadata_store.read(session_id)
            metadata.pop("pending_transcription", None)
            if result.ok and result.text:
                recent = list(metadata.get("recent_transcriptions") or [])
                recent.append({
                    "media_id": media_id,
                    "text": result.text,
                    "provider": result.provider,
                    "duration_seconds": result.duration_seconds,
                    "cost_usd_estimate": result.cost_usd_estimate,
                })
                metadata["recent_transcriptions"] = recent[-20:]
            else:
                errors = list(metadata.get("transcription_failures") or [])
                errors.append({
                    "media_id": media_id,
                    "error": result.error,
                    "provider": result.provider,
                })
                metadata["transcription_failures"] = errors[-20:]
            self._metadata_store.write(session_id, metadata)
        except Exception:  # noqa: BLE001
            pass

        # Emit analytics
        if self._event_bus:
            await self._emit_event(
                make_wa_interaction(
                    session_id=session_id,
                    tenant_id=self._tenant_id,
                    kind=(
                        "audio_transcribed"
                        if result.ok and result.text
                        else "audio_transcription_failed"
                    ),
                    component_id=media_id,
                    wa_message_id=parsed.message_id,
                    payload_extra={
                        "provider": result.provider,
                        "duration_seconds": result.duration_seconds,
                        "cost_usd_estimate": result.cost_usd_estimate,
                        "latency_ms": result.latency_ms,
                        "error": result.error,
                        "text_len": len(result.text) if result.text else 0,
                    },
                )
            )

        if not result.ok or not result.text:
            # Avisar al cliente que escriba el mensaje
            try:
                if result.error == "too_long":
                    fallback = (
                        "Recibí tu audio pero es muy largo para procesarlo "
                        "automáticamente. ¿Me lo cuentas en un mensaje "
                        "corto? 🤍"
                    )
                else:
                    fallback = (
                        "Recibí tu audio pero no logré entenderlo bien. "
                        "¿Me lo escribís en un mensaje? 🤍"
                    )
                await wa_client.send_message(
                    parsed.phone_number_id,
                    parsed.from_number,
                    fallback,
                )
            except Exception:  # noqa: BLE001
                pass
            return

        # Re-entry: ejecutamos el ingest otra vez con un msg sintético
        # tipo text. El workflow de sales se signaleará normal.
        synthetic = WhatsAppMessage(
            message_id=f"{parsed.message_id}_transcribed",
            from_number=parsed.from_number,
            phone_number_id=parsed.phone_number_id,
            text=result.text,
            media=None,
            timestamp=parsed.timestamp,
            msg_type="text",
            referral=parsed.referral,  # preservar atribución CTWA si vino con audio
            context=parsed.context,
        )
        logger.info(
            "audio_transcribed_reentry",
            session=session_id,
            text_len=len(result.text),
            provider=result.provider,
            cost_usd=result.cost_usd_estimate,
        )
        await self.execute(synthetic)


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _make_episode_snapshot(referral: dict[str, Any] | None) -> dict[str, Any]:
    """Construye el `referral_snapshot` que se persiste en cada episodio.

    El snapshot enriquece el referral raw del payload con el `channel`
    resuelto (`_classify_origin_channel`). Eso le permite al listing use
    case asignar el episodio a una campaña con la atribución del INBOUND
    que abrió ese episodio — no la sticky de la sesión.

    Para clientes sin referral (channel=direct), el snapshot igual se
    persiste con `channel="direct"` para que el bucket del episodio sea
    explícito en disco.
    """
    base: dict[str, Any] = dict(referral) if referral else {}
    base["channel"] = _classify_origin_channel(referral)
    return base


def _classify_origin_channel(referral: dict[str, Any] | None) -> str:
    """Determina el bucket de origen de un inbound según el payload referral.

    Decisión tabla:
      | referral=None             | "direct"
      | referral sin ctwa_clid    | "web_referral"
      | referral con ctwa_clid    | source_type ("ad" | "post"), default "ad"

    `web_referral` cubre WhatsApp Web (donde Meta omite el clid) — el
    referral trae headline/source_id útiles para reporting pero no es
    atribuible vía Conversions API.
    """
    if not referral:
        return "direct"
    if not referral.get("ctwa_clid"):
        return "web_referral"
    source_type = referral.get("source_type")
    if source_type in ("ad", "post"):
        return source_type
    # Defensivo: clid presente pero source_type missing/unknown — default a "ad"
    # (es el caso más común y mantiene compatibilidad con el HU-002).
    return "ad"


def _spawn_safe(coro, *, label: str, session_id: str | None) -> None:
    """Lanza una coroutine como fire-and-forget capturando excepciones.

    Sin esto, `asyncio.create_task(coro)` con una task que crashea genera
    un warning genérico `Task exception was never retrieved` al stderr
    que NO aparece en logging estructurado de la app — debug imposible.

    Acá envolvemos la coro en un wrapper que catchea todo, loguea
    estructurado (con label + session_id para correlación), y descarta.

    Pattern usado en HTTP layer del ingest (no en activities Temporal —
    las activities ya tienen su propio retry policy + logging).
    """
    import asyncio

    async def _run():
        try:
            await coro
        except Exception as e:  # noqa: BLE001 — fire-and-forget safety
            logger.warning(
                "background_task_failed",
                label=label,
                session_id=session_id,
                error_type=type(e).__name__,
                error=str(e),
            )

    asyncio.create_task(_run())
