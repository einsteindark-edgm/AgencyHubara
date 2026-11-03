"""Cupones personales: una concesión por persona, de un uso y con vencimiento.

Reseñas tras la entrega (2026-10-13): quien califica su pedido recibe 5 % (10 %
con foto) para la próxima compra. El código es uno solo por porcentaje
(GRACIAS5 / GRACIAS10) con la condición `granted_only`, pero solo lo usa quien
lo recibió, una vez y antes de que venza. Medusa no cuenta usos de pedidos
borrador (los del bot) ni limita por persona: la concesión vive en el vault,
`_promotions/grants/<sesión>.json` (escribe el plugin de reseñas; leen y marcan
el uso los workers de ventas).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.platform.state import atomic_write_json

#: Una sesión del vault (`wa_573…`): nada que arme una ruta.
_SAFE_SESSION = re.compile(r"[A-Za-z0-9_+-]{1,80}")


def _session_key(session_id: str) -> str:
    if not isinstance(session_id, str) or not _SAFE_SESSION.fullmatch(session_id):
        raise ValueError(f"sesión inválida para cupones personales: {session_id!r}")
    return session_id


def _code(code: str) -> str:
    return (code or "").strip().upper()


@dataclass(frozen=True)
class CouponGrant:
    """Un cupón que una persona puede usar una vez."""

    code: str
    session_id: str
    percent: int
    granted_at_ms: int
    expires_at_ms: int
    #: Qué lo originó ("review") y la referencia (el pedido calificado).
    source: str
    reference: str
    used_order_id: str | None = None
    used_at_ms: int | None = None

    def is_active(self, now_ms: int) -> bool:
        return self.used_order_id is None and now_ms < self.expires_at_ms


@runtime_checkable
class CouponGrantsStore(Protocol):
    def grant(self, grant: CouponGrant) -> CouponGrant: ...
    def list_for(self, session_id: str) -> list[CouponGrant]: ...
    def active(self, session_id: str, code: str, now_ms: int) -> CouponGrant | None: ...
    def first_active(self, session_id: str, now_ms: int) -> CouponGrant | None: ...
    def mark_used(
        self, session_id: str, code: str, order_id: str, now_ms: int
    ) -> CouponGrant | None: ...


# ── Lógica común sobre la lista de concesiones de UNA persona ───────────────


def _add(grants: list[CouponGrant], grant: CouponGrant) -> tuple[list[CouponGrant], CouponGrant]:
    """Idempotente por (código, referencia): el mismo pedido calificado no
    concede dos veces."""
    normalized = replace(grant, code=_code(grant.code))
    for existing in grants:
        if existing.code == normalized.code and existing.reference == normalized.reference:
            return grants, existing
    return [*grants, normalized], normalized


def _active(grants: list[CouponGrant], code: str | None, now_ms: int) -> CouponGrant | None:
    """La concesión vigente que vence primero (de un código), o, sin código,
    la de MAYOR porcentaje (premortem C8: a quien le prometimos 10 % con foto
    no se le aplica el 5 % de otra calificación porque vence antes); a igual
    porcentaje, la que vence primero."""
    candidates = [
        g for g in grants if g.is_active(now_ms) and (code is None or g.code == _code(code))
    ]
    if code is None:
        return min(candidates, key=lambda g: (-g.percent, g.expires_at_ms), default=None)
    return min(candidates, key=lambda g: g.expires_at_ms, default=None)


def _use(
    grants: list[CouponGrant], code: str, order_id: str, now_ms: int
) -> tuple[list[CouponGrant], CouponGrant | None]:
    """Marca usada la concesión que vence primero. Idempotente por pedido: el
    reintento del registro no gasta una segunda."""
    for existing in grants:
        if existing.code == _code(code) and existing.used_order_id == order_id:
            return grants, existing
    target = _active(grants, code, now_ms)
    if target is None:
        return grants, None
    used = replace(target, used_order_id=order_id, used_at_ms=now_ms)
    return [used if g is target else g for g in grants], used


class FakeCouponGrantsStore:
    """Doble oficial para tests (P-27)."""

    def __init__(self) -> None:
        self._by_session: dict[str, list[CouponGrant]] = {}

    def grant(self, grant: CouponGrant) -> CouponGrant:
        key = _session_key(grant.session_id)
        self._by_session[key], saved = _add(self._by_session.get(key, []), grant)
        return saved

    def list_for(self, session_id: str) -> list[CouponGrant]:
        return list(self._by_session.get(_session_key(session_id), []))

    def active(self, session_id: str, code: str, now_ms: int) -> CouponGrant | None:
        return _active(self.list_for(session_id), code, now_ms)

    def first_active(self, session_id: str, now_ms: int) -> CouponGrant | None:
        return _active(self.list_for(session_id), None, now_ms)

    def mark_used(
        self, session_id: str, code: str, order_id: str, now_ms: int
    ) -> CouponGrant | None:
        key = _session_key(session_id)
        self._by_session[key], used = _use(self._by_session.get(key, []), code, order_id, now_ms)
        return used


class VaultCouponGrantsStore:
    """`_promotions/grants/<sesión>.json`, una lista por persona. Las
    escrituras (conceder, marcar uso) van bajo `flock`: la API, el worker de
    reseñas y los de ventas comparten el disco del vault."""

    def __init__(self, vault_dir: Path) -> None:
        self._dir = Path(vault_dir) / "_promotions" / "grants"

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{_session_key(session_id)}.json"

    def _read(self, session_id: str) -> list[CouponGrant]:
        path = self._path(session_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        items = raw.get("grants") if isinstance(raw, dict) else None
        return [CouponGrant(**item) for item in items or [] if isinstance(item, dict)]

    @contextlib.contextmanager
    def _locked(self, session_id: str) -> Iterator[None]:
        self._dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._dir / f".{_session_key(session_id)}.lock"
        with open(lock_path, "w", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _update(
        self,
        session_id: str,
        change: Callable[[list[CouponGrant]], tuple[list[CouponGrant], CouponGrant | None]],
    ) -> CouponGrant | None:
        with self._locked(session_id):
            before = self._read(session_id)
            after, result = change(before)
            if after is not before:
                atomic_write_json(
                    self._path(session_id), {"grants": [asdict(g) for g in after]}
                )
            return result

    def grant(self, grant: CouponGrant) -> CouponGrant:
        saved = self._update(grant.session_id, lambda gs: _add(gs, grant))
        assert saved is not None
        return saved

    def list_for(self, session_id: str) -> list[CouponGrant]:
        return self._read(session_id)

    def active(self, session_id: str, code: str, now_ms: int) -> CouponGrant | None:
        return _active(self._read(session_id), code, now_ms)

    def first_active(self, session_id: str, now_ms: int) -> CouponGrant | None:
        return _active(self._read(session_id), None, now_ms)

    def mark_used(
        self, session_id: str, code: str, order_id: str, now_ms: int
    ) -> CouponGrant | None:
        return self._update(session_id, lambda gs: _use(gs, code, order_id, now_ms))


__all__ = [
    "CouponGrant",
    "CouponGrantsStore",
    "FakeCouponGrantsStore",
    "VaultCouponGrantsStore",
]
