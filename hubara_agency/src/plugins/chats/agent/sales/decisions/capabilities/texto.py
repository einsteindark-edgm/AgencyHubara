"""Capacidades sobre el TEXTO del LLM (diseño v2 §07, familia B; fase F5).

Se preguntan en las tools y activities que ya graban su resultado (el
workflow solo lee el resultado grabado: una regla que decide commands dentro
del workflow es lógica de replay, L-21). Mismo marco que
`capabilities/__init__.py`: la regla de hoy de respaldo, una pregunta cerrada
a Jev, la política, el piso y la comparación.

* persona — «¿Deja ver que quien atiende es un bot o una IA?», oración por
  oración (las mismas oraciones que corta `customer_sentences`). La
  autoidentificación («soy un asistente», «asistente virtual», «modelo de
  lenguaje») y el anuncio del relevo a «una persona» / «un humano» son PISO:
  la regla del operador es que el cliente nunca note el relevo. Jev puede
  dejar una frase de marca que la regla bota («Cada vela lleva un toque
  humano»).
* enumeración — «¿Qué le enumera el texto al cliente?» {aromas, colores,
  combinaciones de cupón, productos, nada}, en la activity que cambia una
  lista de variantes escrita por el selector. Las etiquetas del selector las
  sigue sacando el código del catálogo; las combinaciones «Color · Aroma» de
  un cupón nunca van al selector (piso).
* monto — «¿La oración cotiza el precio de un producto?», en el checkout,
  solo en las oraciones donde las palabras de política aceptan un monto que
  el catálogo no explica. La cuenta sigue en código; Jev solo puede hacer el
  chequeo más estricto.
* selector — «¿Estos botones le piden al cliente elegir un producto o una
  variante?», en la tool de quick replies. El namespace del id
  (`product.`, `color.`…) es PISO.
* afirmación — pregunta de respaldo en SOMBRA (F6): «¿afirma algo que solo
  se sabe consultando, sin haber consultado?». La hace la activity de la
  traza después de enviar; nunca actúa.
* relevo — «¿le promete al cliente que una persona del equipo lo va a
  atender?», en la red de seguridad antes de enviar el texto final (escala si
  nadie escaló). Las frases del relevo son PISO; Jev suma paráfrasis.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.plugins.chats.agent.sales.decisions.plan import answer_of, plain
from src.sdk.connectorkit import TypedQuestion

_YES_NO = {"true": "sí", "false": "no"}
#: Más oraciones que esto no se le preguntan a Jev: decide la regla.
MAX_SENTENCES = 12


@dataclass(frozen=True)
class Frases:
    """Las oraciones de un texto para el cliente (`customer_sentences`)."""

    parts: tuple[str, ...]


def _p(result: Any, qid: str) -> float | None:
    p = getattr(answer_of(result, qid), "p", None)
    return float(p) if isinstance(p, (int, float)) else None


def _numbered(parts: Sequence[str]) -> str:
    return "\n".join(f"[{i}] {part}" for i, part in enumerate(parts, 1))


# Piso de persona: lo que ninguna lectura puede dejar pasar.
_PERSONA_FLOOR: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsoy\s+(?:un[a]?|tu)\s+(?:asistente|ia|ai|bot|chatbot|robot|programa|sistema|m[aá]quina)\b", re.IGNORECASE),
    re.compile(r"\b(?:asistente|asesor[a]?)\s+(?:virtual|digital)\b", re.IGNORECASE),
    re.compile(r"\bmodelo\s+de\s+lenguaje\b", re.IGNORECASE),
    re.compile(r"\b(?:agente|asesor[a]?|persona)\s+en\s+vivo\b", re.IGNORECASE),
    re.compile(r"\b(?:te\s+(?:paso|comunico|conecto|transfiero)\s+con|ya\s+te\s+atiende)\s+una\s+persona\b", re.IGNORECASE),
    re.compile(r"\b(?:un[a]?|equipo|asesor[a]?|agente|persona)\s+human[oa]s?\b", re.IGNORECASE),
)


class Persona:
    """«¿Deja ver que quien atiende es un bot o una IA?» (ver el módulo).
    Valor: los índices de las oraciones que se caen."""

    name = "persona"
    thresholds: Mapping[str, float] = {"yes": 0.85, "no": 0.15}

    def rule(self, inp: Frases) -> tuple[int, ...]:
        from src.sdk.textkit import breaks_human_persona

        return tuple(i for i, part in enumerate(inp.parts) if breaks_human_persona(part))

    def ask(self, inp: Frases) -> tuple[str, list[TypedQuestion]] | None:
        if not inp.parts or len(inp.parts) > MAX_SENTENCES:
            return None
        state = (
            "Texto que la tienda le va a enviar a un cliente por WhatsApp, oración por oración. "
            "Quien atiende es un asesor del equipo de la tienda.\n" + _numbered(inp.parts)
        )
        return state, [
            TypedQuestion(
                id=f"persona.{i}",
                kind="noul",
                text=(
                    f"¿La oración [{i}] deja ver que quien atiende es un bot, una IA o un sistema automático, "
                    "o anuncia que lo va a atender «una persona» o «un humano»?"
                ),
                criteria=_YES_NO,
            )
            for i in range(1, len(inp.parts) + 1)
        ]

    def decide(
        self, inp: Frases, result: Any, rule: tuple[int, ...], thresholds: Mapping[str, float]
    ) -> tuple[int, ...] | None:
        th = {**self.thresholds, **thresholds}
        drop: list[int] = []
        answered = False
        for i in range(len(inp.parts)):
            p = _p(result, f"persona.{i + 1}")
            if p is not None:
                answered = True
            if p is not None and p >= th["yes"]:
                drop.append(i)
            elif p is not None and p <= th["no"]:
                continue
            elif i in rule:  # duda o sin respuesta: la regla, para esa oración
                drop.append(i)
        return tuple(drop) if answered else None

    def floor(self, inp: Frases, rule: tuple[int, ...], jev: tuple[int, ...]) -> tuple[int, ...]:
        # Subconjunto de la regla de hoy: con Jev nunca se cae lo que hoy pasa.
        floor = {i for i in rule if i < len(inp.parts) and any(p.search(inp.parts[i]) for p in _PERSONA_FLOOR)}
        return tuple(sorted(set(jev) | floor))

    def same(self, a: Sequence[int], b: Sequence[int]) -> bool:
        return tuple(sorted(a)) == tuple(sorted(b))


PERSONA = Persona()


@dataclass(frozen=True)
class TextoCatalogo:
    """Un texto para el cliente y los aromas y colores del catálogo."""

    text: str
    aromas: tuple[str, ...]
    colors: tuple[str, ...]


def _choice(result: Any, qid: str) -> tuple[str | None, float]:
    answer = answer_of(result, qid)
    choice = getattr(answer, "choice", None)
    if not choice:
        return None, 0.0
    probs = dict(getattr(answer, "probs", ()) or ())
    p = probs.get(choice, getattr(answer, "confidence", None))
    return choice, float(p) if isinstance(p, (int, float)) else 0.0


class Enumeracion:
    """«¿Qué le enumera el texto al cliente?» (activity de enumeración).
    Valor: `("scent" | "color", etiquetas)` si va el selector, `()` si no.
    Las etiquetas las saca el código del catálogo; Jev decide qué enumera
    el texto. Las combinaciones «Color · Aroma» de un cupón nunca van al
    selector (piso)."""

    name = "enumeracion"
    thresholds: Mapping[str, float] = {"confidence": 0.85}
    _OPTIONS: Mapping[str, str] = {
        "aromas": "una lista de aromas para que el cliente escoja uno",
        "colores": "una lista de colores para que el cliente escoja uno",
        "combinaciones_cupon": "combinaciones de color y aroma (por ejemplo las de un cupón), con productos o precios",
        "productos": "productos, o las características de un solo producto",
        "nada": "no enumera opciones para escoger",
    }

    def _found(self, inp: TextoCatalogo) -> Any:
        from src.plugins.chats.agent.sales.variant_enumeration import enumeration_candidates

        return enumeration_candidates(inp.text, aromas=list(inp.aromas), colors=list(inp.colors))

    def rule(self, inp: TextoCatalogo) -> tuple:
        from src.plugins.chats.agent.sales.variant_enumeration import find_enumerated_variants

        hit = find_enumerated_variants(inp.text, aromas=list(inp.aromas), colors=list(inp.colors))
        return () if hit is None else (hit[0], tuple(hit[1]))

    def ask(self, inp: TextoCatalogo) -> tuple[str, list[TypedQuestion]] | None:
        from src.plugins.chats.agent.sales.variant_enumeration import MIN_ENUMERATED

        found = self._found(inp)
        if max(len(found.scents), len(found.colors)) < MIN_ENUMERATED:
            return None  # no hay lista que decidir
        state = f"Texto que la tienda le va a enviar a un cliente por WhatsApp:\n{inp.text.strip()}"
        return state, [
            TypedQuestion(
                id="enumeracion.que", kind="choice",
                text="¿Qué le enumera el texto al cliente, una opción tras otra?", criteria=dict(self._OPTIONS),
            )
        ]

    def decide(self, inp: TextoCatalogo, result: Any, rule: tuple, thresholds: Mapping[str, float]) -> tuple | None:
        from src.plugins.chats.agent.sales.variant_enumeration import MIN_ENUMERATED

        th = {**self.thresholds, **thresholds}
        choice, p = _choice(result, "enumeracion.que")
        if choice is None or p < th["confidence"]:
            return None
        found = self._found(inp)
        if choice == "aromas":
            return ("scent", found.scents) if len(found.scents) >= MIN_ENUMERATED else ()
        if choice == "colores":
            return ("color", found.colors) if len(found.colors) >= MIN_ENUMERATED else ()
        return ()

    def floor(self, inp: TextoCatalogo, rule: tuple, jev: tuple) -> tuple:
        from src.plugins.chats.agent.sales.variant_enumeration import MIN_COMBINATIONS

        return () if self._found(inp).combinations >= MIN_COMBINATIONS else jev

    def same(self, a: tuple, b: tuple) -> bool:
        return tuple(a or ()) == tuple(b or ())


ENUMERACION = Enumeracion()


@dataclass(frozen=True)
class OracionesPrecio:
    """Oraciones que el bot le escribió al cliente donde las palabras de
    política («desde», «envío», «mínimo»…) son lo que acepta un monto
    (`price_quotes.policy_decided_sentences`)."""

    sentences: tuple[str, ...]


class Monto:
    """«¿La oración cotiza el precio de un producto?» (tool del checkout).
    Valor: las oraciones que cotizan un producto; pierden el contexto de
    política y sus montos se cruzan contra el catálogo. La regla de hoy no
    marca ninguna (las palabras de política bastan): Jev solo puede hacer el
    chequeo más estricto. La cuenta sigue en código."""

    name = "monto"
    thresholds: Mapping[str, float] = {"yes": 0.85}

    def rule(self, inp: OracionesPrecio) -> tuple[str, ...]:
        return ()

    def ask(self, inp: OracionesPrecio) -> tuple[str, list[TypedQuestion]] | None:
        if not inp.sentences or len(inp.sentences) > MAX_SENTENCES:
            return None
        state = "Oraciones que la tienda le escribió a un cliente por WhatsApp:\n" + _numbered(inp.sentences)
        return state, [
            TypedQuestion(
                id=f"monto.{i}",
                kind="noul",
                text=(
                    f"¿La oración [{i}] le dice al cliente el precio de un producto (por unidad o por cantidad)? "
                    "No cuenta si es un total con envío, un mínimo para el pago contra entrega, una tarifa de "
                    "envío o lo que le falta para un mínimo."
                ),
                criteria=_YES_NO,
            )
            for i in range(1, len(inp.sentences) + 1)
        ]

    def decide(
        self, inp: OracionesPrecio, result: Any, rule: tuple[str, ...], thresholds: Mapping[str, float]
    ) -> tuple[str, ...] | None:
        th = {**self.thresholds, **thresholds}
        answered = False
        quotes: list[str] = []
        for i, sentence in enumerate(inp.sentences, 1):
            p = _p(result, f"monto.{i}")
            if p is None:
                continue
            answered = True
            if p >= th["yes"]:
                quotes.append(sentence)
        return tuple(quotes) if answered else None

    def floor(self, inp: OracionesPrecio, rule: tuple[str, ...], jev: tuple[str, ...]) -> tuple[str, ...]:
        return jev

    def same(self, a: Sequence[str], b: Sequence[str]) -> bool:
        return set(a) == set(b)


MONTO = Monto()


@dataclass(frozen=True)
class Botones:
    """Un mensaje con botones de respuesta rápida, con lo que ya dijo la
    regla de hoy (el vocabulario del catálogo) y los botones cuyo id delata
    un selector (`product.`, `color.`…)."""

    body: str
    titles: tuple[str, ...]
    rule_rejected: tuple[str, ...] = ()
    by_id: tuple[str, ...] = ()


class Selector:
    """«¿Estos botones le piden al cliente elegir un producto o una
    variante?» (tool de quick replies). Valor: los títulos rechazados (vacío
    = pasan). El namespace del id es PISO; Jev atrapa lo que el vocabulario
    no ve («El rosado») y deja pasar un sí/no que nombra una variante."""

    name = "selector"
    thresholds: Mapping[str, float] = {"yes": 0.85, "no": 0.15}

    def rule(self, inp: Botones) -> tuple[str, ...]:
        return tuple(inp.rule_rejected)

    def ask(self, inp: Botones) -> tuple[str, list[TypedQuestion]] | None:
        if not inp.titles:
            return None
        state = (
            "Mensaje con botones que la tienda le va a enviar a un cliente por WhatsApp:\n"
            f"{inp.body.strip()}\nBotones: " + " · ".join(f"[{t}]" for t in inp.titles)
        )
        return state, [
            TypedQuestion(
                id="selector.elige", kind="noul",
                text=(
                    "¿Estos botones le piden al cliente elegir un producto o una variante (aroma, color, diseño) "
                    "entre varias opciones? No cuenta un sí/no ni seguir/cambiar."
                ),
                criteria=_YES_NO,
            )
        ]

    def decide(self, inp: Botones, result: Any, rule: tuple[str, ...], thresholds: Mapping[str, float]) -> tuple[str, ...] | None:
        th = {**self.thresholds, **thresholds}
        p = _p(result, "selector.elige")
        if p is None:
            return None
        if p >= th["yes"]:
            return tuple(rule) if rule else tuple(inp.titles)
        if p <= th["no"]:
            return ()
        return None

    def floor(self, inp: Botones, rule: tuple[str, ...], jev: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*jev, *inp.by_id]))

    def same(self, a: Sequence[str], b: Sequence[str]) -> bool:
        return bool(a) == bool(b)


SELECTOR = Selector()


@dataclass(frozen=True)
class Afirmacion:
    """Lo que el turno le envió al cliente y las tools que consultó."""

    text: str
    tools_used: tuple[str, ...] = ()


class AfirmacionSinConsultar:
    """Pregunta de respaldo (F6): «¿El texto le afirma algo que solo se sabe
    consultando, sin haber consultado?» (stock, disponibilidad, entrega,
    estado del pedido). Arranca en SOMBRA: su valor nunca actúa (lo pregunta
    la activity de la traza, después de enviar); la regla de hoy no marca
    nada, así que cada «sí» de Jev va a la cola que califica Claude Code."""

    name = "afirmacion"
    thresholds: Mapping[str, float] = {"yes": 0.85, "no": 0.15}

    def rule(self, inp: Afirmacion) -> bool:
        return False  # hoy nada lo revisa

    def ask(self, inp: Afirmacion) -> tuple[str, list[TypedQuestion]] | None:
        if not inp.text.strip():
            return None
        consulted = ", ".join(inp.tools_used) if inp.tools_used else "ninguna"
        state = (
            "Mensaje que la tienda le envió a un cliente por WhatsApp:\n"
            f"{inp.text.strip()}\n\n"
            f"Herramientas que consultó antes de escribirlo: {consulted}."
        )
        return state, [
            TypedQuestion(
                id="afirmacion.sin_consultar",
                kind="noul",
                text=(
                    "¿El mensaje le afirma al cliente algo que solo se sabe consultando (hay stock o está "
                    "disponible, cuándo llega, en qué va su pedido) sin haber usado la herramienta que lo consulta?"
                ),
                criteria=_YES_NO,
            )
        ]

    def decide(self, inp: Afirmacion, result: Any, rule: bool, thresholds: Mapping[str, float]) -> bool | None:
        th = {**self.thresholds, **thresholds}
        p = _p(result, "afirmacion.sin_consultar")
        if p is None:
            return None
        if p >= th["yes"]:
            return True
        if p <= th["no"]:
            return False
        return None

    def floor(self, inp: Afirmacion, rule: bool, jev: bool) -> bool:
        return bool(jev)

    def same(self, a: bool, b: bool) -> bool:
        return bool(a) == bool(b)


AFIRMACION = AfirmacionSinConsultar()

__all__ = [
    "AFIRMACION",
    "ENUMERACION",
    "MAX_SENTENCES",
    "MONTO",
    "PERSONA",
    "SELECTOR",
    "Afirmacion",
    "AfirmacionSinConsultar",
    "Botones",
    "Enumeracion",
    "Frases",
    "Monto",
    "OracionesPrecio",
    "Persona",
    "Selector",
    "TextoCatalogo",
]


@dataclass(frozen=True)
class TextoAlCliente:
    """El texto final que el cliente va a leer."""

    text: str


#: Quién promete el relevo y qué hará por el cliente (texto sin tildes ni
#: mayúsculas). El relevo de verdad lo anuncia `escalate_to_human` («un colega
#: del equipo te responde en este mismo chat»); dicho fuera de esa tool, nadie
#: queda avisado.
_WHO = r"(?:colega|companer[oa]|asesora?|persona del equipo|alguien del equipo)"
_DOES = r"(?:respond|escrib|contact|confirm|coordin|atiend|llam|avis|cuent|ayud)\w*"
_RELEVO_RE = re.compile(
    rf"\b{_WHO}\b[^.!?\n]{{0,60}}?\b(?:te|le|les)\s+(?:va(?:n)?\s+a\s+)?{_DOES}"
    rf"|\b(?:{_WHO}|el equipo|nuestro equipo)\b[^.!?\n]{{0,40}}?\b(?:coordin|confirm|habl|cuadr|organiz)\w*\s+contigo"
    rf"|\b(?:el|nuestro) equipo\s+(?:te|le)\s+(?:va(?:n)?\s+a\s+)?{_DOES}"
    r"|\b(?:le|les) paso\s+(?:el aviso|tu caso|tu pedido|tus datos|tu solicitud|la solicitud|tu mensaje|la informacion)"
    r"|\bte\s+(?:paso|comunico|conecto)\s+con\s+(?:un|una|el|la|alguien|nuestro|nuestra)\b"
)


def promises_a_handoff(text: str | None) -> bool:
    """¿El texto le dice al cliente que una persona del equipo lo va a
    contactar, coordinar o confirmar algo con él? (la regla de `relevo`)."""
    return bool(_RELEVO_RE.search(plain(" ".join(str(text or "").split()))))


class Relevo:
    """«¿El texto le promete al cliente que una persona del equipo lo va a
    atender?» (laboratorio caso-cortesia-1001, 2026-09-30: los dos bots
    dijeron «un colega del equipo coordina contigo la entrega» sin escalar y
    nadie quedaba avisado). La consulta la red de seguridad antes de enviar el
    texto final: si promete el relevo y nadie escaló, escala. Las frases del
    relevo son PISO (la regla vale para el bot de hoy); Jev suma las
    paráfrasis que la regla no conoce. Valor: bool."""

    name = "relevo"
    thresholds: Mapping[str, float] = {"yes": 0.85, "no": 0.15}

    def rule(self, inp: TextoAlCliente) -> bool:
        return promises_a_handoff(inp.text)

    def ask(self, inp: TextoAlCliente) -> tuple[str, list[TypedQuestion]] | None:
        text = " ".join(str(inp.text or "").split())
        if not text:
            return None
        state = f"Mensaje que la tienda le va a enviar a un cliente por WhatsApp:\n{inp.text.strip()}"
        return state, [
            TypedQuestion(
                id="relevo.promete",
                kind="noul",
                text=(
                    "¿El mensaje le dice al cliente que una persona del equipo (un colega, un asesor, alguien de "
                    "despachos) lo va a contactar, a coordinar o a confirmar algo con él?"
                ),
                criteria=_YES_NO,
            )
        ]

    def decide(self, inp: TextoAlCliente, result: Any, rule: bool, thresholds: Mapping[str, float]) -> bool | None:
        th = {**self.thresholds, **thresholds}
        p = _p(result, "relevo.promete")
        if p is None:
            return None
        if p >= th["yes"]:
            return True
        if p <= th["no"]:
            return False
        return None

    def floor(self, inp: TextoAlCliente, rule: bool, jev: bool) -> bool:
        return bool(rule) or bool(jev)

    def same(self, a: bool, b: bool) -> bool:
        return bool(a) == bool(b)


RELEVO = Relevo()
