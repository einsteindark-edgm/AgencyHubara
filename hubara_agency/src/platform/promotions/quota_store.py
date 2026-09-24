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
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta, timezone
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
    #: Desde cuándo se cuentan las vendidas: el primer guardado de filas y
    #: NUNCA avanza (borrar y volver a crear una fila no "devuelve" unidades).
    counting_since: str = ""

    def with_quotas(self, quotas: tuple[PromoUnitQuota, ...]) -> "QuotaSheet":
        return replace(self, quotas=quotas)


class QuotaStoreError(RuntimeError):
    """El cupo guardado no se pudo leer (archivo roto o disco). Falla
    CERRADA: quien lo lea NO debe tratarlo como "cupón sin cupo".

    `reason` dice qué pasó: ``"unreadable"`` (archivo roto o disco) o
    ``"changed"`` (`QuotaSheetChangedError`)."""

    reason = "unreadable"


class QuotaSheetChangedError(QuotaStoreError):
    """Otra persona guardó el cupo después de la versión que se editó
    (`expected_updated_at` no coincide): no se pisa lo nuevo (C-5)."""

    reason = "changed"


def _check_id(promotion_id: str) -> str:
    if not isinstance(promotion_id, str) or not _SAFE_ID.fullmatch(promotion_id):
        raise ValueError(f"id de promoción inválido: {promotion_id!r}")
    return promotion_id


#: Formato de la versión cuando hay que inventarla (1 µs más que la anterior).
_VERSION_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _instant(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _next_version(previous: str, now_iso: str) -> str:
    """`updated_at` de un guardado nuevo, ESTRICTAMENTE mayor que el anterior
    (C-5): dos guardados en el mismo instante (o con el reloj atrás) no
    repiten versión — si no, quien cargó entre los dos pasaría el chequeo de
    `expected_updated_at`. Se calcula bajo el candado del cupo."""
    before, now = _instant(previous), _instant(now_iso)
    if before is None or now is None or now > before:
        return now_iso
    return (before + timedelta(microseconds=1)).astimezone(timezone.utc).strftime(_VERSION_FORMAT)


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
        updated_at=_next_version(current.updated_at, now_iso),
        updated_by=actor,
        counting_since=current.counting_since or (now_iso if kept else ""),
    )


#: Sin chequeo de versión (el llamador no mandó `expected_updated_at`).
_NO_CHECK: Any = object()

_ROW_FIELDS = frozenset(f.name for f in fields(PromoUnitQuota))


def _check_version(current: QuotaSheet, expected_updated_at: Any) -> None:
    """C-5: `expected_updated_at` es el `updated_at` de la versión que editó
    el operador (None = "nunca se guardó"). `_NO_CHECK` = cliente viejo que
    no lo manda: sin chequeo."""
    if expected_updated_at is _NO_CHECK:
        return
    if (current.updated_at or None) != (expected_updated_at or None):
        raise QuotaSheetChangedError(
            f"el cupo de {current.promotion_id} cambió: guardado {current.updated_at!r}, "
            f"editado sobre {expected_updated_at!r}"
        )


def _pruned(current: QuotaSheet, products: tuple[str, ...], *, actor: str, now_iso: str) -> QuotaSheet:
    """La hoja sin las filas de productos que el cupón ya no tiene (conserva
    desde cuándo se cuenta y la preferencia de mostrar cuántas quedan)."""
    kept = tuple(q for q in current.quotas if q.product_id in products)
    return replace(
        current, quotas=kept, updated_at=_next_version(current.updated_at, now_iso), updated_by=actor
    )


def _to_json(sheet: QuotaSheet) -> dict[str, Any]:
    data = asdict(sheet)
    data["quotas"] = [asdict(q) for q in sheet.quotas]
    return data


def _row_from_json(promotion_id: str, row: Any) -> PromoUnitQuota:
    """Una fila guardada. Una clave que este código no conoce (formato más
    nuevo, edición a mano) se ignora; una fila sin sus campos obligatorios o
    con unidades que no son un entero NO se adivina: falla cerrada."""
    if not isinstance(row, dict):
        raise QuotaStoreError(f"fila de cupo ilegible en {promotion_id}")
    try:
        quota = PromoUnitQuota(**{k: v for k, v in row.items() if k in _ROW_FIELDS})
    except TypeError as exc:
        raise QuotaStoreError(f"fila de cupo ilegible en {promotion_id}: {exc}") from exc
    units_ok = isinstance(quota.units, int) and not isinstance(quota.units, bool)
    if not (units_ok and isinstance(quota.id, str) and quota.id and isinstance(quota.product_id, str)):
        raise QuotaStoreError(f"fila de cupo ilegible en {promotion_id}: {row.get('id')!r}")
    return quota


