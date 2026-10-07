"""Capacidades desde el paquete de decisión (PAQUETES_DE_DECISION.md, fase F1).

El paquete `chats/shared/decisions/bundles/ventas/` (YAML certificado) trae las preguntas,
los umbrales y la tabla de decisión de cada capacidad; este módulo pone lo
que sigue siendo código —los builtins: reglas, constructores de estado,
opciones de la entrada, vistas, pisos y comparadores— y arma con los dos un
objeto con la forma de `Capability` (`rule`/`ask`/`decide`/`floor`/`same`),
que corre igual que las clases por el mismo `decide()` del motor.

Los builtins que arrastran el ingest, `use_cases/`, el catálogo o el egreso
viven en `bundled_ingest.py`, `bundled_catalog.py`, `bundled_egress.py` y
`bundled_parts.py` y se cargan por nombre (`LAZY_BUILTINS`) solo cuando una
capacidad los pide: las tools llegan acá por `guards` y no pueden importar
Temporal.

Los builtins que existen están declarados en `bundles/builtins.yaml` (lo
único que lee el certificador); `test_decisions_bundle_parity.py` verifica
que catálogo y código coincidan, y que cada capacidad migrada decida
EXACTAMENTE como su clase.

Sin Temporal: lo usan el ingest, las tools y las activities.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

import structlog

from src.plugins.chats.agent.sales.decisions.capabilities import BUNDLE_FAULT
from src.plugins.chats.agent.sales.decisions.context import customer_window
from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from src.sdk.connectorkit import TypedQuestion
from src.sdk.decisionkit import DOUBT, CompiledCapability, answers_from_result

__all__ = ["BUNDLES_DIR", "CATALOG_PATH", "BUILTINS", "LAZY_BUILTINS", "BundledCapability", "builtin", "builtin_names"]


logger = structlog.get_logger()

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


def _no_items(inp: Any) -> tuple[Any, ...]:
    return ()  # hoy nada se marca: decide Jev o nadie


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


# ── Ítems: la lista sobre la que se decide ítem por ítem ───────────────────


def _items_of(inp: Any, *, field: str) -> list[dict[str, Any]]:
    """Un ítem `{text}` por cada texto del campo `field` de la entrada."""
    return [{"text": str(text)} for text in getattr(inp, field)]


def _numbered_items(inp: Any, *, header: str, max: int, items: list[dict[str, Any]] | None = None) -> str | None:  # noqa: A002
    """«<header>» y los ítems numerados ([1] …). Sin ítems, o con más de
    `max`, no se pregunta (decide la regla)."""
    parts = [item["text"] for item in items or ()]
    if not parts or len(parts) > max:
        return None
    return header + "\n" + "\n".join(f"[{i}] {part}" for i, part in enumerate(parts, 1))


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


def _jev_without_question_mark(inp: Any, rule: Any, jev: Any) -> bool:
    """Un mensaje con signo de pregunta nunca se absorbe (acuse)."""
    text = str(getattr(inp, "text", None) or "")
    return bool(jev) and "?" not in text and "¿" not in text


def _rule_if_same_key(inp: Any, rule: Any, jev: Any, *, key: str) -> Any:
    """Si Jev coincide con la regla en `key`, queda el detalle de la regla."""
    return rule if (rule or {}).get(key) == (jev or {}).get(key) else jev


def _rule_then_jev(inp: Any, rule: Any, jev: Any) -> tuple[Any, ...]:
    """Lo de la regla nunca se quita; Jev solo suma (sin repetir, en orden)."""
    return tuple(dict.fromkeys([*rule, *jev]))


def _rule_order_subset(inp: Any, rule: Any, jev: Any) -> list[Any]:
    """Jev solo quita: queda lo de la regla que Jev dejó, en el orden de la regla."""
    kept = set(jev or ())
    return [x for x in rule if x in kept]


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


def _first_eq(a: Any, b: Any) -> bool:
    return (a or [None])[0] == (b or [None])[0]


def _set_eq(a: Any, b: Any) -> bool:
    return set(a or ()) == set(b or ())


def _tuple_eq(a: Any, b: Any) -> bool:
    return tuple(a or ()) == tuple(b or ())


def _sorted_eq(a: Any, b: Any) -> bool:
    return tuple(sorted(a or ())) == tuple(sorted(b or ()))


BUILTINS: dict[str, dict[str, Callable[..., Any]]] = {
    "rule": {
        "constant_false": _constant_false,
        "constant_empty": _constant_empty,
        "reply_quantity": _reply_quantity,
        "shipping_zone": _shipping_zone,
        "rule_rejected_titles": _rule_rejected_titles,
        "promises_a_handoff": _promises_a_handoff,
        "no_items": _no_items,
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
        "numbered_items": _numbered_items,
    },
    "items": {
        "items_of": _items_of,
    },
    "floor": {
        "jev": _floor_jev,
        "rule_or_jev": _rule_or_jev,
        "closing_invariants": _closing_invariants,
        "jev_and_button_ids": _jev_and_button_ids,
        "jev_without_question_mark": _jev_without_question_mark,
        "rule_if_same_key": _rule_if_same_key,
        "rule_then_jev": _rule_then_jev,
        "rule_order_subset": _rule_order_subset,
    },
    "same": {
        "bool_eq": _bool_eq,
        "text_eq": _text_eq,
        "truthy_eq": _truthy_eq,
        "key_eq": _key_eq,
        "deferral_and_courtesy_eq": _deferral_and_courtesy_eq,
        "first_eq": _first_eq,
        "set_eq": _set_eq,
        "tuple_eq": _tuple_eq,
        "sorted_eq": _sorted_eq,
    },
}


#: Builtins que se cargan solo cuando una capacidad los pide (módulo aparte:
#: arrastran dependencias que las tools no pueden importar).
_INGEST = "src.plugins.chats.agent.sales.decisions.bundled_ingest"
_CATALOG = "src.plugins.chats.agent.sales.decisions.bundled_catalog"
_EGRESS = "src.plugins.chats.agent.sales.decisions.bundled_egress"
_PARTS = "src.plugins.chats.agent.sales.decisions.bundled_parts"
LAZY_BUILTINS: dict[str, dict[str, str]] = {
    "rule": {
        "opt_out_text": _INGEST,
        "reengagement": _INGEST,
        "purchase_signal": _INGEST,
        "closing_ack": _INGEST,
        "coupon_in_play": _INGEST,
        "category_of_query": _CATALOG,
        "color_family": _CATALOG,
        "order_item_for_values": _CATALOG,
        "named_titles": _CATALOG,
        "enumerated_variants": _CATALOG,
        "admin_leak": _EGRESS,
        "first_contact_greeting": _EGRESS,
        "breaks_persona": _PARTS,
        "unavailable_terms_rule": _PARTS,
        "model_preamble": _PARTS,
        "admin_leak_per_part": _PARTS,
        "salvage": _PARTS,
        "portavelas_notice": _PARTS,
    },
    "state": {
        "purchase_context": _INGEST,
        "message_after_close": _INGEST,
        "coupon_talk": _INGEST,
        "category_request": _CATALOG,
        "color_request": _CATALOG,
        "order_item_data": _CATALOG,
        "chat_and_catalog": _CATALOG,
        "text_with_lists": _CATALOG,
        "greeting_texts": _EGRESS,
        "leading_sentences_state": _PARTS,
        "conversation_for_slots": _PARTS,
        "customer_order_text": _PARTS,
    },
    "view": {
        "purchase_window": _INGEST,
        "reply_gap": _INGEST,
        "enumeration_found": _CATALOG,
    },
    "items": {
        "paragraphs": _PARTS,
        "sentences": _PARTS,
        "leading_sentences": _PARTS,
        "order_slot_values": _PARTS,
        "unavailable_term_items": _PARTS,
    },
    "options": {
        "catalog_categories": _CATALOG,
        "product_colors": _CATALOG,
        "accepting_items": _CATALOG,
        "catalog_titles": _CATALOG,
    },
    "floor": {
        "no_enumeration_with_combinations": _CATALOG,
        "persona_floor": _PARTS,
        "strong_preamble": _PARTS,
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
    """Una capacidad del paquete con la forma de `Capability`.

    `builtins`: de dónde salen los builtins que pide el paquete (clase,
    nombre) → función. Por defecto los de ventas; otro paquete con su propio
    catálogo (la App Operador, `chats/shared/operator/decisions`) pasa los
    suyos y corre por el MISMO `decide()` del motor."""

    def __init__(self, table: CompiledCapability, builtins: Callable[[str, str], Callable[..., Any]] | None = None) -> None:
        spec = table.spec
        self._table = table
        self._spec = spec
        #: None = los de ventas (`builtin`, buscado al llamar: las pruebas lo sustituyen).
        self._builtins = builtins
        #: El interruptor (proveedor, panel, traza): el de la capacidad, o el
        #: de la que esta variante pregunta de otra forma (`control:`).
        self.name = table.control
        #: La variante (`destinatario_oracion`) si pregunta por otro control; "" si es el control.
        self.variant = spec.capability if spec.capability != table.control else ""
        self.thresholds = dict(spec.thresholds)
        #: `id@versión` del paquete con el que se decide.
        self.bundle = table.bundle

    def _call(self, kind: str, ref: Any, *args: Any, **extra: Any) -> Any:
        resolve = self._builtins or builtin
        return resolve(kind, ref.builtin)(*args, **ref.params, **extra)

    def _inp(self, inp: Any) -> dict[str, Any]:
        """Lo que las condiciones leen como `inp`: los campos declarados y los
        que deriva la vista."""
        out = {name: getattr(inp, name, None) for name in self._table.input_fields}
        if self._spec.view is not None:
            out.update(self._call("view", self._spec.view, inp))
        return out

    def _options(self, inp: Any) -> dict[str, dict[str, tuple[str, Any]]]:
        """`{pregunta: {opción: (etiqueta, valor)}}` de las preguntas con opciones de la entrada."""
        return {
            q.id: self._call("options", q.options, inp, reserved=tuple(q.criteria))
            for q in self._spec.questions if q.options is not None
        }

    def _items(self, inp: Any) -> list[dict[str, Any]] | None:
        """La lista de una capacidad que decide ítem por ítem (None si no lo es)."""
        if self._spec.items is None:
            return None
        return list(self._call("items", self._spec.items, inp))

    def rule(self, inp: Any) -> Any:
        return self._call("rule", self._spec.rule, inp)

    def ask(self, inp: Any) -> tuple[str, list[TypedQuestion]] | None:
        """La pregunta a Jev, o None (decide la regla). Un builtin que falla
        nunca tumba a quien pregunta: no se pregunta y queda el error
        (premortem 2026-10-02: la guarda caía con un TypeError)."""
        try:
            return self._ask(inp)
        except Exception as exc:  # noqa: BLE001 — decide la regla
            self._failed("ask", exc)
            return None

    def _failed(self, where: str, exc: Exception) -> None:
        logger.error(
            "decisions.builtin_failed", bundle=self.bundle, capability=self.name, where=where,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )

    def _ask(self, inp: Any) -> tuple[str, list[TypedQuestion]] | None:
        if self._spec.state is None:
            return None
        items = self._items(inp)
        # Con ítems, el estado los recibe (numerarlos, ver si hay qué preguntar).
        state = self._call("state", self._spec.state, inp, **({} if items is None else {"items": items}))
        if state is None:
            return None
        fields = self._inp(inp)
        options = self._options(inp)
        questions = [
            TypedQuestion(
                id=q.id, kind=q.kind, text=q.text,
                # Las de la entrada primero; las fijas (ambiguo, ninguno) al final.
                criteria={**{key: label for key, (label, _value) in options.get(q.id, {}).items()}, **q.criteria},
            )
            for q in self._table.questions_for(fields)
        ]
        questions += [
            TypedQuestion(id=q.id, kind=q.kind, text=q.text, criteria=dict(q.criteria))
            for q in self._table.each_questions(items or (), fields)
        ]
        if not questions:
            return None  # ningún ítem que preguntar
        return state, questions

    def decide(self, inp: Any, result: Any, rule: Any, thresholds: Any) -> Any:
        """Lo que decide la tabla con las respuestas de Jev; None = duda. Un
        builtin que falla (opciones, vista, ítems) o una fila con un error es
        BUNDLE_FAULT (decide la regla con `reason=bundle_error`), nunca una
        excepción ni una «duda» de Jev."""
        try:
            return self._decide(inp, result, rule, thresholds)
        except Exception as exc:  # noqa: BLE001 — decide la regla
            self._failed("decide", exc)
            return BUNDLE_FAULT

    def _decide(self, inp: Any, result: Any, rule: Any, thresholds: Any) -> Any:
        options = {qid: {key: value for key, (_label, value) in opts.items()} for qid, opts in self._options(inp).items()}
        fields = self._inp(inp)
        items = self._items(inp)
        # Se leen también las respuestas de los ítems que no se preguntaron
        # (si llegaron, cuentan: igual que las clases).
        questions = [*self._spec.questions, *self._table.each_questions(items or (), fields, all_items=True)]
        decision = self._table.decide_explained(
            answers=answers_from_result(questions, result),
            thresholds=dict(thresholds or {}),
            rule=rule,
            inp=fields,
            options=options,
            items=items,
        )
        if decision.error is not None:
            return BUNDLE_FAULT  # el motor ya dejó el warning con el paquete y la fila
        return None if decision.value is DOUBT else decision.value

    def floor(self, inp: Any, rule: Any, jev: Any) -> Any:
        return self._call("floor", self._spec.floor, inp, rule, jev)

    def same(self, a: Any, b: Any) -> bool:
        return bool(self._call("same", self._spec.same, a, b))
