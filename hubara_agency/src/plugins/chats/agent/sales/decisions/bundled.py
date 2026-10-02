"""Capacidades desde el paquete de decisión (PAQUETES_DE_DECISION.md, fase F1).

El paquete `bundles/ventas/` (YAML certificado) trae las preguntas,
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

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.context import customer_window
from src.sdk.connectorkit import TypedQuestion
from src.sdk.decisionkit import DOUBT, CompiledCapability, answers_from_result

BUNDLES_DIR = Path(__file__).parent / "bundles"
CATALOG_PATH = BUNDLES_DIR / "builtins.yaml"


def customer_text(inp: Any) -> str | None:
    """El texto que escribió el cliente: None si viene vacío o si lo escribió
    la visión (la descripción de una foto no es del cliente)."""
    text = getattr(inp, "text", None)
    if getattr(inp, "synthetic", False) or not isinstance(text, str) or not text.strip():
        return None
    return text


# ── Reglas: lo que decide sin Jev (o cuando Jev duda) ──────────────────────


def _constant_false(inp: Any) -> bool:
    return False


def _constant_empty(inp: Any) -> str:
    return ""  # decide el LLM (cierre por abandono)


def _reply_quantity(inp: Any) -> dict[str, int | None]:
    from src.plugins.chats.agent.sales.use_cases.quantity_capture import read_reply_quantity

    return {"cantidad": read_reply_quantity(inp.last_agent_text, inp.text)}


def _shipping_zone(inp: Any) -> dict[str, str | None]:
    from src.plugins.chats.agent.sales.config.shipping import shipping_zone

    return {"zona": shipping_zone(inp.ciudad)}


def _rule_rejected_titles(inp: Any) -> tuple[str, ...]:
    return tuple(inp.rule_rejected)


def _promises_a_handoff(inp: Any) -> bool:
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import promises_a_handoff

    return promises_a_handoff(inp.text)


# ── Estado: el texto que lee Jev (None = no se pregunta) ───────────────────


def _customer_message(inp: Any, *, header: str) -> str | None:
    text = customer_text(inp)
    if text is None:
        return None
    return f"{header}\n[1] {text.strip()}"


def _customer_message_with_window(inp: Any) -> str | None:
    text = customer_text(inp)
    if text is None:
        return None
    window = customer_window(list(getattr(inp, "events", ()) or ()), burst_wamids=set(), burst_size=0)
    lines = ["CONTEXTO — lo que el cliente vio antes de este mensaje", *window.lines] if window.lines else []
    return "\n".join([*lines, "MENSAJE DEL CLIENTE", f"[1] {text.strip()}"])


def _text_template(inp: Any, *, template: str, required: str) -> str | None:
    """`template` con `{<required>}` = ese campo de la entrada, sin espacios
    de más. Campo vacío = no se pregunta."""
    value = str(getattr(inp, required, None) or "").strip()
    if not value:
        return None
    return template.replace("{" + required + "}", value)


def _proactive_touch(inp: Any) -> str | None:
    if not inp.transcript.strip():
        return None
    when = []
    if inp.touch_number:
        when.append(f"es el toque {inp.touch_number} de la escalera de reactivación")
    if inp.silence_minutes is not None:
        when.append(f"el cliente lleva {inp.silence_minutes} minutos sin escribir")
    return (
        "Conversación de una tienda con un cliente por WhatsApp (lo último al final):\n"
        f"{inp.transcript.strip()}\n\n"
        "La tienda está por escribirle un mensaje proactivo" + (f" ({'; '.join(when)})" if when else "") + "."
    )


def _abandoned_chat(inp: Any) -> str | None:
    if not inp.transcript.strip():
        return None
    return (
        "Conversación de una tienda con un cliente por WhatsApp; el cliente dejó de responder "
        "(lo último al final):\n"
        f"{inp.transcript.strip()}\n\n"
        f"Lo que ya sabe la tienda: el cliente confirmó la compra: {'sí' if inp.purchase_confirmed else 'no'}; "
        f"pedido registrado: {'sí' if inp.order_registered else 'no'}."
    )


def _quantity_reply(inp: Any) -> str | None:
    from src.plugins.chats.agent.sales.decisions.capabilities.lecturas_pedido import _clip_end

    text = (inp.text or "").strip()
    agent = (inp.last_agent_text or "").strip()
    if not inp.open_slot or not text or not agent or text.startswith("["):
        return None
    return "\n".join(["ÚLTIMO MENSAJE DEL ASESOR", f"[asesor] {_clip_end(agent)}", "RESPUESTA DEL CLIENTE", f"[1] {text}"])


def _buttons_message(inp: Any) -> str | None:
    if not inp.titles:
        return None
    return (
        "Mensaje con botones que la tienda le va a enviar a un cliente por WhatsApp:\n"
        f"{inp.body.strip()}\nBotones: " + " · ".join(f"[{t}]" for t in inp.titles)
    )


def _sent_message_with_tools(inp: Any) -> str | None:
    if not inp.text.strip():
        return None
    consulted = ", ".join(inp.tools_used) if inp.tools_used else "ninguna"
    return (
        "Mensaje que la tienda le envió a un cliente por WhatsApp:\n"
        f"{inp.text.strip()}\n\n"
        f"Herramientas que consultó antes de escribirlo: {consulted}."
    )


# ── Pisos: lo que nunca se quita ────────────────────────────────────────────


def _floor_jev(inp: Any, rule: Any, jev: Any) -> Any:
    return jev


def _rule_or_jev(inp: Any, rule: Any, jev: Any) -> bool:
    return bool(rule) or bool(jev)


def _closing_invariants(inp: Any, rule: str, jev: str) -> str:
    """Los invariantes del pedido sobre la etiqueta del cierre: pedido
    registrado = COMPRA_EXITOSA; COMPRA_EXITOSA sin pedido no existe (decide
    el LLM); CONFIRMADO_SIN_DATOS sin confirmación de compra = INTERESADO."""
    if inp.order_registered:
        return "COMPRA_EXITOSA"
    if jev == "COMPRA_EXITOSA":
        return ""
    if jev == "CONFIRMADO_SIN_DATOS" and not inp.purchase_confirmed:
        return "INTERESADO"
    return jev


def _jev_and_button_ids(inp: Any, rule: Any, jev: Any) -> tuple[str, ...]:
    """Los botones cuyo id delata un selector (`product.`, `color.`) son piso."""
    return tuple(dict.fromkeys([*jev, *inp.by_id]))


# ── Comparadores: si dos decisiones coinciden ──────────────────────────────


def _bool_eq(a: Any, b: Any) -> bool:
    return bool(a) == bool(b)


def _text_eq(a: Any, b: Any) -> bool:
    return (a or "") == (b or "")


def _truthy_eq(a: Any, b: Any) -> bool:
    return bool(a) == bool(b)


def _key_eq(a: Any, b: Any, *, key: str) -> bool:
    return (a or {}).get(key) == (b or {}).get(key)


def _deferral_and_courtesy_eq(a: Any, b: Any) -> bool:
    def kind(v: Any) -> Any:
        return ((v or {}).get("deferral") or {}).get("kind")

    return kind(a) == kind(b) and bool((a or {}).get("courtesy")) == bool((b or {}).get("courtesy"))


BUILTINS: dict[str, dict[str, Callable[..., Any]]] = {
    "rule": {
        "constant_false": _constant_false,
        "constant_empty": _constant_empty,
        "reply_quantity": _reply_quantity,
        "shipping_zone": _shipping_zone,
        "rule_rejected_titles": _rule_rejected_titles,
        "promises_a_handoff": _promises_a_handoff,
    },
    "state": {
        "customer_message": _customer_message,
        "customer_message_with_window": _customer_message_with_window,
        "text_template": _text_template,
        "proactive_touch": _proactive_touch,
        "abandoned_chat": _abandoned_chat,
        "quantity_reply": _quantity_reply,
        "buttons_message": _buttons_message,
        "sent_message_with_tools": _sent_message_with_tools,
    },
    "floor": {
        "jev": _floor_jev,
        "rule_or_jev": _rule_or_jev,
        "closing_invariants": _closing_invariants,
        "jev_and_button_ids": _jev_and_button_ids,
    },
    "same": {
        "bool_eq": _bool_eq,
        "text_eq": _text_eq,
        "truthy_eq": _truthy_eq,
        "key_eq": _key_eq,
        "deferral_and_courtesy_eq": _deferral_and_courtesy_eq,
    },
}


#: Builtins que se cargan solo cuando una capacidad los pide (módulo aparte:
#: arrastran dependencias que las tools no pueden importar).
LAZY_BUILTINS: dict[str, dict[str, str]] = {
    "rule": {
        "opt_out_text": "src.plugins.chats.agent.sales.decisions.bundled_ingest",
        "reengagement": "src.plugins.chats.agent.sales.decisions.bundled_ingest",
    },
}


def builtin(kind: str, name: str) -> Callable[..., Any]:
    """La implementación del builtin `name` de clase `kind`."""
    found = BUILTINS.get(kind, {}).get(name)
    if found is not None:
        return found
    module = LAZY_BUILTINS.get(kind, {}).get(name)
    if module is None:
        raise KeyError(f"builtin {kind} desconocido: {name!r}")
    return getattr(importlib.import_module(module), name)


def builtin_names() -> dict[str, str]:
    """Nombre → clase de todos los builtins (los cargados y los perezosos)."""
    names = {name: kind for kind, impls in BUILTINS.items() for name in impls}
    names.update({name: kind for kind, impls in LAZY_BUILTINS.items() for name in impls})
    return names


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
        return builtin(kind, ref.builtin)(*args, **ref.params)

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
            answers=answers_from_result(self._spec.questions, result),
            thresholds=dict(thresholds or {}),
            rule=rule,
            inp={name: getattr(inp, name, None) for name in self._table.input_fields},
        )
        return None if value is DOUBT else value

    def floor(self, inp: Any, rule: Any, jev: Any) -> Any:
        return self._call("floor", self._spec.floor, inp, rule, jev)

    def same(self, a: Any, b: Any) -> bool:
        return bool(self._call("same", self._spec.same, a, b))