def _from_json(promotion_id: str, data: Any) -> QuotaSheet:
    """La hoja guardada; las claves que este código no conoce se ignoran."""
    if not isinstance(data, dict):
        raise QuotaStoreError(f"cupo de {promotion_id} ilegible")
    rows = data.get("quotas") or []
    if not isinstance(rows, list):
        raise QuotaStoreError(f"cupo de {promotion_id} ilegible")
    quotas = [_row_from_json(promotion_id, row) for row in rows]
    return QuotaSheet(
        promotion_id=promotion_id,
        code=str(data.get("code") or ""),
        quotas=tuple(quotas),
        show_units_left=bool(data.get("show_units_left", True)),
        updated_at=str(data.get("updated_at") or ""),
        updated_by=str(data.get("updated_by") or ""),
        counting_since=str(data.get("counting_since") or ""),
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
        expected_updated_at: str | None = ...,
    ) -> QuotaSheet: ...

    def prune_to_products(
        self, promotion_id: str, products: tuple[str, ...], *, actor: str, now_iso: str
    ) -> tuple[PromoUnitQuota, ...]: ...

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
        except (OSError, ValueError) as exc:
            raise QuotaStoreError(f"no pude leer el cupo de {promotion_id}: {exc}") from exc
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
        expected_updated_at: Any = _NO_CHECK,
    ) -> QuotaSheet:
        """Reemplaza las filas. Una hoja ilegible NO se pisa (se perdería
        desde cuándo se cuentan las vendidas): `QuotaStoreError`. Con
        `expected_updated_at` la versión se compara BAJO el candado (C-5)."""

        def _replace(current: QuotaSheet) -> QuotaSheet:
            _check_version(current, expected_updated_at)
            return _merge(
                current, code, quotas, show_units_left=show_units_left, actor=actor, now_iso=now_iso
            )

        return self.update(promotion_id, _replace)

    def prune_to_products(
        self, promotion_id: str, products: tuple[str, ...], *, actor: str, now_iso: str
    ) -> tuple[PromoUnitQuota, ...]:
        """Quita (bajo el candado) las filas de productos que el cupón ya no
        tiene; devuelve las quitadas (vacío = no escribió nada)."""
        removed: list[PromoUnitQuota] = []

        def _prune(current: QuotaSheet) -> QuotaSheet | None:
            removed.extend(q for q in current.quotas if q.product_id not in products)
            return _pruned(current, products, actor=actor, now_iso=now_iso) if removed else None

        self.update(promotion_id, _prune)
        return tuple(removed)

    def list_sheets(self) -> list[QuotaSheet]:
        """Hojas legibles con filas (una rota se saltea acá; quien la lea por
        su id recibe el error)."""
        if not self._dir.is_dir():
            return []
        sheets: list[QuotaSheet] = []
        for path in sorted(self._dir.glob("*.json")):
            if not _SAFE_ID.fullmatch(path.stem):
                continue
            try:
                sheet = self._read(path.stem)
            except QuotaStoreError:
                continue
            if sheet.quotas:
                sheets.append(sheet)
        return sheets

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
        expected_updated_at: Any = _NO_CHECK,
    ) -> QuotaSheet:
        current = self.get(promotion_id)
        _check_version(current, expected_updated_at)
        sheet = _merge(
            current, code, quotas,
            show_units_left=show_units_left, actor=actor, now_iso=now_iso,
        )
        self._sheets[promotion_id] = sheet
        return sheet

    def prune_to_products(
        self, promotion_id: str, products: tuple[str, ...], *, actor: str, now_iso: str
    ) -> tuple[PromoUnitQuota, ...]:
        current = self.get(promotion_id)
        removed = tuple(q for q in current.quotas if q.product_id not in products)
        if removed:
            self._sheets[promotion_id] = _pruned(current, products, actor=actor, now_iso=now_iso)
        return removed

    def list_sheets(self) -> list[QuotaSheet]:
        return [s for _, s in sorted(self._sheets.items()) if s.quotas]

    def delete(self, promotion_id: str) -> None:
        self._sheets.pop(_check_id(promotion_id), None)


__all__ = [
    "FakePromoQuotaStore",
    "PromoQuotaStore",
    "QuotaSheet",
    "QuotaSheetChangedError",
    "QuotaStoreError",
    "VaultPromoQuotaStore",
]
