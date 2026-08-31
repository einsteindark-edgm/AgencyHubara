"""Formas de pago Hubara — constantes compartidas (requisito 2026-08-31).

Las TRES formas de pago que se informan al cliente, con sus condiciones:

  * ``cash_on_delivery`` — contra entrega: el valor se calcula con la
    transportadora. Solo para pedidos > $45.000 COP (política de margen).
  * ``transfer`` — pago anticipado por Nequi o llave (Bre-B).
  * ``payment_link`` — link de pago: recargo adicional de 1,5% sobre la
    venta con Nequi o Bancolombia, 2,69% con otros bancos.

El número de Nequi/llave es dato PÚBLICO del negocio provisto por el
operador (no un dato bancario sensible): puede viajar en textos
deterministas y en las políticas del workspace. Sigue siendo overrideable
por env (``PAYMENT_NEQUI_NUMBER``) para cambiarlo sin tocar código; un
override a string vacío lo desactiva (los renderers degradan fail-closed).

Los datos de transferencia BANCARIA (banco/cuenta/titular) siguen viviendo
EXCLUSIVAMENTE en env ``PAYMENT_TRANSFER_*`` — ver
``activities/flush_ui_intents._render_payment_instructions_text``.
"""
from __future__ import annotations

import os

# Nequi / llave (Bre-B) del negocio. Fuente: requisito del operador
# 2026-08-31 ("Pago anticipado por Nequi o mi llave 3229041190").
PAYMENT_NEQUI_NUMBER_DEFAULT = "3229041190"

# Recargos del link de pago, como se informan al cliente (formato es-CO).
PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA = "1,5%"
PAYMENT_LINK_SURCHARGE_OTHER_BANKS = "2,69%"


def get_nequi_number() -> str:
    """Número Nequi/llave vigente. Env override > default; ``""`` desactiva."""
    override = os.getenv("PAYMENT_NEQUI_NUMBER")
    if override is not None:
        return override.strip()
    return PAYMENT_NEQUI_NUMBER_DEFAULT
