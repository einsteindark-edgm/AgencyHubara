"""Classifier de estado conversacional para el dashboard ads.

Mapea el estado de un episodio (cierre o tag actual + timing) al `AdsState`
que el frontend del plugin `ads` espera:

    "no_reply" | "nuevo" | "activo" | "calificado" | "cotizado" | "ganado" | "perdido"

Dos APIs:

- `classify_episode_state` (PRIMARY) — recibe un episodio + el tag actual
  del metadata. Para sesiones con `episodes[]` poblado (modelo nuevo).
- `classify_state` (LEGACY) — recibe la metadata raíz. Mantiene compatibilidad
  para sesiones sin `episodes[]` (pre-feature).

Heurística pura, sin parsear JSONL (eso queda para un PR siguiente cuando
tengamos `journey_milestones`).

Prioridad de señales (de mayor a menor confianza):

  1. `episode.order_id` is not None         → ganado    (Medusa registró venta)
  2. `episode.closing_tag == COMPRA_EXITOSA` → ganado
  3. `episode.closing_tag == RECHAZO`        → perdido
  4. `episode.closing_tag == CONFIRMADO_SIN_DATOS` → cotizado
  5. `episode.closing_tag == CONFIRMADO_PAGO_PENDIENTE` → ganado
       (orden registrada en Medusa, falta solo verificación humana del
       pago — para el dashboard de ads ya es "ganado" porque la intención
       de compra cerró desde el cliente; el ajuste a "perdido" se hace si
       el humano aborta el pedido y reabre el chat).
  6. episode.closed_at_ms != null (otro)     → cotizado  (cierre desconocido, conservador)
  7. current_tag == INTERESADO               → calificado
  8. current_tag in HUMANO/RETOMA_VENTA      → activo
  9. NO_ETIQUETADO + total_msgs ≤ 2          → nuevo
  10. NO_ETIQUETADO + last_inbound ≥ 24h     → no_reply
  11. fallback                                → activo
"""
from __future__ import annotations

from typing import Any

# Umbral para clasificar como `no_reply`. Si el último inbound del cliente
# es más viejo que esto y la conversación no tiene cierre formal, el
# cliente quedó frío.
NO_REPLY_THRESHOLD_MS: int = 24 * 60 * 60 * 1000  # 24 horas

# Umbral para `nuevo`. Conversación con pocos mensajes del cliente todavía
# no avanzó lo suficiente para clasificarla como `activo` / `no_reply`.
NEW_CONVERSATION_MSG_THRESHOLD: int = 2


# Set canónico de estados válidos — útil para validación downstream.
VALID_STATES: frozenset[str] = frozenset(
    {"no_reply", "nuevo", "activo", "calificado", "cotizado", "ganado", "perdido"}
)


