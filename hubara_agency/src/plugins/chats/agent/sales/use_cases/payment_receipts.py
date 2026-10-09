"""¿El cliente ya mandó el comprobante de pago de este pedido?

Caso del 2026-10-09 (pedido #64): el cliente pagó y mandó el comprobante
mientras lo atendía un humano; al registrar el pedido con «Crear pedido» el
sistema le mandó los datos para pagar. El comprobante ya estaba en la
conversación: la visión lo había leído (``recent_image_descriptions`` y
``media_index`` con ``kind="comprobante_pago"``), o era un PDF (el ingest lo
trata como comprobante probable, ``kind="pdf_document"``).

Cuenta solo lo del episodio ACTIVO: el comprobante de una compra anterior no
paga este pedido (el episodio se cierra al registrar cada pedido).

Funciones puras sobre ``metadata.json`` — sin I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode
from src.sdk.connectorkit import VISION_KIND_PAYMENT_RECEIPT

#: El PDF que manda el cliente (``KIND_PDF_DOCUMENT`` de la plataforma de
#: medios: valor persistido en ``media_index``). El ingest lo trata como
#: comprobante probable y pasa la conversación a verificación humana.
PDF_DOCUMENT_KIND = "pdf_document"

RECEIPT_KINDS = frozenset({VISION_KIND_PAYMENT_RECEIPT, PDF_DOCUMENT_KIND})


@dataclass(frozen=True)
class PaymentReceipt:
    media_id: str
    #: ``comprobante_pago`` (foto leída por la visión) o ``pdf_document``.
    kind: str
    received_at_ms: int | None
    #: Lo que leyó la visión (monto, destino); ``None`` para un PDF.
    description: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "media_id": self.media_id,
            "kind": self.kind,
            "received_at_ms": self.received_at_ms,
            "description": self.description,
        }


def payment_receipt_in_active_episode(metadata: dict[str, Any]) -> PaymentReceipt | None:
    """El último comprobante que el cliente mandó en el episodio activo."""
    active = get_active_episode(metadata)
    episode_id = active.get("episode_id") if active else None
    if not episode_id:
        return None
    descriptions = {
        e.get("media_id"): e
        for e in metadata.get("recent_image_descriptions") or []
        if isinstance(e, dict) and e.get("media_id")
    }
    indexed = {
        e.get("media_id"): e
        for e in metadata.get("media_index") or []
        if isinstance(e, dict) and e.get("media_id")
    }
    found: PaymentReceipt | None = None
    for media_id in [*indexed, *(m for m in descriptions if m not in indexed)]:
        index_entry = indexed.get(media_id) or {}
        described = descriptions.get(media_id) or {}
        kind = index_entry.get("kind") or described.get("kind")
        episode = index_entry.get("episode_id") or described.get("episode_id")
        if kind not in RECEIPT_KINDS or episode != episode_id:
            continue
        at = index_entry.get("created_at_ms")
        found = PaymentReceipt(
            media_id=str(media_id),
            kind=str(kind),
            received_at_ms=int(at) if isinstance(at, (int, float)) else None,
            description=described.get("description") or None,
        )
    return found
