"""Registro de invitaciones y reseñas en el vault (`_reviews/`).

- `requests/<pedido>.json`: la invitación a calificar de un pedido. Se RECLAMA
  creando el archivo en exclusiva (O_EXCL): dos despertares del flujo, un
  reintento o un pedido re-entregado no mandan dos invitaciones.
- `reviews/<pedido>.json`: la reseña (una por pedido; la primera respuesta
  manda — Meta reintenta webhooks). La modera el equipo desde el dashboard.

API y worker comparten el disco del vault; cada escritura es atómica.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from src.sdk.runtime import atomic_write_json

#: Un id de pedido de Medusa (`order_01…`, `draft_…`): nada que arme una ruta.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
REVIEW_STATUSES = ("pending", "approved", "hidden")


def _key(order_id: str) -> str:
    if not isinstance(order_id, str) or not _SAFE_ID.fullmatch(order_id):
        raise ValueError(f"id de pedido inválido para reseñas: {order_id!r}")
    return order_id


def review_id_for(order_id: str) -> str:
    return f"rev_{_key(order_id)}"


@dataclass(frozen=True)
class ReviewRecord:
    """Una calificación (por pedido) con lo que hace falta para publicarla."""

    order_id: str
    display_id: str
    session_id: str
    rating: int
    comment: str | None
    publish_consent: bool
    #: «Ana G.» (nombre de pila + inicial, Ley 1581) y ciudad de envío.
    author_name: str
    author_city: str | None
    #: [{product_id, title, handle}] — la reseña sale en la ficha de cada uno.
    products: tuple[dict[str, Any], ...]
    #: Fotos del cliente guardadas en el vault (rutas relativas a `_reviews/`).
    photo_files: tuple[str, ...]
    submitted_at_ms: int
    wa_message_id: str
    review_id: str = ""
    status: str = "pending"
    #: Cupón concedido: {code, percent, expires_at_ms}.
    coupon: dict[str, Any] = field(default_factory=dict)
    #: Respuesta pública del equipo (sale en la web debajo de la reseña).
    reply: str | None = None
    #: Publicación en Medusa: {at_ms, ids, error}.
    published: dict[str, Any] = field(default_factory=dict)
    #: 1–3 estrellas: se avisó al equipo (conversación a humano).
    team_alerted: bool = False

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["products"] = list(self.products)
        data["photo_files"] = list(self.photo_files)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ReviewRecord:
        fields = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        fields["products"] = tuple(data.get("products") or ())
        fields["photo_files"] = tuple(data.get("photo_files") or ())
        return cls(**fields)


class ReviewsStore:
    def __init__(self, vault_dir: Path) -> None:
        self._dir = Path(vault_dir) / "_reviews"

    @property
    def root(self) -> Path:
        return self._dir

    # ── invitaciones ────────────────────────────────────────────────────────

    def _request_path(self, order_id: str) -> Path:
        return self._dir / "requests" / f"{_key(order_id)}.json"

    def claim_request(self, order_id: str, *, session_id: str, now_ms: int) -> bool:
        """True si esta llamada se quedó con la invitación del pedido."""
        path = self._request_path(order_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return False
        record = {
            "order_id": order_id,
            "session_id": session_id,
            "status": "claimed",
            "claimed_at_ms": now_ms,
        }
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
        return True

    def get_request(self, order_id: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self._request_path(order_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def list_requests(self) -> list[dict[str, Any]]:
        folder = self._dir / "requests"
        if not folder.is_dir():
            return []
        out = []
        for path in folder.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                out.append(data)
        return out

    def update_request(self, order_id: str, **fields: Any) -> dict[str, Any] | None:
        current = self.get_request(order_id)
        if current is None:
            return None
        updated = {**current, **fields}
        atomic_write_json(self._request_path(order_id), updated)
        return updated

    # ── reseñas ─────────────────────────────────────────────────────────────

    def _review_path(self, review_id: str) -> Path:
        if not review_id.startswith("rev_"):
            raise ValueError(f"id de reseña inválido: {review_id!r}")
        return self._dir / "reviews" / f"{_key(review_id[len('rev_'):])}.json"

    def save_review(self, record: ReviewRecord) -> tuple[ReviewRecord, bool]:
        """(reseña guardada, creada ahora). Si el pedido ya tenía reseña, se
        devuelve la existente sin tocarla."""
        review_id = review_id_for(record.order_id)
        existing = self.get_review(review_id)
        if existing is not None:
            return existing, False
        saved = replace(record, review_id=review_id)
        path = self._review_path(review_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, saved.to_json())
        return saved, True

    def get_review(self, review_id: str) -> ReviewRecord | None:
        try:
            data = json.loads(self._review_path(review_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        return ReviewRecord.from_json(data) if isinstance(data, dict) else None

    def update_review(self, review_id: str, **fields: Any) -> ReviewRecord | None:
        current = self.get_review(review_id)
        if current is None:
            return None
        updated = replace(current, **fields)
        atomic_write_json(self._review_path(review_id), updated.to_json())
        return updated

    def list_reviews(self, *, status: str | None = None) -> list[ReviewRecord]:
        folder = self._dir / "reviews"
        if not folder.is_dir():
            return []
        out = []
        for path in folder.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                record = ReviewRecord.from_json(data)
                if status is None or record.status == status:
                    out.append(record)
        return sorted(out, key=lambda r: r.submitted_at_ms, reverse=True)
