"""Mixin para los fakes en memoria del store de metadata de sesión.

El store real (`src.platform.state.FilesystemMetadataStore`) escribe con
candado y SOLO los cambios de cada escritor: `update(session_id, mutator)` y
`write_merged(session_id, base=..., ours=...)` (incidente 2026-10-06). Los
fakes de los tests guardan el documento en un dict; este mixin les da esos dos
métodos sobre su propio `read`/`write`, con el MISMO merge de tres vías
(`merge_changes`), y `is_unreadable` (un dict en memoria siempre se lee). Un
fake que ya define `update` conserva el suyo.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.platform.state import merge_changes


class MergingMetadataStoreMixin:
    """`update` / `write_merged` sobre el `read` / `write` del fake."""

    def update(
        self,
        session_id: str,
        mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        overwrite_unreadable: bool = False,
    ) -> dict[str, Any] | None:
        result = mutator(self.read(session_id))  # type: ignore[attr-defined]
        if result is None:
            return None
        self.write(session_id, result)  # type: ignore[attr-defined]
        return result

    def is_unreadable(self, session_id: str) -> bool:
        return False  # el documento en memoria siempre se lee

    def write_merged(
        self,
        session_id: str,
        *,
        base: dict[str, Any],
        ours: dict[str, Any],
        overwrite_unreadable: bool = False,
        before_merge: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        fresh = self.read(session_id)  # type: ignore[attr-defined]
        if not fresh and base:
            merged = ours
        else:
            if before_merge is not None:
                before_merge(fresh)
            merged = merge_changes(base, ours, fresh)
            if merged is fresh:
                return fresh
        self.write(session_id, merged)  # type: ignore[attr-defined]
        return merged
