"""Mapeos a listas cerradas dentro de las tools (diseño v2 §07, familia B,
fase F3): categoría pedida, familia de color, ítem del pedido y zona de envío.

Cada uno es un choice sobre la lista cerrada más «ambiguo» y «ninguno». El
valor final sale de la lista (una clave que no está en ella no pasa) y lo
sigue validando el código contra el catálogo; «ambiguo» o poca certeza dejan
la regla de hoy. La tool le pide la decisión al motor con
`guards.decide_for_session` y no sabe quién contestó. Tiempo máximo: 2 s.

El producto nombrado del remarketing NO está acá: el remarketing no puede
importar nada de ventas (contrato `agents-independent`) y el motor vive en
ventas.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.plugins.chats.agent.sales.config.shipping import (
    SHIPPING_ZONE_BOGOTA,
    SHIPPING_ZONE_NATIONAL,
    shipping_zone,
)
from src.plugins.chats.agent.sales.decisions.capabilities.respuestas import (
    AMBIGUO,
    NINGUNO,
    choice_of,
    closed_criteria,
    option_keys,
)
from src.plugins.chats.agent.sales.decisions.context import customer_window
from src.sdk.catalogkit import family_of_color, normalize_label, resolve_category, resolve_color_family
from src.sdk.connectorkit import TypedQuestion

# --- categoría pedida ---------------------------------------------------------


@dataclass(frozen=True)
class CategoriaPedida:
    """La categoría que pidió el cliente (tal cual la pasó el LLM a
    `search_products`) y las categorías reales del catálogo."""

    query: str
    categories: Sequence[Any] = ()  # CatalogCategoryDTO


class Categoria:
    """«¿A cuál categoría del catálogo se refiere el cliente?». Regla:
    `resolve_category` (exacto → contenido → parecido de texto ≥ 0,80). Jev
    lee lo que el texto no puede: «velas de santos» son las religiosas; unas
    «estampas religiosas» no son una categoría de velas. Valor:
    `{"categoria": slug | None}`."""

    name = "categoria"
    timeout_s = 2.0
    thresholds: Mapping[str, float] = {"choice": 0.80}

    def rule(self, inp: CategoriaPedida) -> dict[str, str | None]:
        matched = resolve_category(inp.query, list(inp.categories)).matched
        return {"categoria": matched.slug if matched else None}

    @staticmethod
    def _keys(inp: CategoriaPedida) -> dict[str, str]:
        return option_keys(c.slug for c in inp.categories)

    def ask(self, inp: CategoriaPedida) -> tuple[str, list[TypedQuestion]] | None:
        query = (inp.query or "").strip()
        if not query or not inp.categories:
            return None
        labels = {c.slug: c.label for c in inp.categories}
        criteria = closed_criteria(
            {key: labels[slug] for key, slug in self._keys(inp).items()},
            ambiguous="podría ser más de una de estas categorías",
            none="ninguna de estas categorías",
        )
        state = f"El cliente pidió ver la categoría: «{query}»\nCategorías del catálogo: {', '.join(labels.values())}"
        return state, [
            TypedQuestion(
                id="categoria.cual", kind="choice",
                text="¿A cuál categoría del catálogo se refiere el cliente?", criteria=criteria,
            )
        ]

    def decide(
        self, inp: CategoriaPedida, result: Any, rule: dict[str, str | None], thresholds: Mapping[str, float]
    ) -> dict[str, str | None] | None:
        th = {**self.thresholds, **thresholds}
        choice, p = choice_of(result, "categoria.cual")
        if choice is None or p < th["choice"] or choice == AMBIGUO:
            return None
        if choice == NINGUNO:
            return {"categoria": None}
        slug = self._keys(inp).get(choice)
        return {"categoria": slug} if slug is not None else None

    def floor(self, inp: CategoriaPedida, rule: dict[str, str | None], jev: dict[str, str | None]) -> dict[str, str | None]:
        return jev

    def same(self, a: dict[str, str | None], b: dict[str, str | None]) -> bool:
        return (a or {}).get("categoria") == (b or {}).get("categoria")


# --- familia de color ---------------------------------------------------------


@dataclass(frozen=True)
class ColorPedido:
    """Un color que pidió el cliente (una parte de «rojo y azul»), los colores
    reales del producto y la tabla de familias del tenant."""

    color: str
    colores: Sequence[str]
    familias: Any  # ColorFamilies
    producto: str = ""


def _resolution(res: Any) -> dict[str, Any]:
    """`ColorFamilyResolution | None` → JSON (lo que usa `set_order_slot`)."""
    if res is None:
        return {"estado": None, "color": None, "familias": [], "candidatas": [], "tono": False}
    return {
        "estado": res.status,
        "color": res.canonical,
        "familias": list(res.families),
        "candidatas": list(res.candidates),
        "tono": bool(res.shade_requested),
    }


class FamiliaDeColor:
    """«¿A cuál color del producto corresponde lo que pidió el cliente?».
    Regla: la tabla de familias del tenant (`resolve_color_family`; el
    matcheo exacto con el catálogo va antes y no es de esta capacidad). Jev
    lleva «bordó» al Rojo aunque no esté en la tabla y sabe que el «oro» es el
    Dorado cuando la tabla duda entre Amarillo y Dorado. Cuando Jev coincide
    con la regla queda el detalle de la regla (candidatas, familia). Valor:
    `{"estado", "color", "familias", "candidatas", "tono"}`."""

    name = "familia_de_color"
    timeout_s = 2.0
    thresholds: Mapping[str, float] = {"choice": 0.80}

    def rule(self, inp: ColorPedido) -> dict[str, Any]:
        return _resolution(resolve_color_family(inp.color, list(inp.colores), inp.familias))

    def ask(self, inp: ColorPedido) -> tuple[str, list[TypedQuestion]] | None:
        if not normalize_label(inp.color or "") or not inp.colores:
            return None
        criteria = closed_criteria(
            option_keys(inp.colores),
            ambiguous="podría ser más de uno de estos colores",
            none="ninguno de estos colores",
        )
        state = "\n".join([
            f"Producto: {inp.producto}" if inp.producto else "Producto del pedido",
            f"Colores del producto: {', '.join(inp.colores)}",
            f"El cliente pidió el color: «{inp.color.strip()}»",
        ])
        return state, [
            TypedQuestion(
                id="color.cual", kind="choice",
                text="¿A cuál color del producto corresponde lo que pidió el cliente?", criteria=criteria,
            )
        ]

    def decide(self, inp: ColorPedido, result: Any, rule: dict[str, Any], thresholds: Mapping[str, float]) -> dict[str, Any] | None:
        th = {**self.thresholds, **thresholds}
        choice, p = choice_of(result, "color.cual")
        if choice is None or p < th["choice"] or choice == AMBIGUO:
            return None
        if choice == NINGUNO:
            return _resolution(None)
        color = option_keys(inp.colores).get(choice)
        if color is None:
            return None
        family = family_of_color(color, inp.familias)
        return {
            "estado": "resolved", "color": color, "familias": [family.label if family else color],
            "candidatas": [color], "tono": True,  # otras palabras que las del catálogo: el bot confirma el tono
        }

    def floor(self, inp: ColorPedido, rule: dict[str, Any], jev: dict[str, Any]) -> dict[str, Any]:
        return rule if self.same(rule, jev) else jev

    def same(self, a: dict[str, Any], b: dict[str, Any]) -> bool:
        return (a or {}).get("color") == (b or {}).get("color")


# --- ítem del pedido ----------------------------------------------------------


@dataclass(frozen=True)
class DatoDelItem:
    """Aroma/color/diseño que llegan a `set_order_slot` SIN producto en un
    pedido de varios: los productos del pedido (en orden), el ítem en curso,
    qué producto acepta todos los valores (lo valida el catálogo antes) y lo
    último de la conversación."""

    valores: Mapping[str, str]
    items: Sequence[str]
    actual: int
    aceptan: Sequence[bool | None]
    events: Sequence[Mapping[str, Any]] = ()


class ItemDelPedido:
    """«¿A cuál producto del pedido van estos datos?». Regla: `item_for_values`
    (el ítem en curso, salvo que su producto los rechace y otro los acepte).
    Solo se pregunta cuando dos o más productos del pedido los aceptan (ahí la
    regla se queda con el ítem en curso sin mirar lo que dijo el cliente) y
    Jev solo elige entre ellos. Valor: `{"item": índice, "producto": nombre}`."""

    name = "item_del_pedido"
    timeout_s = 2.0
    thresholds: Mapping[str, float] = {"choice": 0.80}

    def rule(self, inp: DatoDelItem) -> dict[str, Any]:
        from src.plugins.chats.agent.sales.use_cases.order_draft import item_for_values

        k = item_for_values(inp.actual, list(inp.aceptan))
        return {"item": k, "producto": inp.items[k]}

    @staticmethod
    def _candidates(inp: DatoDelItem) -> dict[str, int]:
        return {f"item_{k + 1}": k for k, ok in enumerate(inp.aceptan) if ok is True}

    def ask(self, inp: DatoDelItem) -> tuple[str, list[TypedQuestion]] | None:
        candidates = self._candidates(inp)
        if len(candidates) < 2:
            return None
        criteria = closed_criteria(
            {key: inp.items[k] for key, k in candidates.items()},
            ambiguous="podría ser de más de uno de estos productos",
            none="de ninguno de estos productos",
        )
        values = ", ".join(f"{field} «{value}»" for field, value in inp.valores.items())
        lines = ["PRODUCTOS DEL PEDIDO", *(f"- {inp.items[k]}" for k in candidates.values()),
                 f"DATOS QUE LLEGAN SIN PRODUCTO: {values}"]
        window = customer_window(list(inp.events), burst_wamids=set(), burst_size=0)
        if window.lines:
            lines += ["CONVERSACIÓN — lo último", *window.lines]
        return "\n".join(lines), [
            TypedQuestion(
                id="item.cual", kind="choice", text="¿A cuál producto del pedido van estos datos?", criteria=criteria,
            )
        ]

    def decide(self, inp: DatoDelItem, result: Any, rule: dict[str, Any], thresholds: Mapping[str, float]) -> dict[str, Any] | None:
        th = {**self.thresholds, **thresholds}
        choice, p = choice_of(result, "item.cual")
        if choice is None or p < th["choice"] or choice in (AMBIGUO, NINGUNO):
            return None
        k = self._candidates(inp).get(choice)
        return {"item": k, "producto": inp.items[k]} if k is not None else None

    def floor(self, inp: DatoDelItem, rule: dict[str, Any], jev: dict[str, Any]) -> dict[str, Any]:
        return jev

    def same(self, a: dict[str, Any], b: dict[str, Any]) -> bool:
        return (a or {}).get("item") == (b or {}).get("item")


# --- zona de envío ------------------------------------------------------------


@dataclass(frozen=True)
class CiudadDeEnvio:
    ciudad: str | None


class ZonaDeEnvio:
    """«¿En qué zona de envío queda la ciudad?» {bogota, nacional}. Regla:
    `shipping_zone` («bogota» si la ciudad lo dice; si no, no se sabe y valen
    las dos tarifas: no hay lista de municipios cercanos). Jev sabe que Chía
    es un municipio cercano y que Medellín es nacional; la tarifa la valida el
    código (`is_published_rate_for_zone`). Valor: `{"zona": ... | None}`."""

    name = "zona_de_envio"
    timeout_s = 2.0
    thresholds: Mapping[str, float] = {"choice": 0.85}

    def rule(self, inp: CiudadDeEnvio) -> dict[str, str | None]:
        return {"zona": shipping_zone(inp.ciudad)}

    def ask(self, inp: CiudadDeEnvio) -> tuple[str, list[TypedQuestion]] | None:
        city = (inp.ciudad or "").strip()
        if not city:
            return None
        criteria = closed_criteria(
            {
                SHIPPING_ZONE_BOGOTA: "Bogotá o un municipio cercano a Bogotá",
                SHIPPING_ZONE_NATIONAL: "otra ciudad o municipio de Colombia, lejos de Bogotá",
            },
            ambiguous="no se puede saber con lo que dice",
            none="no es una ciudad o municipio de Colombia",
        )
        return f"Ciudad de entrega que dio el cliente: «{city}»", [
            TypedQuestion(
                id="zona.cual", kind="choice", text="¿En qué zona de envío queda la ciudad de entrega?", criteria=criteria,
            )
        ]

    def decide(
        self, inp: CiudadDeEnvio, result: Any, rule: dict[str, str | None], thresholds: Mapping[str, float]
    ) -> dict[str, str | None] | None:
        th = {**self.thresholds, **thresholds}
        choice, p = choice_of(result, "zona.cual")
        if choice is None or p < th["choice"] or choice == AMBIGUO:
            return None
        if choice == NINGUNO:
            return {"zona": None}
        if choice in (SHIPPING_ZONE_BOGOTA, SHIPPING_ZONE_NATIONAL):
            return {"zona": choice}
        return None

    def floor(self, inp: CiudadDeEnvio, rule: dict[str, str | None], jev: dict[str, str | None]) -> dict[str, str | None]:
        return jev

    def same(self, a: dict[str, str | None], b: dict[str, str | None]) -> bool:
        return (a or {}).get("zona") == (b or {}).get("zona")
