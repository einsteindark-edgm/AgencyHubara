"""Decisiones del agente que hoy toma el LLM por instrucción del prompt
(diseño v2 §07, familia D; fase F8). Mismo marco que
`capabilities/__init__.py`, con una diferencia: la «regla de hoy» es que
decida el LLM. Con `reglas` (así nace) la capacidad no cambia nada; con Jev,
una pregunta cerrada decide ANTES de gastar el turno del LLM.

* contactar — «¿Sobra un mensaje proactivo ahora?», antes de redactar el
  gancho de remarketing (lo pregunta la activity que lee el contexto del
  gancho). Si sobra, no se redacta nada: desaparecen las fugas por
  deliberación del turno de remarketing. Si no sobra, o si Jev duda, el LLM
  sigue pudiendo abstenerse con NO_MESSAGE como hoy.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.plugins.chats.agent.sales.decisions.plan import answer_of
from src.sdk.connectorkit import TypedQuestion

_YES_NO = {"true": "sí", "false": "no"}


def _p(result: Any, qid: str) -> float | None:
    p = getattr(answer_of(result, qid), "p", None)
    return float(p) if isinstance(p, (int, float)) else None


@dataclass(frozen=True)
class Contacto:
    """Lo que ve el gancho de remarketing antes de redactar: la conversación
    (lo último al final), qué toque de la escalera es y cuánto silencio real
    lleva el cliente."""

    transcript: str
    touch_number: int | None = None
    silence_minutes: int | None = None


class Contactar:
    """«¿Sobra un mensaje proactivo ahora?» (ver el módulo). Valor: True =
    el toque sobra (no se redacta nada)."""

    name = "contactar"
    timeout_s = 2.0
    thresholds: Mapping[str, float] = {"yes": 0.85, "no": 0.15}

    def rule(self, inp: Contacto) -> bool:
        return False  # hoy decide el LLM (puede abstenerse con NO_MESSAGE)

    def ask(self, inp: Contacto) -> tuple[str, list[TypedQuestion]] | None:
        if not inp.transcript.strip():
            return None
        when = []
        if inp.touch_number:
            when.append(f"es el toque {inp.touch_number} de la escalera de reactivación")
        if inp.silence_minutes is not None:
            when.append(f"el cliente lleva {inp.silence_minutes} minutos sin escribir")
        state = (
            "Conversación de una tienda con un cliente por WhatsApp (lo último al final):\n"
            f"{inp.transcript.strip()}\n\n"
            "La tienda está por escribirle un mensaje proactivo" + (f" ({'; '.join(when)})" if when else "") + "."
        )
        return state, [
            TypedQuestion(
                id="contactar.sobra",
                kind="noul",
                text=(
                    "¿Sobra un mensaje proactivo ahora? Sobra si el cliente ya compró, se despidió o dijo que no, "
                    "pidió que no le escribieran, lo está atendiendo una persona del equipo o la conversación "
                    "sigue viva."
                ),
                criteria=_YES_NO,
            )
        ]

    def decide(self, inp: Contacto, result: Any, rule: bool, thresholds: Mapping[str, float]) -> bool | None:
        th = {**self.thresholds, **thresholds}
        p = _p(result, "contactar.sobra")
        if p is None:
            return None
        if p >= th["yes"]:
            return True
        if p <= th["no"]:
            return False
        return None

    def floor(self, inp: Contacto, rule: bool, jev: bool) -> bool:
        return bool(jev)

    def same(self, a: bool, b: bool) -> bool:
        return bool(a) == bool(b)


CONTACTAR = Contactar()

__all__ = ["CONTACTAR", "Contactar", "Contacto"]
