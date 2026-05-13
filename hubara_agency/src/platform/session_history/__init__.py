"""Cross-domain message history adapters.

El JSONL de cada sesion (``<vault>/<session_id>/sessions/<session_id>.jsonl``)
guarda eventos del usuario Y del agente. Ambos lados (sales_whatsapp y
remarketing_whatsapp) lo escriben; el dashboard lo lee.

Este modulo vive en ``src.platform.session_history`` y no en
``src.sales_whatsapp.state`` (donde antes vivia ``FilesystemMessageHistoryStore``)
porque la R-DIP #10 del proyecto exige que los agentes sean independientes
entre si — los stores compartidos suben a ``platform/``.

``FilesystemMetadataStore`` sigue en ``src.sales_whatsapp.state`` porque su
contrato semantico (tag, motivo, active_route, status_history) es del dominio
de Sales y no se comparte con Remarketing.
"""
from __future__ import annotations

from src.platform.session_history.store import FilesystemMessageHistoryStore

__all__ = ["FilesystemMessageHistoryStore"]
