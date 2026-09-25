"""Contexto REAL para el gancho de remarketing (puro, sin I/O).

Incidente run dc32f7fe (2026-09-10, wa_573000000005): el agente de
remarketing no ve el historial de Sales (el HistoryStore de exoclaw se aísla
por slug de workspace, PR #183) y el ciclo del Window Strategist le pisa el
`motivo` del tag con "Window Strategist: reactivación (<reason>)". Sin
contexto, el LLM inventó "quedó pendiente lo de tu pedido" a un cliente que
quería comprar cera y ya se había despedido.

Acá se digiere lo que el gancho necesita:
  * `tag_motivo`: el motivo que Sales anotó al etiquetar (metadata.motivo).
  * `has_order_draft`: si hay pedido a medias (misma derivación que la
    central: `lead_state_from_metadata`, Decisión #2 del Window Strategist).
  * `transcript`: los últimos mensajes VISIBLES del EPISODIO ACTIVO en el
    transcript del vault (`<vault>/<sid>/sessions/<sid>.jsonl`, el log que lee
    el dashboard) — no de toda la sesión (runs edbb0d8b / 8e73b7dc).
  * `campaign_context`: la campaña que abrió el episodio, si la hubo.
"""
from __future__ import annotations

import unicodedata
from typing import Any

from src.plugins.chats.agent.remarketing.contracts import RemarketingContext
from src.sdk.messagingkit import ladder_state, lead_state_from_metadata

#: cuántos mensajes visibles viajan al gancho (WhatsApp: 12 alcanzan para
#: entender por qué se frenó la charla sin inflar el prompt).
TRANSCRIPT_LIMIT = 12

_LABELS = {"user": "Cliente", "assistant": "Asesor"}


def _label(event: dict[str, Any]) -> str | None:
    role = event.get("role")
    if role not in _LABELS:
        return None
    if role == "assistant" and event.get("sender") == "human":
        return "Asesor (humano)"
    return _LABELS[role]


def render_transcript(events: list[dict[str, Any]], *, limit: int = TRANSCRIPT_LIMIT) -> str:
    """Eventos del JSONL → líneas `Cliente: …` / `Asesor: …` (últimas `limit`).

    Salta turnos sin texto visible (tool-calls puros, vacíos): lo que el
    cliente NO vio no ayuda a entender por qué se fue.
    """
    lines: list[str] = []
    for event in events:
        label = _label(event)
        if label is None:
            continue
        content = event.get("content")
        if not isinstance(content, str):
            continue
        text = " ".join(content.split())
        if not text:
            continue
        lines.append(f"{label}: {text}")
    return "\n".join(lines[-limit:]) if limit > 0 else ""


def _active_episode(meta: dict[str, Any]) -> dict[str, Any] | None:
    episodes = meta.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        return None
    last = episodes[-1]
    if not isinstance(last, dict) or last.get("closed_at_ms") is not None:
        return None
    return last


