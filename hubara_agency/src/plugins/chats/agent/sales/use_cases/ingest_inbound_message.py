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

from typing import Any, Awaitable, Callable, TYPE_CHECKING

import structlog

from src.platform.analytics import (
    EventBus,
    make_referral_captured,
    make_wa_interaction,
    make_web_cart_captured,
    make_web_cart_product_mismatch,
)
from src.platform.constants import (
    ROUTE_HUMANO,
    ROUTE_REMARKETING,
    ROUTE_VENTAS,
    WHATSAPP_SESSION_PREFIX,
)
from src.platform.orchestration import (
    dispatch_envelope_with_client,
    envelope_for,
)
from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.translate import (
    EffectiveText,
    translate_to_effective_text,
)
from src.platform.config import WORKSPACE_VAULT_DIR
from src.sdk.messagingkit import (
    detect_marketing_opt_out,
    update_reengagement_index_entry,
)
from src.platform.session_history import FilesystemMessageHistoryStore
from src.platform.whatsapp.window import (
    compute_ctwa_window_expiry,
    compute_service_window_expiry,
    watchdog_fire_at,
)
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    count_session_jsonl_lines,
    ensure_active_episode,
)
from src.plugins.chats.agent.sales.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)
from src.plugins.chats.agent.sales.use_cases.order_draft import (
    build_order_draft_note,
    get_projectable_draft,
    update_order_draft,
)
from src.plugins.chats.agent.sales.use_cases.web_cart import (
    apply_web_cart_capture,
    build_web_cart_note,
    detect_cart_ref,
    map_cart_to_draft,
    mark_web_cart_degraded,
    mark_web_cart_hydrated,
)
from src.plugins.chats.shared.contracts.events import (
    CustomerRepliedEvent,
    ServiceWindowOpenedEvent,
)

if TYPE_CHECKING:
    from temporalio.client import Client

    from src.sdk.connectorkit import (
        CatalogPort as CatalogPort,
        WebCartReaderPort as WebCartReaderPort,
    )


#: DI-friendly factory for the Temporal client. Async so the composition
#: root can wire `get_temporal_client` directly without wrapping.
TemporalClientFactory = Callable[[], Awaitable["Client"]]

# HU web-cart: timeout de la hidratación inline (patrón L-2 — el webhook no
# puede demorar el primer turno; cualquier fallo degrada en silencio).
_WEB_CART_HYDRATION_TIMEOUT_S = 3.0

