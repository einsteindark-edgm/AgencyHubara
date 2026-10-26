"""Condiciones de pago que leen Ventas y Remarketing (sin Temporal).

El mínimo de contra entrega vive aquí para que el guion de una campaña diga lo
mismo en los dos agentes (operador, 2026-10-19: el combo de 2 cajas paga
contra entrega). `sales/config/shipping.py` lo re-exporta: el formulario, el
resumen y las guardas lo siguen leyendo de allá.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

#: Un monto en pesos por debajo de esto es un error de escritura: `45.000` en el
#: tfvars es el número 45 para Terraform y llegaba como "45".
_MIN_COP = 1_000
#: La política de hoy de la tienda madre. Cada tienda trae la suya de Terraform
#: (`tenants.<t>.store` → SSM → `CASH_ON_DELIVERY_MIN_COP` en el `.env`).
DEFAULT_CASH_ON_DELIVERY_MIN_COP = 45_000


def cash_on_delivery_min_cop(env: Mapping[str, str]) -> int:
    """El mínimo de contra entrega de la tienda; mal escrito no arranca."""
    raw = (env.get("CASH_ON_DELIVERY_MIN_COP") or "").strip()
    if not raw:
        return DEFAULT_CASH_ON_DELIVERY_MIN_COP
    if not raw.isdigit() or int(raw) < _MIN_COP:
        raise ValueError(
            f"CASH_ON_DELIVERY_MIN_COP={raw!r}: un monto en pesos, solo dígitos y desde {_MIN_COP} (p. ej. 45000)"
        )
    return int(raw)


# Contra entrega: solo pedidos con productos DESDE este monto (política de
# margen vs costo del envío). Umbral INCLUSIVO — incidente run ebbc203d
# (2026-09-16): el guion decía "desde $45.000", el bot se lo afirmó al
# cliente, y el código usaba `> 45000` estricto → el formulario de envío
# ocultó "Contra entrega" para un pedido de $45.000 exactos y la clienta
# abandonó el Flow. Este es el ÚNICO lugar donde vive el umbral.
CASH_ON_DELIVERY_MIN_PRODUCTS_COP = cash_on_delivery_min_cop(os.environ)
