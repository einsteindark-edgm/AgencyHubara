"""Payloads del webhook `standby` (D1.4), copiados de la referencia de Meta
"Standby webhooks" (developers.facebook.com, actualizada 2026-08-04).

Sobre común: `entry[].changes[].field == "standby"` y el objeto
`value.standby` contiene UNO de `messages` (+ `contacts`), `message_echoes`
o `statuses`. Los tres ejemplos de abajo son los de la doc con placeholders
reemplazados por teléfonos sintéticos permitidos por el scanner.
"""
from __future__ import annotations

import copy
from typing import Any

CUSTOMER = "573001234567"  # → sesión wa_573001234567
PHONE_NUMBER_ID = "PHONE_777"


def _envelope(standby: dict[str, Any], *, field: str = "standby") -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_1",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "573229041190", "phone_number_id": PHONE_NUMBER_ID},
                            "standby": standby,
                        },
                        "field": field,
                    }
                ],
            }
        ],
    }


def inbound(text: str = "Test standby message", *, wamid: str = "wamid.STANDBY.IN.1", ts: str = "1757300000") -> dict[str, Any]:
    return _envelope(
        {
            "contacts": [{"profile": {"name": "Test User"}, "wa_id": CUSTOMER}],
            "messages": [{"from": CUSTOMER, "id": wamid, "timestamp": ts, "text": {"body": text}, "type": "text"}],
        }
    )


def inbound_image(*, wamid: str = "wamid.STANDBY.IN.IMG") -> dict[str, Any]:
    body = inbound(wamid=wamid)
    body["entry"][0]["changes"][0]["value"]["standby"]["messages"] = [
        {"from": CUSTOMER, "id": wamid, "timestamp": "1757300010", "type": "image",
         "image": {"id": "MEDIA_1", "mime_type": "image/jpeg", "sha256": "x", "caption": "mi comprobante"}}
    ]
    return body


def inbound_with_referral(*, wamid: str = "wamid.STANDBY.IN.AD") -> dict[str, Any]:
    body = inbound("Hola, vi el anuncio", wamid=wamid)
    body["entry"][0]["changes"][0]["value"]["standby"]["messages"][0]["referral"] = {
        "source_url": "https://fb.me/ad1", "source_id": "AD_1", "source_type": "ad", "headline": "Velas",
        "ctwa_clid": "CLID_1",
    }
    return body


def echo_text(text: str = "Hello! Your order #12345 has shipped.", *, wamid: str = "wamid.STANDBY.ECHO.1",
              ts: str = "1757300020") -> dict[str, Any]:
    return _envelope(
        {
            "message_echoes": [
                {
                    "id": wamid,
                    "timestamp": ts,
                    "message": {
                        "messaging_product": "whatsapp",
                        "to": CUSTOMER,
                        "recipient_type": "individual",
                        "type": "text",
                        "text": {"body": text, "preview_url": True},
                        "context": {"message_id": "wamid.STANDBY.IN.1"},
                    },
                }
            ]
        }
    )


def echo_template(*, wamid: str = "wamid.STANDBY.ECHO.TPL") -> dict[str, Any]:
    return _envelope(
        {
            "message_echoes": [
                {
                    "id": wamid,
                    "timestamp": "1757300030",
                    "message": {
                        "messaging_product": "whatsapp", "to": CUSTOMER, "recipient_type": "individual",
                        "type": "template",
                        "template": {"name": "summer_sale_2026", "language": {"code": "en_US"},
                                     "components": [{"type": "body", "parameters": [{"type": "text", "text": "Maria"}]}]},
                    },
                    "template": {"name": "summer_sale_2026", "language": "en_US", "category": "MARKETING",
                                 "components": [{"type": "BODY", "text": "Hi {{1}}, enjoy {{2}} off!"}], "status": "APPROVED"},
                }
            ]
        }
    )


