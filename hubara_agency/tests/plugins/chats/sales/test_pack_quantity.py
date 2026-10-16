"""Las cuentas de lo que se cobra por caja (puro; laboratorio 2026-10-14).

`candle_counts`: las cifras del cliente que pasan de 20 («>20 unidades» es un
pedido al por mayor) pero, contadas en velas, caben en 20 cajas o menos: solo
esas pueden ser un «por mayor» que no lo es («me vendes 36 velitas?» = 3
cajas de 12). `other_box_named`: el cliente nombró la caja de otro producto
(«Dos cajas de 12» y el asesor anota la ondulada, que trae 10).
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.use_cases.pack_quantity import candle_counts, other_box_named

BOXES = (12, 10)
SIZES = {"Velas de deseos cuadradas": 12, "Velas de deseos onduladas": 10}


def test_a_count_of_candles_that_fits_in_boxes() -> None:
    assert candle_counts("Hola! me vendes 36 velitas?", BOXES) == [36]
    assert candle_counts("quiero treinta velitas de las onduladas", BOXES) == [30]


def test_twenty_or_less_is_not_bulk_and_more_than_twenty_boxes_of_candles_is() -> None:
    assert candle_counts("quiero 20 velitas", BOXES) == []
    assert candle_counts("quiero 250 velitas", BOXES) == []  # más de 20 cajas de 12, cuente como cuente
    assert candle_counts("para el 7 de diciembre, a $25.000", BOXES) == []


def test_nothing_sold_by_the_box_counts_nothing() -> None:
    assert candle_counts("me vendes 36 velitas?", ()) == []


def test_the_customer_named_the_box_of_the_other_product() -> None:
    assert other_box_named("Dos cajas de 12 como seria el envio y el pago", 10, SIZES) == "Velas de deseos cuadradas"
    assert other_box_named("me das las de 10?", 12, SIZES) == "Velas de deseos onduladas"


def test_naming_this_box_too_or_no_box_is_nothing() -> None:
    assert other_box_named("una caja de las de 12 y una de las de 10", 10, SIZES) is None
    assert other_box_named("quiero 24 velitas de las cuadradas", 12, SIZES) is None
    assert other_box_named("Dos cajas de 12", 12, SIZES) is None
