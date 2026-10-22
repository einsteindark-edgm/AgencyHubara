"""Perfiles de la persona para el público de las campañas (operador, 2026-10-20).

«Un tag de perfilamiento a esos clientes, que está interesado pero que
todavía no [compra]», que sirva de filtro de público para las campañas de
WhatsApp. La etiqueta de la conversación (`metadata.tag`) cambia sola
(INTERESADO → SIN_RESPUESTA al terminar los toques) y no sirve de público; el
perfil queda en la persona:

    metadata["profile_tags"]["interesado_sin_compra"] = {
        "first_at_ms": la primera vez que mostró intención,
        "at_ms":       la última,
        "episode_id":  el episodio de la última,
        "source":      jev | regla | pedido_a_medias | pasada:<jev|etapa|…>,
        "campaign":    el guion de venta del episodio (`sales_campaign`) o None,
    }

Intención = lo mismo que usa el toque con la oferta del remarketing
(`sales_campaigns.purchase_intent`): eligió un producto, dio datos, aplazó o
confirmó (lo lee Jev con la capacidad `compra`), o tiene un pedido a medias.
Preguntar el precio es interés, no intención.

«Sin compra» no se borra a mano: el público lo calcula (ningún pedido en la
conversación desde ese episodio, `marketing/domain/campaigns.py`). Cuenta el
pedido que queda en la conversación (el `order_id` de un episodio, la
posventa); un pedido hecho solo en la tienda web no queda ahí y no saca a
nadie del filtro. Lo marcan el ingest de Ventas (en vivo) y la pasada
`scripts/backfill_interest_profile.py` (las conversaciones de antes).

PURO: muta el dict que recibe; quien llama lo escribe.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

from src.plugins.chats.shared.sales_campaigns import (
    EPISODE_KEY,
    INTENT_CHOICES,
    INTENT_CONFIDENCE,
    INTENT_SIGNALS,
    intent_rows,
    purchase_intent,
)

PROFILE_KEY = "profile_tags"
INTERESADO_SIN_COMPRA = "interesado_sin_compra"

#: Las etapas del embudo que dicen que ya eligió (la traza del turno).
_CHOSE_STAGES = frozenset({"variantes", "confirmacion", "datos_envio", "cierre"})


def _episodes(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [e for e in metadata.get("episodes") or [] if isinstance(e, dict)]


#: Cierres con pedido (los de `sales/use_cases/after_purchase.py`).
_CLOSED_WITH_ORDER = frozenset({"COMPRA_EXITOSA", "CONFIRMADO_PAGO_PENDIENTE", "CONFIRMADO_SIN_DATOS"})
#: Quien vuelve a escribir dentro de esto después de comprar es posventa
#: (`after_purchase.POSTSALE_MAX_DAYS`).
POSTSALE_DAYS = 30
_DAY_MS = 86_400_000


def is_post_sale(episodes: list[dict[str, Any]], index: int) -> bool:
    """¿El episodio `index` es posventa? Lleva la marca `after_order`, o el
    anterior cerró con su pedido hace menos de `POSTSALE_DAYS` (Medusa no
    respondió y no hubo marca). Revisión del 2026-10-20: «ya te transferí»
    abría un episodio sin pedido y Jev lo leía como intención."""
    episode = episodes[index]
    if episode.get("after_order"):
        return True
    if index == 0:
        return False
    previous = episodes[index - 1]
    if not (previous.get("order_id") or previous.get("closing_tag") in _CLOSED_WITH_ORDER):
        return False
    closed, started = previous.get("closed_at_ms"), episode.get("started_at_ms")
    if not isinstance(closed, (int, float)) or not isinstance(started, (int, float)):
        return True  # sin fechas no se sabe: del lado seguro
    return started - closed <= POSTSALE_DAYS * _DAY_MS


def stamp_interest(
    metadata: dict[str, Any], *, now_ms: int, source: str, episode: Mapping[str, Any] | None = None
) -> bool:
    """Marca el perfil con el episodio activo (o `episode`). No marca un
    episodio que ya tiene su pedido (ahí compró) ni uno de posventa
    (`is_post_sale`: acaba de comprar)."""
    episodes = _episodes(metadata)
    if episode is None:
        episode = episodes[-1] if episodes else None
    if not isinstance(episode, Mapping) or episode.get("order_id"):
        return False
    index = next((i for i, e in enumerate(episodes) if e is episode or e.get("episode_id") == episode.get("episode_id")), None)
    if index is not None and is_post_sale(episodes, index):
        return False
    profiles = metadata.get(PROFILE_KEY)
    if not isinstance(profiles, dict):
        profiles = {}
        metadata[PROFILE_KEY] = profiles
    before = profiles.get(INTERESADO_SIN_COMPRA)
    first = before.get("first_at_ms") if isinstance(before, Mapping) else None
    profiles[INTERESADO_SIN_COMPRA] = {
        "first_at_ms": first if isinstance(first, int) else now_ms,
        "at_ms": now_ms,
        "episode_id": episode.get("episode_id"),
        "source": source,
        "campaign": episode.get(EPISODE_KEY),
    }
    return True


def _answers_show_intent(answers: Iterable[Any]) -> bool:
    for answer in answers or ():
        if not isinstance(answer, Mapping) or answer.get("q") != "compra.que_hace":
            continue
        sure = answer.get("confidence", answer.get("p"))
        if answer.get("choice") in INTENT_CHOICES and isinstance(sure, (int, float)) and sure >= INTENT_CONFIDENCE:
            return True
    return False


def intent_in_reading(readings: Any) -> str | None:
    """La intención en las lecturas de ESTE mensaje (`decisions.readings`):
    «jev» si la leyó Jev (capacidad `compra`), «regla» si la leyó la regla de
    hoy (el aplazamiento del texto, el carrito, el botón «Confirmar»); None
    si no la hay."""
    compra = next(
        (v for v in getattr(readings, "verdicts", ()) or () if isinstance(v, Mapping) and v.get("capability") == "compra"),
        None,
    )
    if compra is not None and _answers_show_intent(compra.get("answers") or ()):
        return "jev"
    purchase = getattr(readings, "purchase", None) or (None, None)
    if purchase and purchase[0] in INTENT_SIGNALS:
        return "jev" if compra is not None and compra.get("by") == "jev" else "regla"
    return None


class HistoryInterest(NamedTuple):
    """Lo que encontró la pasada: el episodio, de dónde se sabe y cuándo
    mostró la intención (la lectura de Jev, la traza del turno o la señal; sin
    hora, el inicio del episodio)."""

    episode: dict[str, Any]
    source: str
    at_ms: int | None


def history_interest(
    metadata: Mapping[str, Any],
    *,
    decisions: Iterable[Mapping[str, Any]],
    traces: Iterable[Mapping[str, Any]],
) -> HistoryInterest | None:
    """Para la pasada por las conversaciones de antes: el último episodio en
    que quiso comprar, o None si nunca quiso o compró después. Fuentes, en
    orden: lo que leyó Jev (`evals/decisions.jsonl`), un pedido a medias, la
    etapa del embudo de sus turnos (`evals/turn_traces.jsonl`: ya había
    elegido) y la señal de compra del último mensaje."""
    episodes = _episodes(metadata)
    rows = [r for r in decisions if isinstance(r, Mapping)]
    # episodio → la última traza en que ya había elegido (None: sin hora).
    chose: dict[str, int | None] = {}
    for trace in traces:
        if isinstance(trace, Mapping) and trace.get("stage_out") in _CHOSE_STAGES:
            key, at = str(trace.get("episode_id")), trace.get("recorded_at_ms")
            known = [x for x in (chose.get(key), at) if isinstance(x, (int, float))]
            chose[key] = int(max(known)) if known else None
    signal = metadata.get("last_inbound_signal") if isinstance(metadata.get("last_inbound_signal"), Mapping) else {}
    for index in range(len(episodes) - 1, -1, -1):
        episode = episodes[index]
        source, at_ms = None, None
        start = episode.get("started_at_ms")
        eid = str(episode.get("episode_id"))
        following = episodes[index + 1].get("started_at_ms") if index + 1 < len(episodes) else None
        own_rows = [
            r for r in rows
            if isinstance(r.get("at_ms"), (int, float)) and (following is None or r["at_ms"] < following)
        ]
        read_by_jev = [r["at_ms"] for r in intent_rows(episode, own_rows)]
        if read_by_jev:
            source, at_ms = "jev", max(read_by_jev)
        elif purchase_intent(episode, ()):
            source, at_ms = "pedido_a_medias", chose.get(eid)
        elif eid in chose:
            source, at_ms = "etapa", chose[eid]
        elif (
            index == len(episodes) - 1
            and signal.get("kind") in INTENT_SIGNALS
            and isinstance(start, (int, float))
            and isinstance(signal.get("at_ms"), (int, float))
            and signal["at_ms"] >= start
        ):
            source, at_ms = "senal", signal["at_ms"]
        if source is None or is_post_sale(episodes, index):
            continue
        if any(e.get("order_id") or e.get("after_order") for e in episodes[index:]):
            return None
        if at_ms is None and isinstance(start, (int, float)):
            at_ms = start
        return HistoryInterest(episode, source, int(at_ms) if at_ms is not None else None)
    return None
