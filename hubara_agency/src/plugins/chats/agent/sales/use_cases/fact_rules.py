"""Las reglas de los hechos que el bot afirma: pago, estado del pedido, cupón y
lo que niega tener (puro, sin I/O; incidente 2026-10-13).

Cada regla propone lo que el texto le dice al cliente y no coincide con lo que
sabe el sistema, como pares `(lo que dice, lo cierto)`: «pagar contra
entrega» / «este pedido suma $25.000 en productos y contra entrega es desde
$45.000». Las capacidades del paquete (`pago_ajeno`, `estado_ajeno`,
`cupon_ajeno`, `negacion_falsa`) le preguntan a Jev por cada par si el texto de
verdad lo afirma; Jev solo quita. Las frases de lo cierto son las mismas que
lee Jev y que recibe el modelo en el mensaje (`reply_facts.fact_message`).

Las frases que buscan son de la tienda y viven en el paquete (el `with:` de
los ítems de cada capacidad, `PaymentWords` y compañía): un falso positivo se
corrige con otra frase en una versión nueva del paquete, no aquí. Aquí queda
lo que no es vocabulario: cómo se lee una frase (`phrases`), qué es una
cláusula y qué la niega (`product_truth`), los porcentajes y los códigos.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from src.plugins.chats.agent.sales.pricing import format_cop
from src.plugins.chats.shared.product_truth import affirms, clauses, denied_labels, sentences
from src.sdk.catalogkit import normalize_label

Claim = tuple[str, str]

# ── Las frases del paquete ──────────────────────────────────────────────────

#: «…» entre dos palabras: hasta tres palabras en medio («tu pago … confirmad*»
#: = «tu pago quedó confirmado», «tu pago ya fue confirmado»), nunca una
#: negación («tu pago no está confirmado» no lo dice).
_GAP = r"(?:(?!(?:no|ni|tampoco|nunca)\b)\w+\s+){0,3}"
_NEVER = r"(?!)"


@lru_cache(maxsize=256)
def phrases(spec: str) -> str:
    """El regex de una lista de frases del paquete, sobre el texto sin tildes
    ni mayúsculas (`normalize_label`). Frases separadas por comas, palabras
    completas; `*` al final de una palabra = cualquier final («despachad*»:
    despachado, despachada); `a/b` = una u otra palabra («tu/su pago»); «…»
    (o «...») = hasta tres palabras en medio. Las tildes de la frase no
    importan. Sin frases, nada coincide. `ValueError` si una frase trae otra
    cosa que palabras (nunca un regex del paquete)."""
    out: list[str] = []
    for raw in spec.split(","):
        tokens = ["…" if t == "..." else t for t in raw.split()]
        if not tokens:
            continue
        if tokens[0] == "…" or tokens[-1] == "…":
            raise ValueError(f"«…» va entre dos palabras: {raw.strip()!r}")
        parts: list[str] = []
        for i, token in enumerate(tokens):
            if token == "…":
                parts.append(_GAP)
                continue
            parts.append(_word(token, raw))
            if i + 1 < len(tokens):
                parts.append(r"\s+")
        out.append(r"\b" + "".join(parts) + r"\b")
    return "|".join(f"(?:{p})" for p in out) or _NEVER


def _word(token: str, raw: str) -> str:
    alternatives: list[str] = []
    for alt in token.split("/"):
        star = alt.endswith("*")
        core = normalize_label(alt[:-1] if star else alt)
        if not re.fullmatch(r"[^\W_]+", core):
            raise ValueError(f"palabra inválida {alt!r} en la frase {raw.strip()!r}")
        alternatives.append(re.escape(core) + (r"\w*" if star else ""))
    return f"(?:{'|'.join(alternatives)})"


def says_any(text: str | None, spec: str) -> bool:
    """¿Alguna cláusula del texto dice una de las frases (sin mirar negaciones)?"""
    regex = re.compile(phrases(spec))
    return any(regex.search(c) for c in clauses(text))


def offering_sentences(text: str | None, *, offers: str, not_offers: str) -> str:
    """Las oraciones del texto que ofrecen algo (una frase de `offers`: «viene
    en», «te las dejo») y no hablan de otra cosa (`not_offers`: el empaque,
    las notas de un aroma, otro producto), una por línea. Lo que juzga una
    regla que decide sola (premortem del PR #426: nombres, notas y empaques)."""
    says, other = re.compile(phrases(offers)), re.compile(phrases(not_offers))
    kept = []
    for sentence in sentences(text):
        norm = normalize_label(sentence)
        if says.search(norm) and not other.search(norm):
            kept.append(sentence)
    return "\n".join(kept)


# ── Pago ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PaymentWords:
    """Las frases de `pago_ajeno` (el `with:` de sus ítems en el paquete)."""

    #: Ofrecer pagar al recibir: «contra entrega», «pagas al recibir».
    ofrece_contra_entrega: str
    #: La condición general («desde $45.000», «a partir de…») o una condición
    #: sobre el pedido («si agregas otra vela…»): enuncia la regla, no le
    #: ofrece nada a su pedido (sonda 2026-10-13).
    regla_general: str
    #: Una cláusula que habla del recargo (no «3 cuotas al 0 %»)…
    habla_de_recargo: str
    #: …y no de un descuento.
    no_es_recargo: str
    #: El link de pago: el «sin recargo» que cuenta es el suyo (no el de Nequi directo).
    habla_del_link: str
    sin_recargo: str


_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
COD_TERM = "puede pagar su pedido contra entrega"
NO_SURCHARGE_TERM = "el link de pago no tiene recargo"


def cod_fact(subtotal: int, cod_min: int) -> str:
    return f"este pedido suma {format_cop(subtotal)} en productos y contra entrega es desde {format_cop(cod_min)}"


def surcharge_fact(surcharges: Sequence[str]) -> str:
    first, *rest = list(surcharges) or ["(sin dato)"]
    others = f" y {rest[0]} con otros bancos" if rest else ""
    return f"el recargo del link de pago es {first} con Nequi o Bancolombia{others}"


def percent(raw: str) -> float:
    """«1,5», «1.5», «1,50» y «1,5 %» son el mismo porcentaje."""
    return float(raw.replace("%", "").strip().replace(",", "."))


_percent = percent


def payment_claims(
    text: str | None, *, subtotal: int | None, cod_min: int, surcharges: Sequence[str], words: PaymentWords
) -> list[Claim]:
    """Contra entrega ofrecida a un pedido que no llega al mínimo, y un recargo
    del link distinto del configurado (o «sin recargo»)."""
    out: list[Claim] = []
    general = re.compile(phrases(words.regla_general))
    surcharge, not_surcharge = re.compile(phrases(words.habla_de_recargo)), re.compile(phrases(words.no_es_recargo))
    link = re.compile(phrases(words.habla_del_link))
    offers = [c for c in clauses(text, keep_parens=True) if not general.search(c)]
    if subtotal is not None and subtotal < cod_min and any(affirms(c, phrases(words.ofrece_contra_entrega)) for c in offers):
        out.append((COD_TERM, cod_fact(subtotal, cod_min)))
    allowed = {_percent(s) for s in surcharges}
    for clause in clauses(text):
        if not surcharge.search(clause) or not_surcharge.search(clause):
            continue
        for raw in _PERCENT.findall(clause):
            if _percent(raw) not in allowed:
                term = f"el recargo del link de pago es del {raw.replace('.', ',')} %"
                if term not in [t for t, _ in out]:
                    out.append((term, surcharge_fact(surcharges)))
    if any(affirms(c, phrases(words.sin_recargo)) for c in clauses(text) if link.search(c)):
        out.append((NO_SURCHARGE_TERM, surcharge_fact(surcharges)))
    return out


# ── Estado del pedido ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class StatusWords:
    """Las frases de `estado_ajeno` (el `with:` de sus ítems en el paquete)."""

    #: «Tu pago quedó confirmado» (no «el pago por Nequi…»: eso lista los medios de pago).
    pago_confirmado: str
    despachado: str
    entregado: str
    #: Lo que todavía no pasó: «será», «una vez…», «apenas…», «te avisamos».
    todavia_no: str


PAID_TERM = "el pago de su pedido quedó confirmado"
SHIPPED_TERM = "su pedido ya salió o va en camino"
DELIVERED_TERM = "su pedido ya fue entregado"
#: Lo que se le dice al cliente de cada etapa y pago (el mismo vocabulario de `check_order_status`).
STAGE_LABELS = {
    "new": "recibido", "preparing": "en preparación", "ready": "listo para envío",
    "shipping": "en camino", "delivered": "entregado", "cancelled": "cancelado",
}
PAY_LABELS = {"paid": "confirmado", "partial": "con un abono", "pending": "pendiente", "refund": "reembolsado"}


def status_fact(order: str | None, *, stage: str | None = None, pay_status: str | None = None) -> str:
    which = f"pedido {order}" if order else "pedido"
    if pay_status is not None:
        return f"el pago del {which} está {PAY_LABELS.get(pay_status, pay_status)}"
    return f"el {which} está {STAGE_LABELS.get(stage or '', stage)}"


def status_claims(
    text: str | None, *, order: str | None, stage: str | None, pay_status: str | None, words: StatusWords
) -> list[Claim]:
    """El pago dado por confirmado, el pedido dado por despachado o por
    entregado, cuando el sistema dice otra cosa (sin pedido, nada)."""
    out: list[Claim] = []
    not_yet = phrases(words.todavia_no)

    def says(spec: str) -> bool:
        # «apenas recibamos tu pago» no lo dice; «tu pago quedó confirmado y te
        # avisamos cuando…» sí (lo que viene después no lo vuelve futuro).
        return affirms(text, phrases(spec), unless=not_yet)

    if pay_status is not None and pay_status != "paid" and says(words.pago_confirmado):
        out.append((PAID_TERM, status_fact(order, pay_status=pay_status)))
    if stage is not None and stage not in ("shipping", "delivered") and says(words.despachado):
        out.append((SHIPPED_TERM, status_fact(order, stage=stage)))
    if stage is not None and stage != "delivered" and says(words.entregado):
        out.append((DELIVERED_TERM, status_fact(order, stage=stage)))
    return out


def talks_about_order(text: str | None, words: StatusWords) -> bool:
    """¿Vale la pena consultar el pedido? Solo si, con un pedido sin pagar ni
    despachar, el texto diría algo (sin eso no se consulta Medusa)."""
    return bool(status_claims(text, order=None, stage="new", pay_status="pending", words=words))


# ── Cupones ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CouponWords:
    """Las frases de `cupon_ajeno` (el `with:` de sus ítems en el paquete)."""

    #: Lo que nombra un código como código: «el cupón amor2026».
    nombra_codigo: str
    #: Una cláusula que habla de un descuento (sus porcentajes se comparan)…
    habla_de_descuento: str
    #: …y no del recargo del link.
    no_es_descuento: str


#: Un código escrito como código: letras (y números) en mayúscula, desde 4
#: («AMOR2026», «HALLOWEEN»); sin palabras de cupón igual se consultan.
_CODE_LIKE = re.compile(r"(?<!\w)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{4,}(?!\w)")


def _names_code(text: str | None, code: str, words: CouponWords) -> bool:
    """¿El texto nombra el código como código? Escrito tal cual («AMOR2026»,
    «HALLOWEEN») o, en otra forma, junto a «cupón» o «código»: «las velas de
    Halloween» y «te damos la bienvenida» no nombran un código."""
    as_written = re.search(rf"(?<!\w){re.escape(code)}(?!\w)", text or "")
    key = rf"\b{re.escape(normalize_label(code))}\b"
    names = re.compile(phrases(words.nombra_codigo))
    with_word = any(names.search(c) and re.search(key, c) for c in clauses(text))
    return bool(as_written or with_word) and affirms(text, key)


def coupons_fact(valid_lines: Sequence[str]) -> str:
    return "los cupones vigentes son: " + "; ".join(valid_lines) if valid_lines else "no hay cupones vigentes"


def coupon_claims(
    text: str | None, *, known: Sequence[str], valid: Sequence[str], valid_lines: Sequence[str],
    valid_percents: Sequence[int], words: CouponWords,
) -> list[Claim]:
    """Un código que Medusa conoce y no está vigente para este cliente, o un
    porcentaje de descuento que ningún cupón vigente da."""
    out: list[Claim] = []
    fact = coupons_fact(valid_lines)
    valid_codes = {c.casefold() for c in valid}
    for code in known:
        if code.casefold() not in valid_codes and _names_code(text, code, words):
            out.append((code, fact))
    discount, not_discount = re.compile(phrases(words.habla_de_descuento)), re.compile(phrases(words.no_es_descuento))
    for clause in clauses(text):
        if not discount.search(clause) or not_discount.search(clause):
            continue
        for raw in _PERCENT.findall(clause):
            if _percent(raw) not in {float(p) for p in valid_percents}:
                term = f"un descuento del {raw.replace('.', ',')} %"
                if term not in [t for t, _ in out]:
                    out.append((term, fact))
    return out


def talks_about_coupons(text: str | None, words: CouponWords) -> bool:
    """¿Vale la pena consultar los cupones? Si el texto nombra un código o un
    descuento, o escribe algo como un código (sin eso no se consulta Medusa)."""
    return (
        says_any(text, words.nombra_codigo) or says_any(text, words.habla_de_descuento)
        or bool(_CODE_LIKE.search(text or ""))
    )


# ── Lo que niega tener ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class DenialWords:
    """Las frases de `negacion_falsa` (el `with:` de sus ítems en el paquete).
    Los verbos van detrás de «no», «ya no», «tampoco» o «ni» (y un pronombre:
    «no la manejamos»); eso es gramática y queda en `product_truth`."""

    #: Negar TENERLO (un producto, un aroma…): «no tenemos», «ya no hay».
    niega_tener: str
    #: Además, negar cómo VIENE (un aroma, un color, una presentación): «no viene en».
    niega_venir: str
    #: Lo que parece negación y no niega tener nada: «no hay problema».
    no_es_negacion: str


def denial_claims(
    text: str | None, *, titles: Sequence[str], own: Sequence[str], products: Sequence[str], words: DenialWords
) -> list[Claim]:
    """Lo que el texto niega tener y el catálogo sí tiene: un producto del
    catálogo, o un aroma, color o presentación del producto del que habla."""
    which = " y ".join(products) or "el producto del que hablan"
    have, comes, idioms = phrases(words.niega_tener), phrases(words.niega_venir), phrases(words.no_es_negacion)
    # Un producto solo se niega con un verbo de tenerlo; un aroma, un color o
    # una presentación también con «no viene en».
    out: list[Claim] = [(t, f"«{t}» está en el catálogo") for t in denied_labels(text, titles, verbs=have, ignore=idioms)]
    out += [
        (label, f"{which} sí viene en «{label}»")
        for label in denied_labels(text, own, verbs=f"{have}|{comes}", ignore=idioms)
    ]
    return out


__all__ = [
    "COD_TERM",
    "CouponWords",
    "DenialWords",
    "PaymentWords",
    "StatusWords",
    "coupon_claims",
    "denial_claims",
    "offering_sentences",
    "payment_claims",
    "percent",
    "phrases",
    "says_any",
    "status_claims",
    "talks_about_coupons",
    "talks_about_order",
]
