"""Capacidades de lectura del cliente en el ingest (diseño v2 §07, familia A):
compra, retoma, baja y acuse. Cada una con la regla de hoy de respaldo; ver
el marco en `capabilities/__init__.py`.

Entrada común: `Inbound` (`decisions/readings.py`). Si el texto lo escribió la
visión (la descripción de una foto), no es del cliente: ninguna lectura de
texto lo lee (bug: un comprobante de pago pausaba la reactivación una
semana).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.plugins.chats.agent.sales.decisions.context import customer_window, order_facts
from src.plugins.chats.agent.sales.decisions.plan import answer_of
from src.plugins.chats.shared.purchase_signals import classify_inbound_purchase_signal
from src.sdk.connectorkit import TypedQuestion

_YES_NO = {"true": "sí", "false": "no"}


def _customer_text(inp: Any) -> str | None:
    text = getattr(inp, "text", None)
    if getattr(inp, "synthetic", False) or not isinstance(text, str) or not text.strip():
        return None
    return text


def _p(result: Any, qid: str) -> float | None:
    p = getattr(answer_of(result, qid), "p", None)
    return float(p) if isinstance(p, (int, float)) else None


def _choice(result: Any, qid: str) -> tuple[str | None, float]:
    answer = answer_of(result, qid)
    choice = getattr(answer, "choice", None)
    if not choice:
        return None, 0.0
    probs = dict(getattr(answer, "probs", ()) or ())
    p = probs.get(choice, getattr(answer, "confidence", None))
    return choice, float(p) if isinstance(p, (int, float)) else 0.0


def _context_state(inp: Any, header: str) -> tuple[str, Any]:
    """El `state` con lo que el cliente vio antes de este mensaje y los hechos
    del pedido (motor F1), y la ventana (para saber qué se le preguntó)."""
    window = customer_window(list(getattr(inp, "events", ()) or ()), burst_wamids=set(), burst_size=0)
    lines: list[str] = []
    if window.lines:
        lines += ["CONTEXTO — lo que el cliente vio antes de este mensaje", *window.lines]
    facts = order_facts(getattr(inp, "metadata", {}) or {}, stage=getattr(inp, "stage", "etapa_descubrimiento"))
    lines += ["HECHOS DEL PEDIDO", *facts, header, f"[1] {inp.text.strip()}"]
    return "\n".join(lines), window


class Compra:
    """«¿Qué hace el cliente con la compra?», con lo que le preguntó el asesor
    a la vista. Queda una sola definición de «compra confirmada»: la de esta
    capacidad (valor `[tipo, fuente]` de `classify_inbound_purchase_signal`).

    * El carrito y el botón «Confirmar» son estructurales: los lee el código.
    * Confirma solo con p ≥ `confirm` y la pregunta de compra a la vista
      (p ≥ `purchase_question`, o la tarjeta de confirmación como lo último
      que vio el cliente).
    * Retira un «sí» que respondía otra cosa: p ≤ `retract` de que se le
      preguntó por la compra (el costo de equivocarse es una pregunta más).
    """

    name = "compra"
    timeout_s = 1.5
    thresholds: Mapping[str, float] = {"choice": 0.70, "confirm": 0.85, "purchase_question": 0.85, "retract": 0.20}

    _OPTIONS = {
        "confirma": "confirma que compra o acepta el pedido",
        "aplaza": "aplaza la compra (luego, otro día, voy en camino)",
        "rechaza": "dice que no quiere comprar",
        "pregunta": "hace una pregunta",
        "da_datos": "da datos de envío o de pago",
        "elige": "elige un producto, color, aroma, diseño o cantidad",
        "se_despide": "se despide o agradece",
        "otro": "otra cosa",
    }

    def rule(self, inp: Any) -> list[Any]:
        kind, source = classify_inbound_purchase_signal(
            _customer_text(inp), interactive=getattr(inp, "interactive", None), order=getattr(inp, "order", None)
        )
        return [kind, source]

    def ask(self, inp: Any) -> tuple[str, list[TypedQuestion]] | None:
        text = _customer_text(inp)
        if text is None or self.rule(inp)[1] != "text":
            return None
        state, window = _context_state(inp, "ESTE MENSAJE DEL CLIENTE")
        questions = [
            TypedQuestion(
                id="compra.que_hace", kind="choice",
                text="¿Qué hace el cliente con la compra en ESTE MENSAJE?", criteria=dict(self._OPTIONS),
            )
        ]
        if window.lines and window.bot_asked_known is None:
            questions.append(
                TypedQuestion(
                    id="compra.pregunta_compra", kind="noul",
                    text="¿El último mensaje del asesor en el CONTEXTO le pregunta al cliente si confirma la compra o el pedido?",
                    criteria=_YES_NO,
                )
            )
        return state, questions

    def decide(self, inp: Any, result: Any, rule: list[Any], thresholds: Mapping[str, float]) -> list[Any] | None:
        th = {**self.thresholds, **thresholds}
        choice, p = _choice(result, "compra.que_hace")
        if choice is None or p < th["choice"]:
            return None
        if choice == "aplaza":
            return ["deferral", "text"]
        if choice != "confirma":
            return [None, "text"]
        window = customer_window(list(getattr(inp, "events", ()) or ()), burst_wamids=set(), burst_size=0)
        if window.bot_asked_known is not None:
            asked = 1.0 if window.bot_asked_known == "confirmar_compra" else 0.0
        elif not window.lines:
            return None  # sin contexto no se sabe qué respondió: decide la regla
        else:
            asked = _p(result, "compra.pregunta_compra")
            if asked is None:
                return None
        if p >= th["confirm"] and asked >= th["purchase_question"]:
            return ["affirmation", "text"]
        if asked <= th["retract"]:
            return [None, "text"]
        return None

    def floor(self, inp: Any, rule: list[Any], jev: list[Any]) -> list[Any]:
        return jev

    def same(self, a: list[Any], b: list[Any]) -> bool:
        return (a or [None])[0] == (b or [None])[0]


class Retoma:
    """«¿Dice que retomará más adelante?» y «¿es solo una cortesía?». La fecha
    la sigue calculando el código (`parse_reengagement_deferral`); Jev decide
    SI hay aplazamiento: veta uno que la regla leyó (p ≤ `no`) o, sin fecha
    del código, uno en que el cliente dice que él escribe otro día (pausa
    abierta, como la regla de hoy para «yo les escribo cuando…»).
    Valor: `{"deferral": {"kind", "until_ms"} | None, "courtesy": bool}`."""

    name = "retoma"
    timeout_s = 1.5
    thresholds: Mapping[str, float] = {"yes": 0.85, "no": 0.15}

    def rule(self, inp: Any) -> dict[str, Any]:
        from src.sdk.messagingkit import is_courtesy_text, parse_reengagement_deferral

        text = _customer_text(inp)
        parsed = parse_reengagement_deferral(text, inp.now_ms, inp.tz) if text else None
        return {
            "deferral": {"kind": parsed.kind, "until_ms": parsed.until_ms} if parsed else None,
            "courtesy": bool(text) and is_courtesy_text(text),
        }

    def ask(self, inp: Any) -> tuple[str, list[TypedQuestion]] | None:
        text = _customer_text(inp)
        if text is None:
            return None
        state = f"Mensaje del cliente:\n[1] {text.strip()}"
        return state, [
            TypedQuestion(
                id="retoma.aplaza", kind="noul",
                text="¿En este mensaje el cliente dice que retomará la conversación o la compra más adelante (otro día u otra fecha)?",
                criteria=_YES_NO,
            ),
            TypedQuestion(
                id="retoma.cuando", kind="choice", text="¿Para cuándo lo deja?",
                criteria={
                    "hoy": "hoy mismo, más tarde",
                    "otro_dia_con_fecha": "otro día, con fecha o momento (mañana, el jueves, la otra semana, la quincena)",
                    "otro_dia_sin_fecha": "otro día, sin decir cuándo (yo les escribo cuando…)",
                    "no_aplica": "no está aplazando",
                },
            ),
            TypedQuestion(
                id="retoma.cortesia", kind="noul",
                text="¿El mensaje es solo una cortesía (gracias, ok, un emoji) sin nada más?", criteria=_YES_NO,
            ),
        ]

    def decide(self, inp: Any, result: Any, rule: dict[str, Any], thresholds: Mapping[str, float]) -> dict[str, Any] | None:
        from src.sdk.messagingkit import DEFERRAL_KIND_OPEN, OPEN_DEFERRAL_MS

        th = {**self.thresholds, **thresholds}
        aplaza, cortesia = _p(result, "retoma.aplaza"), _p(result, "retoma.cortesia")
        if aplaza is None or cortesia is None:
            return None
        courtesy = True if cortesia >= th["yes"] else False if cortesia <= th["no"] else bool(rule.get("courtesy"))
        if aplaza <= th["no"]:
            return {"deferral": None, "courtesy": courtesy}
        if aplaza < th["yes"]:
            return None
        if rule.get("deferral"):
            return {"deferral": rule["deferral"], "courtesy": courtesy}
        when, _ = _choice(result, "retoma.cuando")
        if when == "otro_dia_sin_fecha":
            return {"deferral": {"kind": DEFERRAL_KIND_OPEN, "until_ms": inp.now_ms + OPEN_DEFERRAL_MS}, "courtesy": courtesy}
        return {"deferral": None, "courtesy": courtesy}  # «más tarde» del mismo día no pausa (regla de hoy)

    def floor(self, inp: Any, rule: dict[str, Any], jev: dict[str, Any]) -> dict[str, Any]:
        return jev

    def same(self, a: dict[str, Any], b: dict[str, Any]) -> bool:
        kind = lambda v: ((v or {}).get("deferral") or {}).get("kind")  # noqa: E731
        return kind(a) == kind(b) and bool((a or {}).get("courtesy")) == bool((b or {}).get("courtesy"))


class Baja:
    """«¿Pide dejar de recibir mensajes o promociones?» (piso legal). Solo se
    pregunta con una promoción reciente (la condición de hoy). La frase
    explícita de hoy es PISO: nunca se quita; Jev solo agrega bajas que la
    regla no ve («no me escriban», «elimínenme de la lista»). Achicar el piso
    es una decisión legal del operador (tras la sombra)."""

    name = "baja"
    timeout_s = 1.5
    thresholds: Mapping[str, float] = {"yes": 0.85, "no": 0.15}

    def rule(self, inp: Any) -> bool:
        from src.sdk.messagingkit import is_opt_out_text

        text = _customer_text(inp)
        return bool(text) and is_opt_out_text(text)

    def ask(self, inp: Any) -> tuple[str, list[TypedQuestion]] | None:
        text = _customer_text(inp)
        if text is None:
            return None
        state = f"Mensaje del cliente, que hace poco recibió una promoción de la tienda:\n[1] {text.strip()}"
        return state, [
            TypedQuestion(
                id="baja.pide", kind="noul",
                text="¿El cliente pide dejar de recibir mensajes o promociones de la tienda?", criteria=_YES_NO,
            )
        ]

    def decide(self, inp: Any, result: Any, rule: bool, thresholds: Mapping[str, float]) -> bool | None:
        th = {**self.thresholds, **thresholds}
        p = _p(result, "baja.pide")
        if p is None:
            return None
        if p >= th["yes"]:
            return True
        if p <= th["no"]:
            return False
        return None

    def floor(self, inp: Any, rule: bool, jev: bool) -> bool:
        return bool(rule) or bool(jev)

    def same(self, a: bool, b: bool) -> bool:
        return bool(a) == bool(b)


class Acuse:
    """«¿Es solo un acuse o una cortesía a la despedida?» (run 4cb3a34f,
    #379). El agente ya se despidió y cerró el episodio; un acuse («☺️👍»,
    «gracias», «igualmente») queda en el chat y no despierta al agente. Lo
    estructural lo decide el código antes de preguntar (episodio cerrado, una
    reacción o un sticker, una plantilla posterior): esta capacidad solo lee
    el TEXTO, con lo que el cliente vio antes a la vista.

    Sesgo a la seguridad (tragarse una pregunta real es peor que un saludo de
    más): absorbe solo con p ≥ `absorb`; si Jev duda, despierta al bot. Piso:
    un mensaje con signo de pregunta nunca se absorbe. Sin contexto (no se
    ve la despedida) o con un texto que no escribió el cliente, decide la
    regla de hoy (`is_closing_ack`). Valor: bool (True = no despierta)."""

    name = "acuse"
    timeout_s = 1.5
    thresholds: Mapping[str, float] = {"absorb": 0.90}

    def rule(self, inp: Any) -> bool:
        # La regla de hoy lee el texto del mensaje tal como llega (una
        # descripción de la visión nunca calza con sus palabras de cortesía).
        from src.plugins.chats.agent.sales.use_cases.closing_ack import is_closing_ack

        return is_closing_ack(getattr(inp, "text", None))

    def ask(self, inp: Any) -> tuple[str, list[TypedQuestion]] | None:
        text = _customer_text(inp)
        if text is None:
            return None
        window = customer_window(list(getattr(inp, "events", ()) or ()), burst_wamids=set(), burst_size=0)
        if not window.lines:
            return None  # sin la despedida a la vista no se sabe a qué responde
        # Encabezado neutro: el episodio pudo cerrarse por inactividad, sin
        # despedida; si la despedida no está a la vista, Jev dice que no.
        state = "\n".join([
            "CONTEXTO — lo que el cliente vio antes de este mensaje (esa conversación ya se había cerrado)",
            *window.lines,
            "MENSAJE DEL CLIENTE",
            f"[1] {text.strip()}",
        ])
        return state, [
            TypedQuestion(
                id="acuse.solo_cortesia", kind="noul",
                text=(
                    "¿Este mensaje del cliente es solo un acuse de recibo o una cortesía a la despedida del asesor "
                    "(gracias, ok, un emoji, una bendición), sin preguntar, pedir, responder ni contar nada nuevo?"
                ),
                criteria=_YES_NO,
            )
        ]

    def decide(self, inp: Any, result: Any, rule: bool, thresholds: Mapping[str, float]) -> bool | None:
        th = {**self.thresholds, **thresholds}
        p = _p(result, "acuse.solo_cortesia")
        if p is None:
            return None  # sin respuesta: decide la regla
        return p >= th["absorb"]  # con duda, despierta al bot

    def floor(self, inp: Any, rule: bool, jev: bool) -> bool:
        text = str(getattr(inp, "text", None) or "")
        return bool(jev) and "?" not in text and "¿" not in text

    def same(self, a: bool, b: bool) -> bool:
        return bool(a) == bool(b)
