"""Revisión de cada dato de `set_order_slot` (diseño v2 §04, fase F6).

El LLM a veces guarda un dato de envío o de pago que el cliente nunca dio (lo
supone, o lo trae de un pedido anterior). El desglose lo hace el LLM y lo
hace bien (producción guarda así lo que el cliente manda en un mensaje): la
revisión solo confirma que cada valor salió del cliente.

1. En código: el valor que está en lo que el cliente escribió (sin tildes,
   mayúsculas ni signos; el LLM completa «cll», agrega tildes o junta un
   teléfono) se guarda sin preguntarle a Jev. Laboratorio caso-fotos-0930,
   4567 t22 (r10 y r11): el cliente mandó ciudad, dirección, quien recibe y
   pago en un mensaje; Jev, que ve los datos personales tapados («recibe
   [nombre]»), contestó 0,08 al nombre, no se guardó y el complemento del
   turno se lo volvió a pedir.
2. Lo que no está en sus palabras (un «sí» a lo que propuso el asesor, o un
   dato inventado) se le pregunta a Jev, un dato por pregunta. Los datos
   personales nunca van en la pregunta: Jev los ve tapados en la conversación.

Valor: los campos que NO se guardan. Solo bloquea con certeza alta (p ≤ 0,15);
si Jev duda, cae o tarda, el dato se guarda. Con `reglas` no hay ninguna
revisión: la regla deja pasar todo.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.plugins.chats.agent.sales.decisions.plan import answer_of
from src.sdk.connectorkit import TypedQuestion
from src.plugins.chats.agent.sales.decisions.retiro import en_retiro

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
#: Los que Jev ve tapados en la conversación ([nombre], [dirección],
#: [teléfono]…): su valor nunca va en la pregunta.
PERSONAL_SLOTS: frozenset[str] = frozenset({"nombre_recibe", "direccion", "barrio", "telefono", "cedula"})
#: Lo último de la conversación que ve Jev (y donde se buscan las palabras
#: del cliente).
MAX_LINES = 14

#: Números que el cliente separa como quiere («300 123 45 67», «1.020.345»):
#: se comparan los dígitos seguidos, desde 7.
_NUMBER_SLOTS = frozenset({"telefono", "cedula"})
_MIN_DIGITS = 7
_NUMBER_RE = re.compile(r"\d[\d .\-]*\d|\d")
#: Palabras que el LLM completa o agrega y que no identifican el dato.
_FILLER = frozenset({
    "calle", "cll", "cl", "carrera", "cra", "cr", "kr", "kra", "avenida", "av", "diagonal", "dg", "transversal",
    "tv", "casa", "cs", "apto", "apartamento", "ap", "torre", "interior", "int", "bloque", "piso", "edificio",
    "conjunto", "manzana", "mz", "lote", "numero", "no", "nro",
    "sr", "sra", "srta", "senor", "senora", "don", "dona",
    "de", "del", "la", "las", "el", "los", "en", "al",
})


@dataclass(frozen=True)
class DatosDelPedido:
    """Los datos que el LLM quiere guardar y lo último de la conversación."""

    values: tuple[tuple[str, str], ...]
    events: tuple[Mapping[str, Any], ...] = ()


def _p(result: Any, qid: str) -> float | None:
    p = getattr(answer_of(result, qid), "p", None)
    return float(p) if isinstance(p, (int, float)) else None


def _window(events: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """Lo último de la conversación: `(quién, texto)`."""
    out: list[tuple[str, str]] = []
    for event in events:
        role = event.get("role")
        text = " ".join(str(event.get("content") or "").split())
        if not text or role not in ("user", "assistant"):
            continue
        out.append(("cliente" if role == "user" else ("equipo" if event.get("sender") == "human" else "asesor"), text))
    return out[-MAX_LINES:]


def _plain(text: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def same_value(a: Any, b: Any) -> bool:
    """¿Es el mismo dato? (sin tildes, mayúsculas ni signos)."""
    return isinstance(a, str) and isinstance(b, str) and bool(_plain(a)) and _plain(a) == _plain(b)


def in_customer_words(slot: str, value: str, said: Sequence[str]) -> bool:
    """¿El valor sale de lo que escribió el cliente? Un teléfono o una cédula:
    sus dígitos, aunque el cliente los separe. Lo demás: cada número y cada
    palabra que lo identifica (no «calle», «casa» ni «sra.», que el LLM
    completa o agrega)."""
    if slot in _NUMBER_SLOTS:
        digits = _digits(value)
        runs = [_digits(m) for text in said for m in _NUMBER_RE.findall(text)]
        return len(digits) >= _MIN_DIGITS and any(
            digits in run or (len(run) >= _MIN_DIGITS and run in digits) for run in runs
        )
    marks = {w for w in _plain(value).split() if (w.isdigit() or len(w) >= 2) and w not in _FILLER}
    return bool(marks) and marks <= set(_plain(" ".join(said)).split())


def _question_text(slot: str, value: str) -> str:
    label = _LABELS.get(slot, slot)
    if slot in PERSONAL_SLOTS:
        return (
            f"¿El cliente dio en la conversación el dato «{label}», con sus palabras o confirmándolo cuando se lo "
            "preguntaron? Los datos personales aparecen tapados, como [nombre], [dirección] o [teléfono]."
        )
    return (
        f"¿El cliente dio este dato en la conversación, con sus palabras o confirmándolo cuando se lo "
        f"preguntaron? {label}: «{value}»"
    )


@en_retiro("clase:datos")
class Datos:
    """«¿El cliente dio este dato?» (ver el módulo)."""

    name = "datos"
    thresholds: Mapping[str, float] = {"no": 0.15}

    def rule(self, inp: DatosDelPedido) -> tuple[str, ...]:
        return ()  # hoy nada se revisa: se guarda todo

    def ask(self, inp: DatosDelPedido) -> tuple[str, list[TypedQuestion]] | None:
        window = _window(inp.events)
        said = [text for who, text in window if who == "cliente"]
        values = [
            (k, v) for k, v in inp.values
            if k in CHECKED_SLOTS and v.strip() and not in_customer_words(k, v, said)
        ]
        if not values or not window:
            return None
        lines = [f"[{who}] {text[:400]}" for who, text in window]
        state = "Conversación de una tienda con un cliente por WhatsApp (lo último al final):\n" + "\n".join(lines)
        return state, [
            TypedQuestion(id=f"datos.{slot}", kind="noul", text=_question_text(slot, value), criteria=_YES_NO)
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

__all__ = ["CHECKED_SLOTS", "DATOS", "PERSONAL_SLOTS", "Datos", "DatosDelPedido", "in_customer_words", "same_value"]
