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
import re
from dataclasses import dataclass
from typing import Any

import unicodedata

from src.platform.attribution import matching_campaign_touch

#: Origen de la baja: el cliente respondió pidiéndola ("NO MÁS") …
OPT_OUT_SOURCE_TEXT = "texto"
#: … o la pidió desde WhatsApp (Meta rechaza el envío con 131050).
OPT_OUT_SOURCE_META = "meta"

#: Código de Meta: "el destinatario eligió dejar de recibir mensajes de
#: marketing de tu negocio". Es una baja hecha en WhatsApp, no un fallo.
META_OPT_OUT_ERROR_CODE = "131050"
_META_OPT_OUT_ERROR_TYPE = f"TemplateMetaError{META_OPT_OUT_ERROR_CODE}"

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


def is_opt_out_text(text: str) -> bool:
    """¿El texto pide la baja? Las frases de hoy (la regla de la capacidad
    «baja» del motor de decisiones y su piso: la frase explícita siempre
    cuenta)."""
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


#: Nombre anterior (lo usan tests y scripts de revisión).
_is_opt_out_text = is_opt_out_text


def has_recent_marketing_context(
    metadata: dict[str, Any], now_ms: int, *, text: str | None = None
) -> bool:
    """¿Le llegó hace poco algo que promete la baja? Una campaña (touch dentro
    de la ventana de atribución de 7 días) o una plantilla de la escalera de
    reactivación. Fuera de ese contexto, "no más" es conversación normal.

    La invitación a calificar (premortem C4) da contexto 48 h y, si viene el
    `text` del cliente, solo con una baja sin ambigüedad: tras un pedido
    entregado el cliente sigue comprando, y «quiero otra igual, no más que en
    azul» no puede ser una baja para siempre — pero «no quiero recibir más
    mensajes» sí lo es en cualquier mensaje. Sin `text`, cuenta (el piso
    legal: ante la duda, la baja)."""
    if (
        matching_campaign_touch(metadata.get("campaign_touches"), now_ms) is not None
        or _recent_ladder_template(metadata, now_ms)
    ):
        return True
    if not _recent_template(
        metadata.get("review_request_touches"), now_ms, window_ms=_REVIEW_TEMPLATE_CONTEXT_MS
    ):
        return False
    return text is None or _is_unambiguous_opt_out(text)


#: «no más» en Colombia también es «solo» («no más que en azul»): tras la
#: invitación a calificar cuenta solo si es el mensaje entero.
_AMBIGUOUS_PHRASES: frozenset[str] = frozenset({"no mas"})


def _is_unambiguous_opt_out(text: str) -> bool:
    """Tras la invitación a calificar: las frases sin ambigüedad («no quiero
    recibir», «de baja», «no me mandes»…) cuentan en cualquier mensaje; la
    ambigua («no más») y las palabras sueltas, solo como mensaje entero."""
    normalized = _normalize(text)
    if any(p in normalized for p in _OPT_OUT_PHRASES if p not in _AMBIGUOUS_PHRASES):
        return True
    return is_standalone_opt_out(text)


def detect_marketing_opt_out(
    text: str | None, metadata: dict[str, Any], now_ms: int
) -> bool:
    """True si este inbound es un pedido de baja de promociones: contexto de
    marketing reciente (`has_recent_marketing_context`) y la frase
    (`is_opt_out_text`)."""
    if not text:
        return False
    if not has_recent_marketing_context(metadata, now_ms, text=text):
        return False
    return is_opt_out_text(text)


#: Cortesías que pueden acompañar la frase sola («no más, gracias»).
_COURTESY_WORDS: frozenset[str] = frozenset(
    {"por", "favor", "porfa", "porfavor", "gracias", "ya", "mas", "plis", "please", "pls"}
)
#: Frases de baja que el cliente escribe solas (sobre texto normalizado).
_STANDALONE_PHRASES: tuple[tuple[str, ...], ...] = tuple(
    tuple(phrase.split()) for phrase in (*_OPT_OUT_PHRASES, *_OPT_OUT_SHORT_WORDS, "dame de baja", "denme de baja")
)


def is_standalone_opt_out(text: str) -> bool:
    """¿El mensaje es SOLO el pedido de baja? («NO MÁS», «No más 🙏», «no
    mas, gracias», «Baja», «No me escribas más»). Una frase de baja dentro de
    otra charla («quiero otra igual, no más que en azul») no lo es."""
    words = re.findall(r"[a-z0-9]+", _normalize(text))
    if not words:
        return False
    for phrase in _STANDALONE_PHRASES:
        size = len(phrase)
        for start in range(len(words) - size + 1):
            if tuple(words[start : start + size]) == phrase:
                rest = words[:start] + words[start + size :]
                if all(w in _COURTESY_WORDS for w in rest):
                    return True
    return False


