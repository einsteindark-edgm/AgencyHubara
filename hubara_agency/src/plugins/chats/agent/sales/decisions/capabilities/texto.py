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
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.plugins.chats.agent.sales.decisions.plan import answer_of
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
    timeout_s = 2.0
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
    timeout_s = 2.0
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

__all__ = ["ENUMERACION", "MAX_SENTENCES", "PERSONA", "Enumeracion", "Frases", "Persona", "TextoCatalogo"]
