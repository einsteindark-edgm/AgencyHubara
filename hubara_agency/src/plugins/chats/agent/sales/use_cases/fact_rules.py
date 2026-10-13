"""Las reglas de los hechos que el bot afirma: pago, estado del pedido, cupón y
lo que niega tener (puro, sin I/O; incidente 2026-10-13).

Cada regla propone lo que el texto le dice al cliente y no coincide con lo que
sabe el sistema, como pares `(lo que dice, lo cierto)`: «pagar contra
entrega» / «este pedido suma $25.000 en productos y contra entrega es desde
$45.000». Las capacidades del paquete (`pago_ajeno`, `estado_ajeno`,
`cupon_ajeno`, `negacion_falsa`) le preguntan a Jev por cada par si el texto de
verdad lo afirma; Jev solo quita. Las frases de lo cierto son las mismas que
lee Jev y que recibe el modelo en el mensaje (`reply_facts.fact_message`).
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from src.plugins.chats.agent.sales.pricing import format_cop
from src.plugins.chats.shared.product_truth import HAVE_DENIAL, affirms, clauses, denied_labels

Claim = tuple[str, str]

# ── Pago ────────────────────────────────────────────────────────────────────

#: Ofrecer pagar al recibir (sobre el texto sin tildes ni mayúsculas).
COD_RE = r"contra\s*entrega|pag\w*\s+(?:al|cuando)\s+(?:recibir|te\s+llegu\w*|llegu\w*)"
#: La condición general («contra entrega es desde $45.000», «a partir de…»):
#: enuncia la regla, no le ofrece nada a su pedido (sonda 2026-10-13).
_GENERAL_RULE = re.compile(
    r"\bdesde\b|a partir de|\bminimo\b|mas de|por encima de|en adelante|al menos|debe sumar|deben sumar"
    # …o una condición sobre el pedido («si agregas otra vela…»).
    r"|\bsi\s+(?:agregas|sumas|llevas|compras|pides|completas|anades|le sumas)"
)
#: Una cláusula que habla del recargo (no «3 cuotas al 0 %» ni otro porcentaje).
_SURCHARGE_CONTEXT = re.compile(r"recargo|comision")
#: El link de pago: el «sin recargo» que cuenta es el suyo (no el de Nequi directo).
_LINK = re.compile(r"\blink\b|enlace")
_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_NO_SURCHARGE = r"sin\s+(?:ningun\s+)?(?:recargo|comision|costo\s+adicional)|no\s+tiene\s+(?:ningun\s+)?(?:recargo|comision)"
COD_TERM = "puede pagar su pedido contra entrega"
NO_SURCHARGE_TERM = "el link de pago no tiene recargo"


def cod_fact(subtotal: int, cod_min: int) -> str:
    return f"este pedido suma {format_cop(subtotal)} en productos y contra entrega es desde {format_cop(cod_min)}"


def surcharge_fact(surcharges: Sequence[str]) -> str:
    first, *rest = list(surcharges) or ["(sin dato)"]
    others = f" y {rest[0]} con otros bancos" if rest else ""
    return f"el recargo del link de pago es {first} con Nequi o Bancolombia{others}"


def _percent(raw: str) -> float:
    """«1,5», «1.5» y «1,50» son el mismo porcentaje."""
    return float(raw.replace("%", "").strip().replace(",", "."))


def _surcharge_clauses(text: str | None) -> list[str]:
    return [c for c in clauses(text) if _SURCHARGE_CONTEXT.search(c) and "descuento" not in c]


def _link_clauses(text: str | None) -> list[str]:
    return [c for c in clauses(text) if _LINK.search(c)]


def payment_claims(text: str | None, *, subtotal: int | None, cod_min: int, surcharges: Sequence[str]) -> list[Claim]:
    """Contra entrega ofrecida a un pedido que no llega al mínimo, y un recargo
    del link distinto del configurado (o «sin recargo»)."""
    out: list[Claim] = []
    offers = [c for c in clauses(text) if not _GENERAL_RULE.search(c)]
    if subtotal is not None and subtotal < cod_min and any(affirms(c, COD_RE) for c in offers):
        out.append((COD_TERM, cod_fact(subtotal, cod_min)))
    allowed = {_percent(s) for s in surcharges}
    for clause in _surcharge_clauses(text):
        for raw in _PERCENT.findall(clause):
            if _percent(raw) not in allowed:
                term = f"el recargo del link de pago es del {raw.replace('.', ',')} %"
                if term not in [t for t, _ in out]:
                    out.append((term, surcharge_fact(surcharges)))
    if any(affirms(c, _NO_SURCHARGE) for c in _link_clauses(text)):
        out.append((NO_SURCHARGE_TERM, surcharge_fact(surcharges)))
    return out


# ── Estado del pedido ───────────────────────────────────────────────────────

#: «Tu pago» o «su pago» (no «el pago por Nequi…»: eso lista los medios de pago).
_PAID_RE = (
    r"(?:tu|su)\s+pago\s+(?:\w+\s+){0,3}(?:confirmad|recibid|aprobad|verificad|registrad)\w*"
    r"|(?:confirm|recib|aprob|verific)\w*\s+(?:\w+\s+){0,2}(?:tu|su)\s+pago"
)
_SHIPPED_RE = (
    r"despachad\w*|en\s+camino|ya\s+(?:lo|la|los|las)\s+(?:enviamos|despachamos)|ya\s+sali\w*"
    r"|(?:pedido|paquete|orden)\s+(?:\w+\s+){0,3}enviad[oa]"
)
_DELIVERED_RE = (
    r"(?:pedido|paquete|orden)\s+(?:\w+\s+){0,3}entregad[oa]"
    r"|ya\s+(?:te\s+)?lleg\w*\s+(?:tu|el|su)\s+(?:pedido|paquete|orden)"
)
#: Lo que todavía no pasó: «será», «una vez…», «apenas…», «cuando…», «te avisamos».
_NOT_YET = re.compile(
    r"\b(?:cuando|apenas|una vez|en cuanto|sera|seran|vamos a|va a|van a|te avisamos|te aviso|te avisare|"
    r"te confirmo|te confirmamos|te escribimos|te escribo)\b"
)
PAID_TERM = "el pago de su pedido quedó confirmado"
SHIPPED_TERM = "su pedido ya salió o va en camino"
DELIVERED_TERM = "su pedido ya fue entregado"
#: Lo que se le dice al cliente de cada etapa y pago (el mismo vocabulario de `check_order_status`).
STAGE_LABELS = {
    "new": "recibido", "preparing": "en preparación", "ready": "listo para envío",
    "shipping": "en camino", "delivered": "entregado", "cancelled": "cancelado",
}
PAY_LABELS = {"paid": "confirmado", "partial": "con un abono", "pending": "pendiente", "refund": "reembolsado"}
#: Sin alguna de estas formas no hay nada que comparar: no se consulta el pedido.
STATUS_HINT = re.compile(f"{_PAID_RE}|{_SHIPPED_RE}|{_DELIVERED_RE}")


def status_fact(order: str | None, *, stage: str | None = None, pay_status: str | None = None) -> str:
    which = f"pedido {order}" if order else "pedido"
    if pay_status is not None:
        return f"el pago del {which} está {PAY_LABELS.get(pay_status, pay_status)}"
    return f"el {which} está {STAGE_LABELS.get(stage or '', stage)}"


def status_claims(text: str | None, *, order: str | None, stage: str | None, pay_status: str | None) -> list[Claim]:
    """El pago dado por confirmado, el pedido dado por despachado o por
    entregado, cuando el sistema dice otra cosa (sin pedido, nada)."""
    out: list[Claim] = []
    now = [c for c in clauses(text) if not _NOT_YET.search(c)]

    def says(pattern: str) -> bool:
        return any(affirms(c, pattern) for c in now)

    if pay_status is not None and pay_status != "paid" and says(_PAID_RE):
        out.append((PAID_TERM, status_fact(order, pay_status=pay_status)))
    if stage is not None and stage not in ("shipping", "delivered") and says(_SHIPPED_RE):
        out.append((SHIPPED_TERM, status_fact(order, stage=stage)))
    if stage is not None and stage != "delivered" and says(_DELIVERED_RE):
        out.append((DELIVERED_TERM, status_fact(order, stage=stage)))
    return out


# ── Cupones ─────────────────────────────────────────────────────────────────

_DISCOUNT_CONTEXT = re.compile(r"descuento|cupon|promo|rebaja|\boff\b")
#: Las palabras que hacen falta para consultar los cupones (sin ellas, no se mira Medusa).
COUPON_HINT = re.compile(r"cupon|codigo|descuento|promo|rebaja|%")


_CODE_CONTEXT = re.compile(r"cupon|codigo")


def _names_code(text: str | None, code: str) -> bool:
    """¿El texto nombra el código como código? Escrito tal cual («AMOR2026»,
    «HALLOWEEN») o, en otra forma, junto a «cupón» o «código»: «las velas de
    Halloween» y «te damos la bienvenida» no nombran un código."""
    as_written = re.search(rf"(?<!\w){re.escape(code)}(?!\w)", text or "")
    key = rf"\b{re.escape(code.casefold())}\b"
    with_word = any(_CODE_CONTEXT.search(c) and re.search(key, c) for c in clauses(text))
    return bool(as_written or with_word) and affirms(text, key)


def coupons_fact(valid_lines: Sequence[str]) -> str:
    return "los cupones vigentes son: " + "; ".join(valid_lines) if valid_lines else "no hay cupones vigentes"


def coupon_claims(
    text: str | None, *, known: Sequence[str], valid: Sequence[str], valid_lines: Sequence[str],
    valid_percents: Sequence[int],
) -> list[Claim]:
    """Un código que Medusa conoce y no está vigente para este cliente, o un
    porcentaje de descuento que ningún cupón vigente da."""
    out: list[Claim] = []
    fact = coupons_fact(valid_lines)
    valid_codes = {c.casefold() for c in valid}
    for code in known:
        if code.casefold() not in valid_codes and _names_code(text, code):
            out.append((code, fact))
    for clause in clauses(text):
        if not _DISCOUNT_CONTEXT.search(clause) or "recargo" in clause:
            continue
        for raw in _PERCENT.findall(clause):
            if _percent(raw) not in {float(p) for p in valid_percents}:
                term = f"un descuento del {raw.replace('.', ',')} %"
                if term not in [t for t, _ in out]:
                    out.append((term, fact))
    return out


# ── Lo que niega tener ──────────────────────────────────────────────────────


def denial_claims(
    text: str | None, *, titles: Sequence[str], own: Sequence[str], products: Sequence[str]
) -> list[Claim]:
    """Lo que el texto niega tener y el catálogo sí tiene: un producto del
    catálogo, o un aroma, color o presentación del producto del que habla."""
    which = " y ".join(products) or "el producto del que hablan"
    # Un producto solo se niega con un verbo de tenerlo; un aroma, un color o
    # una presentación también con «no viene en».
    out: list[Claim] = [(t, f"«{t}» está en el catálogo") for t in denied_labels(text, titles, how=(HAVE_DENIAL,))]
    out += [(label, f"{which} sí viene en «{label}»") for label in denied_labels(text, own)]
    return out


__all__ = [
    "COD_TERM",
    "COUPON_HINT",
    "STATUS_HINT",
    "coupon_claims",
    "denial_claims",
    "payment_claims",
    "status_claims",
]
