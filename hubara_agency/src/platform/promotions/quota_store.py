"""Almacén del cupo por unidad — vive en Hubara (el vault), no en Medusa.

La API admin de Medusa no deja escribir `metadata` en una promoción, así que
las filas del cupo (producto + color + aroma + unidades) y la preferencia
`show_units_left` (D3) van a `_promotions/quotas/<promotion_id>.json`. La API
escribe; los workers de ventas leen. Read-modify-write bajo `fcntl.flock`
(mismo patrón que `FilesystemMetadataStore.update`): el vault es un disco
compartido por todos los procesos del host.

Las VENDIDAS no se guardan acá: se derivan de los pedidos (`coupon_sales`).
"""
from __future__ import annotations

import fcntl
import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from src.platform.promotions.quotas import PromoUnitQuota
from src.platform.state import atomic_write_json

#: Un id de promoción de Medusa (`promo_01K…`): nada que arme una ruta.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True)
class QuotaSheet:
    """Las filas del cupo de UN cupón + su preferencia de mostrar cuántas quedan."""

    promotion_id: str
    code: str
    quotas: tuple[PromoUnitQuota, ...]
    show_units_left: bool = True
    updated_at: str = ""
    updated_by: str = ""

    def with_quotas(self, quotas: tuple[PromoUnitQuota, ...]) -> "QuotaSheet":
        return replace(self, quotas=quotas)


def _check_id(promotion_id: str) -> str:
    if not isinstance(promotion_id, str) or not _SAFE_ID.fullmatch(promotion_id):
        raise ValueError(f"id de promoción inválido: {promotion_id!r}")
    return promotion_id


def _merge(
    current: QuotaSheet,
    code: str,
    quotas: list[PromoUnitQuota],
    *,
    show_units_left: bool,
    actor: str,
    now_iso: str,
) -> QuotaSheet:
    """Reemplaza las filas conservando quién y cuándo creó cada combinación
    que ya existía (el id es el de la combinación)."""
    born = {q.id: q for q in current.quotas}
    kept = tuple(
        replace(q, created_at=born[q.id].created_at, created_by=born[q.id].created_by)
        if q.id in born
        else q
        for q in quotas
    )
    return QuotaSheet(
        promotion_id=current.promotion_id,
        code=code,
        quotas=kept,
        show_units_left=show_units_left,
        updated_at=now_iso,
        updated_by=actor,
    )


def _to_json(sheet: QuotaSheet) -> dict[str, Any]:
    data = asdict(sheet)
    data["quotas"] = [asdict(q) for q in sheet.quotas]
    return data


def _from_json(promotion_id: str, data: Any) -> QuotaSheet:
    if not isinstance(data, dict):
        return QuotaSheet(promotion_id, "", ())
    quotas: list[PromoUnitQuota] = []
    for row in data.get("quotas") or []:
        try:
            quotas.append(PromoUnitQuota(**row))
        except TypeError:
            continue  # fila de otra versión: no se inventa
    return QuotaSheet(
        promotion_id=promotion_id,
        code=str(data.get("code") or ""),
        quotas=tuple(quotas),
        show_units_left=bool(data.get("show_units_left", True)),
        updated_at=str(data.get("updated_at") or ""),
        updated_by=str(data.get("updated_by") or ""),
    )


@runtime_checkable
class PromoQuotaStore(Protocol):
    def get(self, promotion_id: str) -> QuotaSheet: ...

    def replace(
        self,
        promotion_id: str,
        code: str,
        quotas: list[PromoUnitQuota],
        *,
        show_units_left: bool,
        actor: str,
        now_iso: str,
    ) -> QuotaSheet: ...

    def list_sheets(self) -> list[QuotaSheet]: ...

    def delete(self, promotion_id: str) -> None: ...


class VaultPromoQuotaStore:
    def __init__(self, vault_dir: Path) -> None:
        self._dir = Path(vault_dir) / "_promotions" / "quotas"

    def _path(self, promotion_id: str) -> Path:
        return self._dir / f"{_check_id(promotion_id)}.json"

    def _read(self, promotion_id: str) -> QuotaSheet:
        path = self._path(promotion_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return QuotaSheet(promotion_id, "", ())
        except (OSError, ValueError):
            return QuotaSheet(promotion_id, "", ())
        return _from_json(promotion_id, data)

    def get(self, promotion_id: str) -> QuotaSheet:
        return self._read(promotion_id)

    def update(
        self, promotion_id: str, mutator: Callable[[QuotaSheet], QuotaSheet | None]
    ) -> QuotaSheet:
        """Read-modify-write atómico bajo el lock del cupón."""
        path = self._path(promotion_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.parent / f"{path.name}.lock", "w", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                current = self._read(promotion_id)
                result = mutator(current)
                if result is None:
                    return current
                atomic_write_json(path, _to_json(result))
                return result
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def replace(
        self,
        promotion_id: str,
        code: str,
        quotas: list[PromoUnitQuota],
        *,
        show_units_left: bool,
        actor: str,
        now_iso: str,
    ) -> QuotaSheet:
        return self.update(
            promotion_id,
            lambda current: _merge(
                current, code, quotas, show_units_left=show_units_left, actor=actor, now_iso=now_iso
            ),
        )

    def list_sheets(self) -> list[QuotaSheet]:
        if not self._dir.is_dir():
            return []
        sheets = [self._read(p.stem) for p in sorted(self._dir.glob("*.json")) if _SAFE_ID.fullmatch(p.stem)]
        return [s for s in sheets if s.quotas]

    def delete(self, promotion_id: str) -> None:
        self._path(promotion_id).unlink(missing_ok=True)


class FakePromoQuotaStore:
    """Doble oficial: mismo comportamiento, en memoria."""

    def __init__(self, sheets: list[QuotaSheet] | None = None) -> None:
        self._sheets = {s.promotion_id: s for s in sheets or []}

    def get(self, promotion_id: str) -> QuotaSheet:
        return self._sheets.get(_check_id(promotion_id)) or QuotaSheet(promotion_id, "", ())

    def replace(
        self,
        promotion_id: str,
        code: str,
        quotas: list[PromoUnitQuota],
        *,
        show_units_left: bool,
        actor: str,
        now_iso: str,
    ) -> QuotaSheet:
        sheet = _merge(
            self.get(promotion_id), code, quotas,
            show_units_left=show_units_left, actor=actor, now_iso=now_iso,
        )
        self._sheets[promotion_id] = sheet
        return sheet

    def list_sheets(self) -> list[QuotaSheet]:
        return [s for _, s in sorted(self._sheets.items()) if s.quotas]

    def delete(self, promotion_id: str) -> None:
        self._sheets.pop(_check_id(promotion_id), None)


__all__ = [
    "FakePromoQuotaStore",
    "PromoQuotaStore",
    "QuotaSheet",
    "VaultPromoQuotaStore",
]
