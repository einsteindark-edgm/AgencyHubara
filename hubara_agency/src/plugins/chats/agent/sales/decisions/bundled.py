"""Capacidades desde el paquete de decisión (PAQUETES_DE_DECISION.md, fase F1).

El paquete `bundles/hubara-ventas/` (YAML certificado) trae las preguntas,
los umbrales y la tabla de decisión de cada capacidad; este módulo pone lo
que sigue siendo código —los builtins: reglas, constructores de estado,
pisos y comparadores— y arma con los dos un objeto con la forma de
`Capability` (`rule`/`ask`/`decide`/`floor`/`same`), que corre igual que las
clases por el mismo `decide()` del motor.

Los builtins que existen están declarados en `bundles/builtins.yaml` (lo
único que lee el certificador); `test_decisions_bundle_parity.py` verifica
que catálogo y código coincidan, y que cada capacidad migrada decida
EXACTAMENTE como su clase.

Sin Temporal: lo usan el ingest, las tools y las activities.
"""
from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.capabilities.lecturas import _customer_text
from src.plugins.chats.agent.sales.decisions.context import customer_window
from src.sdk.connectorkit import TypedQuestion
from src.sdk.decisionkit import DOUBT, CompiledCapability, answers_from_result, load_bundle

BUNDLES_DIR = Path(__file__).parent / "bundles"
BUNDLE_DIR = BUNDLES_DIR / "hubara-ventas"
CATALOG_PATH = BUNDLES_DIR / "builtins.yaml"


# ── Reglas: lo que decide sin Jev (o cuando Jev duda) ──────────────────────


def _constant_false(inp: Any) -> bool:
    return False


def _opt_out_text(inp: Any) -> bool:
    from src.sdk.messagingkit import is_opt_out_text

    text = _customer_text(inp)
    return bool(text) and is_opt_out_text(text)


# ── Estado: el texto que lee Jev (None = no se pregunta) ───────────────────


def _customer_message(inp: Any, *, header: str) -> str | None:
    text = _customer_text(inp)
    if text is None:
        return None
    return f"{header}\n[1] {text.strip()}"


def _customer_message_with_window(inp: Any) -> str | None:
    text = _customer_text(inp)
    if text is None:
        return None
    window = customer_window(list(getattr(inp, "events", ()) or ()), burst_wamids=set(), burst_size=0)
    lines = ["CONTEXTO — lo que el cliente vio antes de este mensaje", *window.lines] if window.lines else []
    return "\n".join([*lines, "MENSAJE DEL CLIENTE", f"[1] {text.strip()}"])


# ── Pisos: lo que nunca se quita ────────────────────────────────────────────


def _floor_jev(inp: Any, rule: Any, jev: Any) -> Any:
    return jev


def _rule_or_jev(inp: Any, rule: Any, jev: Any) -> bool:
    return bool(rule) or bool(jev)


# ── Comparadores: si dos decisiones coinciden ──────────────────────────────


def _bool_eq(a: Any, b: Any) -> bool:
    return bool(a) == bool(b)


BUILTINS: dict[str, dict[str, Callable[..., Any]]] = {
    "rule": {"constant_false": _constant_false, "opt_out_text": _opt_out_text},
    "state": {"customer_message": _customer_message, "customer_message_with_window": _customer_message_with_window},
    "floor": {"jev": _floor_jev, "rule_or_jev": _rule_or_jev},
    "same": {"bool_eq": _bool_eq},
}


class BundledCapability:
    """Una capacidad del paquete con la forma de `Capability`."""

    def __init__(self, table: CompiledCapability) -> None:
        spec = table.spec
        self._table = table
        self._spec = spec
        self.name = spec.capability
        self.thresholds = dict(spec.thresholds)
        #: `id@versión` del paquete con el que se decide.
        self.bundle = table.bundle
        self._questions = [
            TypedQuestion(id=q.id, kind=q.kind, text=q.text, criteria=dict(q.criteria)) for q in spec.questions
        ]

    def _call(self, kind: str, ref: Any, *args: Any) -> Any:
        return BUILTINS[kind][ref.builtin](*args, **ref.params)

    def rule(self, inp: Any) -> Any:
        return self._call("rule", self._spec.rule, inp)

    def ask(self, inp: Any) -> tuple[str, list[TypedQuestion]] | None:
        if self._spec.state is None:
            return None
        state = self._call("state", self._spec.state, inp)
        if state is None:
            return None
        return state, list(self._questions)

    def decide(self, inp: Any, result: Any, rule: Any, thresholds: Any) -> Any:
        value = self._table.decide(
            answers=answers_from_result(self._spec.questions, result), thresholds=dict(thresholds or {}), rule=rule
        )
        return None if value is DOUBT else value

    def floor(self, inp: Any, rule: Any, jev: Any) -> Any:
        return self._call("floor", self._spec.floor, inp, rule, jev)

    def same(self, a: Any, b: Any) -> bool:
        return bool(self._call("same", self._spec.same, a, b))


@lru_cache(maxsize=1)
def _bundle() -> Any:
    return load_bundle(BUNDLE_DIR, CATALOG_PATH)


@lru_cache(maxsize=None)
def bundled_capability(name: str) -> BundledCapability:
    """La capacidad `name` del paquete de ventas (certificado al cargar: un
    paquete que no compila lanza `BundleError`; CI lo atrapa antes)."""
    return BundledCapability(_bundle().capability(name))
