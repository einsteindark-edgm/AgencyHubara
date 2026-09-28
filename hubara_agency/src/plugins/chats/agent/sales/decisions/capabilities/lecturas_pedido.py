"""Lecturas del cliente sobre el pedido (diseño v2 §07, familia A, fase F3):
cupón, fuera de catálogo y cantidad. Cada una con la regla de hoy de
respaldo; ver el marco en `capabilities/__init__.py`.

* cupón y fuera de catálogo se preguntan en el ingest (1,5 s: el workflow
  igual espera 1,5 s de silencio antes del turno);
* cantidad, en la activity que arma el prompt (`build_prompt` de ventas),
  con el mismo tiempo máximo.

Sin Temporal al importar: las reglas de hoy viven en `use_cases/` (cuyo
paquete arrastra el ingest y Temporal), así que se importan dentro de cada
regla, cuando ya corren en su proceso.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.plugins.chats.agent.sales.decisions.capabilities.respuestas import YES_NO, choice_of, noul_p
from src.plugins.chats.agent.sales.decisions.context import customer_window
from src.plugins.chats.shared.product_truth import unavailable_terms
from src.sdk.connectorkit import TypedQuestion

_MAX_AGENT_CHARS = 500


def _clip_end(text: str) -> str:
    """Un mensaje largo del asesor se corta por el principio: la pregunta suele ir al final."""
    text = " ".join(text.split())
    return text if len(text) <= _MAX_AGENT_CHARS else "…" + text[-(_MAX_AGENT_CHARS - 1):].lstrip()


# --- cupón --------------------------------------------------------------------


@dataclass(frozen=True)
class CuponEnJuego:
    """El mensaje del cliente (texto efectivo: un audio o una foto ya leídos),
    el metadata de la sesión y lo que el cliente vio antes de este mensaje."""

    metadata: Mapping[str, Any]
    text: str | None
    events: Sequence[Mapping[str, Any]] = ()


class Cupon:
    """«¿Habla del cupón o de sus productos?». Regla: `coupon_in_play` (ante
    la duda dice que sí: cuesta una relectura del cupo y una nota más larga,
    nunca una venta). Solo se pregunta cuando la regla LEE el texto: sin
    cupón, con un cupón de envío o de todo el catálogo la respuesta es la de
    hoy. Jev corrige los dos lados: «código postal» no es el cupón; «¿sigue
    el beneficio?» sí lo es. Valor: bool."""

    name = "cupon"
    timeout_s = 1.5
    thresholds: Mapping[str, float] = {"yes": 0.70, "no": 0.15}

    def rule(self, inp: CuponEnJuego) -> bool:
        from src.plugins.chats.agent.sales.use_cases.coupons import coupon_in_play

        return coupon_in_play(inp.metadata, inp.text)  # type: ignore[arg-type]

    def ask(self, inp: CuponEnJuego) -> tuple[str, list[TypedQuestion]] | None:
        from src.plugins.chats.agent.sales.use_cases.coupons import coupon_talk_subject

        text = (inp.text or "").strip()
        subject = coupon_talk_subject(inp.metadata)  # type: ignore[arg-type]
        if not text or subject is None:
            return None
        code, titles = subject
        lines = [f"CUPÓN APLICADO EN EL PEDIDO: {code}" + (f" — vale en: {', '.join(titles)}" if titles else "")]
        window = customer_window(list(inp.events), burst_wamids=set(), burst_size=0)
        if window.lines:
            lines += ["CONTEXTO — lo que el cliente vio antes de este mensaje", *window.lines]
        lines += ["ESTE MENSAJE DEL CLIENTE", f"[1] {text}"]
        return "\n".join(lines), [
            TypedQuestion(
                id="cupon.habla", kind="noul",
                text="¿En ESTE MENSAJE el cliente habla del cupón, del descuento o de alguno de los productos del cupón?",
                criteria=YES_NO,
            )
        ]

    def decide(self, inp: CuponEnJuego, result: Any, rule: bool, thresholds: Mapping[str, float]) -> bool | None:
        th = {**self.thresholds, **thresholds}
        p = noul_p(result, "cupon.habla")
        if p is None:
            return None
        if p >= th["yes"]:
            return True
        if p <= th["no"]:
            return False
        return None

    def floor(self, inp: CuponEnJuego, rule: bool, jev: bool) -> bool:
        return jev

    def same(self, a: bool, b: bool) -> bool:
        return bool(a) == bool(b)


# --- fuera de catálogo --------------------------------------------------------


@dataclass(frozen=True)
class PedidoDelCliente:
    """Lo que escribió o mostró el cliente (la foto reentra como texto) y los
    productos del catálogo."""

    text: str
    products: Sequence[Any] = ()


class FueraDeCatalogo:
    """El regex de hoy (`unavailable_terms`) propone los términos que el
    cliente pide y no existen; por cada uno, «¿es algo que el cliente pide o
    muestra?». Jev solo QUITA candidatos falsos (una ciudad, una fecha, una
    persona), y solo con certeza (p ≤ `no`): nunca inventa un término (el
    piso es quedarse con los de la regla). La nota la arma el código de hoy
    con los que quedan. Valor: la lista de términos."""

    name = "fuera_de_catalogo"
    timeout_s = 1.5
    thresholds: Mapping[str, float] = {"no": 0.15}

    @staticmethod
    def _qid(k: int) -> str:
        return f"fuera_de_catalogo.termino_{k}"

    def rule(self, inp: PedidoDelCliente) -> list[str]:
        return unavailable_terms(inp.text, list(inp.products))

    def ask(self, inp: PedidoDelCliente) -> tuple[str, list[TypedQuestion]] | None:
        terms = self.rule(inp)
        if not terms:
            return None
        state = f"Mensaje del cliente (lo que mostró en una foto va entre corchetes):\n[1] {inp.text.strip()}"
        return state, [
            TypedQuestion(
                id=self._qid(k), kind="noul",
                text=(
                    f"En el mensaje, ¿«{term}» es algo que el cliente pide o muestra: un producto, una forma, "
                    "un envase, un tamaño o un diseño?"
                ),
                criteria={
                    "true": "sí, lo pide o lo muestra",
                    "false": "no: es otra cosa (un lugar, una fecha, una persona, un medio de pago, un material o una forma de hablar)",
                },
            )
            for k, term in enumerate(terms, 1)
        ]

    def decide(self, inp: PedidoDelCliente, result: Any, rule: list[str], thresholds: Mapping[str, float]) -> list[str] | None:
        th = {**self.thresholds, **thresholds}
        ps = [noul_p(result, self._qid(k)) for k in range(1, len(rule) + 1)]
        if all(p is None for p in ps):
            return None
        return [term for term, p in zip(rule, ps) if p is None or p > th["no"]]

    def floor(self, inp: PedidoDelCliente, rule: list[str], jev: list[str]) -> list[str]:
        kept = set(jev or ())
        return [term for term in rule if term in kept]

    def same(self, a: list[str], b: list[str]) -> bool:
        return list(a or ()) == list(b or ())


# --- cantidad ------------------------------------------------------------------


@dataclass(frozen=True)
class RespuestaDeCantidad:
    """La última burbuja que el cliente vio del asesor, su respuesta y si hay
    dónde escribir una cantidad (`quantity_slot_open`: si no, ninguna lectura
    escribiría nada y no se le pregunta a Jev)."""

    last_agent_text: str | None
    text: str | None
    open_slot: bool = True


class Cantidad:
    """«¿El asesor preguntó cuántas?» y «¿Qué cantidad dio?» {1…20, otra,
    ninguna}. Regla: `agent_asked_quantity` + `parse_leading_quantity`
    (`read_reply_quantity`). Capturar exige certeza (una cantidad equivocada
    en el pedido es peor que volver a preguntar); «otra» o la duda dejan la
    regla. El valor sale de la lista cerrada y lo escribe el código con sus
    compuertas (`apply_reply_quantity`). Valor: `{"cantidad": int | None}`."""

    name = "cantidad"
    timeout_s = 1.5
    thresholds: Mapping[str, float] = {"asked": 0.85, "not_asked": 0.15, "quantity": 0.85, "none": 0.70}
    MAX = 20

    def rule(self, inp: RespuestaDeCantidad) -> dict[str, int | None]:
        from src.plugins.chats.agent.sales.use_cases.quantity_capture import read_reply_quantity

        return {"cantidad": read_reply_quantity(inp.last_agent_text, inp.text)}

    def ask(self, inp: RespuestaDeCantidad) -> tuple[str, list[TypedQuestion]] | None:
        text = (inp.text or "").strip()
        agent = (inp.last_agent_text or "").strip()
        if not inp.open_slot or not text or not agent or text.startswith("["):
            return None
        state = "\n".join(
            ["ÚLTIMO MENSAJE DEL ASESOR", f"[asesor] {_clip_end(agent)}", "RESPUESTA DEL CLIENTE", f"[1] {text}"]
        )
        options = {str(n): "1 unidad" if n == 1 else f"{n} unidades" for n in range(1, self.MAX + 1)}
        options["otra"] = f"otra cantidad (más de {self.MAX}, o no dice cuántas exactamente)"
        options["ninguna"] = "no dice una cantidad de unidades"
        return state, [
            TypedQuestion(
                id="cantidad.pregunto", kind="noul",
                text="¿El último mensaje del asesor le pregunta al cliente cuántas unidades quiere?", criteria=YES_NO,
            ),
            TypedQuestion(
                id="cantidad.dio", kind="choice",
                text="¿Qué cantidad de unidades da el cliente en su respuesta?", criteria=options,
            ),
        ]

    def decide(
        self, inp: RespuestaDeCantidad, result: Any, rule: dict[str, int | None], thresholds: Mapping[str, float]
    ) -> dict[str, int | None] | None:
        th = {**self.thresholds, **thresholds}
        asked = noul_p(result, "cantidad.pregunto")
        choice, p = choice_of(result, "cantidad.dio")
        if asked is None:
            return None
        if asked <= th["not_asked"]:
            return {"cantidad": None}  # el asesor no la preguntó (compuerta 1 de hoy)
        if asked < th["asked"] or choice is None:
            return None
        if choice == "ninguna" and p >= th["none"]:
            return {"cantidad": None}
        if choice.isdigit() and 1 <= int(choice) <= self.MAX and p >= th["quantity"]:
            return {"cantidad": int(choice)}
        return None  # «otra» o poca certeza: decide la regla

    def floor(self, inp: RespuestaDeCantidad, rule: dict[str, int | None], jev: dict[str, int | None]) -> dict[str, int | None]:
        return jev

    def same(self, a: dict[str, int | None], b: dict[str, int | None]) -> bool:
        return (a or {}).get("cantidad") == (b or {}).get("cantidad")
