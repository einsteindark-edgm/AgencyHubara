"""Condiciones de un cupón que viven en Hubara (el vault), no en Medusa.

Caso del 2026-10-09: la página ofrece un 5 % de bienvenida para la PRIMERA
compra y ese cupón no existía; una clienta lo pidió por el chat y el bot dijo
que no había descuento. Medusa no deja escribir `metadata` en una promoción y
no cuenta los pedidos borrador (los del bot), así que «solo primera compra»
va a `_promotions/conditions/<CÓDIGO>.json`, por código: la central lo escribe
ANTES de crear el cupón en Medusa (si el vault falla, no queda un cupón sin su
condición).

`ConditionedPromotionsPort` pega la condición en cada `PromotionDTO` que lee
el bot: la ven `list_promotions`, `apply_coupon`, la campaña que aplica su
cupón sola y la app del operador, por el mismo camino (L-32). Si la condición
no se puede leer, el cupón queda como `scope_unresolved`: no se ofrece ni se
aplica (falla cerrada, igual que unas reglas de Medusa ilegibles).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from src.platform.state import atomic_write_json

#: Un código de cupón normalizado (letras y números): nada que arme una ruta.
_SAFE_CODE = re.compile(r"[A-Z0-9]{1,40}")


def _key(code: str) -> str:
    normalized = (code or "").strip().upper()
    if not _SAFE_CODE.fullmatch(normalized):
        raise ValueError(f"código de cupón inválido: {code!r}")
    return normalized


@dataclass(frozen=True)
class CouponConditions:
    """Lo que Hubara exige de un cupón además de lo que dice Medusa."""

    code: str
    #: Solo para quien compra por primera vez (sin pedidos anteriores).
    first_purchase_only: bool = False
    updated_at: str = ""
    updated_by: str = ""


class CouponConditionsError(RuntimeError):
    """Las condiciones guardadas no se pudieron leer (archivo roto o disco).
    Falla CERRADA: quien lea no debe tratarlo como «cupón sin condiciones»."""


@runtime_checkable
class CouponConditionsStore(Protocol):
    def get(self, code: str) -> CouponConditions: ...

    def put(self, conditions: CouponConditions) -> CouponConditions: ...

    def delete(self, code: str) -> None: ...


class FakeCouponConditionsStore:
    """Doble oficial para tests (P-27)."""

    def __init__(self) -> None:
        self._saved: dict[str, CouponConditions] = {}

    def get(self, code: str) -> CouponConditions:
        key = _key(code)
        return self._saved.get(key, CouponConditions(key))

    def put(self, conditions: CouponConditions) -> CouponConditions:
        saved = replace(conditions, code=_key(conditions.code))
        self._saved[saved.code] = saved
        return saved

    def delete(self, code: str) -> None:
        self._saved.pop(_key(code), None)


class VaultCouponConditionsStore:
    """`_promotions/conditions/<CÓDIGO>.json`. La API escribe; los workers de
    ventas leen. Un archivo por cupón con escritura atómica: no hace falta
    candado (cada guardado reemplaza el archivo entero)."""

    def __init__(self, vault_dir: Path) -> None:
        self._dir = Path(vault_dir) / "_promotions" / "conditions"

    def _path(self, code: str) -> Path:
        return self._dir / f"{_key(code)}.json"

    def get(self, code: str) -> CouponConditions:
        path = self._path(code)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return CouponConditions(_key(code))
        except (OSError, ValueError) as exc:
            raise CouponConditionsError(f"no pude leer las condiciones de {code}: {exc}") from exc
        if not isinstance(data, dict):
            raise CouponConditionsError(f"condiciones de {code} ilegibles")
        return CouponConditions(
            code=_key(code),
            first_purchase_only=bool(data.get("first_purchase_only")),
            updated_at=str(data.get("updated_at") or ""),
            updated_by=str(data.get("updated_by") or ""),
        )

    def put(self, conditions: CouponConditions) -> CouponConditions:
        saved = replace(conditions, code=_key(conditions.code))
        path = self._path(saved.code)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, asdict(saved))
        return saved

    def delete(self, code: str) -> None:
        self._path(code).unlink(missing_ok=True)


class ConditionedPromotionsPort:
    """El lector de promociones con las condiciones de Hubara pegadas.

    `conditions`: fábrica del almacén (se resuelve en cada lectura: el vault
    lo aíslan los tests)."""

    def __init__(self, inner: Any, conditions: Callable[[], CouponConditionsStore]) -> None:
        self._inner = inner
        self._conditions = conditions

    def _with_conditions(self, promotion: Any) -> Any:
        try:
            found = self._conditions().get(promotion.code)
        except ValueError:
            # Un código que la central no usaría (creado a mano en Medusa):
            # no puede tener condiciones guardadas.
            return promotion
        except CouponConditionsError:
            return replace(promotion, scope_unresolved=True)
        if not found.first_purchase_only:
            return promotion
        return replace(promotion, first_purchase_only=True)

    async def list_active(self) -> list[Any]:
        return [self._with_conditions(p) for p in await self._inner.list_active()]

    async def get_by_code(self, code: str) -> Any:
        promotion = await self._inner.get_by_code(code)
        return self._with_conditions(promotion) if promotion is not None else None

    def invalidate(self) -> None:
        invalidate = getattr(self._inner, "invalidate", None)
        if callable(invalidate):
            invalidate()


__all__ = [
    "ConditionedPromotionsPort",
    "CouponConditions",
    "CouponConditionsError",
    "CouponConditionsStore",
    "FakeCouponConditionsStore",
    "VaultCouponConditionsStore",
]
