"""Revisión de cada dato de `set_order_slot` (diseño v2 §04, fase F6).

El LLM a veces guarda un dato de envío o de pago que el cliente nunca dio (lo
supone, o lo trae de un pedido anterior). Antes de escribirlo, el motor le
pregunta a Jev, por cada dato, si el cliente lo dio en la conversación (con
sus palabras o confirmándolo cuando se lo preguntaron). Valor: los campos que
NO se guardan. Solo bloquea con certeza alta (p ≤ 0,15); si Jev duda, cae o
tarda, el dato se guarda como hoy. Hoy no hay ninguna revisión: la regla deja
pasar todo.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.plugins.chats.agent.sales.decisions.plan import answer_of
from src.sdk.connectorkit import TypedQuestion

_YES_NO = {"true": "sí", "false": "no"}

#: Los datos que revisa: los de envío y pago (los del producto los valida el
#: catálogo).
CHECKED_SLOTS: tuple[str, ...] = (
    "ciudad", "barrio", "direccion", "telefono", "nombre_recibe", "cedula", "metodo_pago",
)
_LABELS: Mapping[str, str] = {
    "ciudad": "ciudad de envío",
    "barrio": "barrio",
    "direccion": "dirección de envío",
    "telefono": "teléfono de contacto",
    "nombre_recibe": "nombre de quien recibe",
    "cedula": "cédula de quien recibe",
    "metodo_pago": "método de pago",
}
#: Lo último de la conversación que ve Jev.
MAX_LINES = 14


@dataclass(frozen=True)
class DatosDelPedido:
    """Los datos que el LLM quiere guardar y lo último de la conversación."""

    values: tuple[tuple[str, str], ...]
    events: tuple[Mapping[str, Any], ...] = ()


def _p(result: Any, qid: str) -> float | None:
    p = getattr(answer_of(result, qid), "p", None)
    return float(p) if isinstance(p, (int, float)) else None


def _lines(events: Sequence[Mapping[str, Any]]) -> list[str]:
    out: list[str] = []
    for event in events:
        role = event.get("role")
        text = " ".join(str(event.get("content") or "").split())
        if not text or role not in ("user", "assistant"):
            continue
        who = "cliente" if role == "user" else ("equipo" if event.get("sender") == "human" else "asesor")
        out.append(f"[{who}] {text[:400]}")
    return out[-MAX_LINES:]


class Datos:
    """«¿El cliente dio este dato?» (ver el módulo)."""

    name = "datos"
    timeout_s = 2.0
    thresholds: Mapping[str, float] = {"no": 0.15}

    def rule(self, inp: DatosDelPedido) -> tuple[str, ...]:
        return ()  # hoy nada se revisa: se guarda todo

    def ask(self, inp: DatosDelPedido) -> tuple[str, list[TypedQuestion]] | None:
        values = [(k, v) for k, v in inp.values if k in CHECKED_SLOTS and v.strip()]
        lines = _lines(inp.events)
        if not values or not lines:
            return None
        state = "Conversación de una tienda con un cliente por WhatsApp (lo último al final):\n" + "\n".join(lines)
        return state, [
            TypedQuestion(
                id=f"datos.{slot}",
                kind="noul",
                text=(
                    f"¿El cliente dio este dato en la conversación, con sus palabras o confirmándolo cuando se lo "
                    f"preguntaron? {_LABELS.get(slot, slot)}: «{value}»"
                ),
                criteria=_YES_NO,
            )
            for slot, value in values
        ]

    def decide(
        self, inp: DatosDelPedido, result: Any, rule: tuple[str, ...], thresholds: Mapping[str, float]
    ) -> tuple[str, ...] | None:
        th = {**self.thresholds, **thresholds}
        answered = False
        blocked: list[str] = []
        for slot, value in inp.values:
            if slot not in CHECKED_SLOTS or not value.strip():
                continue
            p = _p(result, f"datos.{slot}")
            if p is None:
                continue
            answered = True
            if p <= th["no"]:
                blocked.append(slot)
        return tuple(blocked) if answered else None

    def floor(self, inp: DatosDelPedido, rule: tuple[str, ...], jev: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(jev)

    def same(self, a: Sequence[str], b: Sequence[str]) -> bool:
        return set(a) == set(b)


DATOS = Datos()

__all__ = ["CHECKED_SLOTS", "DATOS", "Datos", "DatosDelPedido"]
