"""ConnectorKit — los PORTS de capability hacia sistemas externos (F-SDK-4).

El lado *driven* del hexágono, formalizado: los plugins consumen CONTRATOS
(``typing.Protocol``) + factories de composición — jamás un client de vendor
(Medusa, Meta, ...) directo. El vendor es un detalle de deployment; cambiarlo
= otro adapter detrás del MISMO port, cero cambios en plugins.

Las 4 reglas del kit (docs/_sdk/07-connectorkit.md):

1. Plugins importan ports de ACÁ (P-31 congela los toques de vendor legacy).
2. Ningún port sin FAKE oficial (testear sin red ni credenciales).
3. Ningún adapter sin contract suite (la misma corre contra fake y vendor).
4. HTTP honesto: timeout por la CADENA del upstream; connect-error = "NO se
   aplicó" (502); read-timeout = "DESCONOCIDO, verificá antes de reintentar"
   (504) — lección L-1.

CARGA LAZY (premortem F-SDK, PEP 562): la atribución es stdlib-pura y se
importa eager; los 9 ports + factories se resuelven recién en el PRIMER
acceso al símbolo. Razón: las cadenas de composición de los ports arrastran
vendors pesados (medusa/litellm: medido +759 módulos y +5s de import) y un
consumidor liviano (ads solo necesita atribución) NO debe pagarlas. El guard
``tests/architecture/test_sdk_lazy_surface.py`` lo exige: importar este
módulo no carga ningún vendor.
"""
from __future__ import annotations

import importlib
from typing import Any

from src.platform.attribution import (
    CAMPAIGN_ATTRIBUTION_WINDOW_MS as CAMPAIGN_ATTRIBUTION_WINDOW_MS,
    AttributionReadPort as AttributionReadPort,
    AttributionSession as AttributionSession,
    FilesystemAttributionStore as FilesystemAttributionStore,
    InMemoryAttributionStore as InMemoryAttributionStore,
    matching_campaign_touch as matching_campaign_touch,
)

