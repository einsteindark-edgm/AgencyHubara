"""DTOs JSON-safe para outbound e inbound de WhatsApp Cloud API.

R-JSON: todo `@dataclass(frozen=True)` y serializable a dict primitivo. Los
workflows que decidan emitir UI cruzan estos DTOs como envelopes hacia las
activities, y las activities los convierten al JSON de Meta vía `client.py`.

Separación con `client.py`:
* Acá vive la *forma* (qué es un Button, qué es un ListSection, etc.).
* En `client.py` vive el *envío* (cómo Meta espera el JSON).
* `outbound.py` traduce DTO → JSON Meta.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# =============================================================================
# Common
# =============================================================================


@dataclass(frozen=True)
class OutboundResult:
    """Resultado del envío. wa_message_id es el id que asigna Meta para
    tracking de delivery/read/analytics."""

    wa_message_id: str | None
    ok: bool
    error: str | None = None


# =============================================================================
# Media outbound (image, audio, video, document)
# =============================================================================


@dataclass(frozen=True)
class ImageOutbound:
    """Imagen por link (preferido) o media_id (si fue subida previamente)."""

    link: str | None = None
    media_id: str | None = None
    caption: str | None = None


@dataclass(frozen=True)
class AudioOutbound:
    link: str | None = None
    media_id: str | None = None


@dataclass(frozen=True)
class VideoOutbound:
    link: str | None = None
    media_id: str | None = None
    caption: str | None = None


@dataclass(frozen=True)
class DocumentOutbound:
    link: str | None = None
    media_id: str | None = None
    caption: str | None = None
    filename: str | None = None


# =============================================================================
# Interactive — Reply Buttons
# =============================================================================


@dataclass(frozen=True)
class ReplyButton:
    id: str  # estable, documentado (ej. "payment.cash_on_delivery")
    title: str  # ≤ 20 chars


@dataclass(frozen=True)
class InteractiveButtonsOutbound:
    body: str  # ≤ 1024
    buttons: list[ReplyButton]  # 1..3
    header_text: str | None = None  # ≤ 60
    footer: str | None = None  # ≤ 60


# =============================================================================
# Interactive — List
# =============================================================================


@dataclass(frozen=True)
class ListRow:
    id: str  # 1..200 chars (Hubara: usamos el handle del producto)
    title: str  # ≤ 24
    description: str | None = None  # ≤ 72


@dataclass(frozen=True)
class ListSection:
    title: str  # ≤ 24
    rows: list[ListRow]  # 1..10


@dataclass(frozen=True)
class InteractiveListOutbound:
    body: str
    button_label: str  # ≤ 20, e.g. "Ver opciones"
    sections: list[ListSection]  # 1..10
    header_text: str | None = None  # ≤ 60
    footer: str | None = None


# =============================================================================
# Interactive — CTA URL Button
# =============================================================================


@dataclass(frozen=True)
class InteractiveCTAUrlOutbound:
    body: str
    button_text: str  # ≤ 20
    url: str
    header_text: str | None = None
    footer: str | None = None


# =============================================================================
# Interactive — Location Request
# =============================================================================


@dataclass(frozen=True)
class InteractiveLocationRequestOutbound:
    body: str  # texto que acompaña el botón "Compartir ubicación"


# =============================================================================
# Interactive — Flow
# =============================================================================


@dataclass(frozen=True)
class InteractiveFlowOutbound:
    flow_id: str
    flow_token: str  # único por sesión — lo emite el agente
    flow_cta: str  # ≤ 20 — texto del botón que abre el Flow
    flow_action: Literal["navigate", "data_exchange"] = "navigate"
    flow_action_screen: str | None = None  # required si flow_action="navigate"
    flow_action_data: dict | None = None  # data inicial pasada al Flow
    body: str | None = None
    header_text: str | None = None
    footer: str | None = None
    mode: Literal["draft", "published"] = "published"


# =============================================================================
# Interactive — Product / Product List (requiere Meta Catalog)
# =============================================================================


@dataclass(frozen=True)
class InteractiveProductOutbound:
    catalog_id: str
    product_retailer_id: str  # = CatalogProductDTO.id
    body: str | None = None
    footer: str | None = None


@dataclass(frozen=True)
class ProductItem:
    product_retailer_id: str  # = CatalogProductDTO.id


@dataclass(frozen=True)
class ProductSection:
    title: str  # ≤ 24
    product_items: list[ProductItem]


@dataclass(frozen=True)
class InteractiveProductListOutbound:
    catalog_id: str
    header_text: str  # ≤ 60
    body: str  # ≤ 1024
    sections: list[ProductSection]  # 1..10, hasta 30 items totales
    footer: str | None = None


# =============================================================================
# Interactive — Order Details (requiere Meta Catalog + payment gateway)
# =============================================================================


@dataclass(frozen=True)
class OrderItem:
    retailer_id: str  # = CatalogProductDTO.id
    name: str
    amount_offset: int  # precio unitario × 100 (minor units, ej. centavos)
    quantity: int
    sale_amount_offset: int | None = None  # si hay descuento


@dataclass(frozen=True)
class OrderTax:
    value_1000: int  # minor units × 1000
    description: str | None = None


@dataclass(frozen=True)
class OrderShipping:
    value_1000: int
    description: str | None = None


@dataclass(frozen=True)
class OrderDiscount:
    value_1000: int
    description: str | None = None
    discount_program_name: str | None = None


@dataclass(frozen=True)
class OrderTotal:
    value_1000: int  # subtotal + tax + shipping − discount (todo × 1000)
    offset: int = 1000


@dataclass(frozen=True)
class PaymentConfig:
    """Configuración de pago. Wompi para Colombia."""

    type: Literal["payment_gateway"]
    payment_gateway_type: Literal["wompi", "payu", "razorpay", "stripe"]
    configuration_name: str  # nombre registrado en Meta Commerce Manager


@dataclass(frozen=True)
class PhysicalGoodsBeneficiary:
    name: str
    address_line_1: str
    address_line_2: str | None
    city: str
    state: str | None
    country: str  # ISO-2 (CO)
    postal_code: str | None


@dataclass(frozen=True)
class InteractiveOrderDetailsOutbound:
    reference_id: str  # HUB-{tenant}-{session_id}-{epoch}
    catalog_id: str
    items: list[OrderItem]
    subtotal_1000: int
    total: OrderTotal
    tax: OrderTax | None
    shipping: OrderShipping | None
    discount: OrderDiscount | None
    currency: str  # "COP"
    payment_settings: list[PaymentConfig]
    shipping_address: PhysicalGoodsBeneficiary | None = None
    body_text: str = ""
    footer: str | None = None
    expiration_unix: int | None = None


# =============================================================================
# Reactions / Contacts / Native location
# =============================================================================


@dataclass(frozen=True)
class ReactionOutbound:
    message_id: str  # el msg del cliente al que reaccionamos
    emoji: str  # debe estar en HUBARA_REACTION_ALLOWLIST


@dataclass(frozen=True)
class ContactPhone:
    phone: str
    type: Literal["CELL", "WORK", "HOME"] = "CELL"
    wa_id: str | None = None  # si querés que aparezca como contacto WA


@dataclass(frozen=True)
class ContactName:
    formatted_name: str
    first_name: str | None = None
    last_name: str | None = None


@dataclass(frozen=True)
class ContactCard:
    name: ContactName
    phones: list[ContactPhone] = field(default_factory=list)
    org_name: str | None = None
    org_title: str | None = None


@dataclass(frozen=True)
class LocationOutbound:
    latitude: float
    longitude: float
    name: str | None = None
    address: str | None = None


# =============================================================================
# Inbound — referral (CTWA attribution) — extra documented
# =============================================================================


@dataclass(frozen=True)
class ReferralInbound:
    """Payload de atribución cuando el cliente llega desde un CTWA ad / FB post.

    Campos completos según Meta Cloud API webhook:
      - ctwa_clid: Click ID generado por Meta (Android/iOS only; ausente en web)
      - source_url: URL del ad/post de Meta
      - source_id: ID interno de Meta (ad_id o post_id)
      - source_type: "ad" | "post"
      - headline: título del ad
      - body: descripción del ad
      - media_type: "image" | "video"
      - image_url, video_url, thumbnail_url: media raw del ad
      - referred_product: si el cliente clickeó un producto específico del
        catalog dentro del ad (DPA / catálogo)

    Se persiste íntegro en el state.json de la sesión para attribution
    server-side via Conversions API.
    """

    source_url: str | None = None
    source_id: str | None = None
    source_type: Literal["ad", "post"] | None = None
    headline: str | None = None
    body: str | None = None
    media_type: Literal["image", "video"] | None = None
    image_url: str | None = None
    video_url: str | None = None
    thumbnail_url: str | None = None
    ctwa_clid: str | None = None
    referred_product: dict | None = None  # {"catalog_id": "...", "product_retailer_id": "..."}


# =============================================================================
# Inbound — interactive responses
# =============================================================================


@dataclass(frozen=True)
class ButtonReplyInbound:
    id: str
    title: str


@dataclass(frozen=True)
class ListReplyInbound:
    id: str
    title: str
    description: str | None = None


@dataclass(frozen=True)
class FlowReplyInbound:
    """Submit del Flow (nfm_reply).

    response_json es el payload completo que devolvió el Flow. Lo dejamos como
    dict y el ingestor extrae lo que necesite.
    """

    name: str  # nombre del Flow
    body: str | None  # texto que el cliente ve cuando recibió el Flow
    response_json: dict


@dataclass(frozen=True)
class OrderInboundItem:
    product_retailer_id: str
    quantity: int
    item_price: str  # como string (Decimal-safe)
    currency: str


@dataclass(frozen=True)
class OrderInbound:
    """`type: "order"` — cliente agregó productos al carrito de WA y
    confirmó. Llega antes del Order Details/payment."""

    catalog_id: str
    text: str | None
    product_items: list[OrderInboundItem]


@dataclass(frozen=True)
class OrderStatusInbound:
    """Resultado de pago via Order Details A.12."""

    reference_id: str
    status: Literal["captured", "failed", "pending", "canceled"]
    payment_method: str | None = None
    gateway_reference: str | None = None
    error_code: str | None = None
    error_message: str | None = None
