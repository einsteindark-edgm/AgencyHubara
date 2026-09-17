"""Reglas para el VALOR DEL ENVÍO — textos deterministas (requisito del
operador 2026-09-07).

El valor del envío NUNCA se le da al cliente como definitivo: las tarifas
publicadas son MÍNIMAS y el valor final lo recalcula la transportadora
antes de despachar (según tamaño y peso del paquete). Dos consecuencias:

  1. "¿Cuánto vale el envío?" → el sistema responde con
     ``SHIPPING_RATES_MESSAGE`` tal cual (tool ``send_shipping_rates`` →
     intent ``shipping_rates``). El LLM no redacta las tarifas.
  2. El "Resumen de tu pedido" (``present_order_confirmation``) muestra
     el subtotal de productos, "Envío: Por confirmar*" y
     ``ORDER_SUMMARY_SHIPPING_NOTE`` — sin valor de envío ni total que lo
     incluya. El ``shipping_cop`` que pasa el LLM sigue viajando en el
     intent (analytics + consistencia con ``register_order``), pero no se
     renderiza.

Los montos se mantienen acá y en las políticas del workspace
(``skills/hubara_catalog``) como conocimiento del agente; el texto que ve
el cliente sale de estas constantes.
"""
from __future__ import annotations

SHIPPING_RATE_BOGOTA_COP = 7_900
SHIPPING_RATE_NATIONAL_COP = 16_940

# Contra entrega: solo pedidos con productos DESDE este monto (política de
# margen vs costo del envío). Umbral INCLUSIVO — incidente run ebbc203d
# (2026-09-16): el guion decía "desde $45.000", el bot se lo afirmó al
# cliente, y el código usaba `> 45000` estricto → el formulario de envío
# ocultó "Contra entrega" para un pedido de $45.000 exactos y la clienta
# abandonó el Flow. Este es el ÚNICO lugar donde vive el umbral; el
# formulario, el fallback de texto y los prompts lo citan desde acá.
CASH_ON_DELIVERY_MIN_PRODUCTS_COP = 45_000


def cash_on_delivery_available(products_subtotal_cop: int) -> bool:
    """¿El pedido califica para contra entrega? Se compara el subtotal de
    PRODUCTOS (sin envío) contra el mínimo, inclusive."""
    return int(products_subtotal_cop) >= CASH_ON_DELIVERY_MIN_PRODUCTS_COP

# Mensaje estándar, verbatim del operador (2026-09-07).
SHIPPING_RATES_MESSAGE = (
    "Nuestras tarifas mínimas de envío son 🚚:\n"
    "• Bogotá y municipios cercanos: $7.900\n"
    "• Nivel Nacional: $16.940\n"
    "El valor definitivo se confirma al despachar según el tamaño y "
    "peso de tu paquete📏📦📦"
)

# Línea del envío en el resumen del pedido + nota al pie (verbatim del
# operador, 2026-09-07).
ORDER_SUMMARY_SHIPPING_LINE = "Envío: Por confirmar*"
ORDER_SUMMARY_SHIPPING_NOTE = (
    "📌 El valor final del envío se recalculará directamente con la "
    "transportadora antes de despachar y te lo confirmaremos para cerrar "
    "tu pedido."
)


def _fold(text: str) -> str:
    import unicodedata

    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    return stripped.casefold().strip()


def shipping_rate_for_city(city: str | None) -> int:
    """Tarifa MÍNIMA de envío según la ciudad de entrega (D1.2b).

    La usa el contrato ``session-actions@v1 /order`` cuando el pedido llega sin
    ``shipping_cop`` (Meta Business Agent no manda montos): Bogotá y su
    entorno → ``SHIPPING_RATE_BOGOTA_COP``; cualquier otra ciudad (o ninguna)
    → ``SHIPPING_RATE_NATIONAL_COP``. Es la MISMA tarifa mínima que muestra
    el resumen del pedido (#241): nunca un valor definitivo.
    """
    folded = _fold(city or "")
    if "bogota" in folded:
        return SHIPPING_RATE_BOGOTA_COP
    return SHIPPING_RATE_NATIONAL_COP
