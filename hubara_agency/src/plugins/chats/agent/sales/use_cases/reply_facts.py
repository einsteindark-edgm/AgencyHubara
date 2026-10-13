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
    cod_min: int = 0
    surcharges: tuple[str, ...] = ()
    #: El pedido según OrderFacts (None = no hay pedido o no se miró).
    order: str | None = None
    stage: str | None = None
    pay_status: str | None = None
    #: Los cupones (vacío = no se miró): los que Medusa conoce y los vigentes.
    coupons_known: tuple[str, ...] = ()
    coupons_valid: tuple[str, ...] = ()
    coupons_lines: tuple[str, ...] = ()
    coupons_percents: tuple[int, ...] = ()
    #: Los títulos del catálogo y lo que tiene el foco (para lo que niega).
    titles_all: tuple[str, ...] = ()
    own_labels: tuple[str, ...] = ()

    @property
    def price_lines(self) -> tuple[str, ...]:
        """«Título: $25.000» por producto en foco con precio en el catálogo."""
        return tuple(
            f"{p.title}: {' o '.join(format_cop(x) for x in sorted(catalog_prices(p)))}"
            for p in self.focus if catalog_prices(p)
        )

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

    def __bool__(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))


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
    return f"Le dijiste {said} como precio, y {real}."


def _said(pairs: tuple[tuple[str, str], ...]) -> str:
    quoted = [f"«{term}»" for term, _fact in pairs]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " y " + quoted[-1]


def _truth(pairs: tuple[tuple[str, str], ...]) -> str:
    return "; ".join(dict.fromkeys(fact for _term, fact in pairs))


def fact_message(findings: Findings, facts: ReplyFacts, *, tool: str = "send_reply") -> str:
    """El mensaje con que el texto vuelve al modelo: cada cosa que no
    coincide con lo cierto y, al final, la salida (mismo texto si no aplica)."""
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
    return f"No se envió: {' '.join(problems)} {correction_tail(tool)}"


__all__ = ["Findings", "ReplyFacts", "discounted", "fact_message"]
