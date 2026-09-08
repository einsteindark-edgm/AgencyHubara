"""Link de guía (tracking) opcional en la notificación "en camino".

Comportamiento a fijar: cuando el operador mueve un pedido a ``shipping`` y
adjunta un link de guía, ese link viaja en el MISMO mensaje que avisa el
cambio de estado — como URL cruda en su propia línea (WhatsApp la linkifica
y el cliente puede tocarla). Sin link, el mensaje es byte-a-byte el de hoy.

Fuera de la ventana 24h el único canal es el template aprobado
``order_status_utility_v2``: el link va dentro del slot ``status_label`` y
el registry lo tiene que aceptar (max_length del catálogo).
"""
from __future__ import annotations

from src.plugins.eta.agent.eta.prompts import (
    build_status_template_variables,
    render_stage_notification,
)

URL = "https://www.servientrega.com/wps/portal/rastreo-envio?guia=1234567890"


def test_render_shipping_with_tracking_url_appends_tappable_link():
    msg = render_stage_notification(
        stage="shipping", customer_name="Ana", order_display_id="#9",
        total_label="", pay_type="confirmed", payment_confirmed=False,
        items_label="Difusor", tracking_url=URL,
    )
    assert msg == (
        "Tu pedido #9 (Difusor) ya va en camino 🚚. Te aviso cuando esté por llegar."
        f"\n\nPuedes seguir tu envío aquí: {URL}"
    )
    # La URL va cruda (sin markdown, sin paréntesis pegados) para que
    # WhatsApp la reconozca como link tappable.
    assert f": {URL}" in msg and msg.endswith(URL)


def test_render_shipping_with_tracking_url_keeps_payment_line_before_link():
    msg = render_stage_notification(
        stage="shipping", customer_name="", order_display_id="#9",
        total_label="$ 50.000", pay_type="cod", payment_confirmed=False,
        tracking_url=URL,
    )
    assert msg.index("pagas $ 50.000 al repartidor") < msg.index(URL)
    assert msg.endswith(f"Puedes seguir tu envío aquí: {URL}")


def test_render_shipping_without_tracking_url_is_unchanged():
    kwargs = dict(
        stage="shipping", customer_name="Ana", order_display_id="#9",
        total_label="", pay_type="confirmed", payment_confirmed=False,
        items_label="Difusor",
    )
    assert render_stage_notification(**kwargs) == render_stage_notification(
        **kwargs, tracking_url=None
    )
    assert render_stage_notification(**kwargs, tracking_url="   ") == (
        render_stage_notification(**kwargs)
    )
    assert "seguir tu envío" not in render_stage_notification(**kwargs)


def test_render_other_stages_ignore_tracking_url():
    """El link es de la etapa "en camino": entregado/cancelado no lo repiten."""
    for stage in ("preparing", "ready", "delivered", "cancelled"):
        msg = render_stage_notification(
            stage=stage, customer_name="", order_display_id="#9",
            total_label="", pay_type="confirmed", payment_confirmed=False,
            tracking_url=URL,
        )
        assert msg and URL not in msg, stage


def test_template_variables_shipping_with_tracking_url_pass_registry_validation():
    """Fuera de ventana el link viaja en ``status_label`` del template v2 y
    el registry (max_length del catálogo) lo tiene que dejar pasar — si no,
    la notificación con guía se perdería justo en el caso más común (el
    envío ocurre días después de la compra)."""
    from src.platform.whatsapp.composition import get_template_registry
    from src.platform.whatsapp.templates.registry import validate_variables

    facts = {"order_display_id": "#22", "items_label": "Plegaria de Luz"}
    variables = build_status_template_variables("shipping", facts, tracking_url=URL)

    assert variables["order_reference"] == "#22 (Plegaria de Luz)"
    assert variables["status_label"] == f"en camino. Sigue tu envío aquí: {URL}"
    # Meta rechaza params con saltos de línea / tabs / 4+ espacios.
    assert "\n" not in variables["status_label"] and "\t" not in variables["status_label"]
    assert "    " not in variables["status_label"]

    spec = get_template_registry()["order_status_utility_v2"]
    assert validate_variables(spec, variables) == []


def test_template_variables_without_tracking_url_unchanged():
    assert build_status_template_variables("shipping", {}) == (
        build_status_template_variables("shipping", {}, tracking_url=None)
    )
    assert build_status_template_variables("shipping", {})["status_label"] == "En camino"
    # Solo "en camino" lleva el link en el template.
    assert build_status_template_variables("delivered", {}, tracking_url=URL)[
        "status_label"
    ] == "Entregado"
