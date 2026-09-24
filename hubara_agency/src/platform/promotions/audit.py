"""Registro de cambios de la central de cupones (D6: sin roles, con actor).

Cualquier usuario del dashboard crea, edita, pausa y borra cupones; cada
acción deja una línea JSON en `_promotions/audit.jsonl` del vault con el
actor VERIFICADO de la sesión (`current_actor`), nunca uno que mande el
cliente. Append-only bajo `flock`.
"""
from __future__ import annotations

import fcntl
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass(frozen=True)
class CouponAuditEntry:
    ts: str
    actor: str
    action: str
    promotion_id: str
    code: str
    detail: dict[str, Any] = field(default_factory=dict)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _newest_first(
    entries: list[CouponAuditEntry], promotion_id: str | None, limit: int
) -> list[CouponAuditEntry]:
    picked = [e for e in reversed(entries) if promotion_id is None or e.promotion_id == promotion_id]
    return picked[:limit]


@runtime_checkable
class CouponAuditPort(Protocol):
    def append(
        self,
        *,
        actor: str,
        action: str,
        promotion_id: str,
        code: str,
        detail: dict[str, Any] | None = None,
    ) -> CouponAuditEntry: ...

    def entries(self, *, promotion_id: str | None = None, limit: int = 50) -> list[CouponAuditEntry]: ...


class CouponAuditLog:
    def __init__(self, vault_dir: Path, *, clock: Callable[[], str] = _utc_iso) -> None:
        self._path = Path(vault_dir) / "_promotions" / "audit.jsonl"
        self._clock = clock

    def append(
        self,
        *,
        actor: str,
        action: str,
        promotion_id: str,
        code: str,
        detail: dict[str, Any] | None = None,
    ) -> CouponAuditEntry:
        entry = CouponAuditEntry(self._clock(), actor, action, promotion_id, code, dict(detail or {}))
        line = json.dumps(entry.__dict__, ensure_ascii=False, sort_keys=False) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.write(line)
                fh.flush()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return entry

    def entries(self, *, promotion_id: str | None = None, limit: int = 50) -> list[CouponAuditEntry]:
        try:
            raw_lines = self._path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        parsed: list[CouponAuditEntry] = []
        for raw in raw_lines:
            try:
                parsed.append(CouponAuditEntry(**json.loads(raw)))
            except (ValueError, TypeError):
                continue  # una línea rota no tumba el registro
        return _newest_first(parsed, promotion_id, limit)


class FakeCouponAuditLog:
    """Doble oficial: en memoria."""

    def __init__(self) -> None:
        self.recorded: list[CouponAuditEntry] = []

    def append(
        self,
        *,
        actor: str,
        action: str,
        promotion_id: str,
        code: str,
        detail: dict[str, Any] | None = None,
    ) -> CouponAuditEntry:
        entry = CouponAuditEntry(_utc_iso(), actor, action, promotion_id, code, dict(detail or {}))
        self.recorded.append(entry)
        return entry

    def entries(self, *, promotion_id: str | None = None, limit: int = 50) -> list[CouponAuditEntry]:
        return _newest_first(self.recorded, promotion_id, limit)


__all__ = ["CouponAuditEntry", "CouponAuditLog", "CouponAuditPort", "FakeCouponAuditLog"]
