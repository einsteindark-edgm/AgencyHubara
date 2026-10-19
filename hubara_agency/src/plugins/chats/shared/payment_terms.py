"""Condiciones de pago que leen Ventas y Remarketing (sin Temporal).

El mínimo de contra entrega vive aquí para que el guion de una campaña diga lo
mismo en los dos agentes (operador, 2026-10-19: el combo de 2 cajas paga
contra entrega). `sales/config/shipping.py` lo re-exporta: el formulario, el
resumen y las guardas lo siguen leyendo de allá.
"""
from __future__ import annotations

# Contra entrega: solo pedidos con productos DESDE este monto (política de
# margen vs costo del envío). Umbral INCLUSIVO — incidente run ebbc203d
# (2026-09-16): el guion decía "desde $45.000", el bot se lo afirmó al
# cliente, y el código usaba `> 45000` estricto → el formulario de envío
# ocultó "Contra entrega" para un pedido de $45.000 exactos y la clienta
# abandonó el Flow. Este es el ÚNICO lugar donde vive el umbral.
CASH_ON_DELIVERY_MIN_PRODUCTS_COP = 45_000
