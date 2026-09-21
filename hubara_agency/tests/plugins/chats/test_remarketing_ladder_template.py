"""Elección de la plantilla del toque con la CSW cerrada (pura).

Hallazgo M-1 de la revisión: la central puede recomendar `utility` PAGA (fase B
con gancho transaccional) y la escalera solo tiene plantillas de MARKETING
(15× más caras). Regla del operador: no perder dinero — la plantilla de la
escalera sale SOLO cuando es gratis (ventana de 72h del anuncio), y solo si el
copy aplica al caso.
"""
from __future__ import annotations

from src.plugins.chats.agent.remarketing.activities.ladder_template import (
    TEMPLATE_CART,
    TEMPLATE_FOLLOWUP,
    choose_ladder_template,
)


def _meta(**extra) -> dict:
    return {"tag": "INTERESADO", **extra}


def test_lead_sin_carrito_recibe_el_seguimiento_generico():
    assert choose_ladder_template(_meta(), is_free=True) == (TEMPLATE_FOLLOWUP, {})


def test_pedido_a_medias_recibe_recovery_con_el_producto():
    meta = _meta(episodes=[{"episode_id": "ep_001",
                            "order_draft": {"slots": {"producto": "Vela Calabaza"}}}])
    assert choose_ladder_template(meta, is_free=True) == (
        TEMPLATE_CART, {"product_label": "Vela Calabaza"},
    )


def test_fuera_de_la_ventana_gratis_no_se_paga_marketing():
    assert choose_ladder_template(_meta(), is_free=False) is None


def test_pedido_registrado_o_pago_pendiente_no_recibe_lo_que_estabas_mirando():
    # Ese cliente ya compró/está pagando: el copy genérico sería absurdo y su
    # seguimiento es de otros (ETA / verificación humana del pago).
    con_orden = _meta(episodes=[{"episode_id": "ep_001", "order_id": "order_01TEST"}])
    assert choose_ladder_template(con_orden, is_free=True) is None
    pago = _meta(tag="CONFIRMADO_PAGO_PENDIENTE")
    assert choose_ladder_template(pago, is_free=True) is None