#: Misma ventana que la atribución de campañas: una plantilla de hace más de
#: 7 días ya no es el contexto del "no más".
_LADDER_TEMPLATE_CONTEXT_MS = 7 * 24 * 60 * 60 * 1000
#: La invitación a calificar da contexto de baja menos tiempo (premortem C4):
#: después el cliente que escribe está comprando otra vez, no contestándola.
_REVIEW_TEMPLATE_CONTEXT_MS = 48 * 60 * 60 * 1000


def _recent_ladder_template(metadata: dict[str, Any], now_ms: int) -> bool:
    """¿Salió hace poco una PLANTILLA de la escalera de reactivación? Esas
    plantillas (`followup_interest_marketing_v1`, `cart_recovery_marketing_v2`)
    prometen la baja igual que la de campañas — hallazgo H-2 (2026-09-18): la
    promesa no se cumplía porque el detector solo miraba `campaign_touches`.
    Un gancho free-form NO cuenta: no promete baja."""
    return _recent_template(metadata.get("remarketing_touches"), now_ms)


def _recent_template(
    touches: Any, now_ms: int, *, window_ms: int = _LADDER_TEMPLATE_CONTEXT_MS
) -> bool:
    """¿Alguno de estos toques es una PLANTILLA de la ventana (7 días por
    defecto)? También la invitación a calificar (`review_request_touches`,
    reseñas 2026-10-13, 48 h): es de marketing (dice el descuento) y promete
    la baja."""
    for touch in touches or []:
        if (
            isinstance(touch, dict)
            and touch.get("kind") == "template"
            and isinstance(touch.get("at_ms"), int)
            and 0 <= now_ms - touch["at_ms"] <= window_ms
        ):
            return True
    return False


# =============================================================================
# Registro de la baja — quién, cuándo, por qué vía y qué campaña la provocó
# =============================================================================


@dataclass(frozen=True)
class MarketingOptOut:
    """La baja tal como quedó en el metadata. Campos None = baja registrada
    antes de guardar detalle (solo el flag), o sin campaña atribuible."""

    at_ms: int | None
    source: str | None
    campaign_id: str | None


def opt_out_campaign_id(metadata: dict[str, Any], now_ms: int) -> str | None:
    """La campaña que provocó la baja: el touch de campaña más reciente en
    ventana de atribución (el mismo criterio con que se atribuye una venta).
    None si el contexto era solo la escalera de remarketing."""
    touch = matching_campaign_touch(metadata.get("campaign_touches"), now_ms)
    if touch is None:
        return None
    campaign_id = touch.get("campaign_id")
    return campaign_id if isinstance(campaign_id, str) and campaign_id else None


def mark_marketing_opt_out(
    metadata: dict[str, Any],
    *,
    now_ms: int,
    source: str,
    campaign_id: str | None = None,
) -> dict[str, Any]:
    """Estampa la baja en el metadata (mutación in-place, devuelve el dict).

    Sticky: si ya estaba de baja NO se pisa el registro — la métrica de bajas
    por campaña cuenta la campaña que la PROVOCÓ, no la última que rebotó.
    Solo el operador la revierte editando el metadata.
    """
    if metadata.get("marketing_opt_out"):
        return metadata
    metadata["marketing_opt_out"] = True
    metadata["marketing_opt_out_at_ms"] = now_ms
    metadata["marketing_opt_out_source"] = source
    metadata["marketing_opt_out_campaign_id"] = campaign_id
    return metadata


def marketing_opt_out_info(metadata: dict[str, Any]) -> MarketingOptOut | None:
    """La baja registrada, o None si el contacto no está de baja. Tolera el
    metadata viejo (solo `marketing_opt_out: true`)."""
    if not metadata.get("marketing_opt_out"):
        return None
    at_ms = metadata.get("marketing_opt_out_at_ms")
    source = metadata.get("marketing_opt_out_source")
    campaign_id = metadata.get("marketing_opt_out_campaign_id")
    return MarketingOptOut(
        at_ms=at_ms if isinstance(at_ms, int) and not isinstance(at_ms, bool) else None,
        source=source if isinstance(source, str) and source else None,
        campaign_id=campaign_id if isinstance(campaign_id, str) and campaign_id else None,
    )


def is_meta_opt_out_failure(error_type: str | None) -> bool:
    """¿Este fallo del send es la baja del cliente en WhatsApp (131050)?

    `error_type` es el `type` del ApplicationError que levanta
    `send_template_to_session` (`TemplateMetaError<código>`). El workflow de
    campañas lo usa para registrar la baja en vez de contar un fallo."""
    return error_type == _META_OPT_OUT_ERROR_TYPE
