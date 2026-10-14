"""El resumen del panel «Reseñas» (puro): invitaciones, reseñas y cupones."""
from __future__ import annotations

from typing import Any


#: Descartes que no son «no se invitó»: ya se había invitado o calificado.
_NOT_A_MISS = ("already_requested", "already_reviewed")


def _by_reason(plans: list[dict[str, Any]], action: str, requested: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for plan in plans:
        reason = str(plan.get("reason") or "")
        if plan.get("action") != action or plan.get("order_id") in requested or reason in _NOT_A_MISS:
            continue
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def summarize(
    requests: list[dict[str, Any]],
    reviews: list[Any],
    grants: list[Any],
    plans: list[dict[str, Any]] = (),  # type: ignore[assignment]
) -> dict[str, Any]:
    """`requests`: registros de invitación; `reviews`: ReviewRecord (sin las
    ocultas por ofensivas no se excluye nada: todas cuentan); `grants`:
    concesiones de origen `review`; `plans`: la última decisión del flujo de
    cada pedido (por qué no salió o qué espera — premortem P4)."""
    sent = [r for r in requests if r.get("status") == "sent"]
    requested = {str(r.get("order_id")) for r in requests}
    ratings = [int(r.rating) for r in reviews]
    distribution = {str(n): ratings.count(n) for n in range(1, 6)}
    average = round(sum(ratings) / len(ratings), 1) if ratings else None
    return {
        "requests": {
            "sent_flow": sum(1 for r in sent if r.get("channel") == "flow"),
            "sent_template": sum(1 for r in sent if r.get("channel") == "template"),
            "failed": sum(1 for r in requests if r.get("status") == "failed"),
            "skipped": sum(1 for r in requests if r.get("status") == "skipped"),
            # El envío se cayó a mitad: no se sabe si llegó (no se reintenta).
            "unconfirmed": sum(1 for r in requests if r.get("status") == "claimed"),
            "total": len(requests),
        },
        "not_invited": _by_reason(list(plans), "skip", requested),
        "waiting": _by_reason(list(plans), "wait", requested),
        "reviews": {
            "total": len(ratings),
            "average_rating": average,
            "distribution": distribution,
            "with_photo": sum(1 for r in reviews if r.photo_files),
            "low_rating": sum(1 for n in ratings if n <= 3),
        },
        "coupons": {
            "granted": sum(1 for r in reviews if r.coupon),
            "used": sum(1 for g in grants if getattr(g, "used_order_id", None)),
        },
    }
