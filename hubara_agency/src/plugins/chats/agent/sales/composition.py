"""Composition root del webhook handler de Sales (lado HTTP, NO worker).

Construye el grafo de deps que necesita el `IngestInboundMessage` use case:

* ``FilesystemMetadataStore(WORKSPACE_VAULT_DIR)``
* ``FilesystemMessageHistoryStore(WORKSPACE_VAULT_DIR)``
* ``WorkspaceConfig`` (sales runtime) -> ``get_workspace_path()`` (DEHA)
* ``client_factory`` -> ``get_temporal_client``

PR-D Sales: el `BrainLoaderPort` y la const `_SALES_BRAIN_DIR` ya no aplican
al path Sales — la identidad / tono / catalogo viven en `workspace/*.md` y se
leen via `ContextBuilder` durante `build_prompt`.

PR-D global cleanup (ADR-2026-05-06-10): tras la migracion DEHA workspace de
Remarketing (PR-A/PR-B remarketing), `RemarketingSessionWorkflow` tambien lee
desde su workspace canonico. Los `_REMARKETING_BRAIN_DIR` y
`remarketing_brain_loader` se eliminaron — `LoadOrStartSalesSession` ya no
los acepta. `BrainLoaderPort` y `DefaultBrainLoader` se neuterizaron (estan
pendientes de `git rm`).

PR-E: imports actualizados al layout lean — los stores de filesystem se
importan de ``state`` (modulo unificado top-level), los use cases de
``use_cases/`` (no mas ``application/use_cases/``).

Cachea la instancia del use case en proceso (la vault dir y el runtime
workspace path no cambian en runtime). Si en el futuro hace falta DI
sofisticada (FastAPI Depends, multi-tenant, etc.) este es el unico modulo
que cambia.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import os

from exoclaw_temporal.config import WorkspaceConfig
from loguru import logger

from src.platform.analytics.composition import setup_analytics
import src.platform.config as _cfg
from src.platform.config import WORKSPACE_VAULT_DIR, mba_customer_allowed
from src.platform.session_history import FilesystemMessageHistoryStore
from src.platform.state import FilesystemMetadataStore as _PlatformFsMetaStore
from src.platform.temporal.client import get_temporal_client
from src.platform.whatsapp.composition import get_current_rate_card
from src.plugins.chats.agent.sales.config.env import get_workspace_path
from src.plugins.chats.agent.sales.inbound_ledger_store import FilesystemInboundLedger
from src.plugins.chats.agent.sales.sender_identity_store import FilesystemSenderIdentity
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.use_cases.ingest_delivery_status import (
    IngestDeliveryStatus,
)
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.plugins.chats.agent.sales.use_cases.coupon_application import (
    CouponApplication,
    RecentSoldUnits,
    resolve_coupon_application,
)
from src.plugins.chats.agent.sales.use_cases.coupon_quota import QuotaOffer, quota_offer
from src.plugins.chats.agent.sales.use_cases.coupons import promotion_from_snapshot
from src.plugins.chats.agent.sales.use_cases.ingest_handover import IngestHandover
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import emit_watchdog_events
from src.plugins.chats.agent.sales.use_cases.ingest_standby import IngestStandby
from src.plugins.chats.agent.sales.use_cases.photo_product import PhotoIdentifier
from src.plugins.chats.agent.sales.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)


_INGEST_USE_CASE: IngestInboundMessage | None = None
_DELIVERY_STATUS_USE_CASE: IngestDeliveryStatus | None = None
_STANDBY_USE_CASE: IngestStandby | None = None
_HANDOVER_USE_CASE: IngestHandover | None = None


def build_session_metadata_store() -> FilesystemMetadataStore:
    """Store de metadata de sesión del vault canónico, para tools que leen o
    escriben `metadata.json` sin importar platform (R-DIP #7)."""
    return FilesystemMetadataStore(WORKSPACE_VAULT_DIR)


def build_vault_dir() -> Path:
    """El vault canónico, para tools que le piden decisiones al motor de
    decisiones (registro de bots, cola de desacuerdos) sin importar
    platform (R-DIP #7). Se lee al llamar: los tests lo aíslan."""
    return Path(WORKSPACE_VAULT_DIR)


def build_inbound_ledger() -> FilesystemInboundLedger:
    """Ledger durable de inbound del webhook, DENTRO del vault (volumen EBS:
    sobrevive a deploys, a diferencia de los logs del container). `_ledger/`
    no empieza con `wa_` → ningún scanner de sesiones lo toma por conversación."""
    return FilesystemInboundLedger(WORKSPACE_VAULT_DIR / "_ledger" / "webhook")


def build_sender_identity() -> FilesystemSenderIdentity:
    """Qué conversación es de cada id de Meta (BSUID) de un cliente con nombre
    de usuario, dentro del vault (`_identity/` no empieza con `wa_`)."""
    return FilesystemSenderIdentity(WORKSPACE_VAULT_DIR / "_identity" / "whatsapp_user_ids")


def build_phone_identity() -> FilesystemSenderIdentity:
    """Qué conversación es de cada teléfono de un cliente que empezó SIN
    teléfono (`wa_<CC><id>`): cuando Meta empieza a mandar su teléfono se
    recuerda, y un mensaje que después llegue solo con `from` sigue en su
    conversación (premortem 2026-10-09)."""
    return FilesystemSenderIdentity(WORKSPACE_VAULT_DIR / "_identity" / "whatsapp_phones")


def has_phone_conversation(phone: str) -> bool:
    """¿El teléfono ya tiene su conversación (`wa_<teléfono>`, de antes del
    nombre de usuario)? Entonces no se recuerda como alias de la conversación
    sin teléfono: lo que llegue solo con él sigue en la suya (revisión del
    premortem 2026-10-09)."""
    return (WORKSPACE_VAULT_DIR / f"wa_{phone}").is_dir()


def build_session_history_reader() -> Callable[[str], list[dict[str, Any]]]:
    """`session_key -> eventos del historial JSONL` para tools que auditan lo
    que el bot ya le escribió al cliente (`verify_order_for_checkout`, run
    ebbc203d). Único lugar del plugin que conoce el store de platform (R-DIP)."""
    return FilesystemMessageHistoryStore(WORKSPACE_VAULT_DIR).read_events


def build_ingest_use_case() -> IngestInboundMessage:
    """Devuelve un singleton del `IngestInboundMessage` use case.

    Los stores son stateless (solo leen `WORKSPACE_VAULT_DIR`), asi que es
    seguro compartirlos entre requests. El `client_factory` se invoca por
    request para reaprovechar el patron actual de `get_temporal_client`.

    HU-002: arma también el `EventBus` analytics y lo inyecta para que el
    ingest emita eventos de referral CTWA + click tracking al sink
    filesystem (auditoría local). El envío a Meta CAPI NO pasa por este
    bus — vive en `send_capi_event_activity` (cierre de episodio).
    """
    global _INGEST_USE_CASE
    if _INGEST_USE_CASE is not None:
        return _INGEST_USE_CASE

    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    history_store = FilesystemMessageHistoryStore(WORKSPACE_VAULT_DIR)

    # WorkspaceConfig canonico del agente Sales (donde viven IDENTITY.md,
    # SOUL.md, USER.md, TOOLS.md, AGENTS.md, memory/* y skills/*). Cruza el
    # workflow boundary como string en `SalesSessionInput.runtime_workspace_path`
    # y `bootstrap_sales_session_activity` lo consume para `WorkspaceConfig(path=...)`.
    sales_runtime_workspace = WorkspaceConfig(path=str(get_workspace_path()))

    load_session = LoadOrStartSalesSession(
        client_factory=get_temporal_client,
        metadata_store=metadata_store,
        sales_runtime_workspace=sales_runtime_workspace,
    )

    # Analytics bus singleton — filesystem siempre, Meta CAPI si hay token.
    event_bus = setup_analytics()
    tenant_id = os.getenv("HUBARA_TENANT_ID", "hubara")

    # HU web-cart: reader de la Store API (Null si falta la publishable key
    # — jamás rompe el boot) + catálogo para el matching de la hidratación.
    # Imports via src.sdk.connectorkit (P-28: plugins no tocan platform).
    from src.sdk.connectorkit import get_catalog_client, get_web_cart_reader

    validate_campaign_coupon, coupon_units_now = _coupon_checks()
    catalog = get_catalog_client()
    _INGEST_USE_CASE = IngestInboundMessage(
        history_store=history_store,
        load_session=load_session,
        metadata_store=metadata_store,
        event_bus=event_bus,
        tenant_id=tenant_id,
        web_cart_reader=get_web_cart_reader(),
        catalog=catalog,
        campaign_coupon=validate_campaign_coupon,
        coupon_units_now=coupon_units_now,
        photo_identifier=build_photo_identifier(catalog),
        # Texto antes de la foto: el workflow de ventas espera la foto.
        photo_notifier=load_session.notify_photo_reading,
        # El cliente que escribe tras comprar: post-venta con el pedido real.
        order_facts=build_order_facts_reader(),
    )
    return _INGEST_USE_CASE


#: Tope para leer un pedido en el webhook: sin datos a tiempo, la nota de siempre.
_ORDER_FACTS_TIMEOUT_S = 3.0


def _order_facts_port() -> Any:
    from src.sdk.connectorkit import get_order_facts_port

    return get_order_facts_port()


def build_order_facts_reader() -> Callable[[str], Awaitable[Any]]:
    """OrderFacts de UN pedido (``None`` si Medusa no lo conoce), con tope:
    el ingest corre en el webhook y nunca espera más que esto."""
    import asyncio

    async def read(order_id: str) -> Any:
        snapshot = await asyncio.wait_for(_order_facts_port().get_facts([order_id]), timeout=_ORDER_FACTS_TIMEOUT_S)
        return snapshot.facts.get(order_id)

    return read


def build_photo_color_reader() -> Callable[[str, str, Any], Awaitable[str | None]] | None:
    """De qué color es la vela de una foto del catálogo (caso 2026-10-09:
    «¿no viene en este color?» citando la foto de una ardilla café). Se lee
    una vez por foto y queda junto al snapshot (`<snapshot>/photo_colors`).
    None si la visión no se puede armar en este proceso: la foto va sin color.
    Lo usan `present_product_detail` del worker y el de la app del operador."""
    from src.sdk.catalogkit import color_of_photo, get_photo_color_store
    from src.sdk.connectorkit import get_photo_color_port

    try:
        store, reader = get_photo_color_store(), get_photo_color_port()
    except Exception as exc:  # noqa: BLE001 — el color es un extra
        logger.warning("Sin lector del color de las fotos: {}", f"{type(exc).__name__}: {exc}"[:200])
        return None

    async def _read(url: str, title: str, palette: Any) -> str | None:
        return await color_of_photo(url, title=title, palette=palette, store=store, reader=reader)

    return _read


def build_photo_identifier(catalog: Any) -> PhotoIdentifier:
    """Qué producto nuestro es la foto del cliente (``use_cases/photo_product``):
    el texto que se lee en ella y, si no alcanza, el índice de fotos del
    catálogo (junto al snapshot), los embeddings de imagen y el verificador.
    Los mismos puertos que el laboratorio, por el SDK (P-28)."""
    from src.sdk.catalogkit import get_catalog_photo_index
    from src.sdk.connectorkit import get_image_embedding_port, get_photo_match_port

    return PhotoIdentifier(
        catalog=catalog,
        index=get_catalog_photo_index(),
        embedder=get_image_embedding_port(),
        matcher=get_photo_match_port(),
    )


def _coupon_checks() -> tuple[
    Callable[[str, int], Awaitable[CouponApplication]],
    Callable[[dict[str, Any]], Awaitable[QuotaOffer]],
]:
    """Lo que el webhook mira del cupón, con los mismos puertos del SDK que
    usa `apply_coupon` (se resuelven al primer uso: sin Medusa configurado
    falla y el ingest degrada al bot):

    * validar el cupón que anuncia la campaña (L-32);
    * releer cuánto queda del cupo del cupón aplicado, con cada mensaje.

    Las vendidas de Medusa se comparten unos segundos entre las dos y entre
    clientes (`RecentSoldUnits`): una campaña masiva no escanea Medusa una
    vez por mensaje. El registro las relee bajo el candado."""
    shared: dict[str, Any] = {}

    def _sales() -> RecentSoldUnits | None:
        from src.sdk.connectorkit import get_coupon_sales_reader

        if "sales" not in shared:
            reader = get_coupon_sales_reader()
            shared["sales"] = RecentSoldUnits(reader) if reader is not None else None
        return shared["sales"]

    async def _validate(code: str, now_ms: int) -> CouponApplication:
        from src.sdk.connectorkit import (
            get_catalog_client,
            get_promo_quota_store,
            get_promotions_port,
        )

        return await resolve_coupon_application(
            code,
            promotions=get_promotions_port(),
            quotas=get_promo_quota_store(),
            sales=_sales(),
            catalog=get_catalog_client(),
            now_ms=now_ms,
        )

    async def _units_now(promotion: dict[str, Any]) -> QuotaOffer:
        from src.sdk.connectorkit import get_catalog_client, get_promo_quota_store

        return await quota_offer(
            promotion_from_snapshot(promotion),
            quotas=get_promo_quota_store(),
            sales=_sales(),
            catalog=get_catalog_client(),
        )

    return _validate, _units_now


def build_ingest_delivery_status_use_case() -> IngestDeliveryStatus:
    """Singleton del `IngestDeliveryStatus` use case (HU-WA24H-001 F1.10).

    Consumido por el handler `statuses[]` del webhook FastAPI. Reusa el
    `MetadataStore` + `EventBus` que ya construye `build_ingest_use_case`,
    y agrega:

      * `RateCard` vigente (singleton del composition root WhatsApp).
      * `vault_dir` para escanear sesiones por wa_message_id.
      * Dead-letter path en `<vault>/_orphan_delivery_statuses.jsonl`.
    """
    global _DELIVERY_STATUS_USE_CASE
    if _DELIVERY_STATUS_USE_CASE is not None:
        return _DELIVERY_STATUS_USE_CASE

    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    event_bus = setup_analytics()
    tenant_id = os.getenv("HUBARA_TENANT_ID", "hubara")

    _DELIVERY_STATUS_USE_CASE = IngestDeliveryStatus(
        metadata_store=metadata_store,
        # Proveedor, NO instancia: este singleton vive lo que vive el proceso y
        # una tarjeta resuelta acá quedaba congelada (el 1-oct-2026 cambia).
        rate_card_for=get_current_rate_card,
        event_bus=event_bus,
        vault_dir=WORKSPACE_VAULT_DIR,
        tenant_id=tenant_id,
    )
    return _DELIVERY_STATUS_USE_CASE


def build_ingest_standby_use_case() -> IngestStandby:
    """Singleton del oído ``standby`` (D1.4 MBA): persiste al vault lo que el
    cliente y Meta Business Agent se dicen mientras MBA controla el hilo. Sin
    Temporal, sin analytics: solo stores del vault (flock en metadata)."""
    global _STANDBY_USE_CASE
    if _STANDBY_USE_CASE is not None:
        return _STANDBY_USE_CASE
    async def _standby_window_events(session_id: str, metadata: dict) -> None:
        # D1.7: el mismo emisor del watchdog que usa el ingest regular
        # (ServiceWindowOpenedEvent / CustomerRepliedEvent vía dispatcher),
        # con la fábrica REAL de Temporal: sin ella el emisor es un no-op.
        await emit_watchdog_events(session_id, metadata, temporal_client_factory=get_temporal_client)

    _STANDBY_USE_CASE = IngestStandby(
        metadata_store=_PlatformFsMetaStore(WORKSPACE_VAULT_DIR),
        history_store=FilesystemMessageHistoryStore(WORKSPACE_VAULT_DIR),
        vault_dir=WORKSPACE_VAULT_DIR,
        is_customer_allowed=mba_customer_allowed,
        emit_window_events=_standby_window_events,
    )
    return _STANDBY_USE_CASE


def build_ingest_handover_use_case() -> IngestHandover:
    """Singleton de ``messaging_handovers`` (D1.5 MBA): persiste quién
    controla el hilo. Misma lista cerrada que el oído ``standby``; nuestro
    app id se lee de la config en cada evento (parcheable en tests)."""
    global _HANDOVER_USE_CASE
    if _HANDOVER_USE_CASE is not None:
        return _HANDOVER_USE_CASE
    # Una vez por proceso: qué app id cree Hubara que es "nosotros" (F0: debe
    # coincidir con el `new_owner_app_id` de un take propio).
    logger.info(
        "[chats.handover] WHATSAPP_APP_ID {} — sin él ningún handover decide dueño",
        f"configurado (…{_cfg.WHATSAPP_APP_ID[-4:]})" if _cfg.WHATSAPP_APP_ID else "NO configurado",
    )
    _HANDOVER_USE_CASE = IngestHandover(
        metadata_store=_PlatformFsMetaStore(WORKSPACE_VAULT_DIR),
        vault_dir=WORKSPACE_VAULT_DIR,
        is_customer_allowed=mba_customer_allowed,
        our_app_id=lambda: _cfg.WHATSAPP_APP_ID,
    )
    return _HANDOVER_USE_CASE
