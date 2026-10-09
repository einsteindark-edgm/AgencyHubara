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

La POLÍTICA es de la tienda, no del motor (forge, 2026-10-09): tarifas,
zona local y mínimo de contra entrega nacen en Terraform
(``tenants.<t>.store`` → SSM → ``.env``) y se leen acá una vez, al importar
(``ShippingPolicy.from_env``). Sin variable manda la política de hoy de la
tienda madre. Las políticas del workspace (``skills/<tienda>_catalog``)
repiten los montos como conocimiento del agente; el texto que ve el cliente
sale de esta política.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from src.plugins.chats.agent.sales.decisions.retiro import en_retiro


def _fold(text: str) -> str:
    import unicodedata

    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    return stripped.casefold().strip()


def _amount(env: Mapping[str, str], var: str, default: int) -> int:
    raw = (env.get(var) or "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"{var}={raw!r}: un monto en pesos, solo dígitos y mayor que 0 (p. ej. 7900)")
    return int(raw)


@dataclass(frozen=True)
class ShippingPolicy:
    """La política de envío de la tienda.

    ``local_zone`` es como se le nombra la zona local al cliente; la ciudad
    de entrega cae en ella si contiene ``local_city`` (sin tildes ni
    mayúsculas). Fuera de ella, la tarifa nacional.
    """

    local_zone: str = "Bogotá y municipios cercanos"
    local_city: str = "Bogotá"
    local_cop: int = 7_900
    national_cop: int = 16_940
    # Contra entrega: solo pedidos con productos DESDE este monto (política de
    # margen vs costo del envío). Umbral INCLUSIVO — incidente run ebbc203d
    # (2026-09-16): el guion decía "desde $45.000", el bot se lo afirmó al
    # cliente, y el código usaba `> 45000` estricto → el formulario de envío
    # ocultó "Contra entrega" para un pedido de $45.000 exactos y la clienta
    # abandonó el Flow. Este es el ÚNICO lugar donde vive el umbral; el
    # formulario, el fallback de texto y los prompts lo citan desde acá.
    cod_min_products_cop: int = 45_000

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ShippingPolicy:
        base = cls()
        return cls(
            local_zone=(env.get("SHIPPING_LOCAL_ZONE") or "").strip() or base.local_zone,
            local_city=(env.get("SHIPPING_LOCAL_CITY") or "").strip() or base.local_city,
            local_cop=_amount(env, "SHIPPING_RATE_LOCAL_COP", base.local_cop),
            national_cop=_amount(env, "SHIPPING_RATE_NATIONAL_COP", base.national_cop),
            cod_min_products_cop=_amount(env, "CASH_ON_DELIVERY_MIN_COP", base.cod_min_products_cop),
        )


#: La política de la tienda de este proceso (config de Terraform, o la de hoy).
POLICY = ShippingPolicy.from_env(os.environ)

SHIPPING_RATE_BOGOTA_COP = POLICY.local_cop  # la tarifa de la zona local
SHIPPING_RATE_NATIONAL_COP = POLICY.national_cop
CASH_ON_DELIVERY_MIN_PRODUCTS_COP = POLICY.cod_min_products_cop
#: Cómo se le nombra la zona local al cliente («Bogotá y municipios cercanos»).
SHIPPING_LOCAL_ZONE = POLICY.local_zone


def cash_on_delivery_available(products_subtotal_cop: int, policy: ShippingPolicy = POLICY) -> bool:
    """¿El pedido califica para contra entrega? Se compara el subtotal de
    PRODUCTOS (sin envío) contra el mínimo, inclusive."""
    return int(products_subtotal_cop) >= policy.cod_min_products_cop


def _cop(amount: int) -> str:
    return "$" + f"{amount:,}".replace(",", ".")


def rates_message(policy: ShippingPolicy) -> str:
    """Mensaje estándar de tarifas, verbatim del operador (2026-09-07)."""
    return (
        "Nuestras tarifas mínimas de envío son 🚚:\n"
        f"• {policy.local_zone}: {_cop(policy.local_cop)}\n"
        f"• Nivel Nacional: {_cop(policy.national_cop)}\n"
        "El valor definitivo se confirma al despachar según el tamaño y "
        "peso de tu paquete📏📦📦"
    )


SHIPPING_RATES_MESSAGE = rates_message(POLICY)

# Línea del envío en el resumen del pedido + nota al pie (verbatim del
# operador, 2026-09-07).
ORDER_SUMMARY_SHIPPING_LINE = "Envío: Por confirmar*"
ORDER_SUMMARY_SHIPPING_NOTE = (
    "📌 El valor final del envío se recalculará directamente con la "
    "transportadora antes de despachar y te lo confirmaremos para cerrar "
    "tu pedido."
)

#: El `flow_id` que encola `request_shipping_details`: el Flow real lo
#: resuelve `shipping_flow_id` (env) o, sin él, se piden los datos por texto.
SHIPPING_FLOW_PLACEHOLDER = "FLOW_ID_SHIPPING_PLACEHOLDER"


def shipping_flow_id(intent_flow_id: str | None = None) -> str | None:
    """El WhatsApp Flow de datos de envío que sale, o None si no hay: entonces
    el flush pide los datos con la lista de campos por texto. Primero
    `META_FLOW_ID_SHIPPING` (productivo: cambiar el Flow en Meta es un
    redeploy, sin tocar código), después el `flow_id` del intent; el
    placeholder no cuenta. Una sola regla para el flush (que envía) y la tool
    (que dice en `customer_text` qué va a leer el cliente)."""
    env_flow_id = (os.environ.get("META_FLOW_ID_SHIPPING") or "").strip()
    flow_id = env_flow_id if env_flow_id and env_flow_id != SHIPPING_FLOW_PLACEHOLDER else intent_flow_id
    return flow_id if flow_id and flow_id != SHIPPING_FLOW_PLACEHOLDER else None


def shipping_rate_for_city(city: str | None, policy: ShippingPolicy = POLICY) -> int:
    """Tarifa MÍNIMA de envío según la ciudad de entrega (D1.2b).

    La usa el contrato ``session-actions@v1 /order`` cuando el pedido llega sin
    ``shipping_cop`` (Meta Business Agent no manda montos): Bogotá y su
    entorno → ``SHIPPING_RATE_BOGOTA_COP``; cualquier otra ciudad (o ninguna)
    → ``SHIPPING_RATE_NATIONAL_COP``. Es la MISMA tarifa mínima que muestra
    el resumen del pedido (#241): nunca un valor definitivo.
    """
    if shipping_zone(city, policy) == SHIPPING_ZONE_BOGOTA:
        return policy.local_cop
    return policy.national_cop


# Decisión del operador (2026-09-23): el envío lo cobra la transportadora a su
# tarifa, SIN descuentos ni "envío gratis". El monto que manda el LLM tiene que
# ser una tarifa mínima publicada (L-19: un monto del LLM se compara contra la
# tabla).
def rate_rule(policy: ShippingPolicy) -> str:
    return (
        f"El envío es SIEMPRE una tarifa mínima publicada: {_cop(policy.local_cop)} "
        f"para {policy.local_zone}, {_cop(policy.national_cop)} a nivel "
        "nacional. No existe envío gratis ni descuento en el envío: lo cobra la "
        "transportadora. Pasa esa tarifa también con contra entrega (el resumen "
        "igual dice \"Por confirmar\")."
    )


SHIPPING_RATE_RULE = rate_rule(POLICY)


#: Descripción del parámetro `shipping_cop` de `present_order_confirmation` y
#: `register_order`: la guía del LLM sale de las MISMAS constantes que valida
#: `is_published_shipping_rate` (un umbral en dos lugares diverge — L-19).
def shipping_cop_param_description(policy: ShippingPolicy) -> str:
    return (
        f"Tarifa MÍNIMA publicada de envío en COP: {policy.local_cop} "
        f"({_cop(policy.local_cop)}) para {policy.local_zone}, "
        f"{policy.national_cop} ({_cop(policy.national_cop)}) a nivel "
        "nacional. No existe envío gratis: nunca 0. También con contra entrega "
        "(el resumen igual dice \"Por confirmar\" y el total a registrar es "
        "subtotal + envío − cupón)."
    )


SHIPPING_COP_PARAM_DESCRIPTION = shipping_cop_param_description(POLICY)


#: Zonas de envío (la tarifa mínima publicada de cada una). La regla de hoy
#: solo sabe "bogota"; "nacional" llega únicamente si la decide el motor de
#: decisiones (capacidad `zona_de_envio`), que conoce los municipios cercanos.
SHIPPING_ZONE_BOGOTA = "bogota"
SHIPPING_ZONE_NATIONAL = "nacional"


@en_retiro("funcion:is_published_shipping_rate")
def is_published_shipping_rate(shipping_cop: int, city: str | None = None) -> bool:
    """¿``shipping_cop`` es una tarifa mínima publicada para esta ciudad?

    Bogotá paga la de Bogotá. Fuera de Bogotá valen las dos: no hay lista de
    "municipios cercanos" y esa decisión la toma el bot con la política
    publicada. Nunca $0 ni un monto inventado. Es LEER la zona
    (`shipping_zone`) y validar la tarifa de esa zona
    (`is_published_rate_for_zone`); las tools le piden la zona al motor.
    """
    return is_published_rate_for_zone(shipping_cop, shipping_zone(city))


def shipping_zone(city: str | None, policy: ShippingPolicy = POLICY) -> str | None:
    """LECTURA de la regla de hoy: la zona local (id ``bogota``) si la ciudad
    dice la ciudad de la tienda; None si no se sabe (no hay lista de
    municipios cercanos)."""
    if city is not None and _fold(policy.local_city) in _fold(city):
        return SHIPPING_ZONE_BOGOTA
    return None


def is_published_rate_for_zone(shipping_cop: int, zone: str | None) -> bool:
    """¿``shipping_cop`` es la tarifa mínima publicada de la zona? Sin zona
    (None) valen las dos; nunca $0 ni un monto inventado."""
    if shipping_cop not in (SHIPPING_RATE_BOGOTA_COP, SHIPPING_RATE_NATIONAL_COP):
        return False
    if zone == SHIPPING_ZONE_BOGOTA:
        return shipping_cop == SHIPPING_RATE_BOGOTA_COP
    if zone == SHIPPING_ZONE_NATIONAL:
        return shipping_cop == SHIPPING_RATE_NATIONAL_COP
    return True
