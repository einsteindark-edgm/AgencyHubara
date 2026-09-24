"""Puertos de plataforma en modo sandbox del laboratorio (plan §3.6, PR 11).

La plataforma es dueña de sus fábricas de composición; este módulo las pone
en modo sandbox. `installed_sandbox_ports` reemplaza las que hablan con
Medusa ANTES de importar un worker: el de ventas captura los puertos al
importarse (`_promotions`, `_order_query_port`, `_checkout_verifier`,
`_order_registration_port`), así que tienen que nacer ya del sandbox.
Los plugins lo usan por `src.sdk.labkit`.

| Efecto (producción)                   | En el sandbox                                    |
|---------------------------------------|--------------------------------------------------|
| Medusa (settings, cliente HTTP)       | no existe: falla en voz alta, nunca red          |
| Verificar precio al cobrar (en vivo)  | el verificador REAL con `SnapshotLiveMedusa`     |
| Promociones y cupones (Medusa)        | `promotions.json` exportado con el banco         |
| Registrar pedido (draft order real)   | `StubOrderRegistration` (id ficticio `HUB-…`)    |
| Estado de pedido (Medusa en vivo)     | vacío (`EmptyOrderQuery`)                         |

El catálogo no se reemplaza: ya es un snapshot en disco
(`CATALOG_SNAPSHOT_DIR` apunta a la copia del sandbox). WhatsApp y CAPI
tampoco: sin llaves, el cliente de WhatsApp ya simula el envío y CAPI se
salta (el guard de la caja garantiza que no hay llaves). El test de fugas
verifica todo lo anterior con los hooks de auditoría de Python.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.platform.catalog.errors import ProductNotFoundError
from src.platform.promotions.port import FakePromotionsPort, PromotionDTO


class SandboxNoMedusaError(RuntimeError):
    """El sandbox del laboratorio no tiene Medusa: nada puede llamarlo."""


def _no_medusa(*_args: Any, **_kwargs: Any) -> Any:
    raise SandboxNoMedusaError("sandbox del laboratorio: Medusa no existe aquí")


class SnapshotLiveMedusa:
    """"Medusa en vivo" que responde desde el snapshot del banco: el
    verificador de checkout compara el snapshot contra sí mismo, como en el
    camino feliz de producción (snapshot y Medusa coinciden)."""

    def __init__(self, catalog: Any) -> None:
        self._catalog = catalog

    async def list(self, *, handle: str | None = None, limit: int | None = None, **_: Any) -> SimpleNamespace:
        if not handle:
            return SimpleNamespace(products=[])
        try:
            product = await self._catalog.get_by_handle(handle)
        except ProductNotFoundError:
            return SimpleNamespace(products=[])
        return SimpleNamespace(products=[product])


_PROMO_FIELDS = {f.name for f in dataclasses.fields(PromotionDTO)}
_TUPLE_FIELDS = {"product_ids", "variant_ids", "collection_ids", "tag_values"}


def load_promotions(path: Path) -> list[PromotionDTO]:
    """Las promociones exportadas con el banco (`promotions.json`)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[PromotionDTO] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        data = {k: (tuple(v or ()) if k in _TUPLE_FIELDS else v) for k, v in item.items() if k in _PROMO_FIELDS}
        try:
            out.append(PromotionDTO(**data))
        except TypeError:
            continue
    return out


@contextmanager
def installed_sandbox_ports(*, promotions_path: Path, catalog: Any) -> Iterator[None]:
    """Reemplaza las fábricas con efectos por las del sandbox y las restaura
    al salir. En la caja se instala una vez por proceso (un proceso por
    caso), antes de importar el worker de ventas."""
    import src.platform.medusa.composition as medusa
    import src.platform.orders.composition as orders
    import src.platform.promotions.composition as promos
    from src.platform.orders.empty_query import EmptyOrderQuery
    from src.platform.orders.facts import OrderFactsStore
    from src.platform.orders.stub import StubOrderRegistration

    promotions = FakePromotionsPort(load_promotions(promotions_path))
    query = EmptyOrderQuery()
    facts = OrderFactsStore(query)
    registration = StubOrderRegistration()
    live = SnapshotLiveMedusa(catalog)
    replacements: list[tuple[Any, str, Any]] = [
        (medusa, "get_medusa_settings", _no_medusa),
        (medusa, "get_medusa_client", _no_medusa),
        (medusa, "get_medusa_product_service", lambda: live),
        (promos, "get_promotions_port", lambda: promotions),
        (orders, "get_order_registration_port", lambda: registration),
        (orders, "get_order_query_port", lambda: query),
        (orders, "get_order_facts_port", lambda: facts),
        (orders, "_raw_order_query", lambda: query),
    ]
    # Cada módulo ya cargado que se quedó con la fábrica original (`from x
    # import f`, el caché de `src.sdk.connectorkit`) también se reapunta: si
    # no, el worker podría construir el puerto de Medusa por otro camino.
    saved: list[tuple[Any, str, Any]] = []
    for module, name, value in replacements:
        original = getattr(module, name)
        holders = [m for m in list(sys.modules.values()) if m is not None and vars(m).get(name) is original]
        for holder in {id(m): m for m in [module, *holders]}.values():
            saved.append((holder, name, original))
            setattr(holder, name, value)
    try:
        yield
    finally:
        for module, name, value in saved:
            setattr(module, name, value)
