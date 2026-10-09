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

import copy
from typing import Any, Awaitable, Callable, Protocol, TYPE_CHECKING

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
from src.plugins.chats.agent.sales.cart_lines import CartLine, build_cart_note, lines_from_products
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.translate import (
    EffectiveText,
    translate_to_effective_text,
)
from src.platform.config import WORKSPACE_VAULT_DIR
from src.sdk.messagingkit import (
    fresh_resume_label,
    opt_out_campaign_id,
    resolve_local_timezone,
    update_reengagement_index_entry,
)
from src.platform.session_history import FilesystemMessageHistoryStore
from src.platform.whatsapp.window import (
    compute_ctwa_window_expiry,
    compute_service_window_expiry,
    watchdog_fire_at,
)
from src.plugins.chats.agent.sales.use_cases.after_purchase import (
    CLOSED_WITH_ORDER,
    OrderFactsReader,
    after_order_marker,
    build_after_purchase_note,
    order_in_course,
)
from src.plugins.chats.agent.sales.use_cases.campaign_reply import (
    build_campaign_reply_note,
    campaign_label,
    mark_campaign_episode,
    quoted_campaign_touch,
    quote_campaign_in_turn,
    unanswered_campaign_touch,
)
from src.plugins.chats.agent.sales.use_cases.closing_ack import (
    ACK_TEXT,
    ack_shape,
)
from src.plugins.chats.agent.sales.use_cases.episode_memory import (
    quote_template_in_turn,
    request_clean_llm_history,
    unseen_template_text,
    with_previous_episode,
)
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    CAMPAIGN_CLOSING_TAG,
    close_episode,
    count_session_jsonl_lines,
    ensure_active_episode,
    get_active_episode,
)
from src.plugins.chats.agent.sales.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)
from src.plugins.chats.agent.sales.metadata_reads import read_retrying_transient_errors
from src.plugins.chats.shared.quotes import build_quote_note, resolve_quote
from src.plugins.chats.shared.purchase_signals import (
    build_deferral_note,
)
from src.plugins.chats.agent.sales.decisions.readings import (
    Inbound,
    apply_readings,
    read_catalog_gap,
    read_coupon_talk,
)
from src.plugins.chats.agent.sales.use_cases.funnel_stage import resolve_funnel_stage
from src.plugins.chats.agent.sales.use_cases.order_draft import (
    build_order_draft_note,
    get_projectable_draft,
    update_order_draft,
)
from src.plugins.chats.agent.sales.use_cases.coupon_application import (
    CouponApplication,
    store_coupon_application,
)
from src.plugins.chats.agent.sales.use_cases.catalog_gap import catalog_gap_note
from src.plugins.chats.agent.sales.use_cases.photo_product import (
    PhotoIdentification,
    build_photo_facts_note,
    build_photo_product_note,
    photo_reentry_text,
)
from src.plugins.chats.agent.sales.use_cases.coupon_quota import QuotaOffer
from src.plugins.chats.agent.sales.use_cases.photo_reads import PhotoReads
from src.plugins.chats.agent.sales.use_cases.coupons import (
    applied_coupon,
    build_coupon_note,
)
from src.plugins.chats.agent.sales.use_cases.web_product_ref import (
    CATALOG_ORIGIN,
    apply_web_product_capture,
    build_web_product_note,
    detect_agent_source,
    detect_product_ref,
    mark_web_product_resolved,
    mark_web_product_unresolved,
    record_agent_referral,
    referred_product_id,
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
    from pathlib import Path

    from temporalio.client import Client

    from src.sdk.connectorkit import (
        CatalogPort as CatalogPort,
        WebCartReaderPort as WebCartReaderPort,
    )


#: DI-friendly factory for the Temporal client. Async so the composition
#: root can wire `get_temporal_client` directly without wrapping.
TemporalClientFactory = Callable[[], Awaitable["Client"]]


class InboundReadingsProvider(Protocol):
    """Las lecturas del cliente (motor de decisiones, `decisions/readings.py`):
    compra, retoma y baja (`read`) y el acuse tras la despedida (`read_ack`,
    que se pide antes del ciclo de episodios)."""

    async def read(self, inbound: Inbound) -> Any: ...

    async def read_ack(self, inbound: Inbound) -> Any: ...


#: `execute(..., customer_text=_FROM_MESSAGE)`: el texto del cliente es el del mensaje.
_FROM_MESSAGE: Any = object()

#: `(código, now_ms) → CouponApplication`: valida el cupón de la campaña
#: contra Medusa y su cupo (`resolve_coupon_application`), sin escribir.
CampaignCouponApplier = Callable[[str, int], Awaitable[CouponApplication]]

#: `snapshot de la promoción → QuotaOffer`: cuánto queda ahora del cupo del
#: cupón aplicado (el cupo del vault menos lo vendido en Medusa).
CouponUnitsReader = Callable[[dict[str, Any]], Awaitable[QuotaOffer]]

# HU web-cart: timeout de la hidratación inline (patrón L-2 — el webhook no
# puede demorar el primer turno; cualquier fallo degrada en silencio).
_WEB_CART_HYDRATION_TIMEOUT_S = 3.0
#: Tope de la espera de un mensaje del cliente por su foto (visión ~1,5 s,
#: hasta ~4,5 s comparando con el catálogo; el paso de imagen tiene tope de 6 s).
PHOTO_WAIT_MAX_S = 10.0
#: Tope del aviso al workflow de que una foto se está leyendo (una señal).
_PHOTO_NOTICE_TIMEOUT_S = 2.0

#: Validar el cupón de la campaña (promociones de Medusa + vendidas del cupo)
#: antes del primer turno. Si no alcanza, el bot lo aplica con `apply_coupon`.
_CAMPAIGN_COUPON_TIMEOUT_S = 5.0

#: Lo que el cliente pide y no existe: el catálogo es el snapshot local (~30
#: productos); el tope evita leer de más si crece, el timeout es por si no
#: responde.
_CATALOG_GAP_LIMIT = 200
_CATALOG_GAP_TIMEOUT_S = 2.0

#: Errores de transcripción en los que la nota de voz nunca llegó a Google
#: (no se bajó de Meta, o el proveedor falló o limitó): no se cobran a la
#: conversación. Cualquier otro (vacío, inaudible, muy larga) sí: Google la oyó.
_AUDIO_NOT_BILLED = ("media_fetch_failed", "rate_limit", "provider_error")

#: Cap de descarga para documentos PDF inbound (comprobantes). La restricción
#: de subida la impone WhatsApp (100 MB); de nuestro lado, por encima de este
#: cap NO se descarga (el fetcher corta con el `file_size` declarado, antes de
#: bajar bytes). 10 MB cubre todo comprobante legítimo: uno digital pesa
#: 30–150 KB, un escaneo B/N 200–500 KB y un escaneo a color de una página
#: llega a ~8–10 MB.
_MAX_INBOUND_DOCUMENT_BYTES = 10 * 1024 * 1024

logger = structlog.get_logger()


#: Meta reintenta un webhook no aceptado hasta 7 días (+1 de margen): un
#: `timestamp` más viejo no puede ser una re-entrega real — es un reloj
#: sintético (simulador, laboratorio, fixtures) y cuenta la llegada.
_META_RETRY_HORIZON_MS = 8 * 24 * 3600 * 1000


def _customer_sent_ms(parsed: WhatsAppMessage, now_ms: int) -> int:
    """Cuándo escribió el cliente: el `timestamp` de Meta, sin pasar de
    `now_ms` (un reloj de Meta adelantado no estira la ventana). Sin
    timestamp válido o fuera del horizonte de reintentos de Meta, la llegada."""
    ts_ms = _inbound_meta(parsed)["ts_ms"]
    if not isinstance(ts_ms, int) or now_ms - ts_ms > _META_RETRY_HORIZON_MS:
        return now_ms
    return min(now_ms, ts_ms)


def _inbound_meta(parsed: WhatsAppMessage) -> dict[str, Any]:
    """`{wamid, ts_ms, kind}` del inbound (Meta manda la hora en segundos)."""
    ts = str(parsed.timestamp or "").strip()
    return {
        "wamid": parsed.message_id or None,
        "ts_ms": int(ts) * 1000 if ts.isdigit() else None,
        "kind": parsed.msg_type or "text",
    }


async def catalog_gap_note_for(
    catalog: CatalogPort | None, *, vault_dir: Path, session_id: str, text: str | None
) -> str | None:
    """Nota de lo que no existe en el catálogo; None sin catálogo o si falla
    (un aviso de más no justifica demorar ni tumbar el turno). Los términos
    los decide el motor (capacidad `fuera_de_catalogo`: la regla de hoy los
    propone y Jev solo puede quitar alguno); la nota es la de siempre con los
    que quedan. La usan el ingest y el sandbox del laboratorio: los dos arman
    exactamente la misma nota."""
    import asyncio

    if catalog is None or not text:
        return None
    try:
        result = await asyncio.wait_for(
            catalog.search("", limit=_CATALOG_GAP_LIMIT),
            timeout=_CATALOG_GAP_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — degrade, never break the ingest
        logger.warning("catalog_gap_check_failed", reason=type(exc).__name__)
        return None
    try:
        verdict = await read_catalog_gap(
            vault_dir,
            session_id=session_id,
            text=text,
            products=list(result.results),
        )
    except Exception as exc:  # noqa: BLE001 — degrade, never break the ingest
        logger.error("catalog_gap_reading_failed", session_id=session_id, error=f"{type(exc).__name__}: {exc}"[:300])
        return None
    return catalog_gap_note(verdict.value)


class PhotoIdentifierPort(Protocol):
    """Qué producto nuestro es la foto (``use_cases/photo_product``)."""

    async def identify(self, vision: Any, image: Any) -> PhotoIdentification: ...

    def refresh_index_soon(self) -> None: ...


#: `(session_id, wamid, done)`: el ingest empezó a leer una foto del cliente
#: (`done=False`) o la foto ya entró al bot (`done=True`). Texto antes de la
#: foto (2026-09-30): el workflow espera la foto en la misma ráfaga.
PhotoNotifier = Callable[[str, str, bool], Awaitable[None]]


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
        campaign_coupon: CampaignCouponApplier | None = None,
        coupon_units_now: CouponUnitsReader | None = None,
        readings: InboundReadingsProvider | None = None,
        photo_identifier: PhotoIdentifierPort | None = None,
        photo_wait_s: float = PHOTO_WAIT_MAX_S,
        photo_notifier: PhotoNotifier | None = None,
        order_facts: OrderFactsReader | None = None,
    ) -> None:
        self._history_store = history_store
        # Los datos reales de un pedido (OrderFacts): el cliente que escribe
        # justo después de comprar abre el episodio en post-venta, con la etapa
        # y el pago del pedido (caso pedido #64). Sin él, la nota de siempre.
        self._order_facts = order_facts
        self._photo_identifier = photo_identifier
        # Aviso al workflow de ventas: una foto del cliente se está leyendo (y
        # cuándo ya entró). Sin él, la foto entra como hoy.
        self._photo_notifier = photo_notifier
        # Las fotos de cada cliente que se están leyendo: sus mensajes esperan
        # a que la foto entre al bot (ráfaga partida, 2026-09-30).
        self._photo_reads = PhotoReads()
        self._photo_wait_s = photo_wait_s
        # Motor de decisiones (enchufe 1): las lecturas del cliente (compra,
        # retoma, baja) las da el proveedor; el ingest escribe los MISMOS
        # campos de hoy. Sin proveedor, el del motor con el registro de bots
        # (`reglas` por defecto: el resultado es el de siempre).
        self._readings = readings
        # Cupón de la campaña: se valida y aplica solo cuando el cliente
        # responde (sin esto el bot lo aplica con `apply_coupon`).
        self._campaign_coupon = campaign_coupon
        # Cupo del cupón aplicado: cuánto queda AHORA, releído con cada
        # mensaje (sin esto, lo que se guardó al aplicar el cupón).
        self._coupon_units_now = coupon_units_now
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
        customer_text: Any = _FROM_MESSAGE,
        photo_note: str | None = None,
        from_photo: bool = False,
    ) -> None:
        """Procesa un inbound parseado.

        ``persisted_image_url`` es un canal interno del reentry de visión: cuando
        ``_describe_image_and_reenter`` reinyecta la descripción como texto
        sintético, pasa acá la URL de la imagen ya persistida en el media store
        para que el evento del cliente en el JSONL la lleve y el dashboard la
        renderice. Los inbounds normales (texto del webhook) lo dejan en None.

        ``customer_text``: lo que ESCRIBIÓ el cliente, para las lecturas
        (compra, retoma, baja). Por defecto, el texto del mensaje. El reentry
        de visión pasa solo el texto que el cliente puso en la foto (o None):
        la descripción la escribió la visión y no se lee como si fuera del
        cliente (bug: un comprobante pausaba la reactivación una semana).

        ``photo_note``: otro canal interno del reentry de visión: la nota del
        turno cuando la foto se reconoció como un producto nuestro
        (``photo_product.build_photo_product_note``).

        ``from_photo``: el mensaje ES la foto que reentra (no espera a nada).
        Cualquier otro mensaje del cliente que llega mientras se lee una foto
        suya espera a que la foto entre al bot (con tope ``photo_wait_s``):
        así la foto y el «¿tienes esta?» van en la misma ráfaga y en orden.
        Las fotos no se esperan entre sí.
        """
        session_id = f"{WHATSAPP_SESSION_PREFIX}{parsed.from_number}"
        if not from_photo and not _is_photo(parsed) and self._photo_reads.reading(session_id):
            waited_s = await self._photo_reads.wait(session_id, max_s=self._photo_wait_s)
            logger.info(
                "inbound_waited_for_photo",
                session=session_id,
                waited_ms=int(waited_s * 1000),
                gave_up=self._photo_reads.reading(session_id),
            )

        # --- 1. Read metadata UNA vez al principio (atribución + typing) ---
        # Lo que había en disco al leer (incidente 2026-10-06): cada escritura
        # de este `execute` lleva SOLO lo que cambió desde acá (o desde su
        # escritura anterior), no la copia entera. Entre la lectura y la
        # escritura grande se espera a Jev varios segundos; en ese rato el
        # flush del turno anterior saca de la cola la foto que ya mandó, y la
        # copia entera la devolvía (salía otra vez en el turno siguiente).
        try:
            # Un error pasajero se reintenta (hasta 3 veces, sin frenar el bucle).
            metadata = await read_retrying_transient_errors(self._metadata_store, session_id)
            base: dict[str, Any] = copy.deepcopy(metadata)
        except Exception as exc:  # noqa: BLE001 — best-effort: el mensaje no se pierde
            # Sigue fallando tras los reintentos (el store lanza: nunca da una
            # copia vieja ni `{}`). El mensaje sigue al historial y al router,
            # que relee; lo que el ingest decida sobre `{}` NO se escribe
            # (pisaba el origen).
            logger.warning(
                "ingest_metadata_unreadable", session=session_id, error=repr(exc)[:200]
            )
            metadata = {}
            base = _Unread()

        # --- 1b. Nombre de perfil de WhatsApp (`contacts[].profile.name`) ---
        # La bandeja de la app y los incendios muestran quién escribe, no solo
        # el número. Va por `update()` (lock + lectura fresca) y entra a la copia
        # local Y a la base: ya está en disco, así que no cuenta como cambio de
        # este ingest en el merge de tres vías de más abajo.
        if parsed.profile_name and self._remember_profile_name(session_id, parsed.profile_name):
            for doc in (metadata, base):
                if isinstance(doc, dict):
                    profile = doc.get("profile") if isinstance(doc.get("profile"), dict) else {}
                    doc["profile"] = {**profile, "name": parsed.profile_name}

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
            base=base,
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
                base=base,
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
        # La ventana de 24 h la abre el CLIENTE al escribir: sale de cuándo
        # envió el mensaje (`messages[].timestamp`), no de cuándo llegó el
        # webhook. Meta reintenta hasta ~7 días un webhook que no aceptamos
        # (clientes sin teléfono, 2026-10-09): re-entregado días después, la
        # ventana ya está cerrada y el bot no puede escribirle texto libre.
        sent_ms = _customer_sent_ms(parsed, now_ms)
        arrived_after_window = compute_service_window_expiry(sent_ms) <= now_ms
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
        _prev_closed_episode: dict[str, Any] | None = None
        # El pedido anterior aún en curso (OrderFacts): post-venta.
        _after_facts: Any = None
        campaign_reply_note: str | None = None
        campaign_reply_touch: dict[str, Any] | None = None
        # El episodio que se cerró cuando este mensaje abrió uno nuevo con el
        # historial del LLM cortado: su resumen encabeza el primer mensaje.
        previous_episode: dict[str, Any] | None = None
        # Acuse de la despedida (run 4cb3a34f): "☺️👍" 37 s después de que
        # Ventas cerró con RECHAZO abrió otro episodio y el bot saludó de cero
        # ("bienvenido a Hubara… ¿en qué te puedo ayudar hoy?"). Si el
        # cliente solo acusa recibo del cierre, el mensaje queda en el chat
        # y no abre episodio ni despierta al agente (return tras persistirlo).
        # Lo que dice el texto lo lee el motor (capacidad `acuse`).
        closing_ack = False
        _campaign_touch: dict[str, Any] | None = None
        if metadata.get("active_route") != ROUTE_HUMANO:
            # Respuesta a campaña (bug 2026-09-22, runs 31c15a38/01a0caee):
            # la PRIMERA respuesta tras el envío es una intención nueva. Se
            # cierra el episodio abierto (su draft/cupón/nota web se apagan)
            # y el turno lleva la nota con lo que recibió el cliente — que el
            # LLM no ve (la plantilla no entra a su historial). Corre ANTES
            # de pisar `last_inbound_at_ms` (lo usa para saber si ya
            # respondió).
            # Si cita el mensaje de una campaña, es de ESA (2026-09-25).
            _campaign_touch = unanswered_campaign_touch(
                metadata, now_ms, quoted_message_id=(parsed.context or {}).get("id")
            )
            # Un 👍 a la campaña es respuesta a la campaña, no acuse.
            closing_ack = _campaign_touch is None and await self._is_closing_ack(
                session_id,
                metadata,
                parsed,
                now_ms=now_ms,
                synthetic=customer_text is not _FROM_MESSAGE,
            )
        if metadata.get("active_route") != ROUTE_HUMANO and not closing_ack:
            if _campaign_touch is not None:
                close_episode(
                    metadata,
                    closing_tag=CAMPAIGN_CLOSING_TAG,
                    closing_motivo=(
                        "El cliente respondió a la campaña "
                        f"«{campaign_label(_campaign_touch)}»"
                    ),
                    now_ms=now_ms,
                    msgs_count_at_close=msgs_count_at_start,
                )
                campaign_reply_note = build_campaign_reply_note(_campaign_touch)
                campaign_reply_touch = _campaign_touch
            _episodes_before = metadata.get("episodes") or []
            _episode_count_before = len(_episodes_before)
            _prev_closed_episode = (
                _episodes_before[-1]
                if _episodes_before
                and _episodes_before[-1].get("closed_at_ms") is not None
                else None
            )
            # Con respuesta a campaña la nota de la campaña reemplaza a la de
            # frontera ("saluda y pregunta en qué ayudar" la contradice).
            if _prev_closed_episode is not None and campaign_reply_note is None:
                _after_facts = await self._order_in_course(_prev_closed_episode, session_id, now_ms)
                episode_boundary_note = (
                    build_after_purchase_note(_after_facts)
                    if _after_facts is not None
                    else build_episode_boundary_note(_prev_closed_episode)
                )
            ensure_active_episode(
                metadata,
                now_ms=now_ms,
                session_id=session_id,
                inbound_message_id=parsed.message_id,
                referral_snapshot=_make_episode_snapshot(
                    parsed.referral, has_cart_ref=bool(cart_ref)
                ),
                msgs_count_at_start=msgs_count_at_start,
            )
            _episodes_now = metadata["episodes"]
            if _after_facts is not None and len(_episodes_now) > _episode_count_before:
                # La etapa del episodio nuevo es post-venta (`resolve_funnel_stage`).
                _episodes_now[-1]["after_order"] = after_order_marker(_after_facts)
            # Memoria por episodio (run 28a8e407): el historial del LLM era de
            # TODA la sesión — tras el pedido #44 el cliente escribió "AMOR26"
            # y el bot contestó sobre ese pedido pese a la nota de frontera.
            # Todo episodio NUEVO que sigue a otro (cierre con desenlace, 14
            # días sin actividad o campaña) pide cortar el historial del LLM
            # de cada agente (`reset_llm_history_for_episode`, en el worker) y
            # lo anterior viaja UNA vez, en este primer mensaje: hechos del
            # episodio que se cerró, no su motivo. Una pausa dentro del mismo
            # episodio (INTERESADO no cierra) no corta nada.
            # Por conteo, no por id: el id sale de `len(episodes)+1` y un
            # historial con huecos podría repetirlo.
            if _episode_count_before >= 1 and len(_episodes_now) > _episode_count_before:
                previous_episode = _episodes_now[-2]
                request_clean_llm_history(_episodes_now[-1])
            if _campaign_touch is not None:
                # Runs edbb0d8b / 8e73b7dc: el episodio guarda la campaña que
                # lo abrió (el gancho de remarketing la lee).
                mark_campaign_episode(_episodes_now[-1], _campaign_touch)

        # HU-WA24H-001 F1.1: persistir timestamps de la ventana de servicio.
        # Cada inbound del cliente reabre la ventana 24h — esto es lo que
        # permite al watchdog (Sprint 2) saber cuándo está por cerrarse y
        # disparar un utility template legítimo.
        metadata["last_inbound_at_ms"] = sent_ms
        metadata["service_window_expires_at_ms"] = compute_service_window_expiry(sent_ms)
        # Lecturas del cliente (motor de decisiones, enchufe 1): compra,
        # retoma y baja las da el proveedor, con el metadata de ANTES de este
        # mensaje y lo que el cliente vio; acá solo se escriben, igual que hoy.
        tz = resolve_local_timezone(session_id)
        # Lo que el cliente vio ANTES de este mensaje: contexto de las
        # lecturas y, más abajo, del cupón (cuando este mensaje ya quedó en el
        # historial).
        events_before = self._session_events(session_id)
        readings = await self._read_inbound(
            Inbound(
                session_id=session_id,
                text=parsed.text if customer_text is _FROM_MESSAGE else customer_text,
                now_ms=now_ms,
                message_id=parsed.message_id,
                interactive=parsed.interactive,
                order=parsed.order,
                metadata=metadata,
                events=events_before,
                stage=resolve_funnel_stage(metadata),
                tz=tz,
            )
        )
        # Se escriben los MISMOS campos de siempre (una sola función, la misma
        # que usa el sandbox del laboratorio):
        # * señal de compra (2026-09-14): un "después" bloquea el cierre en
        #   este turno; un "sí" con producto en el draft registra la
        #   confirmación que exigen request_shipping_details /
        #   CONFIRMADO_SIN_DATOS / ORDER_PENDING_SHIPPING_DETAILS;
        # * aplazamiento con fecha ("les escribo la otra semana"): hasta esa
        #   fecha remarketing y el watchdog no le escriben (runs 337efe8c /
        #   ee3cec91: 4 toques en 24h → "No más"); una cortesía no la levanta;
        # * baja de marketing (campañas directas): el template promete
        #   "respóndeme NO MÁS y te doy de baja". La frase explícita es piso:
        #   con el motor en `jev`, Jev solo agrega bajas. Sticky: solo la
        #   revierte el operador. Queda registrado cuándo, por qué vía y qué
        #   campaña la provocó: la que citó (2026-09-25) o la del touch
        #   reciente. Una prueba citada no carga la baja.
        _quoted = quoted_campaign_touch(metadata, (parsed.context or {}).get("id"))
        written = apply_readings(
            metadata,
            readings,
            text=parsed.text,
            now_ms=now_ms,
            message_id=parsed.message_id,
            tz=tz,
            opt_out_campaign_id=(
                _quoted["campaign_id"]
                if _quoted is not None and not _quoted.get("test")
                else opt_out_campaign_id(metadata, now_ms)
            ),
        )
        # El cliente solo agradece o saluda (capacidad `cortesia`): el episodio
        # nuevo no abre venta (caso del 2026-09-29, «pedido listo»).
        if episode_boundary_note is not None and _prev_closed_episode is not None and readings.courtesy_only:
            episode_boundary_note = (
                build_after_purchase_note(_after_facts, courtesy=True)
                if _after_facts is not None
                else build_episode_boundary_note(_prev_closed_episode, courtesy=True)
            )
        if written.signal is not None:
            logger.info(
                "inbound_purchase_signal",
                session_id=session_id,
                kind=written.signal,
                text_preview=(parsed.text or "")[:60],
            )
        if written.opted_out:
            # Pidió la baja: no se le sigue conversando la campaña.
            campaign_reply_note = None
            campaign_reply_touch = None
            if isinstance(base, _Unread):
                # La primera lectura falló: la baja no depende de ella y tiene
                # que quedar (sticky). Se escribe sobre la lectura FRESCA.
                self._persist_opt_out_over_fresh(
                    session_id, (parsed.context or {}).get("id"), now_ms
                )
            logger.info(
                "marketing_opt_out_detected",
                session_id=session_id,
                campaign_id=metadata.get("marketing_opt_out_campaign_id"),
                text_preview=(parsed.text or "")[:60],
            )

        # HU-WA24H-001 F1.3: ventana extendida 72h CTWA. Solo se setea la
        # PRIMERA vez que vemos ctwa_clid — la ventana CTWA NO se renueva
        # con inbounds subsecuentes. Defensivo: chequeamos presencia previa
        # del campo, no `referral_already_seen` (que es por ad_id, no por
        # ventana de pricing).
        #
        # Fix 2026-09-18: la guarda era `"ctwa_window_expires_at_ms" not in
        # metadata` — el campo quedaba pegado PARA SIEMPRE y un cliente que
        # volvía semanas después por OTRO anuncio no abría ventana nueva,
        # aunque Meta sí se la da (Free Entry Point por cada entrada desde
        # anuncio). Ahora: se abre si no hay ventana o si la anterior ya
        # venció; con la ventana ABIERTA no se extiende (Meta tampoco).
        _ctwa_exp = metadata.get("ctwa_window_expires_at_ms")
        if (
            parsed.referral
            and parsed.referral.get("ctwa_clid")
            and not (isinstance(_ctwa_exp, int) and now_ms < _ctwa_exp)
        ):
            metadata["ctwa_window_expires_at_ms"] = compute_ctwa_window_expiry(sent_ms)

        # Escalera de reactivación (decisión 2026-09-18): `SIN_RESPUESTA`
        # marca a quien agotó los toques sin contestar. Si VUELVE a escribir
        # ya no es "sin respuesta" — levantar la etiqueta acá (determinista)
        # lo saca del filtro del operador y deja rastro en el historial.
        if metadata.get("tag") == "SIN_RESPUESTA":
            metadata["tag"] = "NO_ETIQUETADO"
            metadata["motivo"] = "El cliente volvió a escribir tras agotar la escalera."
            metadata.setdefault("status_history", []).append(
                {
                    "tag": "NO_ETIQUETADO",
                    "motivo": metadata["motivo"],
                    "active_route": metadata.get("active_route"),
                    "timestamp": now_ms / 1000,
                    "source": "ingest:customer_returned",
                }
            )

        # Un humano tomó la conversación mientras se esperaba a Jev (revisión
        # del PR #393): el ciclo del bot que este mensaje iba a mover (episodio
        # nuevo, etiqueta reiniciada) se descarta. Sin esto el merge dejaba
        # `tag=NO_ETIQUETADO` y un episodio abierto bajo el humano (la
        # etiqueta del escritor gana el conflicto). Mismo principio que la
        # guarda de arriba con la ruta humana ya leída. Se decide con lo que
        # hay en disco BAJO el candado de la escritura (segunda revisión: una
        # lectura previa dejaba una ventana para la toma).
        def _yield_if_a_human_took_over(fresh: dict[str, Any]) -> None:
            if base.get("active_route") != ROUTE_HUMANO and fresh.get("active_route") == ROUTE_HUMANO:
                _yield_to_human(metadata, base)

        self._safe_write_metadata(
            session_id, metadata, base, before_merge=_yield_if_a_human_took_over
        )

        # Punto 2 (escala Window Strategist): mantener el índice liviano de
        # reactivación en el mismo momento del estampado — el snapshot builder
        # shortlistea sin escanear el vault. Best-effort: un índice roto JAMÁS
        # tumba el ingest (el fallback del builder es full scan).
        try:
            if not isinstance(base, _Unread):  # sin leer el documento, nada que indexar
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

            fresh_after_capture = self._update_best_effort(
                session_id, _capture_mutator, what="carrito_web"
            )
            if fresh_after_capture is not None:
                metadata = fresh_after_capture
                _rebase(base, metadata)

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

                updated = self._update_best_effort(session_id, _apply_mutator, what="carrito_web_resuelto")
                if updated is not None:
                    metadata = updated
                    _rebase(base, metadata)
                self._emit_web_cart_events(
                    session_id=session_id, metadata=metadata, cart_id=cart_ref
                )

        # --- 2f. Product ref from the PDP's WhatsApp button (`ref: HUB-…`) ---
        # Same shape as 2e: deterministic detection, RMW capture (FM-01),
        # resolution against the catalog by SKU with a short timeout, and a
        # note the LLM only sees once the SKU is verified. Best-effort: a
        # catalog failure records the reason and the conversation goes on.
        product_ref = detect_product_ref(parsed.text)
        agent_source = detect_agent_source(parsed.text) if product_ref else None

        # Attribution runs BEFORE the human-route guard: which agent sent the
        # customer is a fact about the conversation, not bot machinery, and the
        # conversations a human took over are often the ones that sold.
        if product_ref and agent_source:

            def _referral_mutator(fresh: dict[str, Any]) -> dict[str, Any] | None:
                appended = record_agent_referral(
                    fresh, source=agent_source, sku=product_ref, now_ms=now_ms
                )
                return fresh if appended else None

            logged = self._update_best_effort(session_id, _referral_mutator, what="ref_de_agente")
            if logged is not None:
                metadata = logged
                _rebase(base, metadata)

        if product_ref and metadata.get("active_route") != ROUTE_HUMANO:
            captured_ref = {"new": False}

            def _capture_ref_mutator(fresh: dict[str, Any]) -> dict[str, Any] | None:
                if fresh.get("active_route") == ROUTE_HUMANO:
                    return None
                captured_ref["new"] = apply_web_product_capture(
                    fresh, sku=product_ref, source=agent_source, now_ms=now_ms
                )
                return fresh if captured_ref["new"] else None

            fresh_after_ref = self._update_best_effort(
                session_id, _capture_ref_mutator, what="ref_de_producto"
            )
            if fresh_after_ref is not None:
                metadata = fresh_after_ref
                _rebase(base, metadata)

            if captured_ref["new"]:
                product, reason = await self._resolve_product_ref(product_ref)

                def _apply_ref_mutator(fresh: dict[str, Any]) -> dict[str, Any] | None:
                    state = fresh.get("web_product_ref") or {}
                    if state.get("sku") != product_ref:
                        return None  # another ref won mid-resolution
                    if product is not None:
                        mark_web_product_resolved(
                            fresh, handle=product.handle, title=product.title
                        )
                    else:
                        mark_web_product_unresolved(fresh, reason=reason or "unknown")
                    return fresh

                updated_ref = self._update_best_effort(session_id, _apply_ref_mutator, what="ref_de_producto_resuelto")
                if updated_ref is not None:
                    metadata = updated_ref
                    _rebase(base, metadata)

        # --- 2f-bis. «Enviar mensaje a la empresa» desde la ficha del catálogo ---
        # Meta manda el producto exacto (`context.referred_product`); hasta el
        # 2026-09-30 se descartaba y el bot solo veía «¿la tienen disponible?».
        # Mismo estado que el botón de la web (`web_product_ref`, por
        # episodio), resuelto contra el catálogo como el carrito. La nota solo
        # llega con el producto verificado.
        referred = referred_product_id(parsed.context) if not product_ref else None
        if referred and metadata.get("active_route") != ROUTE_HUMANO:
            captured_card = {"new": False}

            def _capture_card_mutator(fresh: dict[str, Any]) -> dict[str, Any] | None:
                if fresh.get("active_route") == ROUTE_HUMANO:
                    return None
                captured_card["new"] = apply_web_product_capture(
                    fresh, sku=referred, source=None, now_ms=now_ms, origin=CATALOG_ORIGIN
                )
                return fresh if captured_card["new"] else None

            fresh_after_card = self._update_best_effort(session_id, _capture_card_mutator, what="tarjeta_del_catalogo")
            if fresh_after_card is not None:
                metadata = fresh_after_card
                _rebase(base, metadata)

            if captured_card["new"]:
                card_lines = await self._catalog_lines([{"product_retailer_id": referred}])
                card_line = (card_lines or {}).get(referred)
                card_reason = "not_in_catalog" if card_lines is not None else "catalog_unavailable"

                def _apply_card_mutator(fresh: dict[str, Any]) -> dict[str, Any] | None:
                    state = fresh.get("web_product_ref") or {}
                    if state.get("sku") != referred:
                        return None  # otro producto ganó mientras se resolvía
                    if card_line is not None:
                        mark_web_product_resolved(
                            fresh, handle=card_line.handle, title=card_line.title, variant=card_line.variant
                        )
                    else:
                        mark_web_product_unresolved(fresh, reason=card_reason)
                    return fresh

                updated_card = self._update_best_effort(session_id, _apply_card_mutator, what="tarjeta_del_catalogo_resuelta")
                if updated_card is not None:
                    metadata = updated_card
                    _rebase(base, metadata)

        # --- 2g. Cupón de la campaña: se aplica solo ---
        # Conversación de prueba del 2026-09-24 (AMOR2026 con cupo por
        # unidad): la nota pedía validarlo "si lo menciona", el bot nunca
        # llamó apply_coupon y ofreció todo a precio lleno. Quien responde a
        # una campaña con cupón viene por esa promoción: se valida acá
        # (Medusa + cupo, timeout corto — L-2) y queda en el episodio de la
        # campaña (RMW atómico, FM-01). Si no se puede validar a tiempo, la
        # nota le pide al bot `apply_coupon` antes de ofrecer precios. Un
        # "NO MÁS" ya apagó la campaña (`campaign_reply_touch` = None).
        coupon_checked_now = False
        if campaign_reply_touch is not None:
            coupon_code = str(campaign_reply_touch.get("coupon_code") or "").strip()
            if coupon_code:
                coupon_checked_now = True
                application = await self._check_campaign_coupon(coupon_code, now_ms)
                if application is not None and application.applied:
                    campaign_episode_id = (metadata.get("episodes") or [{}])[-1].get(
                        "episode_id"
                    )

                    def _coupon_mutator(fresh: dict[str, Any]) -> dict[str, Any] | None:
                        episodes = fresh.get("episodes") or []
                        if not episodes or episodes[-1].get("episode_id") != campaign_episode_id:
                            return None  # otra ráfaga abrió otro episodio
                        if episodes[-1].get("applied_coupon"):
                            return None  # ya tiene un cupón: no se pisa
                        return store_coupon_application(fresh, application, now_ms=now_ms)

                    stored = self._update_best_effort(session_id, _coupon_mutator, what="cupon_de_campana")
                    if stored is not None:
                        metadata = stored
                        _rebase(base, metadata)
                campaign_reply_note = build_campaign_reply_note(
                    campaign_reply_touch, coupon=application
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
        # mejor que perder el inbound entero. Un mensaje que llegó con su
        # ventana ya cerrada no programa nada: no hay ventana que vigilar.
        if not arrived_after_window:
            await self._emit_watchdog_events(session_id, metadata)

        # --- 3. Traducir a texto efectivo (LLM-ready) ---
        # `catalog=None` por ahora — list_reply usa el title raw del cliente.
        # Wire del catalog port queda como follow-up cuando se exponga via
        # composition.
        # El carrito de WhatsApp llega con códigos: se leen en el catálogo
        # para que el bot (y el operador en Chats) vean nombre, variante y
        # precio (2026-09-30).
        cart_lines = await self._cart_lines(parsed)
        effective: EffectiveText = await translate_to_effective_text(
            parsed,
            catalog=None,
            referral_already_seen=referral_already_seen,
            cart_lines=cart_lines,
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
            self._safe_write_metadata(session_id, metadata, base)
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
            self._safe_write_metadata(session_id, metadata, base)
            self._photo_reads.begin(session_id)
            # Texto ANTES de la foto: el bot ya tiene el texto; que espere la foto.
            await self._notify_photo(session_id, parsed.message_id, done=False)
            _spawn_safe(
                self._read_photo(parsed, session_id),
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
                self._safe_write_metadata(session_id, metadata, base)
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
            nombre = effective.document_filename or "sin nombre"
            document_media_id = effective.document_media_id

            def _route_pdf(target: dict[str, Any]) -> bool:
                """Indexa el PDF y pasa la conversación a verificación humana
                si no lo estaba. True si la pasó."""
                if persisted_doc:
                    self._index_persisted_media(
                        target,
                        media_id=document_media_id,
                        filename=persisted_doc[1],
                        kind=KIND_PDF_DOCUMENT,
                    )
                if target.get("active_route", ROUTE_VENTAS) == ROUTE_HUMANO:
                    return False
                self._apply_human_route(
                    target,
                    motivo=(
                        f"Cliente envió un documento PDF ({nombre}). Posible "
                        "comprobante de pago — verificar la recepción del pago "
                        "en el dashboard de orders y confirmar el envío o "
                        "abortar."
                    ),
                    reason_category="PAYMENT_VERIFICATION_PENDING",
                )
                return True

            if isinstance(base, _Unread):
                # La primera lectura falló (tras sus reintentos): la ruta
                # humana no depende de ella y tiene que quedar. Se escribe
                # sobre la lectura FRESCA (mejor esfuerzo, octava revisión).
                routed = {"to_human": False}

                def _pdf_over_fresh(fresh: dict[str, Any]) -> dict[str, Any] | None:
                    routed["to_human"] = _route_pdf(fresh)
                    return fresh if (routed["to_human"] or persisted_doc) else None

                route_written = (
                    self._update_best_effort(session_id, _pdf_over_fresh, what="ruta_humana_por_pdf")
                    is not None
                )
                notify_client = routed["to_human"] and route_written
                if notify_client:
                    _route_pdf(metadata)  # lo que sigue de este ingest también lo ve
            else:
                to_human = _route_pdf(metadata)
                route_written = (
                    self._safe_write_metadata(session_id, metadata, base)
                    if (to_human or persisted_doc)
                    else True
                )
                notify_client = to_human and route_written
            # «Un colega lo revisa» solo si la ruta humana quedó escrita: si
            # no, nadie tendría el caso (octava revisión).
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

        # Plantilla a la que responde (fase 3, run 28a8e407): el LLM no ve
        # las plantillas (van solo al JSONL del dashboard). Se lee ANTES de
        # persistir este mensaje; la campaña ya trae su propia cita.
        unseen_template: str | None = None
        if campaign_reply_touch is None:
            unseen_template = unseen_template_text(self._session_events(session_id))

        # --- 6. Persistir history (texto efectivo, NO el JSON raw) ---
        # `persisted_image_url` solo viene poblado desde el reentry de visión:
        # el evento del cliente queda con la foto adjunta para el dashboard.
        # Los kwargs de documento solo se pasan cuando el PDF quedó persistido
        # (mantiene compatible cualquier store/fake con la firma anterior).
        # `wamid` + `reply_to`: el dashboard muestra qué mensaje citó el
        # cliente (caso run 541d90e0: "que el velón sea este" sin rastro de
        # a qué foto respondía).
        reply_kwargs = _build_reply_kwargs(parsed, metadata, events_before)
        if persisted_document_url:
            self._history_store.append_user_event(
                session_id,
                effective.text,
                image_url=persisted_image_url,
                document_url=persisted_document_url,
                document_filename=effective.document_filename,
                **reply_kwargs,
            )
        else:
            self._history_store.append_user_event(
                session_id,
                effective.text,
                image_url=persisted_image_url,
                **reply_kwargs,
            )

        # --- 6.5. Limpiar flag de Flow pendiente (sesión c4e3416f) ---
        # Solo la respuesta del formulario (`nfm_reply`) termina la espera de
        # 10 min. Premortem 2026-10-09: cualquier mensaje la borraba; si el
        # cliente preguntaba algo mientras lo llenaba y tardaba, a los 5 min el
        # cierre por abandono lo etiquetaba, la red escalaba al humano y el
        # formulario caía en una bandeja humana sin acuse. Si nunca llega, la
        # marca vence sola (`read_idle_timeout_seconds`, 10 min).
        is_flow_reply = (effective.structured_payload or {}).get("kind") == "nfm_reply"
        cleared_flow_flag = is_flow_reply and (
            metadata.pop("shipping_flow_awaiting_reply_since_ms", None) is not None
        )

        # --- 7. Persistir last_inbound_message_id para typing indicator ---
        if parsed.message_id:
            metadata["last_inbound_message_id"] = parsed.message_id
            self._safe_write_metadata(session_id, metadata, base)
        elif cleared_flow_flag:
            # No hay message_id PERO limpiamos el flag arriba — persistimos
            # el pop para que no quede zombie en metadata.
            self._safe_write_metadata(session_id, metadata, base)

        # Re-entrega tardía (ver 2): el mensaje ya quedó en el chat con la
        # ventana cerrada; el bot no puede escribirle texto libre (Meta lo
        # rechaza, 131047). No es un traspaso al humano (una falla técnica no
        # lo es): el operador lo ve y lo reactiva con plantilla.
        if arrived_after_window:
            logger.warning(
                "inbound_after_service_window",
                session_id=session_id,
                wa_message_id=parsed.message_id,
                late_ms=now_ms - sent_ms,
            )
            return

        # Acuse de la despedida (ver 2c): ya quedó en el chat; el agente no
        # tiene nada que contestar.
        if closing_ack:
            logger.info(
                "closing_ack_absorbed",
                session_id=session_id,
                msg_type=parsed.msg_type,
                text_preview=effective.text[:60],
            )
            return

        # --- 8. Analytics de interacciones (clicks) ---
        if effective.structured_payload:
            await self._emit_interaction_event(
                session_id=session_id,
                structured=effective.structured_payload,
                wa_message_id=parsed.message_id,
            )

        # --- 8.5. Cupón aplicado: ¿este mensaje habla de él? ---
        # Prueba en vivo 2026-09-24 (15:59 Bogotá): a "¿Si tienes 2 de esa?"
        # el bot dijo "Sí, claro" con cupo 1 — no fue a mirar cuánto quedaba.
        # Si el mensaje toca el cupón (`coupon_in_play`: lo nombra, nombra un
        # producto o una combinación suya, o el pedido va en un producto del
        # cupón), se relee el cupo (vault) menos lo vendido (Medusa, lectura
        # compartida unos segundos) y la nota, el selector y set_order_slot lo
        # leen del episodio; si no responde a tiempo queda lo último que se
        # supo (la confirmación y el registro vuelven a mirar). Si el cliente
        # habla de otra cosa, el turno es del catálogo normal: no se lee el
        # cupo y la nota lo recuerda en una línea (pedido del operador,
        # 2026-09-24). Después del texto efectivo: un audio o una foto se
        # deciden por lo que dicen, una sola vez. Lo decide el motor de
        # decisiones (capacidad `cupon`, con `coupon_in_play` de regla de hoy).
        coupon_talk = coupon_checked_now or await self._coupon_talk(
            session_id, metadata, effective.text, events_before
        )
        if coupon_talk and not coupon_checked_now and metadata.get("active_route") != ROUTE_HUMANO:
            reread = await self._reread_coupon_units(session_id, metadata, now_ms)
            if reread is not None:
                metadata = reread
                _rebase(base, metadata)
        elif not coupon_talk and applied_coupon(metadata) is not None:
            logger.info("coupon_not_in_play", session_id=session_id)

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
        # Cualquier otro mensaje citado (su comprobante, su foto, un texto
        # nuestro): caso 2026-10-09, pedido #64.
        photo_citation_note = build_photo_citation_note(
            parsed.context, metadata
        ) or build_quote_note(reply_kwargs.get("reply_to"))
        # HU web-cart: nota de lead caliente — se proyecta cada turno
        # mientras el episodio activo no tenga orden registrada (mismo ciclo
        # de vida que el breadcrumb del draft).
        web_cart_note = build_web_cart_note(metadata)
        web_product_note = build_web_product_note(metadata)
        # Aplazamiento del cliente ("voy en camino", "luego"): el LLM responde
        # texto breve y no avanza el cierre (las tools de cierre también lo
        # rechazan — defensa en profundidad).
        deferral_note = build_deferral_note(
            metadata, resume_label=fresh_resume_label(metadata)
        )
        # Cupón aplicado en el episodio: el LLM lo recuerda cada turno y sabe
        # que el monto lo calcula el sistema (no promete otro descuento).
        coupon_note = build_coupon_note(metadata, in_play=coupon_talk)
        # Lo que el cliente pidió o mostró (la foto reentra como texto) y no
        # existe en el catálogo (incidente 2026-09-23: «¿y en vaso?» + foto de
        # una vela de dragón, y Ventas solo reenvió el catálogo).
        gap_note = (
            await self._catalog_gap_note(session_id, effective.text)
            if metadata.get("active_route") != ROUTE_HUMANO
            else None
        )
        # Las fotos del cliente ya reconocidas como productos nuestros en el
        # episodio: cada turno siguiente las recuerda (laboratorio 4567 t13:
        # «no están todas» y el bot negó lo verificado). La foto que reentra
        # ya lleva su propia nota.
        photo_facts_note = None if from_photo else build_photo_facts_note(metadata)
        # El carrito: el handle de cada producto, para seguir la venta.
        cart_note = build_cart_note(
            (parsed.order or {}).get("product_items") or [] if isinstance(parsed.order, dict) else [],
            cart_lines,
        )
        # Respuesta a campaña: la nota solo viaja por la ruta Sales (el
        # remarketing no recibe plugin_context) — el turno va a Ventas.
        route_kwargs: dict[str, Any] = (
            {"prefer_sales": True} if campaign_reply_note else {}
        )
        # …y el turno lleva la campaña citada (run edbb0d8b: la nota sola
        # perdió contra un historial reciente sobre otro pedido).
        turn_message = (
            quote_campaign_in_turn(campaign_reply_touch, effective.text)
            if campaign_reply_touch is not None
            else effective.text
        )
        if unseen_template is not None:
            turn_message = quote_template_in_turn(unseen_template, turn_message)
        if previous_episode is not None:
            turn_message = with_previous_episode(previous_episode, turn_message)
        await self._load_session.execute(
            session_id=session_id,
            message=turn_message,
            phone_number_id=parsed.phone_number_id,
            **route_kwargs,
            # Traza v2 (plan del laboratorio, PR 3): wamid, hora y tipo del
            # mensaje; el workflow arma `inbound[]` de la ráfaga con esto. El
            # texto crudo (sin la campaña citada ni el episodio anterior) es lo
            # que lee el clasificador de las capas nuevas (PR 14).
            inbound_meta={**_inbound_meta(parsed), "text": effective.text},
            extra_context=[
                note
                for note in (
                    campaign_reply_note,
                    episode_boundary_note,
                    deferral_note,
                    web_cart_note,
                    web_product_note,
                    order_draft_note,
                    coupon_note,
                    photo_facts_note,
                    photo_note,
                    photo_citation_note,
                    cart_note,
                    gap_note,
                )
                if note
            ]
            or None,
        )

    # =========================================================================
    # Helpers
    # =========================================================================

    async def _read_inbound(self, inbound: Inbound) -> Any:
        """Las lecturas del cliente con el proveedor inyectado o el del motor.
        Si el motor no puede leer (p. ej. el paquete de la tienda no compila),
        las reglas del código: el mensaje nunca se pierde (premortem 2026-10-02)."""
        provider = self._readings
        if provider is None:
            from src.plugins.chats.agent.sales.decisions.readings import EngineReadings

            provider = EngineReadings(WORKSPACE_VAULT_DIR)
        try:
            return await provider.read(inbound)
        except Exception as exc:  # noqa: BLE001 — el ingest nunca pierde el mensaje
            from src.plugins.chats.agent.sales.decisions.readings import rules_readings

            logger.error("ingest.readings_failed", session_id=inbound.session_id, error=f"{type(exc).__name__}: {exc}"[:300])
            return rules_readings(inbound)

    async def _is_closing_ack(
        self,
        session_id: str,
        metadata: dict[str, Any],
        parsed: WhatsAppMessage,
        *,
        now_ms: int,
        synthetic: bool,
    ) -> bool:
        """¿El cliente solo le acusa recibo a la despedida del agente?

        Lo estructural lo decide el código (`ack_shape`: el agente se
        despidió; una reacción o un sticker son acuse por sí solos; foto,
        audio o botones nunca). Un texto lo lee el motor de decisiones con el
        MISMO proveedor de lecturas (capacidad `acuse`; con `reglas`, la
        regla de hoy `is_closing_ack`). Nunca es acuse si lo último que le
        mandamos es una plantilla posterior a la despedida: la contesta. El
        historial es el de ANTES de persistir este mensaje.
        """
        shape = ack_shape(metadata, parsed)
        if shape is None:
            return False
        events = self._session_events(session_id)
        if shape == ACK_TEXT:
            verdict = await self._read_ack(
                Inbound(
                    session_id=session_id,
                    text=parsed.text,
                    now_ms=now_ms,
                    message_id=parsed.message_id,
                    metadata=metadata,
                    events=events,
                    synthetic=synthetic,
                )
            )
            if not verdict.value:
                return False
            logger.info(
                "closing_ack_read",
                session_id=session_id,
                by=verdict.by,
                provider=verdict.provider,
            )
        return unseen_template_text(events) is None

    async def _read_ack(self, inbound: Inbound) -> Any:
        """El acuse con el proveedor inyectado o el del motor. Un proveedor
        sin `read_ack` (anterior a la capacidad) deja el acuse al del motor."""
        try:
            return await self._read_ack_or_raise(inbound)
        except Exception as exc:  # noqa: BLE001 — el ingest nunca pierde el mensaje
            from src.plugins.chats.agent.sales.decisions.readings import rules_ack

            logger.error("ingest.ack_reading_failed", session_id=inbound.session_id, error=f"{type(exc).__name__}: {exc}"[:300])
            return rules_ack(inbound)

    async def _read_ack_or_raise(self, inbound: Inbound) -> Any:
        read_ack = getattr(self._readings, "read_ack", None)
        if read_ack is None:
            from src.plugins.chats.agent.sales.decisions.readings import EngineReadings

            read_ack = EngineReadings(WORKSPACE_VAULT_DIR).read_ack
        return await read_ack(inbound)

    async def _coupon_talk(
        self,
        session_id: str,
        metadata: dict[str, Any],
        text: str | None,
        events: list[dict[str, Any]],
    ) -> bool:
        """¿Este mensaje habla del cupón aplicado? Lo decide el motor con el
        bot de la conversación (capacidad `cupon`; regla de hoy:
        `coupon_in_play`). `events`: lo que el cliente vio antes."""
        try:
            verdict = await read_coupon_talk(
                WORKSPACE_VAULT_DIR,
                session_id=session_id,
                metadata=metadata,
                text=text,
                events=events,
            )
        except Exception as exc:  # noqa: BLE001 — sin el motor, la regla de hoy
            from src.plugins.chats.agent.sales.decisions.bundled_ingest import coupon_in_play
            from src.plugins.chats.agent.sales.decisions.readings import Inbound as _Inbound

            logger.error("ingest.coupon_reading_failed", session_id=session_id, error=f"{type(exc).__name__}: {exc}"[:300])
            return bool(coupon_in_play(_Inbound(session_id=session_id, text=text, now_ms=0, metadata=metadata)))
        return bool(verdict.value)

    async def _order_in_course(self, prev_episode: dict[str, Any], session_id: str, now_ms: int) -> Any:
        """Los datos (OrderFacts) del pedido con el que cerró el episodio
        anterior, si sigue en curso; ``None`` si no hay pedido, ya terminó o no
        se pudieron leer (nunca frena el ingest: el lector trae su tope)."""
        order_id = prev_episode.get("order_id")
        if self._order_facts is None or not order_id or prev_episode.get("closing_tag") not in CLOSED_WITH_ORDER:
            return None
        try:
            facts = await self._order_facts(str(order_id))
        except Exception as exc:  # noqa: BLE001 — sin datos, la nota de siempre
            logger.warning("after_purchase_facts_failed", session_id=session_id, error=f"{type(exc).__name__}: {exc}"[:200])
            return None
        return facts if order_in_course(prev_episode, facts, now_ms=now_ms) else None

    def _session_events(self, session_id: str) -> list[dict[str, Any]]:
        """El JSONL de la sesión (lo que ve el dashboard). Vacío si el store
        no lo expone o falla: es contexto, nunca bloquea el ingest."""
        read_events = getattr(self._history_store, "read_events", None)
        if not callable(read_events):
            return []
        try:
            return list(read_events(session_id) or [])
        except Exception:  # noqa: BLE001
            return []

    async def _check_campaign_coupon(
        self, code: str, now_ms: int
    ) -> CouponApplication | None:
        """Valida el cupón de la campaña con timeout corto (L-2). None = no se
        pudo saber (sin wiring, Medusa lento o caído): el bot lo aplica con
        `apply_coupon`. Nunca tumba el ingest."""
        import asyncio

        if self._campaign_coupon is None:
            return None
        try:
            application = await asyncio.wait_for(
                self._campaign_coupon(code, now_ms),
                timeout=_CAMPAIGN_COUPON_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — degrada al apply_coupon del bot
            logger.warning(
                "campaign_coupon_check_failed", code=code, reason=type(exc).__name__
            )
            return None
        logger.info(
            "campaign_coupon_checked",
            code=code,
            applied=application.applied,
            reason=application.reason,
            units=len(application.units),
        )
        return application

    async def _reread_coupon_units(
        self, session_id: str, metadata: dict[str, Any], now_ms: int
    ) -> dict[str, Any] | None:
        """Relee cuánto queda del cupo del cupón aplicado y lo guarda en el
        episodio (RMW atómico, FM-01). None = nada que releer o no se pudo
        (queda lo último que se supo). Nunca tumba el ingest."""
        import asyncio

        if self._coupon_units_now is None:
            return None
        episode = get_active_episode(metadata)
        if not episode or episode.get("order_id"):
            return None
        applied = episode.get("applied_coupon")
        if (
            not isinstance(applied, dict)
            or not applied.get("quota")
            or not isinstance(applied.get("promotion"), dict)
        ):
            return None
        code = applied.get("code")
        try:
            offer = await asyncio.wait_for(
                self._coupon_units_now(applied["promotion"]),
                timeout=_CAMPAIGN_COUPON_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — queda lo último que se supo
            logger.warning(
                "coupon_units_reread_failed", code=code, reason=type(exc).__name__
            )
            return None
        if offer is None or not offer.has_quota or offer.reason not in (None, "quota_exhausted"):
            return None
        exhausted = offer.reason == "quota_exhausted"
        episode_id = episode.get("episode_id")

        def _mutator(fresh: dict[str, Any]) -> dict[str, Any] | None:
            current_episode = get_active_episode(fresh) or {}
            current = current_episode.get("applied_coupon")
            if current_episode.get("episode_id") != episode_id or not isinstance(current, dict):
                return None
            if current.get("code") != code:
                return None  # cambió el cupón mientras se releía
            current["units"] = [] if exhausted else [dict(u) for u in offer.units]
            current["sold_out"] = [dict(u) for u in offer.sold_out]
            current["show_units_left"] = offer.show_units_left
            if exhausted:
                current["exhausted"] = True
            else:
                current.pop("exhausted", None)
            current["units_checked_at_ms"] = now_ms
            return fresh

        updated = self._update_best_effort(session_id, _mutator, what="cupos_del_cupon")
        logger.info(
            "coupon_units_reread",
            code=code,
            units=len(offer.units),
            sold_out=len(offer.sold_out),
            exhausted=exhausted,
        )
        return updated

    def _persist_opt_out_over_fresh(
        self, session_id: str, quoted_message_id: str | None, now_ms: int
    ) -> None:
        """La baja «NO MÁS» cuando la primera lectura del ingest falló: no
        depende de esa lectura y es sticky. Se marca sobre la lectura FRESCA
        (con la campaña citada o la del toque reciente), de mejor esfuerzo."""
        from src.sdk.messagingkit import OPT_OUT_SOURCE_TEXT, mark_marketing_opt_out

        def _opt_out(fresh: dict[str, Any]) -> dict[str, Any] | None:
            if fresh.get("marketing_opt_out"):
                return None
            quoted = quoted_campaign_touch(fresh, quoted_message_id)
            campaign_id = (
                quoted["campaign_id"]
                if quoted is not None and not quoted.get("test")
                else opt_out_campaign_id(fresh, now_ms)
            )
            mark_marketing_opt_out(
                fresh, now_ms=now_ms, source=OPT_OUT_SOURCE_TEXT, campaign_id=campaign_id
            )
            return fresh

        self._update_best_effort(session_id, _opt_out, what="baja_de_marketing")

    def _update_best_effort(
        self,
        session_id: str,
        mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        what: str,
    ) -> dict[str, Any] | None:
        """Escritura AUXILIAR del ingest (captura del carrito, del ref, de la
        tarjeta, del cupón): de mejor esfuerzo. Si el disco no deja escribir
        (un error pasajero, disco lleno), se registra y se sigue: el mensaje
        del cliente igual queda en el historial y se despacha. El ingest corre
        después de responder 200 al webhook, así que Meta no lo reintenta.
        ``None`` = no se escribió (como cuando el mutator aborta)."""
        try:
            return self._metadata_store.update(session_id, mutator)
        except Exception as exc:  # noqa: BLE001 — mejor esfuerzo: el mensaje sigue
            logger.warning(
                "ingest_aux_write_failed", session_id=session_id, what=what, error=repr(exc)[:200]
            )
            return None

    async def _catalog_gap_note(self, session_id: str, text: str) -> str | None:
        """Nota de lo que no existe en el catálogo (`catalog_gap_note_for`)."""
        return await catalog_gap_note_for(
            self._catalog,
            vault_dir=WORKSPACE_VAULT_DIR,
            session_id=session_id,
            text=text,
        )

    async def _cart_lines(self, parsed: WhatsAppMessage) -> dict[str, CartLine | None] | None:
        """Los ítems del carrito leídos en el catálogo, o None (sin carrito,
        sin catálogo o si no responde a tiempo: el carrito sale con sus
        códigos, como antes)."""
        order = parsed.order if isinstance(parsed.order, dict) else None
        return await self._catalog_lines((order or {}).get("product_items") or [])

    async def _catalog_lines(self, items: list[Any]) -> dict[str, CartLine | None] | None:
        """Cada `product_retailer_id` → su línea del catálogo (SKU, id de la
        variante o del producto), o None si el catálogo no responde a tiempo."""
        import asyncio

        if not items or self._catalog is None:
            return None
        try:
            result = await asyncio.wait_for(
                self._catalog.search("", limit=_CATALOG_GAP_LIMIT),
                timeout=_WEB_CART_HYDRATION_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — degrade, never break the ingest
            logger.warning("cart_names_unavailable", reason=type(exc).__name__)
            return None
        return lines_from_products(items, list(result.results))

    async def _resolve_product_ref(self, sku: str) -> tuple[Any, str | None]:
        """Resolves a `ref: HUB-…` SKU against the catalog WITHOUT mutating
        metadata. Returns `(product, None)` or `(None, reason)`; a product the
        catalog does not know is `sku_not_found`, anything else (no wiring,
        timeout, snapshot down) is degraded with its class name."""
        import asyncio

        from src.sdk.connectorkit import ProductNotFoundError

        if self._catalog is None:
            return (None, "catalog_unavailable")
        try:
            product = await asyncio.wait_for(
                self._catalog.get_by_sku(sku),
                timeout=_WEB_CART_HYDRATION_TIMEOUT_S,
            )
        except ProductNotFoundError:
            logger.info("web_product_ref_not_found", sku=sku)
            return (None, "sku_not_found")
        except Exception as exc:  # noqa: BLE001 — degrade, never break the ingest
            logger.warning(
                "web_product_ref_resolution_failed", sku=sku, reason=type(exc).__name__
            )
            return (None, type(exc).__name__)
        return (product, None)

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
        base: dict[str, Any],
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
        self._safe_write_metadata(session_id, metadata, base)

    def _handle_referral(
        self,
        *,
        session_id: str,
        metadata: dict[str, Any],
        base: dict[str, Any],
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
        self._safe_write_metadata(session_id, metadata, base)

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

    def _remember_profile_name(self, session_id: str, name: str) -> bool:
        """Guarda `profile.name` si cambió (por `update()`: candado + lectura fresca); True si escribió. Best-effort:
        un fallo acá nunca tumba el ingest del mensaje."""

        def _mutator(fresh: dict[str, Any]) -> dict[str, Any] | None:
            profile = fresh.get("profile") if isinstance(fresh.get("profile"), dict) else {}
            if profile.get("name") == name:
                return None
            fresh["profile"] = {**profile, "name": name}
            return fresh

        try:
            return self._metadata_store.update(session_id, _mutator) is not None
        except Exception:  # noqa: BLE001 — best-effort
            logger.info("profile_name_write_failed_ignored", session=session_id)
            return False

    def _safe_write_metadata(
        self,
        session_id: str,
        data: dict[str, Any],
        base: dict[str, Any],
        *,
        before_merge: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        """Escribe SOLO lo que este ingest cambió en `data` desde `base` (lo que
        leyó, o lo que escribió la vez anterior), sobre lo que hay en disco
        AHORA (`write_merged`, merge de tres vías). Incidente 2026-10-06: la
        copia entera, escrita tras esperar a Jev, devolvía a la cola la foto
        que el flush ya había mandado y borraba su entrega del índice.
        `before_merge`: ver `FilesystemMetadataStore.write_merged`.

        Solo si escribió, `base` pasa a ser `data`: lo ya escrito deja de
        contar como cambio en la escritura siguiente. Una escritura que falló
        (p. ej. un error de lectura pasajero bajo el candado: el store lanza y
        no toca nada) NO cuenta como hecha: sus cambios siguen pendientes para
        la siguiente (segunda revisión del PR #393). Best-effort: un fallo se
        loguea y el mensaje del cliente sigue su camino.

        Si la lectura inicial falló (`base` es `_Unread`), no se escribe: el
        ingest decidió sobre `{}`, no sobre el documento (sexta revisión).
        Devuelve si escribió."""
        if isinstance(base, _Unread):
            logger.info("metadata_write_skipped_unread", session=session_id)
            return False
        try:
            self._metadata_store.write_merged(
                session_id,
                base=base,
                ours=data,
                before_merge=before_merge,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.info(
                "metadata_write_failed_ignored",
                session=session_id,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            return False
        _rebase(base, data)
        return True

    async def _emit_watchdog_events(
        self,
        session_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """Delegado en ``emit_watchdog_events`` (módulo) — ver ahí."""
        await emit_watchdog_events(
            session_id, metadata, temporal_client_factory=self._temporal_client_factory
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

        self._charge_audio(session_id, result)

        # Limpiar pending_transcription
        try:
            metadata = self._metadata_store.read(session_id)
            base = copy.deepcopy(metadata)
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
            self._safe_write_metadata(session_id, metadata, base)
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

    async def _read_photo(self, parsed: WhatsAppMessage, session_id: str) -> None:
        """Lee la foto y la hace entrar al bot; al terminar (o fallar) suelta
        los mensajes del cliente que esperaban por ella y le avisa al workflow
        (después del mensaje de la foto) que ya no hay nada que esperar."""
        try:
            await self._describe_image_and_reenter(parsed)
        finally:
            self._photo_reads.end(session_id)
            await self._notify_photo(session_id, parsed.message_id, done=True)

    async def _notify_photo(self, session_id: str, wamid: str | None, *, done: bool) -> None:
        """El aviso al workflow de ventas de que una foto del cliente se está
        leyendo (o ya entró). Nunca frena la foto: sin aviso, entra como hoy."""
        import asyncio

        if self._photo_notifier is None or not wamid:
            return
        try:
            await asyncio.wait_for(self._photo_notifier(session_id, wamid, done), timeout=_PHOTO_NOTICE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — la foto entra igual
            logger.warning("photo_reading_notice_failed", session=session_id, done=done, error=repr(exc)[:200])

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
        # ¿Qué producto nuestro es la foto? (2026-09-30: capturas de nuestro
        # catálogo negadas o confundidas). Nunca en un comprobante.
        product = (
            await self._identify_photo(result, session_id=session_id, persisted=persisted, media_id=media_id)
            if result.ok and not result.is_payment_receipt
            else None
        )

        # Limpiar pending_vision + registrar el resultado en metadata
        try:
            metadata = self._metadata_store.read(session_id)
        except Exception:  # noqa: BLE001
            metadata = {}
        base = copy.deepcopy(metadata)
        metadata.pop("pending_vision", None)
        if result.ok:
            recent = list(metadata.get("recent_image_descriptions") or [])
            described: dict[str, Any] = {
                "media_id": media_id,
                "kind": result.kind,
                "description": result.description,
                "provider": result.provider,
                "cost_usd_estimate": result.cost_usd_estimate,
            }
            # El episodio de la foto y, si se reconoció, el producto: los turnos
            # siguientes del episodio lo recuerdan (`build_photo_facts_note`).
            active = get_active_episode(metadata)
            if active and active.get("episode_id"):
                described["episode_id"] = active["episode_id"]
            if product is not None:
                described["product"] = {"handle": product.handle, "how": product.how, "title": product.title}
            recent.append(described)
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
        self._safe_write_metadata(session_id, metadata, base)
        # Lo que costó describirla (también un comprobante) va a la conversación.
        if result.ok:
            self._charge_vision(session_id, result.cost_usd_estimate, calls=1)

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
                    "product_handle": product.handle if product is not None else None,
                    "identified_by": product.how if product is not None else None,
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
                # Las lecturas solo leen lo que escribió el cliente en la foto.
                customer_text=caption or None,
                from_photo=True,
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
            # Si se reconoció, la foto entra nombrando el producto (queda en el
            # historial y en el dashboard) y el turno lleva su nota.
            synthetic_text = photo_reentry_text(result.description, product)
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
            # La descripción la escribió la visión: las lecturas del cliente
            # (compra, retoma, baja) solo leen lo que él puso en la foto.
            customer_text=caption or None,
            photo_note=(
                build_photo_product_note(product, result.description)
                if product is not None
                else None
            ),
            from_photo=True,
        )

    def _charge_audio(self, session_id: str, result: Any) -> None:
        """Lo que costó transcribir la nota de voz, al episodio de la
        conversación (`audio_usage`). Google cobra toda llamada que contestó,
        aunque no saliera texto útil; no la que nunca llegó al modelo."""
        from src.sdk.connectorkit import record_audio_cost

        if (result.error or "").startswith(_AUDIO_NOT_BILLED):
            return
        record_audio_cost(session_id, result.cost_usd_estimate, calls=1, store=self._metadata_store)

    def _charge_vision(self, session_id: str, cost_usd: float | None, *, calls: int) -> None:
        """Lo que costó leer la foto, al episodio de la conversación
        (`vision_usage`, como el costo LLM, el de WhatsApp y el de Jev)."""
        from src.sdk.connectorkit import record_vision_cost

        record_vision_cost(session_id, cost_usd, calls=calls, store=self._metadata_store)

    async def _identify_photo(
        self,
        result: Any,
        *,
        session_id: str,
        persisted: tuple[str, str] | None,
        media_id: str,
    ) -> Any:
        """El producto nuestro que es la foto, o None. Nunca tumba el ingest:
        sin identificador, sin catálogo o con cualquier falla, la foto sigue
        como siempre (con su descripción). Deja el índice de fotos del
        catálogo completándose en segundo plano."""
        identifier = self._photo_identifier
        if identifier is None:
            return None

        async def image() -> tuple[bytes, str] | None:
            return await self._inbound_image_bytes(session_id, persisted, media_id)

        try:
            identification = await identifier.identify(result, image)
        except Exception as exc:  # noqa: BLE001 — la foto sigue sin identificar
            logger.warning("photo_product.identify_failed", session=session_id, error_type=type(exc).__name__)
            identification = None
        # La búsqueda por imagen (huella + comparación) también se cobra a la
        # conversación; por texto no cuesta nada.
        calls = int(getattr(identification, "calls", 0) or 0)
        if calls:
            self._charge_vision(session_id, getattr(identification, "cost_usd", 0.0), calls=calls)
        try:
            identifier.refresh_index_soon()
        except Exception as exc:  # noqa: BLE001 — el índice se completa la próxima vez
            logger.warning("photo_product.refresh_failed", error_type=type(exc).__name__)
        product = identification.product if identification is not None else None
        logger.info(
            "photo_product.identified" if product is not None else "photo_product.not_identified",
            session=session_id,
            handle=product.handle if product is not None else None,
            how=product.how if product is not None else None,
            trace=identification.trace if identification is not None else None,
        )
        return product

    @staticmethod
    async def _inbound_image_bytes(
        session_id: str, persisted: tuple[str, str] | None, media_id: str
    ) -> tuple[bytes, str] | None:
        """Los bytes de la foto: la copia que ya se guardó para el dashboard
        o, si no se pudo guardar, otra vez desde Meta."""
        from src.platform.audio.meta_media_fetcher import fetch_media_bytes
        from src.platform.media import resolve_media_file

        if persisted is not None:
            path = resolve_media_file(session_id, persisted[1])
            if path is not None:
                try:
                    return path.read_bytes(), "image/jpeg"
                except OSError:
                    pass
        try:
            fetched = await fetch_media_bytes(media_id)
        except Exception:  # noqa: BLE001 — sin bytes, sin búsqueda por imagen
            return None
        if not fetched:
            return None
        data, mime = fetched
        return data, mime or "image/jpeg"

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
        base = copy.deepcopy(data)
        self._apply_human_route(
            data, motivo=motivo, reason_category=reason_category
        )
        self._safe_write_metadata(session_id, data, base)


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


class _Unread(dict):
    """`base` de un ingest que NO pudo leer `metadata.json` (error pasajero):
    lo que decida sobre `{}` no se escribe (`_safe_write_metadata`). Sigue
    siendo `_Unread` aunque se rebasee: el ingest no vio el documento al
    decidir."""


def _rebase(base: dict[str, Any], data: dict[str, Any]) -> None:
    """`base` pasa a ser una copia de `data` (en el lugar: quien la tiene la
    ve): lo que el ingest ya escribió, o lo que acaba de releer del disco, deja
    de contar como cambio suyo en la próxima `write_merged`."""
    base.clear()
    base.update(copy.deepcopy(data))


#: Lo que el ingest mueve del ciclo del bot y un humano que tomó la
#: conversación manda sobre ello: la etiqueta y su historial.
_HUMAN_OWNED_KEYS = ("tag", "motivo", "status_history")
#: Lo que solo se descarta si este mensaje rotó el episodio (cerró el anterior
#: o abrió uno nuevo): si no rotó, lo que las lecturas escribieron en el
#: episodio activo se queda.
_ROTATION_KEYS = ("episodes", "capi_outbox")


def _episode_marks(metadata: dict[str, Any]) -> list[tuple[Any, Any]]:
    return [
        (episode.get("episode_id"), episode.get("closed_at_ms"))
        for episode in metadata.get("episodes") or []
        if isinstance(episode, dict)
    ]


def _yield_to_human(metadata: dict[str, Any], base: dict[str, Any]) -> None:
    """Un humano tomó la conversación mientras el ingest esperaba: lo que
    iba a mover del ciclo del bot vuelve a como lo leyó (`base`), así el merge
    se queda con lo del humano. Rota el episodio (episodio nuevo, cierre del
    anterior, `CartAbandoned` del cierre por inactividad) → también se descarta."""
    keys = list(_HUMAN_OWNED_KEYS)
    if _episode_marks(metadata) != _episode_marks(base):
        keys.extend(_ROTATION_KEYS)
    for key in keys:
        if key in base:
            metadata[key] = copy.deepcopy(base[key])
        else:
            metadata.pop(key, None)


def build_episode_boundary_note(prev_episode: dict[str, Any], *, courtesy: bool = False) -> str:
    """Nota de frontera de episodio para `plugin_context` en re-engagement.

    Cuando el cliente vuelve tras un episodio CERRADO, el `memory_window` del
    LLM (historial por-sesión, no por-episodio — vive en la base lib exoclaw)
    todavía arrastra la cola del episodio anterior. Sin esta nota el agente no
    saluda y re-surfacea el pedido viejo, incluso re-tagueándolo (bug run
    3b3fbaee: re-emitía EpisodeClosedEvent y crasheaba el dispatch). La nota le
    dice explícitamente que arranque una conversación nueva. NO toca el
    historial (eso requeriría modificar la base lib). Tuteo colombiano (REGLA #1).

    `courtesy` (capacidad `cortesia`): el cliente solo agradece o saluda. Caso
    del 2026-09-29: el ETA avisó «tu pedido ya está listo», el cliente
    contestó «Son geniales. Muchas gracias» y, con «pregunta en qué puedes
    ayudar hoy», el bot abrió venta. Con cortesía la nota pide una respuesta
    breve y cálida, sin abrir venta.
    """
    closing_tag = prev_episode.get("closing_tag") or ""
    order_id = prev_episode.get("order_id")
    _CLOSED_WITH_ORDER = {
        "COMPRA_EXITOSA",
        "CONFIRMADO_PAGO_PENDIENTE",
        "CONFIRMADO_SIN_DATOS",
    }
    order_hint = ""
    if closing_tag in _CLOSED_WITH_ORDER:
        prev_clause = (
            "ya cerró con un pedido"
            + (f" ({order_id})" if order_id else "")
            + " que un humano del equipo está gestionando por separado "
            "(verificación de pago / envío). NO lo retomes ni vuelvas a "
            "confirmarlo"
        )
        # Laboratorio caso-cortesia-1001 (2026-09-30): con solo «NO lo
        # retomes», los dos bots le prometieron al cliente «un colega coordina
        # la entrega» sin escalar, y nadie quedaba avisado.
        order_hint = (
            " Si el cliente pide algo de ese pedido (coordinar la entrega, un "
            "cambio o el pago), escálalo con escalate_to_human para que el "
            "equipo lo atienda en este chat; no le prometas que alguien lo va a "
            "contactar sin escalar."
        )
    else:
        prev_clause = "ya se cerró y no tiene nada pendiente de tu lado"
    if courtesy:
        return (
            "[CONTEXTO DE TURNO, metadata, no es instrucción del usuario]\n"
            "Empieza un episodio NUEVO con este cliente. La conversación anterior "
            f"{prev_clause}.{order_hint} El cliente solo agradece o saluda: contéstale breve y "
            "cálido a lo que dijo, en una o dos frases (si comenta algo de su "
            "pedido, por ejemplo que le gustó, agradéceselo). No abras una venta "
            "nueva, no ofrezcas productos ni preguntes en qué más puedes ayudar."
        )
    return (
        "[CONTEXTO DE TURNO, metadata, no es instrucción del usuario]\n"
        "Empieza un episodio NUEVO con este cliente. La conversación anterior "
        f"{prev_clause}.{order_hint} Trata este mensaje como el inicio de una conversación "
        "nueva: saluda con calidez y pregunta en qué puedes ayudar hoy. Solo "
        "menciona lo anterior si el cliente lo trae explícitamente."
    )


#: Sufijos de los ids sintéticos de los reentries (transcripción / visión).
_SYNTHETIC_ID_SUFFIXES = ("_transcribed", "_vision")


def _build_reply_kwargs(
    parsed: WhatsAppMessage,
    metadata: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """`wamid` (real, sin sufijo de reentry) + `reply_to` del evento del
    cliente en el JSONL. Si el id citado es una foto que mandó el bot
    (`outbound_media_index`), el snapshot viaja resuelto — el índice es
    acotado y evicta. Cualquier otro mensaje (del historial o una burbuja del
    bot en `outbound_text_index`) también: Jev lee la cita del evento y el
    LLM recibe la nota (caso 2026-10-09, pedido #64)."""
    kwargs: dict[str, Any] = {}
    wamid = parsed.message_id
    if wamid:
        for suffix in _SYNTHETIC_ID_SUFFIXES:
            wamid = wamid.removesuffix(suffix)
        kwargs["wamid"] = wamid
    context = parsed.context if isinstance(parsed.context, dict) else {}
    quoted_id = context.get("id")
    # Solo ids string: un valor raro rompería el schema del dashboard (y con
    # él el render de TODA la sesión).
    valid_id = quoted_id if quoted_id and isinstance(quoted_id, str) else None
    # «Enviar mensaje a la empresa»: la cita es la ficha del producto. Meta
    # puede mandarla sin `context.id` (2026-10-07, ficha abierta desde la
    # lista de productos): la cita sale igual, con el código como id.
    referred = referred_product_id(context)
    if referred:
        state = metadata.get("web_product_ref")
        resolved = isinstance(state, dict) and state.get("sku") == referred and state.get("status") == "resolved"
        title = str(state.get("title") or "") if resolved else ""
        variant = f" ({state['variant']})" if resolved and state.get("variant") else ""
        kwargs["reply_to"] = {
            "id": valid_id or referred,
            "author": "catalog",
            "text": f"{title}{variant}" if title else referred,
        }
        return kwargs
    if valid_id is None:
        return kwargs
    reply_to: dict[str, Any] = {"id": valid_id}
    entry = (metadata.get("outbound_media_index") or {}).get(valid_id)
    if isinstance(entry, dict):
        reply_to["author"] = "agent"
        title = entry.get("title") or entry.get("handle")
        if title:
            reply_to["text"] = title
        if entry.get("image_url"):
            reply_to["image_url"] = entry["image_url"]
    else:
        reply_to.update(resolve_quote(valid_id, events or [], metadata.get("outbound_text_index")) or {})
    kwargs["reply_to"] = reply_to
    return kwargs


def build_photo_citation_note(
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


def _is_photo(parsed: WhatsAppMessage) -> bool:
    """¿El mensaje es una foto que va a la visión? (las fotos no se esperan entre sí)"""
    media = parsed.media or {}
    return media.get("type") == "image" and isinstance(media.get("id"), str)


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


async def emit_watchdog_events(
    session_id: str,
    metadata: dict[str, Any],
    *,
    temporal_client_factory: TemporalClientFactory | None,
) -> None:
    """HU-WA24H-001 Sprint 2: emite los eventos del watchdog vía dispatcher.
    Función de módulo (D1.7) para que otros ingests (``IngestStandby``)
    reusen el MISMO emisor con su propia fábrica de Temporal.

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
    if temporal_client_factory is None:
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
        client = await temporal_client_factory()

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