#: Cap de descarga para documentos PDF inbound (comprobantes). La restricción
#: de subida la impone WhatsApp (100 MB); de nuestro lado, por encima de este
#: cap NO se descarga (el fetcher corta con el `file_size` declarado, antes de
#: bajar bytes). 10 MB cubre todo comprobante legítimo: uno digital pesa
#: 30–150 KB, un escaneo B/N 200–500 KB y un escaneo a color de una página
#: llega a ~8–10 MB.
_MAX_INBOUND_DOCUMENT_BYTES = 10 * 1024 * 1024

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
        temporal_client_factory: TemporalClientFactory | None = None,
        web_cart_reader: WebCartReaderPort | None = None,
        catalog: CatalogPort | None = None,
    ) -> None:
        self._history_store = history_store
        self._load_session = load_session
        self._metadata_store = metadata_store
        self._event_bus = event_bus
        self._tenant_id = tenant_id
        # HU web-cart: reader de la Store API + catálogo para el matching de
        # la hidratación. Ambos opcionales — sin ellos, todo cart ref degrada
        # al flujo conversacional normal (la nota de lead caliente queda).
        self._web_cart_reader = web_cart_reader
        self._catalog = catalog
        # HU-WA24H-001 Sprint 2: factory para emitir ServiceWindowOpenedEvent
        # / CustomerRepliedEvent via el dispatcher. Optional para tests del
        # ingest legacy que no necesitan el watchdog wiring.
        self._temporal_client_factory = temporal_client_factory

    async def execute(
        self,
        parsed: WhatsAppMessage,
        *,
        persisted_image_url: str | None = None,
    ) -> None:
        """Procesa un inbound parseado.

        ``persisted_image_url`` es un canal interno del reentry de visión: cuando
        ``_describe_image_and_reenter`` reinyecta la descripción como texto
        sintético, pasa acá la URL de la imagen ya persistida en el media store
        para que el evento del cliente en el JSONL la lleve y el dashboard la
        renderice. Los inbounds normales (texto del webhook) lo dejan en None.
        """
        session_id = f"{WHATSAPP_SESSION_PREFIX}{parsed.from_number}"

        # --- 1. Read metadata UNA vez al principio (atribución + typing) ---
        try:
            metadata = self._metadata_store.read(session_id)
        except Exception:  # noqa: BLE001 — best-effort
            metadata = {}

        # HU web-cart: token `ref:cart_<id>` del texto prellenado que genera
        # la página web. Detección 100% determinista (regex) — jamás del LLM.
        cart_ref = detect_cart_ref(parsed.text)

        # --- 2a. Origin classification (5 buckets para reporting) ---
        # Sticky first-touch + last_touch updated cada inbound. Se persiste
        # ANTES del CTWA flow porque queremos clasificar incluso a clientes
        # `direct` (sin referral) o `web_referral` (referral sin ctwa_clid).
        self._handle_origin(
            session_id=session_id,
            metadata=metadata,
            referral=parsed.referral,
            inbound_message_id=parsed.message_id,
            cart_ref=cart_ref,
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
        # Re-engagement (bug run 3b3fbaee): si el último episodio ya estaba
        # CERRADO, este inbound abre uno nuevo. Capturamos el episodio cerrado
        # ANTES de que `ensure_active_episode` mute `episodes[]`, para inyectar
        # una nota de frontera en el plugin_context del turno (el LLM, cuyo
        # memory_window arrastra la cola del episodio anterior, si no, no
        # saluda y re-surfacea el pedido viejo). Solo dispara en el PRIMER
        # turno del episodio nuevo — el siguiente inbound ya verá el episodio
        # activo y no entrará acá.
        #
        # GUARD (bug sesión wa_573125671604): NO corremos el ciclo de vida de
        # episodios cuando la conversación ya está en manos de un humano
        # (active_route == humano). El cliente le responde al humano (p.ej. el
        # comprobante de pago tras un CONFIRMADO_PAGO_PENDIENTE), no arranca una
        # venta nueva. Si lo corriéramos, `ensure_active_episode` vería el
        # episodio cerrado, abriría uno nuevo y RESETEARÍA `metadata.tag` a
        # NO_ETIQUETADO — borrando el `tag=HUMANO` que dejó `escalate_to_human`
        # y dejando el chat huérfano (route=humano pero FUERA de la bandeja
        # humana del dashboard, que filtra por tag=HUMANO). El mensaje igual se
        # persiste al JSONL más abajo para que el humano lo lea; lo único que
        # saltamos es la rotación de episodios + el reset del tag. Al volver al
        # bot (return-to-bot pone active_route=ventas) se reanuda el ciclo.
        episode_boundary_note: str | None = None
        if metadata.get("active_route") != ROUTE_HUMANO:
            _episodes_before = metadata.get("episodes") or []
            _prev_closed_episode = (
                _episodes_before[-1]
                if _episodes_before
                and _episodes_before[-1].get("closed_at_ms") is not None
                else None
            )
            episode_boundary_note = (
                _build_episode_boundary_note(_prev_closed_episode)
                if _prev_closed_episode is not None
                else None
            )
            ensure_active_episode(
                metadata,
                now_ms=now_ms,
                inbound_message_id=parsed.message_id,
                referral_snapshot=_make_episode_snapshot(
                    parsed.referral, has_cart_ref=bool(cart_ref)
                ),
                msgs_count_at_start=msgs_count_at_start,
            )

        # HU-WA24H-001 F1.1: persistir timestamps de la ventana de servicio.
        # Cada inbound del cliente reabre la ventana 24h — esto es lo que
        # permite al watchdog (Sprint 2) saber cuándo está por cerrarse y
        # disparar un utility template legítimo.
        metadata["last_inbound_at_ms"] = now_ms
        metadata["service_window_expires_at_ms"] = compute_service_window_expiry(now_ms)

        # Opt-out de marketing (plugin marketing, campañas directas): el
        # template aprobado promete "respóndeme NO MÁS y te doy de baja" —
        # este es el punto que la CUMPLE. Determinista (sin LLM): un pedido
        # de baja no puede depender de interpretación. Sticky: solo lo
        # revierte el operador editando el metadata.
        if (
            not metadata.get("marketing_opt_out")
            and parsed.text
            and detect_marketing_opt_out(parsed.text, metadata, now_ms)
        ):
            metadata["marketing_opt_out"] = True
            metadata["marketing_opt_out_at_ms"] = now_ms
            logger.info(
                "marketing_opt_out_detected",
                session_id=session_id,
                text_preview=parsed.text[:60],
            )

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

        # Punto 2 (escala Window Strategist): mantener el índice liviano de
        # reactivación en el mismo momento del estampado — el snapshot builder
        # shortlistea sin escanear el vault. Best-effort: un índice roto JAMÁS
        # tumba el ingest (el fallback del builder es full scan).
        try:
            update_reengagement_index_entry(
                WORKSPACE_VAULT_DIR, session_id, metadata, now_ms=now_ms
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "reengagement_index_update_failed", session_id=session_id
            )

        # --- 2e. Web cart hot lead: hidratación best-effort (HU web-cart) ---
        # Solo la PRIMERA vez que vemos este cart_id EN este episodio (doble
        # tap del link = no-op; cart_id nuevo O re-tap en episodio nuevo =
        # captura nueva, FM-04). Inline con timeout corto (L-2): si CUALQUIER
        # cosa falla, degrada en silencio y el bot vende con lo que dice el
        # mensaje. Guard route humano: con un humano al mando no sembramos
        # drafts ni notas (mismo principio que el episode lifecycle).
        #
        # Premortem FM-01: la hidratación mete un await de hasta ~3s adentro
        # de la ventana read→write del ingest — TODO write de esta sección va
        # por `metadata_store.update()` (RMW atómico bajo flock, lectura
        # FRESCA) para no evaporar writes concurrentes de otra ráfaga.
        if cart_ref and metadata.get("active_route") != ROUTE_HUMANO:
            captured = {"new": False}

            def _capture_mutator(fresh: dict[str, Any]) -> dict[str, Any] | None:
                if fresh.get("active_route") == ROUTE_HUMANO:
                    return None
                captured["new"] = apply_web_cart_capture(
                    fresh, cart_id=cart_ref, now_ms=now_ms
                )
                return fresh if captured["new"] else None

            fresh_after_capture = self._metadata_store.update(
                session_id, _capture_mutator
            )
            if fresh_after_capture is not None:
                metadata = fresh_after_capture

            if captured["new"]:
                episodes = metadata.get("episodes") or []
                existing_slots = (
                    ((episodes[-1].get("order_draft") or {}).get("slots") or {})
                    if episodes
                    else {}
                )
                status, reason, hydration = await self._resolve_web_cart_outcome(
                    cart_ref,
                    existing_slots=existing_slots,
                    session_phone=parsed.from_number,
                )

                def _apply_mutator(fresh: dict[str, Any]) -> dict[str, Any] | None:
                    state = fresh.get("web_cart") or {}
                    if state.get("cart_id") != cart_ref:
                        return None  # otro cart ganó mid-hidratación: abortar
                    if status == "hydrated" and hydration is not None:
                        if hydration.slots:
                            update_order_draft(
                                fresh, slots=hydration.slots, now_ms=_now_ms()
                            )
                        mark_web_cart_hydrated(
                            fresh,
                            items_summary=hydration.items_summary,
                            unmatched_titles=hydration.unmatched_titles,
                        )
                    else:
                        mark_web_cart_degraded(fresh, reason=reason or "unknown")
                        if reason == "cart_not_found":
                            # FM-08: cart VERIFICADO inexistente (404 real) —
                            # el origin no queda envenenado por un token falso.
                            _declassify_web_cart_origin(fresh, parsed)
                    return fresh

                updated = self._metadata_store.update(session_id, _apply_mutator)
                if updated is not None:
                    metadata = updated
                self._emit_web_cart_events(
                    session_id=session_id, metadata=metadata, cart_id=cart_ref
                )

        # --- 2d. HU-WA24H-001 Sprint 2: watchdog wiring ---
        # Después de persistir el timestamp, emitir los eventos que el
        # dispatcher manifest convertirá en (a) arranque del
        # ServiceWindowWatchdogWorkflow para este episodio, y (b) signal
        # de cancel si ya había uno corriendo de un episodio previo.
        #
        # Fire-and-forget (spawn safe): el ingest del webhook no debe
        # demorar por el dispatch. Si el dispatcher falla (Temporal
        # down), el inbound del cliente sigue ruteándose normal — el
        # watchdog quedará no programado para este turno, lo cual es
        # mejor que perder el inbound entero.
        await self._emit_watchdog_events(session_id, metadata)

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

        # --- 4.5. Imagen inbound: defer a la descripción por visión ---
        # El agente (DeepSeek) es text-only. Igual que el audio, encolamos la
        # "lectura" de la imagen en un modelo multimodal (Gemini) y NO
        # delegamos al agente hasta tener la descripción. La reinyección decide
        # (según clasificación) si la conversación va al humano (comprobante de
        # pago) o sigue con el agente (foto normal).
        if effective.requires_vision and effective.image_media_id:
            logger.info(
                "image_inbound_received_pending_vision",
                session=session_id,
                media_id=effective.image_media_id,
            )
            await self._emit_event(
                make_wa_interaction(
                    session_id=session_id,
                    tenant_id=self._tenant_id,
                    kind="image_received",
                    component_id=effective.image_media_id,
                    wa_message_id=parsed.message_id,
                )
            )
            metadata["pending_vision"] = {
                "media_id": effective.image_media_id,
                "inbound_message_id": parsed.message_id,
                "mime_type": effective.image_mime_type,
            }
            self._safe_write_metadata(session_id, metadata)
            _spawn_safe(
                self._describe_image_and_reenter(parsed),
                label="vision.describe_and_reenter",
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

        # --- 5.7. Documento PDF inbound (comprobante de pago probable): bajar
        #     los bytes de Meta (cap 10 MB — arriba de eso NO se descarga) y
        #     persistirlos para que el operador pueda ABRIR el archivo desde el
        #     dashboard. Best-effort: si falla, el marker igual fluye (sin chip
        #     clickeable). Sin visión ni reentry — el marker ya nombra el
        #     archivo. Política espejo del comprobante-imagen: el chat pasa a
        #     verificación humana ANTES de que el bot improvise una
        #     confirmación de pago que nadie miró, y el cliente recibe el aviso
        #     de cortesía. Fail-toward-human: se rutea aunque la descarga
        #     falle. Se muta el dict LOCAL (no `_route_to_human`, que escribe
        #     sobre lectura fresca) para que los writes posteriores de este
        #     mismo `execute` no pisen la ruta. ---
        persisted_document_url: str | None = None
        if effective.document_media_id:
            from src.platform.media import KIND_PDF_DOCUMENT

            persisted_doc = await self._persist_inbound_document(
                session_id,
                effective.document_media_id,
                effective.document_mime_type,
            )
            persisted_document_url = persisted_doc[0] if persisted_doc else None
            metadata_dirty = False
            if persisted_doc:
                self._index_persisted_media(
                    metadata,
                    media_id=effective.document_media_id,
                    filename=persisted_doc[1],
                    kind=KIND_PDF_DOCUMENT,
                )
                metadata_dirty = True
            notify_client = False
            if metadata.get("active_route", ROUTE_VENTAS) != ROUTE_HUMANO:
                nombre = effective.document_filename or "sin nombre"
                self._apply_human_route(
                    metadata,
                    motivo=(
                        f"Cliente envió un documento PDF ({nombre}). Posible "
                        "comprobante de pago — verificar la recepción del pago "
                        "en el dashboard de orders y confirmar el envío o "
                        "abortar."
                    ),
                    reason_category="PAYMENT_VERIFICATION_PENDING",
                )
                metadata_dirty = True
                notify_client = True
            if metadata_dirty:
                self._safe_write_metadata(session_id, metadata)
            if notify_client:
                try:
                    from src.platform.whatsapp import client as wa_client

                    await wa_client.send_message(
                        parsed.phone_number_id,
                        parsed.from_number,
                        "Recibí tu documento 🤍. Un colega del equipo lo "
                        "revisa y te confirma enseguida.",
                    )
                except Exception:  # noqa: BLE001 — cortesía best-effort
                    pass

        # --- 6. Persistir history (texto efectivo, NO el JSON raw) ---
        # `persisted_image_url` solo viene poblado desde el reentry de visión:
        # el evento del cliente queda con la foto adjunta para el dashboard.
        # Los kwargs de documento solo se pasan cuando el PDF quedó persistido
        # (mantiene compatible cualquier store/fake con la firma anterior).
        if persisted_document_url:
            self._history_store.append_user_event(
                session_id,
                effective.text,
                image_url=persisted_image_url,
                document_url=persisted_document_url,
                document_filename=effective.document_filename,
            )
        else:
            self._history_store.append_user_event(
                session_id, effective.text, image_url=persisted_image_url
            )

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
        # Breadcrumb determinista del pedido: si el episodio activo tiene un
        # order_draft proyectable (slots no vacios, episodio sin order_id), lo
        # inyectamos al plugin_context para que el LLM NO vuelva a preguntar
        # datos ya confirmados (color, aroma, ciudad, ...). Episodio-scoped por
        # construccion: un episodio nuevo de re-engagement no tiene draft -> no
        # se proyecta -> no leak (mismo principio que la nota de frontera de
        # arriba). Advisory: register_order sigue siendo la fuente de verdad.
        _projectable_draft = get_projectable_draft(metadata)
        order_draft_note = (
            build_order_draft_note(_projectable_draft)
            if _projectable_draft
            else None
        )
        # Cita de foto: si el cliente respondió CITANDO una foto que
        # enviamos (context.id ∈ outbound_media_index), le decimos al LLM
        # exactamente cuál — "esta me gusta" deja de ser ambiguo (caso
        # wa_573125671604: pedido registrado con el diseño equivocado).
        photo_citation_note = _build_photo_citation_note(
            parsed.context, metadata
        )
        # HU web-cart: nota de lead caliente — se proyecta cada turno
        # mientras el episodio activo no tenga orden registrada (mismo ciclo
        # de vida que el breadcrumb del draft).
        web_cart_note = build_web_cart_note(metadata)
        await self._load_session.execute(
            session_id=session_id,
            message=effective.text,
            phone_number_id=parsed.phone_number_id,
            extra_context=[
                note
                for note in (
                    episode_boundary_note,
                    web_cart_note,
                    order_draft_note,
                    photo_citation_note,
                )
                if note
            ]
            or None,
        )

    # =========================================================================
    # Helpers
    # =========================================================================

    async def _resolve_web_cart_outcome(
        self,
        cart_id: str,
        *,
        existing_slots: dict[str, Any],
        session_phone: str | None,
    ) -> tuple[str, str | None, Any]:
        """Lee el cart de Medusa y computa el mapping SIN mutar metadata.

        Devuelve `(status, reason, hydration)`: `("hydrated", None, h)` o
        `("degraded", <reason>, None)`. Best-effort TOTAL: cualquier fallo
        (sin wiring, timeout, auth, mapping roto) degrada y el flujo sigue —
        la misión es vender. La aplicación al metadata la hace el caller bajo
        el RMW atómico (FM-01)."""
        import asyncio

        if self._web_cart_reader is None or self._catalog is None:
            return ("degraded", "reader_unavailable", None)
        try:
            snapshot = await asyncio.wait_for(
                self._web_cart_reader.get_cart(cart_id),
                timeout=_WEB_CART_HYDRATION_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — degrade, jamás tumbar el ingest
            logger.warning(
                "web_cart_hydration_failed",
                cart_id=cart_id,
                reason=type(exc).__name__,
            )
            return ("degraded", type(exc).__name__, None)
        if snapshot is None:
            # Contrato del port: None == cart VERIFICADO como inexistente.
            return ("degraded", "cart_not_found", None)

        try:
            hydration = await map_cart_to_draft(
                snapshot,
                catalog=self._catalog,
                existing_slots=existing_slots,
                session_phone=session_phone,
            )
        except Exception as exc:  # noqa: BLE001
            # FM-07: un bug de mapping en prod debe ser diagnosticable — log
            # con contexto, no solo el class name en el evento.
            logger.warning(
                "web_cart_mapping_failed",
                cart_id=cart_id,
                reason=type(exc).__name__,
                error=str(exc),
            )
            return ("degraded", type(exc).__name__, None)
        return ("hydrated", None, hydration)

    def _emit_web_cart_events(
        self, *, session_id: str, metadata: dict[str, Any], cart_id: str
    ) -> None:
        """Analytics del web cart (fire-and-forget, mismo patrón referral)."""
        state = metadata.get("web_cart") or {}
        status = state.get("status", "degraded")
        unmatched = state.get("unmatched_titles") or []
        logger.info(
            "web_cart_captured",
            session=session_id,
            cart_id=cart_id,
            status=status,
            reason=state.get("reason"),
        )
        if self._event_bus is None:
            return
        items_summary = state.get("items_summary") or []
        captured = make_web_cart_captured(
            session_id=session_id,
            tenant_id=self._tenant_id,
            cart_id=cart_id,
            status=status,
            reason=state.get("reason"),
            items_count=(
                len(items_summary) + len(unmatched)
                if status == "hydrated"
                else None
            ),
        )
        _spawn_safe(
            self._event_bus.record(captured),
            label="analytics.web_cart_captured",
            session_id=session_id,
        )
        if unmatched:
            mismatch = make_web_cart_product_mismatch(
                session_id=session_id,
                tenant_id=self._tenant_id,
                cart_id=cart_id,
                unmatched_titles=unmatched,
            )
            _spawn_safe(
                self._event_bus.record(mismatch),
                label="analytics.web_cart_product_mismatch",
                session_id=session_id,
            )

    def _handle_origin(
        self,
        *,
        session_id: str,
        metadata: dict[str, Any],
        referral: dict[str, Any] | None,
        inbound_message_id: str | None,
        cart_ref: str | None = None,
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
        channel = _classify_origin_channel(referral, has_cart_ref=bool(cart_ref))
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

    async def _emit_watchdog_events(
        self,
        session_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """HU-WA24H-001 Sprint 2: emite los eventos del watchdog vía dispatcher.

        Dos eventos opcionales (mutuamente NO-exclusivos):

        1. **`ServiceWindowOpenedEvent`** — siempre que haya episodio activo
           y `active_route` esté en {ventas, remarketing} y se pueda
           computar `watchdog_fire_at(metadata)`. El dispatcher manifest lo
           rutea a `start_workflow_with_replace` con workflow_id
           `watchdog-{session_id}-{episode_id}`.

        2. **`CustomerRepliedEvent`** — solo si `metadata.watchdog.workflow_id`
           está poblado (había watchdog corriendo del turno anterior). El
           dispatcher lo rutea a `via=signal, signal_name=cancel_watchdog`
           sobre el mismo workflow_id.

        Fire-and-forget bajo `_spawn_safe`:
          * Errores del dispatcher NO bloquean el routing del inbound (el
            cliente espera respuesta — no podemos demorar por un Temporal
            transient).
          * Si `temporal_client_factory` es None (tests, composition
            parcial), noopea silenciosamente.
          * Si no hay episodio activo ni ruta de bot, noopea — no hay
            sentido en programar watchdog sobre conversaciones humano.

        El método NO toca metadata.watchdog: eso lo hace el workflow del
        watchdog vía `persist_watchdog_outcome_activity`. Acá solo
        emitimos los eventos.
        """
        if self._temporal_client_factory is None:
            return

        active_route = metadata.get("active_route", ROUTE_VENTAS)
        # ROUTE_HUMANO: no programar watchdog (humano tomó el caso). Igual
        # que LoadOrStartSalesSession, el bot debe respetar la decisión.
        if active_route not in (ROUTE_VENTAS, ROUTE_REMARKETING):
            return

        # Episodio activo: el watchdog es per-episodio. Sin episodio activo
        # (caso defensivo — ensure_active_episode debería haber corrido
        # antes), nada que programar.
        episodes = metadata.get("episodes") or []
        if not episodes:
            return
        active_ep = episodes[-1]
        if active_ep.get("closed_at_ms") is not None:
            return
        episode_id = active_ep.get("episode_id")
        if not isinstance(episode_id, str):
            return

        fire_at_ms = watchdog_fire_at(metadata)
        if fire_at_ms is None:
            return

        existing_watchdog = metadata.get("watchdog") or {}
        had_running_watchdog = bool(existing_watchdog.get("workflow_id"))

        async def _do_emit() -> None:
            client = await self._temporal_client_factory()

            # 1. CustomerRepliedEvent FIRST (cancel any prior watchdog) —
            #    debe llegar antes que el start_workflow_with_replace,
            #    pero como replace mata y reinicia, el orden no importa
            #    en práctica. Mantener el orden por claridad de log.
            if had_running_watchdog:
                await dispatch_envelope_with_client(
                    envelope_for(
                        CustomerRepliedEvent(
                            session_id=session_id,
                            episode_id=episode_id,
                        ),
                        source_plugin="chats",
                        source_worker="sales",
                    ),
                    client,
                )

            # 2. ServiceWindowOpenedEvent → arranca (o reemplaza) el watchdog.
            await dispatch_envelope_with_client(
                envelope_for(
                    ServiceWindowOpenedEvent(
                        session_id=session_id,
                        episode_id=episode_id,
                        fire_at_ms=fire_at_ms,
                        suggested_template_kind="",
                    ),
                    source_plugin="chats",
                    source_worker="sales",
                ),
                client,
            )

        _spawn_safe(
            _do_emit(),
            label="watchdog.emit_events",
            session_id=session_id,
        )

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
                        "¿Me lo escribes en un mensaje? 🤍"
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

    async def _describe_image_and_reenter(self, parsed: WhatsAppMessage) -> None:
        """Describe la imagen (Gemini visión) y re-ejecuta el ingest con un
        mensaje text sintético. Background task — el webhook ya devolvió 200.

        Patrón espejo de ``_transcribe_and_reenter`` (audio). Reglas extra:

          * **Comprobante de pago**: si la conversación NO está ya en turno
            humano, la asigna al humano (``active_route=humano``) y le avisa al
            cliente — un humano verifica el pago ANTES de que el agente siga.
            Si ya estaba en turno humano, la descripción solo queda en el
            historial para el humano.
          * **Falla de visión**: reinyecta un placeholder para que el agente le
            pida amablemente al cliente que escriba qué necesita.
        """
        from src.platform.vision.composition import get_image_vision_port
        from src.platform.vision.dtos import VisionRequest
        from src.platform.whatsapp import client as wa_client

        media = parsed.media or {}
        media_id = media.get("id")
        if not isinstance(media_id, str) or not media_id:
            return
        session_id = f"{WHATSAPP_SESSION_PREFIX}{parsed.from_number}"
        caption = media.get("caption")
        caption = caption if isinstance(caption, str) and caption.strip() else None

        # Persistir la imagen para el dashboard ANTES de describirla. Es
        # independiente de la visión: aunque Gemini falle, el humano igual debe
        # poder ver la foto (un comprobante de pago se verifica mirándolo, no
        # leyendo una descripción). `persisted_image_url` viaja después al
        # evento del cliente en el JSONL via el reentry; `persisted` retiene el
        # filename para indexar la imagen (retención, Fase 0).
        persisted = await self._persist_inbound_image(
            session_id, media_id, media.get("mime_type")
        )
        persisted_image_url = persisted[0] if persisted else None

        port = get_image_vision_port()
        result = await port.describe(
            VisionRequest(
                media_id=media_id,
                mime_type=media.get("mime_type") or "image/jpeg",
            )
        )

        # Limpiar pending_vision + registrar el resultado en metadata
        try:
            metadata = self._metadata_store.read(session_id)
        except Exception:  # noqa: BLE001
            metadata = {}
        metadata.pop("pending_vision", None)
        if result.ok:
            recent = list(metadata.get("recent_image_descriptions") or [])
            recent.append(
                {
                    "media_id": media_id,
                    "kind": result.kind,
                    "description": result.description,
                    "provider": result.provider,
                    "cost_usd_estimate": result.cost_usd_estimate,
                }
            )
            metadata["recent_image_descriptions"] = recent[-20:]
        else:
            errs = list(metadata.get("vision_failures") or [])
            errs.append(
                {
                    "media_id": media_id,
                    "error": result.error,
                    "provider": result.provider,
                }
            )
            metadata["vision_failures"] = errs[-20:]
        # Retención (Fase 0): indexar la imagen persistida con su episodio,
        # tipo, clase de retención y timestamp. NO borra nada — deja la traza
        # lista para que un futuro job / S3 Lifecycle decida qué limpiar.
        if persisted is not None:
            self._index_persisted_media(
                metadata,
                media_id=media_id,
                filename=persisted[1],
                kind=result.kind if result.ok else "unknown",
            )
        self._safe_write_metadata(session_id, metadata)

        # Analytics
        await self._emit_event(
            make_wa_interaction(
                session_id=session_id,
                tenant_id=self._tenant_id,
                kind="image_described" if result.ok else "image_vision_failed",
                component_id=media_id,
                wa_message_id=parsed.message_id,
                payload_extra={
                    "kind": result.kind,
                    "provider": result.provider,
                    "cost_usd_estimate": result.cost_usd_estimate,
                    "latency_ms": result.latency_ms,
                    "error": result.error,
                    "is_payment_receipt": result.is_payment_receipt,
                },
            )
        )

        # --- Falla de visión: reinyectar placeholder y dejar que el agente le
        #     pida al cliente que escriba qué necesita ---
        if not result.ok:
            placeholder = "[el cliente envió una imagen que no pude ver bien]"
            if caption:
                placeholder += f' con el texto: "{caption}"'
            await self.execute(
                self._synthetic_text_message(parsed, placeholder),
                persisted_image_url=persisted_image_url,
            )
            return

        # --- Comprobante de pago + conversación aún en turno de bot: asignar a
        #     humano ANTES de reinyectar (así la reentrada no toca al agente) ---
        active_route = metadata.get("active_route", ROUTE_VENTAS)
        if result.is_payment_receipt and active_route != ROUTE_HUMANO:
            self._route_to_human(
                session_id=session_id,
                motivo=(
                    "Cliente envió un comprobante de pago: "
                    f"{result.description}. Verificar la recepción del pago en "
                    "el dashboard de orders y confirmar el envío o abortar."
                ),
                reason_category="PAYMENT_VERIFICATION_PENDING",
            )
            try:
                await wa_client.send_message(
                    parsed.phone_number_id,
                    parsed.from_number,
                    "¡Perfecto! Recibimos tu comprobante 🤍. Tu pedido pasa a "
                    "elaboración y cuando lo tengamos listo te informamos el "
                    "despacho.",
                )
            except Exception:  # noqa: BLE001
                pass

        # --- Reinyectar la descripción como texto y reentrar. Si la ruta quedó
        #     en humano (comprobante o handoff previo), LoadOrStart omite el
        #     dispatch al agente y la descripción solo queda en el historial
        #     para el humano. Si sigue en bot, el agente la procesa. ---
        if result.is_payment_receipt:
            synthetic_text = (
                f"[el cliente envió un comprobante de pago: {result.description}]"
            )
        else:
            synthetic_text = f"[el cliente envió una foto: {result.description}]"
        if caption:
            synthetic_text += f' con el texto: "{caption}"'
        logger.info(
            "image_described_reentry",
            session=session_id,
            kind=result.kind,
            provider=result.provider,
            cost_usd=result.cost_usd_estimate,
        )
        await self.execute(
            self._synthetic_text_message(parsed, synthetic_text),
            persisted_image_url=persisted_image_url,
        )

    async def _persist_inbound_document(
        self, session_id: str, media_id: str, mime_type: str | None
    ) -> tuple[str, str] | None:
        """Descarga un documento PDF del cliente y lo persiste en el media
        store. Espejo de :meth:`_persist_inbound_image` con tres diferencias:

        * **Cap de descarga** (:data:`_MAX_INBOUND_DOCUMENT_BYTES`): WhatsApp
          acepta documentos de hasta 100 MB; por encima de NUESTRO cap el
          fetcher ni descarga (usa el ``file_size`` declarado del metadata).
          El marker igual fluye — solo se pierde el chip clickeable.
        * **Sniff ``%PDF-``**: el mime lo declara Meta; si los bytes no abren
          como PDF no se persiste nada servible desde el vault.
        * **Logs con eventos ``inbound_document_*``** (no ``inbound_image_*``)
          para poder filtrar en prod por tipo de media.

        Best-effort: devuelve ``(url_relativa, filename)`` o ``None``.
        """
        from src.platform.audio.meta_media_fetcher import fetch_media_bytes
        from src.platform.media import media_url_for, persist_inbound_image

        try:
            fetched = await fetch_media_bytes(
                media_id, max_bytes=_MAX_INBOUND_DOCUMENT_BYTES
            )
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning(
                "inbound_document_fetch_failed",
                session=session_id,
                media_id=media_id,
                error=str(e),
            )
            return None
        if not fetched:
            return None
        data, fetched_mime = fetched
        if not data.startswith(b"%PDF-"):
            logger.warning(
                "inbound_document_not_pdf",
                session=session_id,
                media_id=media_id,
                declared_mime=mime_type or fetched_mime,
            )
            return None
        try:
            filename = persist_inbound_image(
                session_id, media_id, data, mime_type or fetched_mime
            )
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning(
                "inbound_document_persist_failed",
                session=session_id,
                media_id=media_id,
                error=str(e),
            )
            return None
        logger.info(
            "inbound_document_persisted",
            session=session_id,
            media_id=media_id,
            filename=filename,
            bytes=len(data),
        )
        return (media_url_for(session_id, filename), filename)

    async def _persist_inbound_image(
        self, session_id: str, media_id: str, mime_type: str | None
    ) -> tuple[str, str] | None:
        """Descarga la imagen de Meta y la persiste en el media store.

        Devuelve ``(url_relativa, filename)`` o None si algo falla — la URL va al
        JSONL/dashboard y el filename se indexa para retención (Fase 0).
        Best-effort: una falla acá NO rompe el pipeline de visión ni el ruteo —
        solo significa que el dashboard mostrará la descripción de texto sin la
        foto. Re-descarga del media_id (la URL temporal de Meta expira a los 5
        min, pero el media_id sigue resolviendo); el costo de un fetch extra de
        una imagen chica en background es despreciable frente al call de visión,
        y mantiene la persistencia desacoplada del adapter de visión.
        """
        from src.platform.audio.meta_media_fetcher import fetch_media_bytes
        from src.platform.media import media_url_for, persist_inbound_image

        try:
            fetched = await fetch_media_bytes(media_id)
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning(
                "inbound_image_fetch_failed",
                session=session_id,
                media_id=media_id,
                error=str(e),
            )
            return None
        if not fetched:
            return None
        data, fetched_mime = fetched
        try:
            filename = persist_inbound_image(
                session_id, media_id, data, mime_type or fetched_mime
            )
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning(
                "inbound_image_persist_failed",
                session=session_id,
                media_id=media_id,
                error=str(e),
            )
            return None
        logger.info(
            "inbound_image_persisted",
            session=session_id,
            media_id=media_id,
            filename=filename,
            bytes=len(data),
        )
        return (media_url_for(session_id, filename), filename)

    def _index_persisted_media(
        self,
        metadata: dict[str, Any],
        *,
        media_id: str,
        filename: str,
        kind: str,
    ) -> None:
        """Indexa una imagen persistida en ``metadata["media_index"]`` (Fase 0).

        Cada entrada vincula el archivo con su episodio, tipo, clase de retención
        y timestamp. Es la fuente de verdad para una limpieza futura: un job en
        disco, o (preferido) el uploader a S3 que traduce ``retention_class`` a
        un object tag y deja que **S3 Lifecycle** borre solo. NO borra ni mueve
        nada; solo muta ``metadata`` in-place (el caller hace el write).

        Idempotente: si el ``media_id`` ya está indexado (reentry doble), noopea.
        El ``episode_id`` es el del episodio activo cuando llegó la imagen.
        """
        from src.platform.media import retention_class_for

        index = list(metadata.get("media_index") or [])
        if any(
            isinstance(e, dict) and e.get("media_id") == media_id for e in index
        ):
            return
        episodes = metadata.get("episodes") or []
        episode_id = None
        if episodes and isinstance(episodes[-1], dict):
            episode_id = episodes[-1].get("episode_id")
        index.append(
            {
                "media_id": media_id,
                "filename": filename,
                "episode_id": episode_id,
                "kind": kind,
                "retention_class": retention_class_for(kind),
                "created_at_ms": _now_ms(),
            }
        )
        metadata["media_index"] = index

    @staticmethod
    def _synthetic_text_message(
        parsed: WhatsAppMessage, text: str
    ) -> WhatsAppMessage:
        """Construye un WhatsAppMessage text sintético preservando atribución +
        contexto (mismo patrón que el reentry de audio)."""
        return WhatsAppMessage(
            message_id=f"{parsed.message_id}_vision",
            from_number=parsed.from_number,
            phone_number_id=parsed.phone_number_id,
            text=text,
            media=None,
            timestamp=parsed.timestamp,
            msg_type="text",
            referral=parsed.referral,
            context=parsed.context,
        )

    @staticmethod
    def _apply_human_route(
        data: dict[str, Any],
        *,
        motivo: str,
        reason_category: str,
    ) -> None:
        """Muta ``data`` in-place con la ruta humano (tag + motivo +
        status_history). El WRITE es responsabilidad del caller: los flujos
        que ya tienen el dict local de ``execute`` en la mano (documento PDF)
        lo aplican acá y persisten UNA vez — llamar a :meth:`_route_to_human`
        (que lee fresco y escribe) dejaría el dict local stale y los writes
        posteriores de ``execute`` pisarían la ruta (lost-update)."""
        data["active_route"] = ROUTE_HUMANO
        data["tag"] = "HUMANO"
        data["motivo"] = motivo
        data["escalation_reason"] = reason_category
        history = data.setdefault("status_history", [])
        history.append(
            {
                "tag": "HUMANO",
                "motivo": motivo,
                "active_route": ROUTE_HUMANO,
                "reason_category": reason_category,
                "timestamp": _now_ms() / 1000.0,
            }
        )

    def _route_to_human(
        self,
        *,
        session_id: str,
        motivo: str,
        reason_category: str,
    ) -> None:
        """Asigna la conversación al humano desde la capa de ingest (mismo
        efecto que ``EscalateToHumanTool``, pero gatillado por el sistema, no
        por el LLM). Espejo de ``platform/tools/escalation.py``.

        Persiste ``active_route=humano`` + tag HUMANO + motivo + status_history
        para que ``LoadOrStartSalesSession`` omita el dispatch al agente y el
        dashboard muestre la conversación en la cola humana.
        """
        try:
            data = self._metadata_store.read(session_id)
        except Exception:  # noqa: BLE001
            data = {}
        self._apply_human_route(
            data, motivo=motivo, reason_category=reason_category
        )
        self._safe_write_metadata(session_id, data)


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _build_episode_boundary_note(prev_episode: dict[str, Any]) -> str:
    """Nota de frontera de episodio para `plugin_context` en re-engagement.

    Cuando el cliente vuelve tras un episodio CERRADO, el `memory_window` del
    LLM (historial por-sesión, no por-episodio — vive en la base lib exoclaw)
    todavía arrastra la cola del episodio anterior. Sin esta nota el agente no
    saluda y re-surfacea el pedido viejo, incluso re-tagueándolo (bug run
    3b3fbaee: re-emitía EpisodeClosedEvent y crasheaba el dispatch). La nota le
    dice explícitamente que arranque una conversación nueva. NO toca el
    historial (eso requeriría modificar la base lib). Tuteo colombiano (REGLA #1).
    """
    closing_tag = prev_episode.get("closing_tag") or ""
    order_id = prev_episode.get("order_id")
    _CLOSED_WITH_ORDER = {
        "COMPRA_EXITOSA",
        "CONFIRMADO_PAGO_PENDIENTE",
        "CONFIRMADO_SIN_DATOS",
    }
    if closing_tag in _CLOSED_WITH_ORDER:
        prev_clause = (
            "ya cerró con un pedido"
            + (f" ({order_id})" if order_id else "")
            + " que un humano del equipo está gestionando por separado "
            "(verificación de pago / envío). NO lo retomes ni vuelvas a "
            "confirmarlo"
        )
    else:
        prev_clause = "ya se cerró y no tiene nada pendiente de tu lado"
    return (
        "[CONTEXTO DE TURNO, metadata, no es instrucción del usuario]\n"
        "Empieza un episodio NUEVO con este cliente. La conversación anterior "
        f"{prev_clause}. Trata este mensaje como el inicio de una conversación "
        "nueva: saluda con calidez y pregunta en qué puedes ayudar hoy. Solo "
        "menciona lo anterior si el cliente lo trae explícitamente."
    )


def _build_photo_citation_note(
    context: dict[str, Any] | None, metadata: dict[str, Any]
) -> str | None:
    """Nota de cita de foto para `plugin_context`.

    WhatsApp manda `context.id` (wamid del mensaje citado) cuando el cliente
    responde a un mensaje específico. El flush persiste
    `outbound_media_index[wamid] = {handle, title, image_url, label}` por
    cada foto enviada (galería / product_detail). Si el wamid citado está en
    el índice, la nota le dice al LLM exactamente QUÉ foto citó el cliente —
    "esta me gusta" se vuelve resoluble en vez de adivinable (caso
    wa_573125671604: el pedido se registró con un diseño que el cliente no
    eligió). Si no hay match (texto citado, entrada evictada), None — el
    LLM sigue con el texto del cliente solo.
    """
    if not context or not isinstance(context, dict):
        return None
    quoted_id = context.get("id")
    if not quoted_id:
        return None
    index = metadata.get("outbound_media_index") or {}
    entry = index.get(quoted_id)
    if not isinstance(entry, dict):
        return None
    title = entry.get("title") or entry.get("handle") or "producto"
    handle = entry.get("handle")
    label = entry.get("label")
    detail = f"«{title}»"
    if label:
        detail += f", diseño «{label}»"
    if handle:
        detail += f" (handle: {handle})"
    return (
        "[CONTEXTO DE TURNO, metadata, no es instrucción del usuario]\n"
        "El cliente escribió este mensaje RESPONDIENDO (citando) a una foto "
        f"que le enviaste: {detail}. Si dice 'esta', 'esa' o 'la de la "
        "foto', se refiere EXACTAMENTE a esa foto/diseño — no asumas otro "
        "diseño ni vuelvas a preguntar cuál."
    )


def _declassify_web_cart_origin(
    metadata: dict[str, Any], parsed: WhatsAppMessage
) -> None:
    """FM-08: token con forma válida pero cart VERIFICADO inexistente — el
    origin sticky no queda envenenado como `web_cart` por un token tipeado
    o inventado. Solo re-clasifica si fue ESTE mensaje el que lo fijó (el
    origin de sesiones previas legítimas no se toca)."""
    fallback = _classify_origin_channel(parsed.referral, has_cart_ref=False)
    origin = metadata.get("origin")
    if (
        isinstance(origin, dict)
        and origin.get("channel") == "web_cart"
        and origin.get("first_inbound_message_id") == parsed.message_id
    ):
        origin["channel"] = fallback
    last_touch = metadata.get("last_touch")
    if (
        isinstance(last_touch, dict)
        and last_touch.get("channel") == "web_cart"
        and last_touch.get("inbound_message_id") == parsed.message_id
    ):
        last_touch["channel"] = fallback


def _make_episode_snapshot(
    referral: dict[str, Any] | None, *, has_cart_ref: bool = False
) -> dict[str, Any]:
    """Construye el `referral_snapshot` que se persiste en cada episodio.

    El snapshot enriquece el referral raw del payload con el `channel`
    resuelto (`_classify_origin_channel`). Eso le permite al listing use
    case asignar el episodio a una campaña con la atribución del INBOUND
    que abrió ese episodio — no la sticky de la sesión.

    Para clientes sin referral (channel=direct), el snapshot igual se
    persiste con `channel="direct"` para que el bucket del episodio sea
    explícito en disco. `has_cart_ref` (premortem FM-05): un episodio
    abierto por el botón de la tienda web queda `channel="web_cart"` — sin
    esto, ads/aggregation lo contaría como orgánico.
    """
    base: dict[str, Any] = dict(referral) if referral else {}
    base["channel"] = _classify_origin_channel(referral, has_cart_ref=has_cart_ref)
    return base


def _classify_origin_channel(
    referral: dict[str, Any] | None, *, has_cart_ref: bool = False
) -> str:
    """Determina el bucket de origen de un inbound según el payload referral.

    Decisión tabla:
      | referral con ctwa_clid    | source_type ("ad" | "post"), default "ad"
      | texto con `ref:cart_<id>` | "web_cart" (HU web-cart hot lead)
      | referral sin ctwa_clid    | "web_referral"
      | referral=None             | "direct"

    El clid GANA sobre el cart ref: la atribución CAPI no se pierde aunque
    el cliente venga con carrito (el carrito queda igual en
    `metadata.web_cart`). `web_referral` cubre WhatsApp Web (donde Meta
    omite el clid) — no atribuible vía Conversions API.
    """
    if referral and referral.get("ctwa_clid"):
        source_type = referral.get("source_type")
        return source_type if source_type in ("ad", "post") else "ad"
    if has_cart_ref:
        return "web_cart"
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
