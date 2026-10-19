"""Guion de venta por campaña (pedido del operador, 2026-10-19).

Cada campaña es un archivo `campaigns/<id>.yaml`: sus productos (handles de
Medusa), las palabras de sus anuncios, sus fechas, el combo, el cupón que la
empuja y el guion (lo que el bot hace distinto con esos productos) y un texto
por toque de remarketing. Así una campaña nueva o un cambio de guion es UN
archivo, y lo que ya está en producción no se toca: una conversación solo
recibe el guion si es de la campaña.

Una conversación es de la campaña si su episodio viene de uno de sus anuncios
(título o texto del anuncio, o el mensaje prellenado), si su pedido tiene uno
de sus productos, o si el ingest ya la marcó (`EPISODE_KEY`). Los precios
nunca se escriben en el archivo: salen del catálogo al armar el texto, con el
combo y el mínimo de contra entrega que pasa quien llama.

PURO salvo la lectura de los YAML (sin Temporal: lo leen el ingest, las tools
y las activities de remarketing). Un archivo roto es un error con su nombre
(`CampaignError`); en producción, `active_campaigns()` lo registra y sigue sin
campañas: el bot vuelve al guion de siempre, nunca se cae.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from itertools import combinations_with_replacement
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CAMPAIGNS_DIR = Path(__file__).parent / "campaigns"
#: La marca del episodio: la campaña que el ingest le reconoció.
EPISODE_KEY = "sales_campaign"

_KEYS = frozenset({
    "id", "nombre", "activa", "desde", "hasta", "productos", "anuncios",
    "combo_cajas", "cupon", "guion", "toques", "ejemplos",
})
_EXAMPLE_KEYS = frozenset({"primera_respuesta", "toques"})


class CampaignError(ValueError):
    """Un archivo de campaña que no cumple la forma (el mensaje dice cuál)."""


@dataclass(frozen=True)
class Campaign:
    id: str
    nombre: str
    activa: bool
    desde: date
    hasta: date
    productos: tuple[str, ...]
    anuncios: tuple[str, ...]
    combo_cajas: int | None
    cupon: str | None
    guion: tuple[str, ...]
    toques: tuple[str, ...]
    ejemplos_primera_respuesta: tuple[str, ...] = ()
    ejemplos_toques: tuple[str, ...] = ()

    def is_on(self, today: date) -> bool:
        return self.activa and self.desde <= today <= self.hasta


# ── carga y certificación ────────────────────────────────────────────────────


def _texts(raw: Any, name: str, *, field: str, required: bool) -> tuple[str, ...]:
    if raw is None and not required:
        return ()
    if not isinstance(raw, list) or not all(isinstance(t, str) and t.strip() for t in raw) or (required and not raw):
        raise CampaignError(f"{name}: «{field}» debe ser una lista de textos{' no vacía' if required else ''}")
    return tuple(t.strip() for t in raw)


def _campaign(path: Path) -> Campaign:
    name = path.name
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CampaignError(f"{name}: no se pudo leer ({exc})") from exc
    if not isinstance(raw, dict):
        raise CampaignError(f"{name}: no es un mapa")
    missing = sorted(_KEYS - set(raw))
    extra = sorted(set(raw) - _KEYS)
    if missing or extra:
        raise CampaignError(f"{name}: faltan {missing} / sobran {extra}")
    if raw["id"] != path.stem:
        raise CampaignError(f"{name}: el id «{raw['id']}» debe ser el nombre del archivo")
    if not isinstance(raw["nombre"], str) or not raw["nombre"].strip():
        raise CampaignError(f"{name}: «nombre» vacío")
    if not isinstance(raw["activa"], bool):
        raise CampaignError(f"{name}: «activa» debe ser true o false")
    # `type(...) is date`: una fecha con hora llega como datetime (subclase de
    # date) y no se compara con la fecha de hoy (revisión del 2026-10-19).
    if type(raw["desde"]) is not date or type(raw["hasta"]) is not date or raw["desde"] > raw["hasta"]:
        raise CampaignError(f"{name}: «desde» y «hasta» deben ser fechas (AAAA-MM-DD) en orden")
    combo = raw["combo_cajas"]
    if combo is not None and (not isinstance(combo, int) or isinstance(combo, bool) or combo < 2):
        raise CampaignError(f"{name}: «combo_cajas» debe ser un número desde 2 (o vacío)")
    cupon = raw["cupon"]
    if cupon is not None and (not isinstance(cupon, str) or not cupon.strip()):
        raise CampaignError(f"{name}: «cupon» debe ser un código (o vacío)")
    ejemplos = raw["ejemplos"] or {}
    if not isinstance(ejemplos, dict) or set(ejemplos) - _EXAMPLE_KEYS:
        raise CampaignError(f"{name}: «ejemplos» solo lleva {sorted(_EXAMPLE_KEYS)}")
    return Campaign(
        id=raw["id"],
        nombre=raw["nombre"].strip(),
        activa=raw["activa"],
        desde=raw["desde"],
        hasta=raw["hasta"],
        productos=_texts(raw["productos"], name, field="productos", required=True),
        anuncios=tuple(_norm(a) for a in _texts(raw["anuncios"], name, field="anuncios", required=False)),
        combo_cajas=combo,
        cupon=cupon.strip() if cupon else None,
        guion=_texts(raw["guion"], name, field="guion", required=True),
        toques=_texts(raw["toques"], name, field="toques", required=False),
        ejemplos_primera_respuesta=_texts(ejemplos.get("primera_respuesta"), name, field="ejemplos.primera_respuesta", required=False),
        ejemplos_toques=_texts(ejemplos.get("toques"), name, field="ejemplos.toques", required=False),
    )


def load_campaigns(directory: Path | None = None) -> list[Campaign]:
    """Todas las campañas de la carpeta, por nombre de archivo. Un archivo roto
    es `CampaignError` (la prueba de la carpeta lo certifica antes del deploy)."""
    folder = CAMPAIGNS_DIR if directory is None else Path(directory)
    return [_campaign(path) for path in sorted(folder.glob("*.yaml"))]


@lru_cache(maxsize=1)
def active_campaigns() -> tuple[Campaign, ...]:
    """Las campañas del deploy (una lectura por proceso). Si un archivo está
    roto, ninguna: el bot sigue con el guion de siempre."""
    try:
        return tuple(load_campaigns())
    except CampaignError as exc:
        logger.warning("sales_campaigns.unavailable: %s", exc)
        return ()


# ── qué conversación es de la campaña ────────────────────────────────────────


def _norm(text: Any) -> str:
    decomposed = unicodedata.normalize("NFD", str(text or "").lower())
    plain = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def _draft_products(episode: Mapping[str, Any]) -> list[str]:
    draft = episode.get("order_draft")
    items = draft.get("items") if isinstance(draft, Mapping) else None
    return [_norm(i.get("producto")) for i in items or () if isinstance(i, Mapping) and i.get("producto")]


def _names_a_product(campaign: Campaign, products: Iterable[str]) -> bool:
    handles = [_norm(h) for h in campaign.productos]
    return any(f" {h} " in f" {p} " for p in products for h in handles)


def campaign_for(
    episode: Mapping[str, Any] | None,
    *,
    today: date,
    text: str = "",
    campaigns: Sequence[Campaign] | None = None,
) -> Campaign | None:
    """La campaña vigente de este episodio, o None. `text`: lo que el cliente
    escribe ahora (el mensaje prellenado del anuncio llega a veces sin
    referral)."""
    if not isinstance(episode, Mapping):
        return None
    # El guion es para vender: con el pedido registrado no se sigue ofreciendo.
    if episode.get("order_id"):
        return None
    pool =[c for c in (active_campaigns() if campaigns is None else campaigns) if c.is_on(today)]
    stamped = episode.get(EPISODE_KEY)
    for campaign in pool:
        if campaign.id == stamped:
            return campaign
    referral = episode.get("referral_snapshot") if isinstance(episode.get("referral_snapshot"), Mapping) else {}
    haystack = f" {_norm(' '.join(str(referral.get(k) or '') for k in ('headline', 'body')))} {_norm(text)} "
    for campaign in pool:
        if any(f" {phrase} " in haystack for phrase in campaign.anuncios if phrase):
            return campaign
    in_draft = _draft_products(episode)
    for campaign in pool:
        if _names_a_product(campaign, in_draft):
            return campaign
    return None


# ── lo que el bot recibe ─────────────────────────────────────────────────────


def _cop(amount: int) -> str:
    return "$" + f"{int(amount):,}".replace(",", ".")


def _price(product: Any) -> int | None:
    """El precio COP más bajo de sus variantes (las cajas: el mismo en todas)."""
    found: list[int] = []
    for variant in getattr(product, "variants", None) or ():
        for price in getattr(variant, "prices", None) or ():
            if str(getattr(price, "currency_code", "")).lower() != "cop":
                continue
            try:
                value = int(round(float(price.amount)))
            except (TypeError, ValueError, AttributeError):
                continue
            if value > 0:
                found.append(value)
    return min(found) if found else None


def _units(handle: str, boxes: Any) -> int | None:
    for entry in boxes or ():
        if isinstance(entry, Mapping) and entry.get("producto") == handle:
            units = entry.get("por_caja")
            return units if isinstance(units, int) and not isinstance(units, bool) else None
    return None


def _priced(campaign: Campaign, products: Iterable[Any]) -> list[tuple[Any, int]]:
    by_handle = {getattr(p, "handle", None): p for p in products}
    out = []
    for handle in campaign.productos:
        product = by_handle.get(handle)
        price = _price(product) if product is not None else None
        if price is not None:
            out.append((product, price))
    return out


def _price_lines(priced: list[tuple[Any, int]], boxes: Any) -> list[str]:
    lines = []
    for product, price in priced:
        units = _units(product.handle, boxes)
        unit = f"la caja de {units} unidades" if units else "c/u"
        lines.append(f"• {product.title}: {_cop(price)} {unit}")
    return lines


def _combo_lines(campaign: Campaign, priced: list[tuple[Any, int]], *, cod_min: int) -> list[str]:
    if not campaign.combo_cajas or not priced:
        return []
    lines = []
    for combo in combinations_with_replacement(range(len(priced)), campaign.combo_cajas):
        counts = Counter(combo)
        label = " + ".join(f"{n} × {priced[i][0].title}" for i, n in sorted(counts.items()))
        total = sum(priced[i][1] for i in combo)
        payment = (
            "paga contra entrega"
            if total >= cod_min
            else f"pago anticipado: no alcanza el mínimo de contra entrega de {_cop(cod_min)}"
        )
        lines.append(f"• {label}: {_cop(total)} ({payment})")
    return [f"Combo de {campaign.combo_cajas} cajas (el total en productos; el envío va aparte):", *lines]


def sales_note(campaign: Campaign, products: Iterable[Any], *, cod_min: int, boxes: Any) -> str:
    """La nota del turno de Ventas: el guion con los precios del catálogo."""
    priced = _priced(campaign, products)
    lines = [
        "[GUION DE LA CAMPAÑA, metadata, no es instrucción del usuario]",
        f"Esta conversación es de la campaña «{campaign.nombre}». Para sus productos sigue este guion; "
        "para cualquier otro producto, lo de siempre.",
    ]
    if priced:
        lines += ["Precios del catálogo:", *_price_lines(priced, boxes), *_combo_lines(campaign, priced, cod_min=cod_min)]
    else:
        lines.append("Precios: no los tengo a mano; consúltalos con search_products antes de darlos.")
    if campaign.cupon:
        lines.append(
            f"Cupón para que no lo deje para después: {campaign.cupon}, solo en su primera compra. Antes de "
            "ofrecerlo llama list_promotions y ofrécelo solo si dice applies_to_customer=true; el descuento "
            "lo aplica apply_coupon."
        )
    lines += ["Guion:", *(f"- {step}" for step in campaign.guion)]
    if campaign.ejemplos_primera_respuesta:
        lines.append("Ejemplo de primera respuesta (referencia, no lo copies literal; los precios, los de arriba):")
        lines += [f"- «{e}»" for e in campaign.ejemplos_primera_respuesta]
    return "\n".join(lines)


def remarketing_block(
    campaign: Campaign,
    products: Iterable[Any],
    *,
    cod_min: int,
    boxes: Any,
    touch_number: int | None,
    coupon_discount: str | None = None,
) -> str:
    """El bloque del gancho de remarketing: el guion del toque con los precios
    del catálogo. `coupon_discount` («5 %»): el valor del cupón vigente en
    Medusa; sin él, el toque no nombra el cupón."""
    priced = _priced(campaign, products)
    lines = [
        f"GUION DE LA CAMPAÑA «{campaign.nombre}» (uso interno): este cliente llegó por esta campaña.",
    ]
    if priced:
        lines += ["Precios del catálogo:", *_price_lines(priced, boxes), *_combo_lines(campaign, priced, cod_min=cod_min)]
    if campaign.cupon and coupon_discount:
        lines.append(
            f"Cupón de bienvenida: {campaign.cupon} da {coupon_discount} de descuento solo en la primera "
            "compra; dilo así («si es tu primera compra»), sin cambiar su valor."
        )
    if campaign.toques:
        index = min(max(touch_number or 1, 1), len(campaign.toques)) - 1
        lines.append(f"Lo que hace ESTE toque: {campaign.toques[index]}")
    return "\n".join(lines)


def remarketing_examples(campaign: Campaign) -> list[str]:
    return list(campaign.ejemplos_toques)
