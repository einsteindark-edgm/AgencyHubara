"""Lo que el bot le va a decir al cliente contra lo que el sistema sabe (puro).

Incidente 2026-10-13 (bot V2): «las de deseos cuadradas vienen en Chanel,
Drakar y Ylang Ylang» (aromas de otro producto) y, en la misma conversación,
«las cuadradas en $23.000» (el catálogo dice $25.000). El código conoce los
hechos —el catálogo, el pedido, las reglas de pago, los cupones—; Jev no
consulta nada. Este módulo es el contrato entre los dos: `ReplyFacts` lo que
el código sabe (lo arma `tools/fact_check.py` UNA vez por texto), `Findings`
lo que las capacidades del paquete encontraron que no coincide, y
`fact_message` el único mensaje con que el texto vuelve al modelo.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from typing import Any

from src.plugins.chats.agent.sales.pricing import catalog_prices, format_cop
from src.plugins.chats.agent.sales.use_cases.product_options import (
    aromas_of,
    colors_of,
    correction_tail,
    option_problem,
)


@dataclass(frozen=True)
class ReplyFacts:
    """Lo que el código sabe para revisar UN texto del bot."""

    #: Los productos de los que habla el texto (`product_options.focus_products`).
    focus: tuple[Any, ...] = ()
    #: Los productos contra cuyos precios se mira el texto: el foco o, si el
    #: texto no habla de ninguno ni hay pedido, los de la charla (lo que el bot
    #: acaba de mostrar, incidente 2026-10-26). Vacío = los del foco.
    price_focus: tuple[Any, ...] = ()
    #: Los aromas y colores de todo el catálogo (donde la regla busca).
    aromas: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    #: La ficha de cada producto en foco con presentaciones para escoger.
    sheets: tuple[str, ...] = ()
    #: La de cada producto en foco de una sola presentación.
    single: tuple[str, ...] = ()
    #: Los precios que valen para el foco (unidad de cada variante, en vivo y
    #: con el descuento aplicado) y los montos de política (mínimo de contra
    #: entrega, tarifas de envío).
    prices: tuple[int, ...] = ()
    policy: tuple[int, ...] = ()
    #: El pago: subtotal de productos del borrador (None = no se sabe), el
    #: mínimo de contra entrega y los recargos del link de pago.
    subtotal: int | None = None
    #: Cómo se arma ese subtotal: «2 × $25.000» (o «2 × $25.000 + 1 × $16.000»).
    order_breakdown: str | None = None
    cod_min: int = 0
    surcharges: tuple[str, ...] = ()
    #: El pedido según OrderFacts (None = no hay pedido o no se miró).
    order: str | None = None
    stage: str | None = None
    pay_status: str | None = None
    #: Los cupones: si se pudieron leer (sin eso no se juzga ninguno: no saber
    #: no es «no hay»), los que Medusa conoce y los vigentes.
    coupons_checked: bool = False
    coupons_known: tuple[str, ...] = ()
    coupons_valid: tuple[str, ...] = ()
    coupons_lines: tuple[str, ...] = ()
    coupons_percents: tuple[int, ...] = ()
    #: Los títulos del catálogo y lo que tiene el foco (para lo que niega).
    titles_all: tuple[str, ...] = ()
    own_labels: tuple[str, ...] = ()
    #: Las palabras del nombre del cliente: «¡Hola Rosa!» no nombra el color Rosa.
    names: tuple[str, ...] = ()
    #: Lo que acaba de escribir el cliente (su ráfaga, del historial), las
    #: cifras ciertas contra las que se miran las suyas y lo cierto en
    #: palabras («su pedido (1 × $25.000 + 1 × $22.500) suma $47.500 en
    #: productos»; sin pedido anotado, los precios de lo que se habló). None
    #: = no se sabe: sus cifras no se juzgan (laboratorio 2026-10-14).
    customer_text: str = ""
    order_figures: tuple[int, ...] = ()
    figure_truth: str | None = None
    #: Lo que se cobra por caja entre los productos en foco (`domain.yaml:
    #: sold_by_box`): (título, unidades por caja, precio de la caja; None = sin
    #: precio). Caso del 2026-10-16: «cada velita vale $25.000» por una caja de 12.
    boxes: tuple[tuple[str, int, int | None], ...] = ()

    @property
    def price_lines(self) -> tuple[str, ...]:
        """«Título: $25.000» por producto de `price_focus` con precio en el catálogo."""
        return tuple(
            f"{p.title}: {' o '.join(format_cop(x) for x in sorted(catalog_prices(p)))}"
            for p in self.price_focus or self.focus if catalog_prices(p)
        )

    @property
    def surcharge_rates(self) -> tuple[float, ...]:
        """Los recargos del link como porcentajes («1,5 %» → 1.5)."""
        from src.plugins.chats.agent.sales.use_cases.fact_rules import percent

        out: list[float] = []
        for raw in self.surcharges:
            try:
                out.append(percent(raw))
            except ValueError:
                continue
        return tuple(out)

    @property
    def titles(self) -> tuple[str, ...]:
        return tuple(p.title for p in self.focus)

    @property
    def own_aromas(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(a for p in self.focus for a in aromas_of(p)))

    @property
    def own_colors(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c for p in self.focus for c in colors_of(p)))


@dataclass(frozen=True)
class Findings:
    """Lo que no coincide (vacío = el texto sale)."""

    aromas: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    variant: bool = False
    prices: tuple[str, ...] = ()
    #: (lo que dice el texto, lo cierto) — los mismos que leyó Jev.
    payments: tuple[tuple[str, str], ...] = ()
    states: tuple[tuple[str, str], ...] = ()
    coupons: tuple[tuple[str, str], ...] = ()
    denials: tuple[tuple[str, str], ...] = ()
    #: (la cifra del cliente a la que el texto le da la razón, lo cierto de su pedido).
    figures: tuple[tuple[str, str], ...] = ()
    #: (el precio de la caja dicho por unidad, lo cierto de la caja).
    boxes: tuple[tuple[str, str], ...] = ()

    def __bool__(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))

    @property
    def terms(self) -> tuple[str, ...]:
        """Todo lo que no coincide, como texto (lo que se le dijo al modelo)."""
        pairs = (*self.payments, *self.states, *self.coupons, *self.denials, *self.figures, *self.boxes)
        return (*self.aromas, *self.colors, *(("presentación",) if self.variant else ()), *self.prices,
                *(term for term, _fact in pairs))

    @property
    def checks(self) -> tuple[str, ...]:
        """Las decisiones del paquete que encontraron algo (la traza y el log)."""
        found = {
            "aroma_ajeno": self.aromas, "color_ajeno": self.colors, "variante_ajena": self.variant,
            "precio_ajeno": self.prices, "pago_ajeno": self.payments, "estado_ajeno": self.states,
            "cupon_ajeno": self.coupons, "negacion_falsa": self.denials, "cifra_del_cliente": self.figures,
            "caja_por_unidad": self.boxes,
        }
        return tuple(name for name, value in found.items() if value)


def discounted(prices: set[int], promotion: dict[str, Any] | None) -> set[int]:
    """Los precios con el descuento del cupón aplicado (porcentaje o fijo)."""
    if not promotion:
        return set()
    kind, value = promotion.get("discount_type"), promotion.get("value")
    if not isinstance(value, int | float) or value <= 0:
        return set()
    if kind == "percentage":
        return {round(p * (100 - value) / 100) for p in prices}
    if kind == "fixed":
        return {int(p - value) for p in prices if p > value}
    return set()


def _price_problem(found: tuple[str, ...], facts: ReplyFacts) -> str:
    said = " y ".join(f"«{amount}»" for amount in found)
    lines = facts.price_lines
    if len(lines) == 1:
        title, _, price = lines[0].partition(": ")
        real = f"en el catálogo {title} cuesta {price}"
    else:
        real = "en el catálogo: " + "; ".join(line.replace(": ", " cuesta ", 1) for line in lines)
    if facts.subtotal is not None and facts.order_breakdown:
        real += f"; este pedido ({facts.order_breakdown}) suma {format_cop(facts.subtotal)} en productos"
    # Laboratorio del 2026-10-14: con «¿son $46.000 las dos, cierto?» el bot
    # corrigió el monto pero empezó con «Así es» y nunca desmintió el anterior.
    return (
        f"Le dijiste {said} como precio, y {real}. No empieces con «así es» ni con «sí» como si fuera "
        "correcto: dale el valor del catálogo y, si antes le habías dado otro valor, cuéntale que fue un "
        "error tuyo."
    )


def _box_problem(pairs: tuple[tuple[str, str], ...]) -> str:
    """Caso del 2026-10-16: el cliente preguntó si cada vela valía el precio de
    la caja y el bot le contestó «Así es, $25.000 cada una»; en el turno
    siguiente lo repitió («el precio es por unidad»)."""
    return (
        f"Le dijiste que {' y '.join(term for term, _fact in pairs)}, y lo cierto es que "
        f"{_truth(pairs)}. No empieces con «así es» ni con «sí» como si fuera correcto: dile que ese es el precio "
        "de la caja y cuántas unidades trae y, si antes le habías dicho que era por unidad, cuéntale que fue un "
        "error tuyo."
    )


def _said(pairs: tuple[tuple[str, str], ...]) -> str:
    quoted = [f"«{term}»" for term, _fact in pairs]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " y " + quoted[-1]


def _truth(pairs: tuple[tuple[str, str], ...]) -> str:
    return "; ".join(dict.fromkeys(fact for _term, fact in pairs))


def _figure_problem(
    pairs: tuple[tuple[str, str], ...], *, known: bool, examples: Sequence[str], tool: str
) -> str:
    """Laboratorio del 2026-10-14: «me da $45.000, ¿no?» y el bot contestó
    «Así es: … $47.500»; el cliente distraído se queda con su cifra. Con el
    pedido anotado (`known`) su cuenta no da; sin él no se sabe: primero la
    salida, después cómo corregir (al cliente que sumó bien le contestó
    «Casi 😊», copiando el ejemplo)."""
    example = f" Por ejemplo: {' / '.join(f'«{e}»' for e in examples)}." if examples else ""
    other = "otra cantidad, un mínimo, una tarifa, un abono o un presupuesto"
    if known:
        return (
            f"El cliente escribió {_said(pairs)} y tu texto le da la razón, pero su cuenta no da: {_truth(pairs)}. "
            f"No le digas «sí» ni «así es»: muéstrale cómo da la cuenta.{example} Si su cifra era de otra cosa "
            f"({other}), déjalo como está."
        )
    return (
        f"El cliente escribió {_said(pairs)} y tu texto le da la razón, pero {_truth(pairs)}. Haz la cuenta con "
        f"esos precios. Si da su cifra, o su cifra era de otra cosa ({other}), vuelve a llamar {tool} con el mismo "
        f"texto. Si da otra, no le digas «sí» ni «así es»: muéstrale cómo da.{example}"
    )


def fact_message(
    findings: Findings, facts: ReplyFacts, *, tool: str = "send_reply", tone: str | None = None,
    price_examples: Sequence[str] = (), sum_examples: Sequence[str] = (),
) -> str:
    """El mensaje con que el texto vuelve al modelo: cada cosa que no
    coincide con lo cierto, cómo corregirlo (`tone`, `price_examples`,
    `sum_examples`: de la tienda, en el paquete) y, al final, la salida
    (mismo texto si no aplica)."""
    problems: list[str] = []
    option = option_problem(
        list(facts.focus), aromas=findings.aromas, colors=findings.colors, variant=findings.variant
    )
    if option:
        problems.append(option)
    if findings.prices:
        problems.append(_price_problem(findings.prices, facts))
    problems += [f"Le dijiste que {term}, y lo cierto es que {fact}." for term, fact in findings.payments]
    problems += [f"Le dijiste que {term}, y {fact}." for term, fact in findings.states]
    if findings.coupons:
        problems.append(f"Le ofreciste {_said(findings.coupons)}, y {_truth(findings.coupons)}.")
    if findings.denials:
        problems.append(
            f"Le dijiste que no tenemos {_said(findings.denials)}, y lo cierto es que {_truth(findings.denials)}. "
            "Si de verdad está agotado hoy, déjalo como está."
        )
    if findings.boxes:
        problems.append(_box_problem(findings.boxes))
    if findings.figures:
        known = facts.subtotal is not None and bool(facts.order_breakdown)
        problems.append(_figure_problem(findings.figures, known=known, examples=sum_examples, tool=tool))
    if tone:
        problems.append(tone.strip())
    if (findings.prices or findings.boxes) and price_examples:
        problems.append("Por ejemplo: " + " / ".join(f"«{e}»" for e in price_examples) + ".")
    return f"No se envió: {' '.join(problems)} {correction_tail(tool)}"


__all__ = ["Findings", "ReplyFacts", "discounted", "fact_message"]
