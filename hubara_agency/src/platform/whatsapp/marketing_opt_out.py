"""Detector determinista de opt-out de marketing (campañas directas).

El template de campañas (`campaign_promo_marketing`) promete al cliente
"respóndeme NO MÁS y te doy de baja". Este módulo cumple esa promesa: el
ingest de chats lo consulta en cada inbound de texto y, si el cliente pide
la baja DESPUÉS de una campaña reciente (touch en ventana de atribución),
estampa `marketing_opt_out=true` — que la audiencia de campañas excluye.

Sin LLM y sin red (puro): un pedido de baja no puede depender de que un
modelo lo interprete bien. Falso negativo = riesgo de sanción; falso
positivo = un cliente deja de recibir promos (recuperable por el operador
editando el metadata). El sesgo es deliberadamente conservador PERO con la
condición de campaña reciente para no confundir "no más velas por ahora"
de una charla de venta normal.
"""
from typing import Any

import unicodedata

from src.platform.attribution import matching_campaign_touch

#: Frases que piden la baja (sobre texto normalizado: lower + sin acentos).
_OPT_OUT_PHRASES: tuple[str, ...] = (
    "no mas",
    "no quiero recibir",
    "de baja",
    "no me envies",
    "no me escribas",
    "no me manden",
    "no me mandes",
    "unsubscribe",
)

#: Palabras que solas (mensaje corto) significan baja.
_OPT_OUT_SHORT_WORDS: frozenset[str] = frozenset({"baja", "stop"})
_SHORT_MESSAGE_MAX_WORDS = 4


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(stripped.split())


def _is_opt_out_text(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in _OPT_OUT_PHRASES):
        return True
    words = [w.strip(".,!¡¿?") for w in normalized.split()]
    if len(words) <= _SHORT_MESSAGE_MAX_WORDS and any(
        w in _OPT_OUT_SHORT_WORDS for w in words
    ):
        return True
    return False


def detect_marketing_opt_out(
    text: str | None, metadata: dict[str, Any], now_ms: int
) -> bool:
    """True si este inbound es un pedido de baja de promociones.

    Requiere una campaña reciente (touch dentro de la ventana de atribución
    de 7 días): fuera de ese contexto, "no más" es conversación normal.
    """
    if not text:
        return False
    if matching_campaign_touch(metadata.get("campaign_touches"), now_ms) is None:
        return False
    return _is_opt_out_text(text)
