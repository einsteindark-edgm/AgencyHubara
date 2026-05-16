"""Cross-domain message history adapters.

El JSONL de cada sesion (``<vault>/<session_id>/sessions/<session_id>.jsonl``)
guarda eventos del usuario Y del agente. Ambos lados (sales y remarketing
del plugin chats) lo escriben; el dashboard lo lee.

Este modulo vive en ``src.platform.session_history`` y no en
``src.plugins.chats.agent.sales.state`` (donde antes vivia
``FilesystemMessageHistoryStore``) porque la R-DIP #10 del proyecto exige
que los agentes sean independientes entre si — los stores compartidos
suben a ``platform/``.

``FilesystemMetadataStore`` ahora vive en ``src.platform.state`` por la misma
regla R-DIP: dashboard handoff (intervene + return-to-bot) lo necesita para
flippear `active_route=humano`, asi que dejo de ser un store agent-specific.
Un shim en ``src.plugins.chats.agent.sales.state`` mantiene el import legacy
funcionando.
"""
from __future__ import annotations

from src.platform.session_history.store import FilesystemMessageHistoryStore

__all__ = ["FilesystemMessageHistoryStore"]
