"""Lógica PURA de `diagnose` — G-AGNOSTIC: solo stdlib (decimal). Tabla de umbrales
determinista: el LLM narra la interpretación, pero el verdict (banderas + recomendación)
sale de acá, así dos corridas sobre los mismos números siempre coinciden.

Portada de ads-analytics-engine/src/ads_engine/diagnosis.py.
"""
from __future__ import annotations

from decimal import Decimal

HIGH_FRICTION_DROP_OFF = Decimal("0.40")  # drop-off por encima de esto = fricción alta de embudo
MIN_HEALTHY_MER = Decimal("2.0")  # MER por debajo de esto = rentabilidad pobre


def run(*, payload: dict) -> dict:
    """payload = {mer, drop_off_rate} (Decimal como string, o None) → verdict.

    Si una de las dos métricas load-bearing es None, no se puede juzgar → insufficient_data.
    """
    mer = payload.get("mer")
    drop = payload.get("drop_off_rate")
    if mer is None or drop is None:
        return {
            "high_friction": False,
            "poor_profitability": False,
            "recommendation": "insufficient_data",
        }
    high_friction = Decimal(str(drop)) > HIGH_FRICTION_DROP_OFF
    mer_d = Decimal(str(mer))
    # MF-6: mer == 0 (revenue 0 sobre spend real). En CTWA la venta ocurre en WhatsApp y
    # puede NO estar cargada/atribuida todavía (caso purchase-conversion — ver dev-error-log).
    # NO es lo mismo que "creativo malo": señal honesta y DISTINTA para que el operador
    # verifique la completitud de las ventas antes de rotar nada.
    if mer_d == 0:
        return {"high_friction": high_friction, "poor_profitability": True,
                "recommendation": "no_revenue_recorded"}
    poor = mer_d < MIN_HEALTHY_MER
    if poor and high_friction:
        rec = "rotate_creative"  # leaky AND no rentable → el embudo es el cuello de botella
    elif poor:
        rec = "review_targeting_or_pricing"  # no rentable pero el embudo convierte
    else:
        rec = "scale_budget"  # rentable (MER >= 2): escalar hace dinero; high_friction queda flagueado
    return {"high_friction": high_friction, "poor_profitability": poor, "recommendation": rec}
