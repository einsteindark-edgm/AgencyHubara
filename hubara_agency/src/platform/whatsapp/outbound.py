"""Builders DTO → JSON Meta WhatsApp Cloud API.

Cada función `build_*` toma un DTO de `dtos.py` y devuelve el dict que
WhatsApp Cloud API espera. Centraliza:

* Validación de límites (delega en `limits.py`).
* Wrapping con `messaging_product`, `recipient_type`, `to`, `type`, etc.
* Inclusión de `context.message_id` si se está respondiendo a un mensaje
  específico del cliente (citación / threading nativo).

Mantenido separado del cliente HTTP (`client.py`) para que los tests
unitarios de los builders no necesiten ni httpx ni un token mock.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.platform.whatsapp import dtos as wa_dtos
from src.platform.whatsapp import limits
from src.platform.whatsapp.media_url import normalize_image_url


# =============================================================================
# Common base
# =============================================================================


def _base(to: str, msg_type: str, reply_to: str | None) -> dict[str, Any]:
    """Skeleton común para casi todos los outbound."""
    data: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": msg_type,
    }
    if reply_to:
        data["context"] = {"message_id": reply_to}
    return data


def _validate_media_source(link: str | None, media_id: str | None, label: str) -> dict[str, Any]:
    """Meta acepta `link` (URL pública HTTPS) XOR `id` (media subido previamente)."""
    if link and media_id:
        raise ValueError(f"{label}: link y media_id son mutuamente excluyentes")
    if not link and not media_id:
        raise ValueError(f"{label}: link o media_id es requerido")
    if link:
        if not link.startswith("https://"):
            raise ValueError(f"{label}: link debe ser HTTPS público")
        return {"link": link}
    return {"id": media_id}


# =============================================================================
# Media
# =============================================================================


def build_image(
    to: str, p: wa_dtos.ImageOutbound, reply_to: str | None
) -> dict[str, Any]:
    # WhatsApp Cloud API solo acepta image/jpeg|image/png en mensajes
    # type=image. El catálogo Hubara guarda assets en .webp — sin esta
    # normalización Meta acepta el POST (200 + wa_message_id) pero
    # silenciosamente no renderiza la imagen al cliente.
    link = normalize_image_url(p.link) if p.link else p.link
    data = _base(to, "image", reply_to)
    image = _validate_media_source(link, p.media_id, "image")
    if p.caption is not None:
        image["caption"] = limits.truncate(p.caption, limits.MAX_CAPTION)
    data["image"] = image
    return data


def build_audio(
    to: str, p: wa_dtos.AudioOutbound, reply_to: str | None
) -> dict[str, Any]:
    data = _base(to, "audio", reply_to)
    data["audio"] = _validate_media_source(p.link, p.media_id, "audio")
    return data


def build_video(
    to: str, p: wa_dtos.VideoOutbound, reply_to: str | None
) -> dict[str, Any]:
    data = _base(to, "video", reply_to)
    video = _validate_media_source(p.link, p.media_id, "video")
    if p.caption is not None:
        video["caption"] = limits.truncate(p.caption, limits.MAX_CAPTION)
    data["video"] = video
    return data


def build_document(
    to: str, p: wa_dtos.DocumentOutbound, reply_to: str | None
) -> dict[str, Any]:
    data = _base(to, "document", reply_to)
    doc = _validate_media_source(p.link, p.media_id, "document")
    if p.caption is not None:
        doc["caption"] = limits.truncate(p.caption, limits.MAX_CAPTION)
    if p.filename is not None:
        doc["filename"] = p.filename
    data["document"] = doc
    return data


# =============================================================================
# Interactive — reply buttons
# =============================================================================


def build_interactive_buttons(
    to: str, p: wa_dtos.InteractiveButtonsOutbound, reply_to: str | None
) -> dict[str, Any]:
    if not p.buttons:
        raise ValueError("interactive.button: al menos 1 botón requerido")
    if len(p.buttons) > limits.MAX_REPLY_BUTTONS:
        raise ValueError(
            f"interactive.button: máx {limits.MAX_REPLY_BUTTONS} botones, "
            f"recibí {len(p.buttons)}"
        )

    buttons_json: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for b in p.buttons:
        if b.id in seen_ids:
            raise ValueError(f"interactive.button: id duplicado: {b.id}")
        seen_ids.add(b.id)
        if len(b.id) > limits.MAX_BUTTON_ID:
            raise ValueError(
                f"interactive.button: id '{b.id}' excede {limits.MAX_BUTTON_ID} chars"
            )
        buttons_json.append({
            "type": "reply",
            "reply": {
                "id": b.id,
                "title": limits.truncate(b.title, limits.MAX_BUTTON_TITLE),
            },
        })

    interactive: dict[str, Any] = {
        "type": "button",
        "body": {"text": limits.truncate(p.body, limits.MAX_BUTTON_BODY)},
        "action": {"buttons": buttons_json},
    }
    if p.header_text is not None:
        interactive["header"] = {
            "type": "text",
            "text": limits.truncate(p.header_text, limits.MAX_BUTTON_HEADER_TEXT),
        }
    if p.footer is not None:
        interactive["footer"] = {
            "text": limits.truncate(p.footer, limits.MAX_BUTTON_FOOTER)
        }

    data = _base(to, "interactive", reply_to)
    data["interactive"] = interactive
    return data


# =============================================================================
# Interactive — list
# =============================================================================


def build_interactive_list(
    to: str, p: wa_dtos.InteractiveListOutbound, reply_to: str | None
) -> dict[str, Any]:
    if not p.sections:
        raise ValueError("interactive.list: al menos 1 sección requerida")
    if len(p.sections) > limits.MAX_LIST_SECTIONS:
        raise ValueError(
            f"interactive.list: máx {limits.MAX_LIST_SECTIONS} secciones, "
            f"recibí {len(p.sections)}"
        )

    sections_json: list[dict[str, Any]] = []
    total_rows = 0
    seen_row_ids: set[str] = set()
    for s in p.sections:
        if not s.rows:
            raise ValueError(f"interactive.list: section '{s.title}' sin rows")
        if len(s.rows) > limits.MAX_LIST_ROWS_PER_SECTION:
            raise ValueError(
                f"interactive.list: section '{s.title}' excede "
                f"{limits.MAX_LIST_ROWS_PER_SECTION} rows"
            )
        rows_json: list[dict[str, Any]] = []
        for r in s.rows:
            if r.id in seen_row_ids:
                raise ValueError(f"interactive.list: row id duplicado: {r.id}")
            seen_row_ids.add(r.id)
            row: dict[str, Any] = {
                "id": r.id,
                "title": limits.truncate(r.title, limits.MAX_LIST_ROW_TITLE),
            }
            if r.description is not None:
                row["description"] = limits.truncate(
                    r.description, limits.MAX_LIST_ROW_DESCRIPTION
                )
            rows_json.append(row)
            total_rows += 1
        sections_json.append({
            "title": limits.truncate(s.title, limits.MAX_LIST_SECTION_TITLE),
            "rows": rows_json,
        })

    interactive: dict[str, Any] = {
        "type": "list",
        "body": {"text": limits.truncate(p.body, limits.MAX_LIST_BODY)},
        "action": {
            "button": limits.truncate(p.button_label, limits.MAX_LIST_BUTTON),
            "sections": sections_json,
        },
    }
    if p.header_text is not None:
        interactive["header"] = {
            "type": "text",
            "text": limits.truncate(p.header_text, limits.MAX_LIST_HEADER_TEXT),
        }
    if p.footer is not None:
        interactive["footer"] = {
            "text": limits.truncate(p.footer, limits.MAX_LIST_FOOTER)
        }

    data = _base(to, "interactive", reply_to)
    data["interactive"] = interactive
    return data


# =============================================================================
# Interactive — CTA URL
# =============================================================================


def build_cta_url(
    to: str, p: wa_dtos.InteractiveCTAUrlOutbound, reply_to: str | None
) -> dict[str, Any]:
    if not p.url.startswith("https://"):
        raise ValueError("interactive.cta_url: URL debe ser HTTPS")
    interactive: dict[str, Any] = {
        "type": "cta_url",
        "body": {"text": limits.truncate(p.body, limits.MAX_CTA_BODY)},
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": limits.truncate(p.button_text, limits.MAX_CTA_BUTTON),
                "url": p.url,
            },
        },
    }
    if p.header_text is not None:
        interactive["header"] = {"type": "text", "text": p.header_text}
    if p.footer is not None:
        interactive["footer"] = {
            "text": limits.truncate(p.footer, limits.MAX_CTA_FOOTER)
        }

    data = _base(to, "interactive", reply_to)
    data["interactive"] = interactive
    return data


# =============================================================================
# Interactive — location request
# =============================================================================


def build_location_request(
    to: str, p: wa_dtos.InteractiveLocationRequestOutbound, reply_to: str | None
) -> dict[str, Any]:
    interactive: dict[str, Any] = {
        "type": "location_request_message",
        "body": {"text": p.body},
        "action": {"name": "send_location"},
    }
    data = _base(to, "interactive", reply_to)
    data["interactive"] = interactive
    return data


# =============================================================================
# Interactive — flow
# =============================================================================


def build_flow(
    to: str, p: wa_dtos.InteractiveFlowOutbound, reply_to: str | None
) -> dict[str, Any]:
    if p.flow_action == "navigate" and not p.flow_action_screen:
        raise ValueError("interactive.flow: flow_action_screen requerido para action=navigate")

    action_payload: dict[str, Any] = {
        "screen": p.flow_action_screen,
    }
    if p.flow_action_data is not None:
        action_payload["data"] = p.flow_action_data

    interactive: dict[str, Any] = {
        "type": "flow",
        "body": {"text": p.body or "Completá los datos para continuar"},
        "action": {
            "name": "flow",
            "parameters": {
                "flow_message_version": "3",
                "flow_token": p.flow_token,
                "flow_id": p.flow_id,
                "flow_cta": p.flow_cta,
                "flow_action": p.flow_action,
                "flow_action_payload": action_payload,
                "mode": p.mode,
            },
        },
    }
    if p.header_text is not None:
        interactive["header"] = {"type": "text", "text": p.header_text}
    if p.footer is not None:
        interactive["footer"] = {"text": p.footer}

    data = _base(to, "interactive", reply_to)
    data["interactive"] = interactive
    return data


# =============================================================================
# Interactive — product / product_list (Meta Catalog)
# =============================================================================


def build_product(
    to: str, p: wa_dtos.InteractiveProductOutbound, reply_to: str | None
) -> dict[str, Any]:
    interactive: dict[str, Any] = {
        "type": "product",
        "action": {
            "catalog_id": p.catalog_id,
            "product_retailer_id": p.product_retailer_id,
        },
    }
    if p.body is not None:
        interactive["body"] = {"text": p.body}
    if p.footer is not None:
        interactive["footer"] = {"text": p.footer}

    data = _base(to, "interactive", reply_to)
    data["interactive"] = interactive
    return data


def build_product_list(
    to: str, p: wa_dtos.InteractiveProductListOutbound, reply_to: str | None
) -> dict[str, Any]:
    if len(p.sections) > limits.MAX_PRODUCT_LIST_SECTIONS:
        raise ValueError(
            f"interactive.product_list: máx {limits.MAX_PRODUCT_LIST_SECTIONS} "
            f"sections, recibí {len(p.sections)}"
        )
    total_items = sum(len(s.product_items) for s in p.sections)
    if total_items > limits.MAX_PRODUCT_LIST_ITEMS_TOTAL:
        raise ValueError(
            f"interactive.product_list: máx {limits.MAX_PRODUCT_LIST_ITEMS_TOTAL} "
            f"items totales, recibí {total_items}"
        )
    if total_items == 0:
        raise ValueError("interactive.product_list: necesita al menos 1 item")

    sections_json = [
        {
            "title": limits.truncate(s.title, limits.MAX_LIST_SECTION_TITLE),
            "product_items": [
                {"product_retailer_id": item.product_retailer_id}
                for item in s.product_items
            ],
        }
        for s in p.sections
    ]

    interactive: dict[str, Any] = {
        "type": "product_list",
        "header": {
            "type": "text",
            "text": limits.truncate(p.header_text, limits.MAX_PRODUCT_LIST_HEADER),
        },
        "body": {"text": limits.truncate(p.body, limits.MAX_PRODUCT_LIST_BODY)},
        "action": {
            "catalog_id": p.catalog_id,
            "sections": sections_json,
        },
    }
    if p.footer is not None:
        interactive["footer"] = {
            "text": limits.truncate(p.footer, limits.MAX_PRODUCT_LIST_FOOTER)
        }

    data = _base(to, "interactive", reply_to)
    data["interactive"] = interactive
    return data


# =============================================================================
# Interactive — order_details
# =============================================================================


def build_order_details(
    to: str, p: wa_dtos.InteractiveOrderDetailsOutbound, reply_to: str | None
) -> dict[str, Any]:
    if not p.items:
        raise ValueError("interactive.order_details: al menos 1 item requerido")
    if not p.payment_settings:
        raise ValueError("interactive.order_details: payment_settings requerido")

    items_json = []
    for it in p.items:
        item: dict[str, Any] = {
            "retailer_id": it.retailer_id,
            "name": it.name,
            "amount": {"value": it.amount_offset, "offset": 100},
            "quantity": it.quantity,
        }
        if it.sale_amount_offset is not None:
            item["sale_amount"] = {"value": it.sale_amount_offset, "offset": 100}
        items_json.append(item)

    order: dict[str, Any] = {
        "status": "pending",
        "items": items_json,
        "subtotal": {"value": p.subtotal_1000, "offset": 1000},
        "catalog_id": p.catalog_id,
    }
    if p.tax is not None:
        order["tax"] = {
            "value": p.tax.value_1000,
            "offset": 1000,
            **({"description": p.tax.description} if p.tax.description else {}),
        }
    if p.shipping is not None:
        order["shipping"] = {
            "value": p.shipping.value_1000,
            "offset": 1000,
            **({"description": p.shipping.description} if p.shipping.description else {}),
        }
    if p.discount is not None:
        order["discount"] = {
            "value": p.discount.value_1000,
            "offset": 1000,
            **({"description": p.discount.description} if p.discount.description else {}),
            **({"discount_program_name": p.discount.discount_program_name}
               if p.discount.discount_program_name else {}),
        }

    payment_settings_json = []
    for pay in p.payment_settings:
        payment_settings_json.append({
            "type": pay.type,
            "payment_gateway": {
                "type": pay.payment_gateway_type,
                "configuration_name": pay.configuration_name,
            },
        })

    interactive: dict[str, Any] = {
        "type": "order_details",
        "body": {"text": p.body_text or "Confirmá tu pedido para continuar con el pago."},
        "action": {
            "name": "review_and_pay",
            "parameters": {
                "reference_id": p.reference_id,
                "type": "physical-goods",
                "currency": p.currency,
                "total_amount": {
                    "value": p.total.value_1000,
                    "offset": p.total.offset,
                },
                "order": order,
                "payment_settings": payment_settings_json,
            },
        },
    }
    if p.expiration_unix is not None:
        interactive["action"]["parameters"]["expiration"] = {
            "timestamp": str(p.expiration_unix),
            "description": "El pedido expira",
        }
    if p.shipping_address is not None:
        interactive["action"]["parameters"]["beneficiary"] = {
            "name": p.shipping_address.name,
            "address_line1": p.shipping_address.address_line_1,
            "address_line2": p.shipping_address.address_line_2 or "",
            "city": p.shipping_address.city,
            "state": p.shipping_address.state or "",
            "country": p.shipping_address.country,
            "postal_code": p.shipping_address.postal_code or "",
        }
    if p.footer is not None:
        interactive["footer"] = {"text": p.footer}

    data = _base(to, "interactive", reply_to)
    data["interactive"] = interactive
    return data


# =============================================================================
# Native — location / contacts / reaction
# =============================================================================


def build_location(
    to: str, p: wa_dtos.LocationOutbound, reply_to: str | None
) -> dict[str, Any]:
    data = _base(to, "location", reply_to)
    loc: dict[str, Any] = {
        "latitude": p.latitude,
        "longitude": p.longitude,
    }
    if p.name is not None:
        loc["name"] = p.name
    if p.address is not None:
        loc["address"] = p.address
    data["location"] = loc
    return data


def build_contacts(
    to: str, contacts: list[wa_dtos.ContactCard], reply_to: str | None
) -> dict[str, Any]:
    if not contacts:
        raise ValueError("contacts: lista vacía")
    data = _base(to, "contacts", reply_to)
    data["contacts"] = [_contact_card_to_json(c) for c in contacts]
    return data


def _contact_card_to_json(c: wa_dtos.ContactCard) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": {
            "formatted_name": c.name.formatted_name,
            **({"first_name": c.name.first_name} if c.name.first_name else {}),
            **({"last_name": c.name.last_name} if c.name.last_name else {}),
        }
    }
    if c.phones:
        out["phones"] = [
            {
                "phone": p.phone,
                "type": p.type,
                **({"wa_id": p.wa_id} if p.wa_id else {}),
            }
            for p in c.phones
        ]
    if c.org_name or c.org_title:
        org = {}
        if c.org_name:
            org["company"] = c.org_name
        if c.org_title:
            org["title"] = c.org_title
        out["org"] = org
    return out


def build_reaction(to: str, p: wa_dtos.ReactionOutbound) -> dict[str, Any]:
    if p.emoji and p.emoji not in limits.HUBARA_REACTION_ALLOWLIST:
        raise ValueError(
            f"reaction: emoji '{p.emoji}' no está en la allowlist Hubara. "
            f"Permitidos: {sorted(limits.HUBARA_REACTION_ALLOWLIST)}"
        )
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "reaction",
        "reaction": {"message_id": p.message_id, "emoji": p.emoji},
    }


# =============================================================================
# Re-export `asdict` for symmetry with callers that want raw dicts
# =============================================================================

__all__ = [
    "build_image",
    "build_audio",
    "build_video",
    "build_document",
    "build_interactive_buttons",
    "build_interactive_list",
    "build_cta_url",
    "build_location_request",
    "build_flow",
    "build_product",
    "build_product_list",
    "build_order_details",
    "build_location",
    "build_contacts",
    "build_reaction",
    "asdict",
]
