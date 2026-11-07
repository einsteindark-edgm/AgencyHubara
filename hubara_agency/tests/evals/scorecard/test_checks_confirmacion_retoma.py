"""CON-01: qué cuenta como «el cliente retomó la compra» después de aplazar.

Solo levanta el aplazamiento un dato del producto que el cliente dio
(producto, color, aroma, diseño, cantidad o líneas). Quitar un producto o
dar solo la ciudad o el pago no es retomar la compra.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj

_CHOSEN = {"producto": "Cubo Love", "cantidad": "1"}


def _con01(t):
    return CODE_CHECKS["CON-01"](t, CheckContext())


def test_con01_removing_a_product_after_the_deferral_does_not_lift_it() -> None:
    t = traj(T(1, inbound="déjame pensarlo", signal="deferral", draft=_CHOSEN),
             T(2, inbound="quita el rojo", draft=_CHOSEN,
               tools=[tool("set_order_slot", producto="Cubo Rojo", quitar=True)]),
             T(3, inbound="ok", draft=_CHOSEN, tools=[tool("request_shipping_details")]))
    r = _con01(t)
    assert (r.verdict, r.turn) == ("falla", 3)


def test_con01_only_the_city_after_the_deferral_does_not_lift_it() -> None:
    t = traj(T(1, inbound="lo pienso y te aviso", signal="deferral", draft=_CHOSEN),
             T(2, inbound="soy de Pereira, cuánto vale el envío", draft=_CHOSEN,
               tools=[tool("set_order_slot", ciudad="Pereira")]),
             T(3, inbound="ok", draft=_CHOSEN, tools=[tool("request_shipping_details")]))
    r = _con01(t)
    assert (r.verdict, r.turn) == ("falla", 3)


def test_con01_lines_with_products_after_the_deferral_lift_it() -> None:
    t = traj(T(1, inbound="después miro", signal="deferral"),
             T(2, inbound="una azul y una verde", draft=_CHOSEN,
               tools=[tool("set_order_slot", lineas=[{"producto": "Cubo Love", "color": "azul"}]),
                      tool("request_shipping_details")]))
    assert _con01(t).verdict == "pasa"
