"""Detector de opt-out de marketing — la promesa del template hecha real.

El template de campañas promete "respóndeme NO MÁS y te doy de baja". Este
detector es quien la cumple: inbound con pedido de baja + campaña reciente
(touch en ventana) ⇒ `marketing_opt_out=true`. Determinista, sin LLM.
"""
import pytest

from src.platform.whatsapp.marketing_opt_out import detect_marketing_opt_out

_NOW = 1_750_000_000_000
_HOUR = 60 * 60 * 1000
_TOUCHED = {
    "campaign_touches": [
        {"campaign_id": "mkt-1", "campaign_name": "Promo", "sent_at_ms": _NOW - _HOUR}
    ]
}


@pytest.mark.parametrize(
    "text",
    [
        "NO MÁS",
        "no mas",
        "No más promociones por favor",
        "no quiero recibir más mensajes",
        "dame de baja",
        "denme de baja por favor",
        "baja",
        "BAJA.",
        "no me envíes más promos",
        "no me escribas más",
    ],
)
def test_detecta_pedidos_de_baja_con_campana_reciente(text) -> None:
    assert detect_marketing_opt_out(text, _TOUCHED, _NOW) is True


@pytest.mark.parametrize(
    "text",
    [
        "quiero 2 velas del catálogo",
        "me gusta la baja del precio, la quiero comprar",  # "baja" en frase larga
        "hola",
        "",
        None,
        "cuánto vale el envío?",
    ],
)
def test_no_detecta_texto_normal_de_venta(text) -> None:
    assert detect_marketing_opt_out(text, _TOUCHED, _NOW) is False


def test_sin_campana_reciente_no_marca_opt_out() -> None:
    """Sin touch en ventana, "no más" puede ser parte de una conversación
    normal con el bot — el detector solo honra la promesa del template."""
    old_touch = {
        "campaign_touches": [
            {"campaign_id": "mkt-1", "sent_at_ms": _NOW - 10 * 24 * _HOUR}
        ]
    }
    assert detect_marketing_opt_out("NO MÁS", {}, _NOW) is False
    assert detect_marketing_opt_out("NO MÁS", old_touch, _NOW) is False


def test_messagingkit_reexporta_el_detector() -> None:
    import src.platform.whatsapp.marketing_opt_out as impl
    import src.sdk.messagingkit as kit

    assert kit.detect_marketing_opt_out is impl.detect_marketing_opt_out
