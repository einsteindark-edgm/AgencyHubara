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
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from itertools import combinations_with_replacement
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

import yaml

logger = logging.getLogger(__name__)

CAMPAIGNS_DIR = Path(__file__).parent / "campaigns"
#: La marca del episodio: la campaña que el ingest le reconoció.
EPISODE_KEY = "sales_campaign"
#: La oferta del cupón que ya se le dijo en el episodio (`CouponOffer.key`).
OFFER_KEY = "sales_campaign_offer"

_KEYS = frozenset({
    "id", "nombre", "activa", "desde", "hasta", "productos", "anuncios",
    "combo_cajas", "envio_gratis_desde_cajas", "cupon", "toque_oferta", "guion", "toques", "ejemplos",
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
    toque_oferta: int | None
    #: Envío gratis desde esta cantidad de cajas de la campaña (operador, 2026-10-20).
    envio_gratis_desde_cajas: int | None
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
    toque = raw["toque_oferta"]
    if toque is not None and (not isinstance(toque, int) or isinstance(toque, bool) or toque < 1):
        raise CampaignError(f"{name}: «toque_oferta» debe ser el número de un toque (o vacío)")
    free_from = raw["envio_gratis_desde_cajas"]
    if free_from is not None and (not isinstance(free_from, int) or isinstance(free_from, bool) or free_from < 2):
        raise CampaignError(f"{name}: «envio_gratis_desde_cajas» debe ser una cantidad de cajas, 2 o más (o vacío)")
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
        toque_oferta=toque,
        envio_gratis_desde_cajas=free_from,
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
    # El guion es para vender: con el pedido registrado, o en la posventa
    # (`after_order`: «ya te transferí»), no se sigue ofreciendo.
    if episode.get("order_id") or episode.get("after_order"):
        return None
    pool = [c for c in (active_campaigns() if campaigns is None else campaigns) if c.is_on(today)]
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


def _free_shipping_line(campaign: Campaign, *, remarketing: bool = False) -> list[str]:
    """El gancho del envío gratis desde N cajas de la campaña (operador,
    2026-10-20), o nada si la campaña no lo tiene."""
    n = campaign.envio_gratis_desde_cajas
    if not n:
        return []
    if remarketing:
        return [
            f"Envío gratis desde {n} cajas de esta campaña (se pueden mezclar), en un pedido solo de estas cajas: "
            "dilo tal cual si le sirve (para regalar a varias personas, o si lo frenó el envío). Con menos de "
            f"{n} cajas, o con otros productos en el pedido, el envío se cobra como siempre: no lo prometas."
        ]
    return [
        f"Envío gratis desde {n} cajas de esta campaña (se pueden mezclar), en un pedido solo de estas cajas: con "
        f"{n} o más el envío no se cobra, y el sistema lo deja en cero al registrar el pedido. Úsalo como gancho "
        "cuando pregunte por el envío, cuando lo frene el costo del envío o cuando lleve varias cajas (para "
        f"regalar). Con menos de {n} cajas, o si el pedido trae otros productos, el envío se cobra como siempre: "
        "no lo prometas."
    ]


def sales_note(
    campaign: Campaign,
    products: Iterable[Any],
    *,
    cod_min: int,
    boxes: Any,
    offer: CouponOffer | None = None,
    offer_now: bool = False,
    already_offered: bool = False,
) -> str:
    """La nota del turno de Ventas: el guion con los precios del catálogo.
    `offer`: el cupón de la campaña tal como rige hoy en Medusa (sin él, la
    nota no nombra cupones). `offer_now`: el cliente acaba de aplazar la
    compra (lo leyó Jev, capacidad `compra`) y la oferta no se le ha dicho
    en este episodio: es el momento de decírsela. `already_offered`: ya se
    le dijo en este episodio (con esta fecha): no se vuelve a ofrecer."""
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
    lines += _free_shipping_line(campaign)
    if offer is not None:
        when = (
            "Ya se la dijiste en este episodio: repítela solo si el cliente pregunta por descuentos"
            if already_offered
            else "Ofrécelo cuando el cliente dude o aplace la compra, o si pregunta por descuentos"
        )
        lines.append(
            f"Cupón de la campaña: {offer_phrase(offer, person='tiene')}. {when}; antes, list_promotions te "
            "confirma que aplica a este cliente (applies_to_customer=true) y apply_coupon lo aplica. "
            f"{_date_rule(offer)}"
        )
        if offer_now:
            check = (
                " Antes confírmalo con list_promotions (applies_to_customer=true); si no le aplica, no lo menciones."
                if offer.first_purchase_only
                else ""
            )
            lines.append(
                "AHORA: el cliente acaba de aplazar la compra. En tu respuesta breve cuéntale, una sola vez y "
                f"sin insistir, que {offer_phrase(offer, person='tiene')}.{check}"
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
    offer: CouponOffer | None = None,
    intent: bool = False,
    offer_touch: bool | None = None,
) -> str:
    """El bloque del gancho de remarketing: el guion del toque con los precios
    del catálogo. La oferta del cupón (`offer`, como rige hoy en Medusa) va
    solo en el toque de la oferta y solo a quien tuvo intención de compra
    (`intent`, `purchase_intent`); a los demás, el toque no nombra cupones.
    `offer_touch`: si este es el toque de la oferta (quien llama lo cuenta
    en el episodio, para que salga una sola vez); sin él, el número del toque
    de la escalera es `toque_oferta`."""
    priced = _priced(campaign, products)
    lines = [
        f"GUION DE LA CAMPAÑA «{campaign.nombre}» (uso interno): este cliente llegó por esta campaña.",
    ]
    if priced:
        lines += ["Precios del catálogo:", *_price_lines(priced, boxes), *_combo_lines(campaign, priced, cod_min=cod_min)]
    lines += _free_shipping_line(campaign, remarketing=True)
    if offer_touch is None:
        offer_touch = bool(campaign.toque_oferta) and touch_number == campaign.toque_oferta
    if offer is not None and intent and offer_touch:
        lines.append(
            "Este toque lleva la oferta para que no lo deje para después (el cliente quiso comprar y no "
            f"terminó): cuéntale, una vez y sin presión, que {offer_phrase(offer, person='tienes')}. "
            f"{_date_rule(offer)}"
        )
    if campaign.toques:
        index = min(max(touch_number or 1, 1), len(campaign.toques)) - 1
        lines.append(f"Lo que hace ESTE toque: {campaign.toques[index]}")
    return "\n".join(lines)


def remarketing_examples(campaign: Campaign) -> list[str]:
    return list(campaign.ejemplos_toques)


# ── la oferta con fecha límite (operador, 2026-10-20) ───────────────────────

#: Una fecha de fin más lejana no empuja a nadie («hasta el 31 de diciembre
#: de 2030»): la oferta va sin fecha.
OFFER_HORIZON_DAYS = 45
_BOGOTA = ZoneInfo("America/Bogota")
_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
)


@dataclass(frozen=True)
class CouponOffer:
    """El cupón de la campaña tal como rige hoy en Medusa. Lo arma quien llama
    con la promoción ya validada (`coupon_problem` = None): la fecha y el valor
    son los de Medusa, nunca los del archivo de la campaña."""

    code: str
    discount: str
    #: El último día que rige (Bogotá), si cae dentro de `OFFER_HORIZON_DAYS`.
    last_day: date | None = None
    first_purchase_only: bool = False

    def key(self) -> str:
        """Lo que se marca en el episodio al decirla: con otra fecha (el
        dashboard la corrió), se puede volver a decir."""
        return f"{self.code}@{self.last_day.isoformat() if self.last_day else '-'}"


def _last_day(ends_at_ms: Any) -> date | None:
    """El «hasta» inclusivo: un fin a las 00:00 en Bogotá (como lo guarda el
    dashboard) tiene como último día el anterior."""
    if not isinstance(ends_at_ms, (int, float)) or isinstance(ends_at_ms, bool):
        return None
    local = datetime.fromtimestamp(ends_at_ms / 1000, tz=_BOGOTA)
    return local.date() - timedelta(days=1) if local.time() == time.min else local.date()


def coupon_offer(promotion: Any, *, today: date, product_ids: Iterable[str] = ()) -> CouponOffer | None:
    """La oferta de una promoción que rige (porcentaje o monto fijo); None si
    no se puede decir su valor o tiene condiciones que la frase no dice (un
    mínimo de compra, colecciones, etiquetas, variantes, reglas que no se
    leyeron, un cupo por unidad, un monto fijo por ítem, o productos que no
    cubren los de la campaña, `product_ids`): falla cerrada, como `apply_coupon`."""
    if getattr(promotion, "scope_unresolved", False) or getattr(promotion, "min_subtotal_cop", None):
        return None
    if getattr(promotion, "max_quantity", None):
        return None  # «5 %» en un combo de 2 cajas que solo cubre 1 (#364)
    if getattr(promotion, "discount_type", None) == "fixed" and getattr(promotion, "allocation", None) == "each":
        return None  # «$5.000 de descuento» sería por cada ítem
    if any(getattr(promotion, field, None) for field in ("collection_ids", "tag_values", "variant_ids")):
        return None
    scoped = set(getattr(promotion, "product_ids", None) or ())
    wanted = set(product_ids)
    if scoped and (not wanted or not wanted <= scoped):
        return None
    code = str(getattr(promotion, "code", "") or "").strip()
    kind, value = getattr(promotion, "discount_type", None), getattr(promotion, "value", None)
    if not code or not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    if kind == "percentage":
        discount = f"{value:g} %"
    elif kind == "fixed":
        discount = _cop(int(value))
    else:
        return None
    last = _last_day(getattr(promotion, "ends_at_ms", None))
    if last is not None and (last < today or (last - today).days > OFFER_HORIZON_DAYS):
        last = None
    return CouponOffer(
        code=code, discount=discount, last_day=last,
        first_purchase_only=bool(getattr(promotion, "first_purchase_only", False)),
    )


def _date_rule(offer: CouponOffer) -> str:
    """Lo que el bot puede decir de la fecha: la del cupón, o ninguna (el
    contexto trae otras, como «7 de diciembre», y el bot las tomaba)."""
    if offer.last_day is not None:
        return "Esa es la única fecha que puedes decir: no inventes otra, ni escasez ni cupos."
    return "Este cupón no tiene una fecha límite que puedas decir: no menciones fechas, escasez ni cupos."


def spanish_day(day: date) -> str:
    return f"{day.day} de {_MONTHS[day.month - 1]}"


def offer_phrase(offer: CouponOffer, *, person: str = "tiene") -> str:
    """«con el cupón X tiene 5 % de descuento si compra hasta el 31 de
    octubre, solo en su primera compra» (`person`: «tiene» o «tienes»)."""
    you = person == "tienes"
    text = f"con el cupón {offer.code} {person} {offer.discount} de descuento"
    if offer.last_day is not None:
        text += f" si {'compras' if you else 'compra'} hasta el {spanish_day(offer.last_day)}"
    if offer.first_purchase_only:
        text += f", solo en {'tu' if you else 'su'} primera compra"
    return text


#: Lo que el cliente hace con la compra (Jev, capacidad `compra`) que muestra
#: que quiso comprar: elegir, dar sus datos o aplazar. Confirmar llega por la
#: DECISIÓN (`INTENT_SIGNALS`: la afirmación que el paquete dejó en pie), no
#: por la respuesta cruda: un «sí, muéstrame el catálogo» que Jev leyó como
#: `confirma` lo retira el paquete (revisión del 2026-10-20). Preguntar el
#: precio es interés, no intención.
INTENT_CHOICES = frozenset({"elige", "da_datos", "aplaza"})
INTENT_SIGNALS = frozenset({"affirmation", "deferral"})
#: El mismo umbral `choice` de la capacidad `compra` del paquete vivo (una
#: prueba lo compara: si otra versión lo cambia, avisa).
INTENT_CONFIDENCE = 0.7


def purchase_intent(episode: Mapping[str, Any] | None, decisions: Iterable[Mapping[str, Any]]) -> bool:
    """¿El cliente quiso comprar en este episodio? Un pedido a medias, o lo que
    leyó Jev en sus mensajes (`evals/decisions.jsonl`, las lecturas del ingest
    de la capacidad `compra`): eligió, confirmó, dio datos o aplazó."""
    if not isinstance(episode, Mapping):
        return False
    if _draft_products(episode):
        return True
    return next(intent_rows(episode, decisions), None) is not None


def intent_rows(episode: Mapping[str, Any], decisions: Iterable[Mapping[str, Any]]) -> Iterator[Mapping[str, Any]]:
    """Las lecturas del ingest de la capacidad `compra` desde que empezó el
    episodio que dicen que quiso comprar (`purchase_intent`), en su orden."""
    since = episode.get("started_at_ms")
    since = int(since) - 5_000 if isinstance(since, (int, float)) else 0
    for row in decisions:
        if not isinstance(row, Mapping) or row.get("capability") != "compra" or row.get("stage") != "ingest":
            continue
        at = row.get("at_ms")
        if not isinstance(at, (int, float)) or at < since:
            continue
        value = row.get("value")
        if isinstance(value, (list, tuple)) and value and value[0] in INTENT_SIGNALS:
            yield row
            continue
        for answer in row.get("answers") or ():
            if not isinstance(answer, Mapping) or answer.get("q") != "compra.que_hace":
                continue
            sure = answer.get("confidence", answer.get("p"))
            if answer.get("choice") in INTENT_CHOICES and isinstance(sure, (int, float)) and sure >= INTENT_CONFIDENCE:
                yield row
                break


# ── envío gratis desde N cajas (operador, 2026-10-20) ───────────────────────


@dataclass(frozen=True)
class FreeShipping:
    """El envío gratis de un pedido: lleva `boxes` cajas de los productos de
    la campaña `campaign`, que no cobra el envío desde `min_boxes`. Viaja como
    dato (`to_json`) en el pedido de Medusa: Orders y el ETA lo leen de ahí
    sin importar este plugin."""

    campaign: str
    min_boxes: int
    boxes: int

    @property
    def label(self) -> str:
        """Lo que dicen la tarjeta del pedido y el cobro: «gratis (6 cajas o más)»."""
        return f"gratis ({self.min_boxes} cajas o más)"

    def to_json(self) -> dict[str, Any]:
        return {"campaign": self.campaign, "min_boxes": self.min_boxes, "boxes": self.boxes}

    @classmethod
    def from_json(cls, raw: Any) -> FreeShipping | None:
        if not isinstance(raw, Mapping):
            return None
        campaign, min_boxes, boxes = raw.get("campaign"), raw.get("min_boxes"), raw.get("boxes")
        if not isinstance(campaign, str) or not campaign:
            return None
        if not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in (min_boxes, boxes)):
            return None
        return cls(campaign=campaign, min_boxes=min_boxes, boxes=boxes)


