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
