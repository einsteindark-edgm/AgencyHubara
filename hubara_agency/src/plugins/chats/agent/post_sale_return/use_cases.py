"""Plan puro del scheduler post-venta (cero I/O — R-DET friendly)."""
from __future__ import annotations

from typing import Any

#: El estado que deja `apply_payment_confirmation_to_chat_metadata` a
#: propósito: compra cerrada por el humano, conversación aún en su bandeja.
_TARGET_TAG = "COMPRA_EXITOSA"
_TARGET_ROUTE = "humano"


def _has_confirmed_payment(metadata: dict[str, Any]) -> bool:
    """True si algún episodio COMPRA_EXITOSA lleva la marca de pago verificado
    (`payment_confirmed_at_ms`, la escribe `apply_payment_confirmation_to_
    chat_metadata` cuando el humano confirma el pago desde orders)."""
    episodes = metadata.get("episodes")
    if not isinstance(episodes, list):
        return False
    return any(
        isinstance(ep, dict)
        and ep.get("closing_tag") == _TARGET_TAG
        and isinstance(ep.get("payment_confirmed_at_ms"), int)
        for ep in episodes
    )


def is_returnable(metadata: dict[str, Any]) -> bool:
    """Predicado ÚNICO de la devolución a sales (lo usan el scan y el re-check
    fresco bajo lock del mutator — misma regla en los dos momentos).

    Regla de negocio: SIN pago confirmado no se devuelve — un tag
    COMPRA_EXITOSA puesto por el bot no alcanza; la conversación se queda en
    humano hasta que el humano verifique el pago.
    """
    return (
        isinstance(metadata, dict)
        and metadata.get("active_route") == _TARGET_ROUTE
        and metadata.get("tag") == _TARGET_TAG
        and _has_confirmed_payment(metadata)
    )


def select_post_sale_sessions(
    sessions: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    """Sesiones que el scheduler diario devuelve al bot de ventas.

    Metadata ausente/malformada nunca selecciona (mejor no tocar que tocar
    mal).
    """
    return [
        session_id for session_id, metadata in sessions if is_returnable(metadata)
    ]
