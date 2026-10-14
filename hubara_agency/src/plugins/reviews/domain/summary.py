"""El resumen del panel «Reseñas» (puro): invitaciones, reseñas y cupones."""
from __future__ import annotations

from typing import Any


def summarize(
    requests: list[dict[str, Any]],
    reviews: list[Any],
    grants: list[Any],
) -> dict[str, Any]:
    """`requests`: registros de invitación; `reviews`: ReviewRecord (sin las
    ocultas por ofensivas no se excluye nada: todas cuentan); `grants`:
    concesiones de origen `review`."""
    sent = [r for r in requests if r.get("status") == "sent"]
    ratings = [int(r.rating) for r in reviews]
    distribution = {str(n): ratings.count(n) for n in range(1, 6)}
    average = round(sum(ratings) / len(ratings), 1) if ratings else None
    return {
        "requests": {
            "sent_flow": sum(1 for r in sent if r.get("channel") == "flow"),
            "sent_template": sum(1 for r in sent if r.get("channel") == "template"),
            "failed": sum(1 for r in requests if r.get("status") == "failed"),
            "skipped": sum(1 for r in requests if r.get("status") == "skipped"),
            "total": len(requests),
        },
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