def echo_flow(*, wamid: str = "wamid.STANDBY.ECHO.FLOW") -> dict[str, Any]:
    return _envelope(
        {
            "message_echoes": [
                {
                    "id": wamid,
                    "timestamp": "1757300040",
                    "message": {
                        "messaging_product": "whatsapp", "to": CUSTOMER, "recipient_type": "individual",
                        "type": "interactive",
                        "interactive": {"type": "flow", "header": {"type": "text", "text": "Book Appointment"},
                                        "body": {"text": "Schedule your visit with us."},
                                        "footer": {"text": "Tap below to continue"},
                                        "action": {"name": "flow", "parameters": {"flow_id": "FLOW_1", "flow_cta": "Book Now"}}},
                    },
                    "flow": {"id": "FLOW_1", "name": "Appointment Booking", "status": "PUBLISHED"},
                }
            ]
        }
    )


def status(*, wamid: str = "wamid.STANDBY.ECHO.1", kind: str = "delivered", category: str = "utility",
           billable: bool = True) -> dict[str, Any]:
    return _envelope(
        {
            "statuses": [
                {
                    "id": wamid, "status": kind, "timestamp": "1757300050", "recipient_id": CUSTOMER,
                    "conversation": {"id": "CONV_1", "origin": {"type": category}},
                    # OJO: la doc de Meta escribe `type`, no `pricing_type`.
                    "pricing": {"billable": billable, "pricing_model": "PMP", "category": category, "type": "regular"},
                }
            ]
        }
    )


OUR_APP_ID = "100000000000001"  # nuestra app (WHATSAPP_APP_ID); id sintético
MBA_APP_ID = "APP_MBA_999"  # el app id con el que Meta firma a Business Agent (a verificar en F0)


def handover(new_owner: str = OUR_APP_ID, previous_owner: str | None = MBA_APP_ID, *, ts: str = "1757300060",
             metadata: str | None = None, customer: str = CUSTOMER, kind: str = "control_taken") -> dict[str, Any]:
    """`messaging_handovers` (D1.5). La referencia de Meta para WhatsApp aún no
    está publicada: el shape sigue el del roadmap (`control_taken` con
    `previous_owner_app_id` / `new_owner_app_id` / `metadata`) sobre el sobre
    de Messenger (`sender.id` = cliente). Verificar en F0."""
    body = _envelope({}, field="messaging_handovers")
    value = body["entry"][0]["changes"][0]["value"]
    value.pop("standby")
    control = {"previous_owner_app_id": previous_owner, "new_owner_app_id": new_owner}
    if metadata is not None:
        control["metadata"] = metadata
    value["messaging_handovers"] = [
        {"sender": {"id": customer}, "recipient": {"id": PHONE_NUMBER_ID}, "timestamp": ts, kind: control}
    ]
    return body


def handover_messenger_style(new_owner: str = MBA_APP_ID, previous_owner: str | None = OUR_APP_ID) -> dict[str, Any]:
    """Variante con el sobre EXACTO del protocolo de traspaso de Messenger
    (`pass_thread_control`, timestamp en ms como int)."""
    body = _envelope({}, field="messaging_handovers")
    value = body["entry"][0]["changes"][0]["value"]
    value.pop("standby")
    value["messaging_handovers"] = [
        {"sender": {"id": CUSTOMER}, "recipient": {"id": PHONE_NUMBER_ID}, "timestamp": 1757300060000,
         "pass_thread_control": {"previous_owner_app_id": previous_owner, "new_owner_app_id": new_owner,
                                 "metadata": "release"}}
    ]
    return body


def merged(*bodies: dict[str, Any]) -> dict[str, Any]:
    """Un solo webhook con varios `changes` (Meta puede agrupar)."""
    out = copy.deepcopy(bodies[0])
    for b in bodies[1:]:
        out["entry"][0]["changes"].extend(copy.deepcopy(b["entry"][0]["changes"]))
    return out