def episode_events(
    events: list[dict[str, Any]], episode: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Los eventos del transcript que pertenecen al episodio activo.

    Runs edbb0d8b / 8e73b7dc: el transcript era la cola de TODA la sesión y
    el gancho retomó la Trilogía del episodio anterior. `msgs_count_at_start`
    es el conteo del JSONL al abrir el episodio (antes de su primer inbound);
    sin él (episodios legacy) se conserva la cola de la sesión.
    """
    start = (episode or {}).get("msgs_count_at_start")
    if isinstance(start, int) and 0 <= start <= len(events):
        return events[start:]
    return events


def campaign_context_for(episode: dict[str, Any] | None) -> str:
    """La campaña que abrió el episodio, redactada para el trigger ("" si no)."""
    campaign = (episode or {}).get("opened_by_campaign")
    if not isinstance(campaign, dict):
        return ""
    name = campaign.get("campaign_name")
    parts = [f"Campaña «{name if isinstance(name, str) and name.strip() else 'de marketing'}»"]
    message = campaign.get("message")
    if isinstance(message, str) and message.strip():
        parts.append(f"lo que recibió: «{message.strip()}»")
    coupon = campaign.get("coupon_code")
    if isinstance(coupon, str) and coupon.strip():
        parts.append(f"cupón {coupon.strip()}")
    handles = [h for h in campaign.get("product_handles") or [] if isinstance(h, str) and h]
    if handles:
        parts.append("productos: " + ", ".join(handles))
    return " — ".join(parts)


#: valores que Medusa pone cuando el producto NO tiene un eje de selección
#: real (una sola variante): no son una "presentación".
_PLACEHOLDER_OPTION_VALUES = frozenset({"unico", "único", "default option value"})
#: tope de la descripción en la ficha: alcanza para anclar un detalle real sin
#: inflar el prompt (el gancho es 1-2 frases).
_FICHA_DESCRIPTION_CHARS = 280


def _fold(text: str) -> str:
    """minúsculas sin tildes: «CUBO DE CORAZON» ≡ «Cubo de corazón»."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def _tag_values(tags: list[str], prefix: str) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        head, sep, value = tag.partition(":")
        if sep and head.strip().lower() == prefix and value.strip() and value.strip() not in out:
            out.append(value.strip())
    return out


def _real_options(product: Any) -> dict[str, list[str]]:
    options = getattr(product, "options", None) or {}
    real: dict[str, list[str]] = {}
    for axis, values in options.items():
        kept = [v for v in values or [] if _fold(v) not in _PLACEHOLDER_OPTION_VALUES]
        if kept:
            real[axis] = kept
    return real


def _short(description: str | None) -> str:
    text = " ".join((description or "").split())
    if len(text) <= _FICHA_DESCRIPTION_CHARS:
        return text
    return text[:_FICHA_DESCRIPTION_CHARS].rsplit(" ", 1)[0] + "…"


def _ficha(product: Any) -> str:
    parts = [f"- {product.title}: {_short(product.description)}".rstrip(": ")]
    options = _real_options(product)
    if options:
        parts.append(
            "Opciones: " + "; ".join(f"{axis}: {', '.join(vals)}" for axis, vals in options.items())
        )
    else:
        parts.append(
            "Presentación única (no viene en otras formas, envases, tamaños ni versiones)"
        )
    colors = _tag_values(product.tags, "color")
    if colors:
        parts.append("Colores: " + ", ".join(colors))
    aromas = _tag_values(product.tags, "aroma")
    if aromas:
        parts.append("Aromas: " + ", ".join(aromas))
    return " | ".join(parts)


def catalog_facts_for(products: list[Any], *, mentioned: str) -> str:
    """(productos del snapshot, texto de la charla) → ficha para el gancho.

    Incidente 2026-09-25: el cliente preguntó por velas «en vaso» y mandó la
    foto de una vela de dragón — ninguna existe — y el gancho, sin catálogo,
    terminó afirmando «el Cubo Love también viene en vaso». Acá va lo que SÍ
    existe: todos los nombres del catálogo (para que lo que no esté ahí no se
    ofrezca) y la ficha real de los que se nombraron en la charla (motivo +
    transcript), con su presentación, colores y aromas.
    """
    if not products:
        return ""
    text = _fold(mentioned)
    named = [p for p in products if _fold(p.title) and _fold(p.title) in text]
    lines = [
        f"Productos que existen ({len(products)}): "
        + ", ".join(p.title for p in products)
        + ". Cualquier otro producto, forma, envase o presentación NO existe."
    ]
    if named:
        lines.append("Ficha de los productos de esta charla:")
        lines.extend(_ficha(p) for p in named)
    return "\n".join(lines)


def customer_text_for(metadata: dict[str, Any] | None, events: list[dict[str, Any]]) -> str:
    """Lo que el cliente escribió en el episodio activo + el motivo de Ventas.

    Todo el episodio, no solo la cola del transcript: en el incidente del
    2026-09-25 el «¿y en vaso también?» ya había salido de la ventana de 12
    mensajes (la llenaban los ganchos), pero seguía vivo en el motivo y en
    los ganchos que lo repetían.
    """
    meta = metadata or {}
    lines = [
        e["content"]
        for e in episode_events(events, _active_episode(meta))
        if e.get("role") == "user" and isinstance(e.get("content"), str)
    ]
    motivo = meta.get("motivo")
    if isinstance(motivo, str):
        lines.append(motivo)
    return "\n".join(lines)


def context_from_metadata(
    metadata: dict[str, Any] | None,
    events: list[dict[str, Any]],
    *,
    now_ms: int | None = None,
) -> RemarketingContext:
    """(metadata.json, eventos del transcript) → `RemarketingContext`.

    Con `now_ms` también digiere la escalera: qué toque es (peldaños ya
    consumidos + 1) y el silencio real del cliente — para que el trigger se lo
    DIGA al LLM en vez de dejarlo adivinar (runs `01a0b0da`…`01a0b586`).
    """
    meta = metadata or {}
    motivo = meta.get("motivo")
    lead = lead_state_from_metadata(meta)
    touch_number: int | None = None
    silence_minutes: int | None = None
    if now_ms is not None:
        touch_number = ladder_state(now_ms, meta).step + 1
        last_inbound = meta.get("last_inbound_at_ms")
        if isinstance(last_inbound, int):
            silence_minutes = max(0, (now_ms - last_inbound) // 60_000)
    episode = _active_episode(meta)
    return RemarketingContext(
        tag_motivo=motivo.strip() if isinstance(motivo, str) else "",
        has_order_draft=lead.has_order_draft,
        transcript=render_transcript(episode_events(events, episode)),
        touch_number=touch_number,
        silence_minutes=silence_minutes,
        campaign_context=campaign_context_for(episode),
    )
