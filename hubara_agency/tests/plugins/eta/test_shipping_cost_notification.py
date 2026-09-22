"""Copy de las notificaciones de etapa (pedido del operador 2026-09-22).

  * ``preparing`` y ``ready`` contra entrega: SIN el recordatorio del monto
    ("pagarás $ X…" / "Ten listos $ X…") — el precio se menciona recién en
    "en camino". ``ready`` sí avisa (foto si la hay, aviso de estado si no).
  * ``shipping``: el operador puede escribir el valor del envío al marcar
    "en camino"; el mensaje lo detalla separado del pedido: valor del
    pedido (total vivo del pedido), valor del envío y total (dentro de
    ventana en líneas de una misma burbuja; fuera de ventana en el slot
    ``status_label`` del template v2). Sin valor, el mensaje es el de hoy.
"""
from __future__ import annotations

from src.plugins.eta.agent.eta.prompts import (
    build_status_template_variables,
    format_cop,
    render_stage_notification,
)

URL = "https://www.servientrega.com/wps/portal/rastreo-envio?guia=1234567890"


def test_format_cop_matches_the_order_total_style():
    assert format_cop(12000) == "$ 12.000"
    assert format_cop(1_250_000) == "$ 1.250.000"
    assert format_cop(0) == ""
    assert format_cop(None) == ""


def test_render_preparing_cod_no_longer_mentions_the_amount():
    msg = render_stage_notification(
        stage="preparing", customer_name="Daniela", order_display_id="#1243",
        total_label="$ 215.000", pay_type="cod", payment_confirmed=False,
        items_label="Vela Cruz",
    )
    assert msg == (
        "¡Hola Daniela! Soy tu asistente de seguimiento de Hubara. Tu pedido "
        "#1243 (Vela Cruz) acaba de entrar en preparación. Te aviso en cada paso 🙌"
    )
    assert "215.000" not in msg and "contra entrega" not in msg


def test_render_ready_cod_no_longer_mentions_the_amount():
    msg = render_stage_notification(
        stage="ready", customer_name="Daniela", order_display_id="#1243",
        total_label="$ 215.000", pay_type="cod", payment_confirmed=False,
    )
    assert msg == (
        "¡Buenas noticias Daniela! Tu pedido #1243 ya está empacado y listo "
        "para salir. Te escribo apenas vaya en camino."
    )
    assert "215.000" not in msg


def test_render_shipping_pending_details_order_shipping_and_total():
    msg = render_stage_notification(
        stage="shipping", customer_name="Ana", order_display_id="#9",
        total_label="$ 50.000", pay_type="confirmed", payment_confirmed=False,
        items_label="Difusor", shipping_cost=12000, order_total_cop=50000,
    )
    assert msg == (
        "Tu pedido #9 (Difusor) ya va en camino 🚚.\n"
        "Valor del pedido: $ 50.000\n"
        "Valor del envío: $ 12.000\n"
        "Total: $ 62.000\n"
        "Te aviso cuando esté por llegar."
    )
    # Una sola burbuja: "\n\n" parte el mensaje en chunks separados.
    assert "\n\n" not in msg


def test_render_shipping_cod_charges_the_grand_total():
    msg = render_stage_notification(
        stage="shipping", customer_name="", order_display_id="#9",
        total_label="$ 50.000", pay_type="cod", payment_confirmed=False,
        shipping_cost=12000, order_total_cop=50000, tracking_url=URL,
    )
    assert msg == (
        "Tu pedido #9 ya va en camino 🚚.\n"
        "Valor del pedido: $ 50.000\n"
        "Valor del envío: $ 12.000\n"
        "Total: $ 62.000\n"
        "Recuerda que al recibirlo pagas $ 62.000 al repartidor (efectivo o "
        f"transferencia).\n\nPuedes seguir tu envío aquí: {URL}"
    )


def test_render_shipping_paid_says_the_order_value_is_paid():
    msg = render_stage_notification(
        stage="shipping", customer_name="Ana", order_display_id="#9",
        total_label="$ 50.000", pay_type="confirmed", payment_confirmed=True,
        items_label="Difusor", shipping_cost=12000, order_total_cop=50000,
    )
    assert msg == (
        "Tu pedido #9 (Difusor) ya va en camino 🚚.\n"
        "Valor del pedido: $ 50.000\n"
        "Valor del envío: $ 12.000\n"
        "Total: $ 62.000\n"
        "El valor del pedido ya está pagado. Te aviso cuando esté por llegar."
    )
    assert "no tienes que pagar nada" not in msg


def test_render_shipping_cost_without_known_order_total_shows_only_shipping():
    """Medusa caído: sin total del pedido NO inventamos pedido ni total."""
    msg = render_stage_notification(
        stage="shipping", customer_name="", order_display_id="order_01HX",
        total_label="", pay_type="confirmed", payment_confirmed=False,
        shipping_cost=12000, order_total_cop=None,
    )
    assert msg == (
        "Tu pedido order_01HX ya va en camino 🚚.\n"
        "Valor del envío: $ 12.000\n"
        "Te aviso cuando esté por llegar."
    )


def test_render_shipping_without_shipping_cost_is_unchanged():
    kwargs = dict(
        stage="shipping", customer_name="Ana", order_display_id="#9",
        total_label="$ 50.000", pay_type="confirmed", payment_confirmed=True,
        items_label="Difusor",
    )
    base = render_stage_notification(**kwargs)
    assert base == render_stage_notification(**kwargs, shipping_cost=None)
    assert base == render_stage_notification(**kwargs, shipping_cost=0)
    assert base == render_stage_notification(**kwargs, order_total_cop=50000)
    assert "envío" not in base.replace("Te aviso", "")


def test_render_other_stages_ignore_shipping_cost():
    for stage in ("preparing", "ready", "delivered", "cancelled"):
        msg = render_stage_notification(
            stage=stage, customer_name="", order_display_id="#9",
            total_label="", pay_type="confirmed", payment_confirmed=False,
            shipping_cost=12000,
        )
        assert msg and "12.000" not in msg, stage


def test_template_variables_shipping_details_pass_registry_validation():
    from src.platform.whatsapp.composition import get_template_registry
    from src.platform.whatsapp.templates.registry import validate_variables

    facts = {"order_display_id": "#22", "items_label": "Plegaria de Luz", "total_cop": 50000}
    variables = build_status_template_variables(
        "shipping", facts, tracking_url=URL, shipping_cost=12000
    )
    assert variables["status_label"] == (
        "en camino. Valor del pedido: $ 50.000, valor del envío: $ 12.000, "
        f"total: $ 62.000. Sigue tu envío aquí: {URL}"
    )
    assert "\n" not in variables["status_label"] and "    " not in variables["status_label"]
    spec = get_template_registry()["order_status_utility_v2"]
    assert validate_variables(spec, variables) == []


def test_template_variables_shipping_cost_without_order_total_or_link():
    variables = build_status_template_variables("shipping", {}, shipping_cost=12000)
    assert variables["status_label"] == "en camino. Valor del envío: $ 12.000"
    assert build_status_template_variables("shipping", {"total_cop": 50000})[
        "status_label"
    ] == "En camino"
    assert build_status_template_variables("delivered", {}, shipping_cost=12000)[
        "status_label"
    ] == "Entregado"
