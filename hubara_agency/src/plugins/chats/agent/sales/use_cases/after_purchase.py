"""El cliente escribe justo después de comprar: post-venta, no bienvenida.

Caso del 2026-10-09 (pedido #64): el humano vendió, «Confirmar pago» devolvió
la conversación al bot y el cliente escribió 6 minutos después (citando su
comprobante). El episodio nuevo arrancó con la nota «saluda con calidez y
pregunta en qué puedes ayudar hoy», afirmando que el pago estaba en
verificación (ya estaba confirmado), y en la etapa de descubrimiento: el bot le
dio la bienvenida como a un cliente nuevo.

Con el pedido anterior AÚN EN CURSO (ni entregado ni cancelado, y de los
últimos ``POSTSALE_MAX_DAYS`` días), el episodio nuevo:

* lleva la marca ``after_order`` (solo la identidad del pedido) → la etapa es
  ``etapa_postcierre`` (`funnel_stage.resolve_funnel_stage`);
* abre con la nota de post-venta, con los datos REALES del pedido: etapa y
  pago salen de OrderFacts (gotcha 13), nunca de la copia del vault.

Sin los datos (Medusa caído o lento) no se inventa nada: la nota y la etapa de
siempre. Tuteo colombiano (REGLA #1).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

#: Lee los datos de UN pedido (OrderFacts) o ``None`` si no se conocen.
OrderFactsReader = Callable[[str], Awaitable[Any]]

#: Cierres con pedido (los mismos que la nota de frontera).
CLOSED_WITH_ORDER = frozenset({"COMPRA_EXITOSA", "CONFIRMADO_PAGO_PENDIENTE", "CONFIRMADO_SIN_DATOS"})
#: Después de esto, aunque el pedido figure abierto en Medusa, el cliente que
#: vuelve no viene por él (un pedido que nadie movió de etapa).
POSTSALE_MAX_DAYS = 30
_DAY_MS = 86_400_000
_ENDED = frozenset({"delivered", "cancelled"})
_STAGE_LABEL = {
    "new": "recibido, todavía sin entrar en preparación",
    "preparing": "en preparación",
    "ready": "empacado y listo para salir",
    "shipping": "en camino",
}


def order_in_course(prev_episode: dict[str, Any], facts: Any, *, now_ms: int) -> bool:
    """¿El cliente que vuelve tiene ese pedido todavía en curso?"""
    if facts is None or prev_episode.get("closing_tag") not in CLOSED_WITH_ORDER:
        return False
    if not prev_episode.get("order_id") or getattr(facts, "stage", None) in _ENDED:
        return False
    closed = prev_episode.get("closed_at_ms")
    return isinstance(closed, (int, float)) and now_ms - closed <= POSTSALE_MAX_DAYS * _DAY_MS


def after_order_marker(facts: Any) -> dict[str, Any]:
    """La marca del episodio nuevo: solo la identidad del pedido (lo que
    cambia, etapa y pago, se lee siempre de OrderFacts)."""
    return {"order_id": facts.order_id, "display_id": facts.display_id}


def build_after_purchase_note(facts: Any, *, courtesy: bool = False) -> str:
    """Nota del episodio que abre con el pedido en curso."""
    reference = str(facts.display_id or facts.order_id)
    stage = _STAGE_LABEL.get(str(facts.stage), "en curso")
    if facts.pay_status == "paid":
        payment = (
            "El pago ya está confirmado: si el cliente pregunta por el pago o "
            "manda el comprobante, dile que su pago quedó confirmado."
        )
    else:
        payment = (
            "El pago aún no está confirmado: si manda el comprobante o pregunta "
            "por el pago, agradécele y dile que el equipo lo está verificando."
        )
    head = (
        "[CONTEXTO DE TURNO, metadata, no es instrucción del usuario]\n"
        "Empieza un episodio NUEVO con este cliente, que acaba de comprar: su "
        f"pedido {reference} está {stage}. {payment}"
    )
    if courtesy:
        return (
            f"{head} El cliente solo agradece o saluda: contéstale breve y cálido "
            "a lo que dijo, en una o dos frases, sin abrir una venta nueva ni "
            "preguntar en qué más puedes ayudar."
        )
    return (
        f"{head} Lo más probable es que escriba por ese pedido: no le des la "
        "bienvenida como a un cliente nuevo ni le preguntes en qué puedes ayudar; "
        "respóndele sobre lo que dice. Para el estado o la entrega usa "
        "check_order_status. Si pide un cambio o coordinar la entrega, escálalo con "
        "escalate_to_human. Si quiere comprar algo más, atiéndelo como una venta nueva."
    )