def _boxes(quantity: Any) -> int:
    if isinstance(quantity, bool):
        return 0
    if isinstance(quantity, int):
        return max(quantity, 0)
    if isinstance(quantity, str) and quantity.strip().isdigit():
        return int(quantity.strip())
    return 0


def free_shipping_for(
    items: Iterable[tuple[str, Any]], *, today: date, campaigns: Sequence[Campaign] | None = None
) -> FreeShipping | None:
    """¿El pedido va sin costo de envío? `items`: (handle, cantidad) de cada
    línea; para los productos de la campaña la cantidad es en cajas (su precio
    en Medusa es el de la caja). Lo da la campaña vigente hoy (`today`, día de
    Bogotá) con `envio_gratis_desde_cajas` cuando el pedido es SOLO de sus
    productos y la suma de las cajas (se pueden mezclar) llega al mínimo: con
    otro producto en el pedido, el envío se cobra (operador, 2026-10-20: «es
    solo para las velas del 7 de diciembre, las onduladas y las cuadradas»).
    Por el contenido del pedido y no por la conversación: vale igual en
    «Crear pedido», en el registro y en su reintento."""
    lines = [(str(handle), _boxes(quantity)) for handle, quantity in items]
    for campaign in active_campaigns() if campaigns is None else campaigns:
        minimum = campaign.envio_gratis_desde_cajas
        if not minimum or not campaign.is_on(today):
            continue
        if any(handle not in campaign.productos for handle, quantity in lines if quantity > 0):
            continue
        boxes = sum(quantity for handle, quantity in lines if handle in campaign.productos)
        if boxes >= minimum:
            return FreeShipping(campaign=campaign.id, min_boxes=minimum, boxes=boxes)
    return None


def campaign_by_id(campaign_id: Any, *, today: date) -> Campaign | None:
    """La campaña vigente hoy con ese id, o None."""
    if not isinstance(campaign_id, str) or not campaign_id:
        return None
    return next((c for c in active_campaigns() if c.id == campaign_id and c.is_on(today)), None)


def bogota_today() -> date:
    """El día de hoy en Bogotá (las fechas de las campañas son de allá)."""
    return datetime.now(_BOGOTA).date()

