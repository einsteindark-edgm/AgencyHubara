"""Los hechos de la puerta de afirmaciones (2026-11-03): un builtin por hecho.

Colossus (el bot V2) afirma cosas que nunca consultó: «tu pedido quedó
registrado», «ya recibimos tu pago», un precio que no es del catálogo, una
cuenta para transferir, «te llega mañana» (huecos del 10-07 al 11-03:
`python -m src.plugins.chats.agent.sales_eval.huecos`). La capacidad de la
puerta tiene la forma de `afirmacion`: Jev dice QUÉ afirma el texto y la tabla
del paquete decide con los HECHOS, que calcula el código con lo que el turno y
el sistema ya saben. Cada hecho es una vista (`view: [ … ]` en el YAML de la
capacidad), con su prueba (`test_decisions_claim_facts.py`):

  order_registered_fact         CON-03  hay un pedido registrado de esta compra
  paid_in_turn_fact             POS-01  `check_order_status` dijo pagado en el turno
  unexplained_amounts_fact      DES-10  montos que no explican el catálogo ni lo consultado
  bank_details_fact             ENV-05  números de cuenta o llaves (la llave pública no)
  shipping_amounts_fact         ENV-05  montos dichos como el valor del envío
  approved_delivery_time_fact   EST-05  el texto usa la frase aprobada de tiempos

Las frases que busca un hecho son de la tienda: van en el `with:` de su vista
(`fact_rules.phrases`: palabras, `*`, `a/b`, `…`; nunca un regex del paquete)
o en su `domain.yaml` (la frase aprobada de tiempos). Un hecho mal leído se
arregla con otra frase en una versión nueva del paquete, no aquí.

La entrada (`ClaimCheck`, tipo `Afirmacion` del catálogo) la arma el egreso
del V2 antes de enviar (`egress.decide_egress`); lo que viene del sistema
(el pedido, los precios del catálogo, la llave pública, la frase de tiempos)
lo reúne su activity (`ClaimFacts`). Sin un dato, el hecho no se afirma: un
hecho que falta nunca retiene por sí solo (la tabla decide con lo que hay).

Puro: sin Temporal ni I/O. Se carga por nombre (`bundled.LAZY_BUILTINS`).
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.plugins.chats.agent.sales.price_quotes import extract_cop_amounts, find_unexplained_amounts, split_sentences
from src.plugins.chats.agent.sales.pricing import format_cop
from src.plugins.chats.agent.sales.use_cases.fact_rules import phrases, says_any
from src.plugins.chats.shared.purchase_signals import has_registered_order
from src.sdk.catalogkit import normalize_label

#: La tool que consulta el pago (POS-01): lo único que lo verifica en el turno.
PAYMENT_CHECK_TOOL = "check_order_status"
#: Un monto que se lee en el resultado de una tool: los pesos van desde mil.
_MIN_AMOUNT = 1_000
#: Un número de cuenta o una llave: 6+ dígitos seguidos (con espacios o
#: guiones), sin separador de miles ni signo de pesos (un precio no es una
#: cuenta). El mismo corte que el scorecard de ENV-05.
_ACCOUNT_NUMBER = re.compile(r"(?<![\d$.,])(?<!\$ )\d(?:[ -]?\d){5,}(?![\d]|[.,]\d)")


@dataclass(frozen=True)
class ClaimFacts:
    """Lo que el sistema sabe y el texto no dice: lo reúne la activity del
    egreso (`egress_activities.claim_facts`). Sin un dato, vacío."""

    #: El metadata de la conversación (el pedido registrado y el episodio).
    metadata: Mapping[str, Any] = field(default_factory=dict)
    #: Los precios del catálogo (todas las variantes de todos los productos).
    known_prices: tuple[int, ...] = ()
    #: Los montos de la política (tarifas de envío, mínimo del contra entrega).
    policy_amounts: tuple[int, ...] = ()
    #: Los recargos del link de pago (porcentajes).
    surcharges: tuple[float, ...] = ()
    #: Las llaves de pago públicas de la tienda (solo dígitos): se pueden escribir.
    public_keys: tuple[str, ...] = ()
    #: La frase aprobada para hablar de tiempos de entrega (`domain.yaml`).
    delivery_phrases: str = ""


@dataclass(frozen=True)
class ClaimCheck:
    """El texto que el turno va a enviar y lo que el turno consultó (tipo
    `Afirmacion` del catálogo: `text`, `tools_used`); los hechos salen de lo
    demás."""

    text: str
    tools_used: tuple[str, ...] = ()
    #: Cada tool del turno con su resultado (`{name, args, result}`, acotado).
    tool_events: tuple[Mapping[str, Any], ...] = ()
    #: `register_order` registró un pedido en este turno.
    turn_registered: bool = False
    facts: ClaimFacts = field(default_factory=ClaimFacts)


def _facts(inp: Any) -> ClaimFacts:
    facts = getattr(inp, "facts", None)
    return facts if isinstance(facts, ClaimFacts) else ClaimFacts()


def _events(inp: Any) -> list[Mapping[str, Any]]:
    return [e for e in getattr(inp, "tool_events", None) or () if isinstance(e, Mapping)]


def _result(event: Mapping[str, Any]) -> Any:
    """El resultado de la tool leído como JSON (None si no se puede)."""
    raw = event.get("result")
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _amounts(value: Any) -> Iterable[int]:
    """Los montos de un resultado: los enteros desde mil y los pesos escritos."""
    if isinstance(value, bool):
        return
    if isinstance(value, int) and value >= _MIN_AMOUNT:
        yield value
    elif isinstance(value, str):
        yield from extract_cop_amounts(value)
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _amounts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _amounts(item)


def _consulted_amounts(event: Mapping[str, Any]) -> set[int]:
    """Los montos que una tool le DEVOLVIÓ al turno: los de su resultado, sin
    los que repite de sus argumentos (lo que escribió el modelo, como el texto
    de `send_reply`, nunca se explica a sí mismo)."""
    result = _result(event)
    found = set(_amounts(result if result is not None else event.get("result")))
    return found - set(_amounts(event.get("args")))


# ── CON-03 ──────────────────────────────────────────────────────────────────


def order_registered_fact(inp: Any) -> dict[str, Any]:
    """`order_registered`: este turno registró un pedido, o hay uno registrado
    de ESTA compra (uno de una compra anterior no cuenta: `has_registered_order`)."""
    registered = bool(getattr(inp, "turn_registered", False)) or has_registered_order(dict(_facts(inp).metadata))
    return {"order_registered": registered}


# ── POS-01 ──────────────────────────────────────────────────────────────────


def paid_in_turn_fact(inp: Any) -> dict[str, Any]:
    """`paid_checked`: `check_order_status` dijo en ESTE turno que el pago de
    un pedido está confirmado (`payment_confirmed`, o `pay_status: paid`)."""
    for event in _events(inp):
        if event.get("name") != PAYMENT_CHECK_TOOL:
            continue
        result = _result(event)
        orders = result.get("orders") if isinstance(result, Mapping) else None
        for order in orders or ():
            if isinstance(order, Mapping) and (order.get("payment_confirmed") is True or order.get("pay_status") == "paid"):
                return {"paid_checked": True}
    return {"paid_checked": False}


# ── DES-10 ──────────────────────────────────────────────────────────────────


def unexplained_amounts_fact(inp: Any, *, politica: str) -> dict[str, Any]:
    """`unexplained_amounts`: los montos del texto que no explica un precio
    del catálogo (por cantidad o sumado, con el recargo del link) ni un monto
    de lo que el turno consultó, ni la política en una oración que dice una
    frase de `politica` (`price_quotes.find_unexplained_amounts`, el mismo
    criterio de `monto` y `precio_ajeno`). Sin ningún precio conocido, no se
    juzga."""
    facts = _facts(inp)
    known = {*facts.known_prices, *(a for e in _events(inp) for a in _consulted_amounts(e))}
    if not known:
        return {"unexplained_amounts": ()}
    found = find_unexplained_amounts(
        getattr(inp, "text", "") or "",
        catalog_prices=known,
        policy_amounts=facts.policy_amounts,
        surcharge_rates=facts.surcharges,
        policy_context=lambda sentence: says_any(sentence, politica),
    )
    return {"unexplained_amounts": tuple(dict.fromkeys(format_cop(u.amount) for u in found))}


# ── ENV-05 ──────────────────────────────────────────────────────────────────


def _talks(sentence: str, spec: str) -> bool:
    return bool(re.search(phrases(spec), normalize_label(sentence)))


def bank_details_fact(inp: Any, *, habla_de_banco: str) -> dict[str, Any]:
    """`bank_details`: los números de cuenta o llaves (6+ dígitos, no un
    precio) en una oración que habla de un banco o un medio de pago (una
    frase de `habla_de_banco`). La llave pública de la tienda se puede
    escribir (`public_keys`); nombrar el medio («paga por Nequi») no es un
    dato bancario."""
    public = tuple(k for k in _facts(inp).public_keys if k)
    found: list[str] = []
    for sentence in split_sentences(getattr(inp, "text", "") or ""):
        if not _talks(sentence, habla_de_banco):
            continue
        for match in _ACCOUNT_NUMBER.finditer(sentence):
            digits = re.sub(r"\D", "", match.group(0))
            if any(digits.endswith(key) for key in public):
                continue
            found.append(match.group(0))
    return {"bank_details": tuple(dict.fromkeys(found))}


def shipping_amounts_fact(inp: Any, *, habla_de_envio: str) -> dict[str, Any]:
    """`shipping_amounts`: los montos de una oración que habla del envío (una
    frase de `habla_de_envio`). La tabla decide si es el valor definitivo
    (sin la tarjeta de tarifas ni la verificación del pedido en el turno)."""
    found = [
        format_cop(amount)
        for sentence in split_sentences(getattr(inp, "text", "") or "")
        if _talks(sentence, habla_de_envio)
        for amount in extract_cop_amounts(sentence)
    ]
    return {"shipping_amounts": tuple(dict.fromkeys(found))}


# ── EST-05 ──────────────────────────────────────────────────────────────────


def approved_delivery_time_fact(inp: Any) -> dict[str, Any]:
    """`approved_delivery_time`: el texto usa la frase aprobada de la tienda
    para hablar de tiempos de entrega (`domain.yaml: delivery_time_phrases`;
    «2 a 3 días hábiles» no es una promesa, «te llega mañana» sí). Sin frase
    aprobada, False."""
    spec = _facts(inp).delivery_phrases
    return {"approved_delivery_time": bool(spec.strip()) and says_any(getattr(inp, "text", "") or "", spec)}