def classify_episode_state(
    episode: dict[str, Any],
    *,
    current_tag: str | None,
    total_msgs: int,
    last_inbound_ms: int | None,
    now_ms: int,
) -> str:
    """Clasifica un episodio en uno de los 7 `AdsState`.

    Args:
      episode: dict con shape de `episode_lifecycle.py` (closing_tag,
        order_id, closed_at_ms, started_at_ms).
      current_tag: `metadata.tag` actual. Aplica solo si el episodio está
        activo (cerrado: se usa episode.closing_tag). El caller decide si
        pasa None para episodios cerrados o el valor real.
      total_msgs: cantidad de mensajes (line count del JSONL — proxy de
        turn count). Simplificación: usa total global de la sesión, no
        por episodio.
      last_inbound_ms: ms epoch del último inbound del cliente. None si
        no se pudo derivar.
      now_ms: ms epoch actual, por DI.

    Returns:
      String del `AdsState`. Siempre uno de `VALID_STATES`.
    """
    # 1. Episodio con order_id → venta cerrada (Medusa registró).
    if episode.get("order_id"):
        return "ganado"

    closing_tag = episode.get("closing_tag")

    # 2-5. Cierre formal via tag.
    if closing_tag == "COMPRA_EXITOSA":
        return "ganado"
    if closing_tag == "RECHAZO":
        return "perdido"
    if closing_tag == "CONFIRMADO_SIN_DATOS":
        return "cotizado"
    if closing_tag == "CONFIRMADO_PAGO_PENDIENTE":
        # La orden está registrada en Medusa. Para el funnel ads cuenta
        # como ganado — la intención del cliente cerró exitosamente; el
        # paso humano de verificar el pago no es responsabilidad del LLM
        # ni del cliente. Si el humano aborta el pedido, el dashboard de
        # orders revierte el estado de la orden en Medusa, no acá.
        return "ganado"

    # 6. Cierre con tag desconocido (defensivo) — conservador: cotizado.
    if episode.get("closed_at_ms") is not None:
        return "cotizado"

    # 6-7. Episodio ACTIVO con tag intermedio (NO de cierre).
    if current_tag == "INTERESADO":
        return "calificado"
    if current_tag in ("HUMANO", "RETOMA_VENTA"):
        return "activo"

    # 8-10. NO_ETIQUETADO o tag desconocido — heurística por timing/msgs.
    if total_msgs <= NEW_CONVERSATION_MSG_THRESHOLD:
        return "nuevo"

    if last_inbound_ms is not None:
        age_ms = now_ms - last_inbound_ms
        if age_ms >= NO_REPLY_THRESHOLD_MS:
            return "no_reply"

    return "activo"


# Tags que cierran formalmente — refleja `episode_lifecycle.CLOSING_TAGS`.
# Repetido acá para evitar import circular (este módulo está más arriba en
# el grafo de imports que `episode_lifecycle`).
_CLOSING_TAG_SET: frozenset[str] = frozenset(
    {
        "COMPRA_EXITOSA",
        "RECHAZO",
        "CONFIRMADO_SIN_DATOS",
        "CONFIRMADO_PAGO_PENDIENTE",
    }
)


def classify_state(
    metadata: dict[str, Any],
    *,
    total_msgs: int,
    last_inbound_ms: int | None,
    now_ms: int,
) -> str:
    """Clasifica una sesión sin `episodes[]` (legacy fallback).

    Para sesiones pre-feature que NO migran a episodios automáticamente
    (e.g. seed data committeado en el repo). Reconstruye un "pseudo-episodio"
    a partir de los campos raíz de metadata y delega a
    `classify_episode_state`. Si el tag actual del metadata es de cierre
    (COMPRA_EXITOSA / RECHAZO / CONFIRMADO_SIN_DATOS), se mapea al
    `closing_tag` del pseudo-episodio (porque legacy no tenía concepto de
    episodio, el tag actual ES el cierre).
    """
    tag = metadata.get("tag")
    is_closing_tag = tag in _CLOSING_TAG_SET

    pseudo_episode: dict[str, Any] = {
        "order_id": None,
        "closing_tag": tag if is_closing_tag else None,
        # Si el tag es de cierre, asumimos closed (heurística legacy).
        # `closed_at_ms` placeholder solo para señalar "no activo".
        "closed_at_ms": 1 if is_closing_tag else None,
        "started_at_ms": None,
    }
    # Si hay registered_order success → ganado (señal más fuerte legacy).
    order = metadata.get("registered_order")
    if isinstance(order, dict) and order.get("success") is True:
        pseudo_episode["order_id"] = order.get("order_id", "legacy_order")

    # current_tag aplica solo a episodio activo. Si es de cierre, ya está
    # en closing_tag — no lo duplicamos en current_tag.
    current_tag = tag if not is_closing_tag else None

    return classify_episode_state(
        pseudo_episode,
        current_tag=current_tag,
        total_msgs=total_msgs,
        last_inbound_ms=last_inbound_ms,
        now_ms=now_ms,
    )
