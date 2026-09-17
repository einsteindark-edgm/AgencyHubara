"""Montos en COP citados en texto — funciones PURAS (sin I/O).

Incidente run ebbc203d (2026-09-16): el referral del anuncio traía
"💲 $45.000", el catálogo tenía el set a $49.500 y el LLM le escribió a la
clienta "tiene un valor de *$45.000 COP*". Tres usos de este módulo:

  * ``mask_prices``: el banner del referral CTWA que ve el LLM se inyecta con
    los montos del anuncio ENMASCARADOS — el anuncio no es fuente de precio.
  * ``find_unexplained_amounts``: al verificar el checkout se cruza lo que el
    bot ESCRIBIÓ en el episodio contra el catálogo. Un monto en una oración de
    precio de producto que no es precio de catálogo (ni múltiplo, ni suma de
    líneas) queda sin explicación → el LLM debe aclararlo ANTES del resumen.
    Los montos de política (umbral de contra entrega, tarifas mínimas de
    envío) solo se aceptan en oraciones con ese contexto ("contra entrega
    desde $45.000") — la MISMA cifra en "tiene un valor de $45.000" es una
    cotización equivocada.
  * El scorecard (sales_eval) usa las mismas funciones para auditar corridas.

Determinista: misma entrada → misma salida. Sin catálogo ni reloj.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

PRICE_MASK = "[precio omitido: usa el del catálogo]"

# "$45.000", "$ 49.500", "$45,000", "$49500" (4+ dígitos) | "16.940 pesos", "17500 COP".
# NO matchea teléfonos (10 dígitos sin $ ni COP) ni "$12" (menos de 4 dígitos
# sin separador de miles).
_AMOUNT = r"\d{1,3}(?:[.,]\d{3})+|\d{4,}"
COP_AMOUNT_RE = re.compile(
    rf"(?<![\w$])\$\s?({_AMOUNT})(?![\d.,]\d)"
    rf"|(?<![\w$.,])({_AMOUNT})\s?(?:cop|pesos)\b",
    re.IGNORECASE,
)

# Contexto de POLÍTICA: en estas oraciones un monto de umbral/tarifa es
# legítimo ("el contra entrega aplica desde $45.000", "te faltan $16.000").
_POLICY_KEYWORDS = (
    "contra entrega", "contraentrega", "envio", "domicilio", "flete",
    "transportadora", "minimo", "desde", "recargo", "tarifa", "umbral",
    "aplica", "faltan", "alcanza",
)

# Corte de oraciones: puntuación seguida de espacio, o salto de línea. NO
# corta en el "." de miles ("$45.000 COP").
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")

_MAX_QUANTITY = 5


@dataclass(frozen=True)
class UnexplainedAmount:
    amount: int
    sentence: str


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in stripped if not unicodedata.combining(c)).casefold()


def _to_int(raw: str) -> int:
    return int(re.sub(r"[.,]", "", raw))


def extract_cop_amounts(text: str | None) -> list[int]:
    """Montos COP del texto, en orden de aparición (duplicados incluidos)."""
    if not text:
        return []
    return [_to_int(m.group(1) or m.group(2)) for m in COP_AMOUNT_RE.finditer(text)]


def mask_prices(text: str | None) -> str:
    """Reemplaza cada monto COP por ``PRICE_MASK``; sin montos, identidad."""
    if not text:
        return text or ""
    return COP_AMOUNT_RE.sub(PRICE_MASK, text)


def split_sentences(text: str | None) -> list[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(text or "") if s and s.strip()]


def has_policy_context(sentence: str) -> bool:
    norm = _normalize(sentence)
    return any(k in norm for k in _POLICY_KEYWORDS)


def _explained_by_catalog(catalog_prices: set[int]) -> set[int]:
    """Precios unitarios, sus múltiplos (cantidad) y sumas de dos líneas."""
    lines = {p * q for p in catalog_prices for q in range(1, _MAX_QUANTITY + 1)}
    pairs = {a + b for a in lines for b in lines}
    return lines | pairs


def find_unexplained_amounts(
    text: str | None,
    *,
    catalog_prices: Iterable[int],
    policy_amounts: Iterable[int],
) -> list[UnexplainedAmount]:
    """Montos del texto que NO se explican por el catálogo ni por la política.

    Explicado = precio de catálogo, múltiplo por cantidad (1..5) o suma de dos
    líneas; en oraciones con contexto de política también los montos de
    política y sus combinaciones con un monto explicado (total con envío,
    "te faltan $X para el mínimo").
    """
    catalog = {int(p) for p in catalog_prices if int(p) > 0}
    policy = {int(p) for p in policy_amounts if int(p) > 0}
    explained = _explained_by_catalog(catalog)
    policy_explained = set(policy)
    for base in explained | {0}:
        for p in policy:
            policy_explained.add(base + p)
            policy_explained.add(abs(p - base))
    hits: list[UnexplainedAmount] = []
    for sentence in split_sentences(text):
        amounts = extract_cop_amounts(sentence)
        if not amounts:
            continue
        policy_ctx = has_policy_context(sentence)
        for amount in amounts:
            if amount in explained:
                continue
            if policy_ctx and amount in policy_explained:
                continue
            hits.append(UnexplainedAmount(amount=amount, sentence=" ".join(sentence.split())))
    return hits
