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
* cierre — la etiqueta del cierre por ghosting (confirmado sin datos,
  interesado, rechazo, compra exitosa). Con Jev, el aviso de ghosting le dice
  al LLM cuál usar (la mecánica del cierre queda igual); el código pone los
  invariantes del pedido.
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


@dataclass(frozen=True)
class Abandono:
    """La conversación que el cliente dejó (lo último al final) y lo que el
    código ya sabe del pedido."""

    transcript: str
    purchase_confirmed: bool = False
    order_registered: bool = False


def _choice(result: Any, qid: str) -> tuple[str | None, float]:
    answer = answer_of(result, qid)
    choice = getattr(answer, "choice", None)
    if not choice:
        return None, 0.0
    probs = dict(getattr(answer, "probs", ()) or ())
    p = probs.get(choice, getattr(answer, "confidence", None))
    return choice, float(p) if isinstance(p, (int, float)) else 0.0


class CierrePorAbandono:
    """La etiqueta del cierre por ghosting (F8). Hoy la elige el LLM con el
    aviso de ghosting (regla = "": decide el LLM). Con Jev, la lectura de la
    conversación la decide y el aviso le dice al LLM cuál usar. Valor: la
    etiqueta en mayúsculas, o "" (decide el LLM). Invariantes del código:
    pedido registrado = COMPRA_EXITOSA; CONFIRMADO_SIN_DATOS sin confirmación
    de compra = INTERESADO (la misma degradación de la tool); COMPRA_EXITOSA
    sin pedido registrado no existe (decide el LLM)."""

    name = "cierre"
    timeout_s = 2.0
    thresholds: Mapping[str, float] = {"choice": 0.85}
    _OPTIONS: Mapping[str, str] = {
        "confirmado_sin_datos": "confirmó la compra pero no terminó de dar los datos de envío",
        "interesado": "mostró interés y quedó pensándolo, o no está claro",
        "rechazo": "dijo que no, o pedía algo que no vendemos y se fue, o solo resolvió una duda y se despidió",
        "compra_exitosa": "compró y el pedido quedó registrado",
    }

    def rule(self, inp: Abandono) -> str:
        return ""  # hoy decide el LLM

    def ask(self, inp: Abandono) -> tuple[str, list[TypedQuestion]] | None:
        if not inp.transcript.strip():
            return None
        state = (
            "Conversación de una tienda con un cliente por WhatsApp; el cliente dejó de responder "
            "(lo último al final):\n"
            f"{inp.transcript.strip()}\n\n"
            f"Lo que ya sabe la tienda: el cliente confirmó la compra: {'sí' if inp.purchase_confirmed else 'no'}; "
            f"pedido registrado: {'sí' if inp.order_registered else 'no'}."
        )
        return state, [
            TypedQuestion(
                id="cierre.etiqueta", kind="choice", text="¿Cómo quedó la conversación?", criteria=dict(self._OPTIONS)
            )
        ]

    def decide(self, inp: Abandono, result: Any, rule: str, thresholds: Mapping[str, float]) -> str | None:
        th = {**self.thresholds, **thresholds}
        choice, p = _choice(result, "cierre.etiqueta")
        if choice not in self._OPTIONS or p < th["choice"]:
            return None
        return str(choice).upper()

    def floor(self, inp: Abandono, rule: str, jev: str) -> str:
        if inp.order_registered:
            return "COMPRA_EXITOSA"
        if jev == "COMPRA_EXITOSA":
            return ""
        if jev == "CONFIRMADO_SIN_DATOS" and not inp.purchase_confirmed:
            return "INTERESADO"
        return jev

    def same(self, a: str, b: str) -> bool:
        return (a or "") == (b or "")


CIERRE = CierrePorAbandono()

__all__ = ["CIERRE", "CONTACTAR", "Abandono", "CierrePorAbandono", "Contactar", "Contacto"]