# Símbolo público → módulo que lo define. La fuente espejo (eager, para quien
# quiera todo explícito) es src/sdk/connectorkit/ports.py — mantener AMBOS en
# sync (el guard test compara).
_LAZY_EXPORTS: dict[str, str] = {
    # Ports (typing.Protocol):
    "AudioTranscriptionPort": "src.platform.audio.port",
    "CatalogPort": "src.platform.catalog.port",
    # Errores del contrato de CatalogPort (premortem web-cart FM-02: los
    # consumers distinguen "producto no existe" de "catálogo caído"):
    "CatalogUnavailableError": "src.platform.catalog.errors",
    "ProductNotFoundError": "src.platform.catalog.errors",
    "CheckoutVerificationPort": "src.platform.catalog.checkout_port",
    # Item de entrada del CheckoutVerificationPort + helpers puros del catálogo
    # (listas cerradas de aromas/colores, mapa signo→color, slug→label) que un
    # plugin necesita para armar envelopes sin tocar src.platform (D1.2 mba):
    "CheckoutItem": "src.platform.catalog.checkout_port",
    "parse_variant_tags": "src.platform.catalog.variant_attrs",
    "match_option": "src.platform.catalog.variant_attrs",
    "parse_variant_colors": "src.platform.catalog.variant_colors",
    "deslugify": "src.platform.catalog.categories",
    # Identidad estable (2026-09-14): retailer_id de Meta = SKU, no id de Medusa.
    "product_retailer_id": "src.platform.catalog.identity",
    "CustomerScoringPort": "src.platform.customer_scoring.port",
    "ImageVisionPort": "src.platform.vision.port",
    "MetaCatalogPort": "src.platform.meta_catalog.port",
    # Central de la Graph API (host + versión únicos) — stdlib-pura, sin vendor:
    "META_GRAPH_API_VERSION": "src.platform.meta.graph",
    "META_GRAPH_BASE_URL": "src.platform.meta.graph",
    "graph_url": "src.platform.meta.graph",
    # Nombres reales de ads/campañas (Marketing API, best-effort) — lo usan
    # ads (tablero) y chats (origen de cada conversación en el inspector):
    "fetch_meta_ad_names": "src.platform.meta.ad_names",
    "meta_marketing_token": "src.platform.meta.ad_names",
    # Meta Conversions API (outbox único — auditoría CAPI 2026-09-08). Los
    # productores de plugins (tools de Sales, watchdog, orders) encolan y
    # flushean por acá; el HTTP vive en platform:
    "CAPI_EVENT_NAMES": "src.platform.whatsapp.capi",
    "enqueue_capi_event": "src.platform.whatsapp.capi_outbox",
    "normalize_capi_contents": "src.platform.whatsapp.capi",
    "flush_capi_outbox": "src.platform.whatsapp.capi_outbox",
    "has_ctwa_attribution": "src.platform.whatsapp.capi_outbox",
    "schedule_capi_flush": "src.platform.whatsapp.capi_outbox",
    "OrderCommandPort": "src.platform.orders.command_port",
    "OrderQueryPort": "src.platform.orders.query_port",
    # Datos canónicos de pedidos ("variables globales" del dashboard — pedido
    # #31): total/pago/etapa/cliente se leen de acá, nunca de copias del vault.
    "OrderFacts": "src.platform.orders.facts",
    "OrderFactsReadPort": "src.platform.orders.facts",
    "OrderFactsSnapshot": "src.platform.orders.facts",
    "InMemoryOrderFacts": "src.platform.orders.facts",
    "OrderRegistrationPort": "src.platform.orders.port",
    # Reparto del cupón por ítem (L-26): viaja con el port de registro.
    "DiscountedUnits": "src.platform.orders.port",
    # Cupones / promociones (Medusa Admin → Promotions): port + DTOs + dobles
    # + reglas puras (el monto del descuento nace acá, nunca en el LLM):
    "PromotionsPort": "src.platform.promotions.port",
    "PromotionDTO": "src.platform.promotions.port",
    "DiscountLineItem": "src.platform.promotions.port",
    "FakePromotionsPort": "src.platform.promotions.port",
    "NullPromotionsPort": "src.platform.promotions.port",
    "PromotionsUnavailableError": "src.platform.promotions.port",
    "COUPON_CODE_RE": "src.platform.promotions.rules",
    "CouponResolution": "src.platform.promotions.rules",
    "DiscountResult": "src.platform.promotions.rules",
    "LineDiscount": "src.platform.promotions.rules",
    "compute_discount": "src.platform.promotions.rules",
    "normalize_coupon_code": "src.platform.promotions.rules",
    "resolve_coupon": "src.platform.promotions.rules",
    # Central de cupones (Marketing → Cupones, CUPONES_PLAN.md): comandos a
    # Medusa (solo la API), cupo por unidad en el vault, ventas DERIVADAS de
    # los pedidos, registro de cambios y el candado de la última unidad:
    "CouponSpecError": "src.platform.promotions.coupon",
    "CouponView": "src.platform.promotions.coupon",
    "parse_coupon_spec": "src.platform.promotions.coupon",
    "PromotionsAdminPort": "src.platform.promotions.admin",
    "FakePromotionsAdmin": "src.platform.promotions.admin",
    "CouponCodeTakenError": "src.platform.promotions.admin",
    "CouponDeleteRefusedError": "src.platform.promotions.admin",
    "CouponNotFoundError": "src.platform.promotions.admin",
    "CouponNotManageableError": "src.platform.promotions.admin",
    "CouponPartialUpdateError": "src.platform.promotions.admin",
    "CouponRejectedError": "src.platform.promotions.admin",
    "REASON_QUOTA_EXHAUSTED": "src.platform.promotions.quotas",
    "PromoUnitQuota": "src.platform.promotions.quotas",
    "QuotaLine": "src.platform.promotions.quotas",
    "QuotaStatus": "src.platform.promotions.quotas",
    "allocate_units": "src.platform.promotions.quotas",
    "quota_exhausted": "src.platform.promotions.quotas",
    "quota_product": "src.platform.promotions.quotas",
    "quota_statuses": "src.platform.promotions.quotas",
    "validate_quota_rows": "src.platform.promotions.quotas",
    "QuotaSheet": "src.platform.promotions.quota_store",
    "QuotaStoreError": "src.platform.promotions.quota_store",
    "FakePromoQuotaStore": "src.platform.promotions.quota_store",
    "FakeCouponAuditLog": "src.platform.promotions.audit",
    "CouponResults": "src.platform.promotions.coupon_sales",
    "coupon_results": "src.platform.promotions.coupon_sales",
    "quota_board": "src.platform.promotions.coupon_sales",
    "sold_units_by_quota": "src.platform.promotions.coupon_sales",
    "QuotaLockTimeout": "src.platform.promotions.quota_lock",
    "WebCartReaderPort": "src.platform.carts.port",
    # DTOs + fakes + errores del web cart (HU web-cart) — viajan con su port:
    "WebCartItem": "src.platform.carts.port",
    "WebCartSnapshot": "src.platform.carts.port",
    "NullWebCartReader": "src.platform.carts.port",
    "FakeWebCartReader": "src.platform.carts.port",
    "WebCartAuthError": "src.platform.carts.port",
    "WebCartUnavailableError": "src.platform.carts.port",
    # Factories de composición (el deployment decide el vendor):
    "get_audio_transcription_port": "src.platform.audio.composition",
    "get_catalog_client": "src.platform.catalog.composition",
    "get_checkout_verification_port": "src.platform.catalog.composition",
    "get_customer_scoring_port": "src.platform.customer_scoring.composition",
    "get_image_vision_port": "src.platform.vision.composition",
    "get_order_command_port": "src.platform.orders.composition",
    "get_order_query_port": "src.platform.orders.composition",
    "get_order_facts_port": "src.platform.orders.composition",
    "get_order_registration_port": "src.platform.orders.composition",
    "get_promotions_port": "src.platform.promotions.composition",
    "get_promotions_admin_port": "src.platform.promotions.composition",
    "get_promo_quota_store": "src.platform.promotions.composition",
    "get_coupon_audit_log": "src.platform.promotions.composition",
    "get_coupon_sales_reader": "src.platform.promotions.composition",
    "get_quota_lock": "src.platform.promotions.composition",
    "get_web_cart_reader": "src.platform.carts.composition",
    # Percepción (plan del laboratorio §4.2): preguntas tipadas a un
    # clasificador (Jev u OpenAI por OpenRouter), con fake y nulo oficiales.
    "PerceptionPort": "src.platform.perception.ports",
    "PerceptionResult": "src.platform.perception.ports",
    "TypedAnswer": "src.platform.perception.ports",
    "TypedQuestion": "src.platform.perception.ports",
    "FakePerceptionAdapter": "src.platform.perception.adapters.fake",
    "NullPerceptionAdapter": "src.platform.perception.adapters.null",
    "anonymize_text": "src.platform.perception.anonymize",
    "get_perception_port": "src.platform.perception.composition",
}


def __getattr__(name: str) -> Any:  # PEP 562 — resolución lazy por símbolo
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value  # cache: el segundo acceso es un dict lookup
    return value


def __dir__() -> list[str]:
    return sorted([*globals().keys(), *_LAZY_EXPORTS.keys()])
