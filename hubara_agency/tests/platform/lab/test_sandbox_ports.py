"""Puertos en modo sandbox del laboratorio (plan §3.6, PR 11): qué responde
en lugar de cada efecto.

  * Medusa no existe en el sandbox: cualquier fábrica que lo pida falla en
    voz alta (nunca una llamada de red).
  * Promociones: las exportadas con el banco. Registrar pedido: el stub (se
    "habría creado"). Estado de pedido: vacío.
  * Verificar precio al cobrar: el verificador REAL de producción, con un
    "Medusa en vivo" que responde desde el mismo snapshot del banco.
  * Reloj: saludo por hora, bloque de hora de Bogotá y "Current Time" del
    prompt de exoclaw salen a la hora del turno original.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.platform.lab.sandbox_ports import (
    SandboxNoMedusaError,
    SnapshotLiveMedusa,
    installed_sandbox_ports,
    load_promotions,
)

PROMO = {
    "id": "promo_1", "code": "AMOR26", "discount_type": "percentage", "value": 10, "currency_code": None,
    "target_type": "items", "allocation": "across", "max_quantity": None, "product_ids": ["prod_1"],
    "variant_ids": [], "collection_ids": [], "min_subtotal_cop": None, "is_automatic": False, "status": "active",
    "starts_at_ms": None, "ends_at_ms": None, "budget_type": None, "budget_limit": None, "budget_used": None,
    "description": "Amor y amistad", "scope_unresolved": False, "tag_values": ["Color: Rosado"], "campo_nuevo": 1,
}


class _Catalog:
    def __init__(self, products: dict) -> None:
        self.products = products

    async def get_by_handle(self, handle: str):
        from src.platform.catalog.errors import ProductNotFoundError

        if handle not in self.products:
            raise ProductNotFoundError(handle)
        return self.products[handle]


def _product(amount: str) -> SimpleNamespace:
    price = SimpleNamespace(amount=amount, currency_code="cop")
    return SimpleNamespace(title="Cubo Love", handle="cubo-love", variants=[SimpleNamespace(prices=[price])])


def test_load_promotions_reads_the_bench_export(tmp_path: Path) -> None:
    path = tmp_path / "promotions.json"
    path.write_text(json.dumps([PROMO]), encoding="utf-8")

    [promo] = load_promotions(path)

    assert promo.code == "AMOR26" and promo.product_ids == ("prod_1",) and promo.tag_values == ("Color: Rosado",)
    assert load_promotions(tmp_path / "no-existe.json") == []


async def test_snapshot_live_medusa_answers_from_the_snapshot() -> None:
    live = SnapshotLiveMedusa(_Catalog({"cubo-love": _product("59000")}))

    page = await live.list(handle="cubo-love", limit=1)
    missing = await live.list(handle="otro", limit=1)

    assert page.products[0].handle == "cubo-love"
    assert missing.products == []


async def test_the_real_checkout_verifier_passes_against_the_snapshot() -> None:
    from src.platform.catalog.checkout_port import CheckoutItem
    from src.platform.catalog.medusa_checkout import MedusaCheckoutVerification

    catalog = _Catalog({"cubo-love": _product("59000")})
    verifier = MedusaCheckoutVerification(medusa=SnapshotLiveMedusa(catalog), snapshot=catalog)

    result = await verifier.verify_items([CheckoutItem(handle="cubo-love", quantity=1)])

    assert result.verified is True and result.catalog_available is True
    assert result.items[0].discrepancy is False


async def test_installed_adapters_replace_every_effectful_factory_and_restore_them(tmp_path: Path) -> None:
    import src.platform.medusa.composition as medusa
    import src.platform.orders.composition as orders
    import src.platform.promotions.composition as promos
    from src.platform.orders.empty_query import EmptyOrderQuery
    from src.platform.orders.stub import StubOrderRegistration

    (tmp_path / "promotions.json").write_text(json.dumps([PROMO]), encoding="utf-8")
    originals = (medusa.get_medusa_settings, promos.get_promotions_port, orders.get_order_registration_port)

    with installed_sandbox_ports(promotions_path=tmp_path / "promotions.json", catalog=_Catalog({})):
        with pytest.raises(SandboxNoMedusaError):
            medusa.get_medusa_settings()
        with pytest.raises(SandboxNoMedusaError):
            medusa.get_medusa_client()
        assert isinstance(medusa.get_medusa_product_service(), SnapshotLiveMedusa)
        assert [p.code for p in await promos.get_promotions_port().list_active()] == ["AMOR26"]
        assert isinstance(orders.get_order_registration_port(), StubOrderRegistration)
        assert isinstance(orders.get_order_query_port(), EmptyOrderQuery)
        assert orders.get_order_facts_port() is orders.get_order_facts_port()

    assert (medusa.get_medusa_settings, promos.get_promotions_port, orders.get_order_registration_port) == originals


def test_connectorkit_resolves_to_the_sandbox_promotions(tmp_path: Path) -> None:
    """El worker de ventas importa `get_promotions_port` desde el SDK: tiene
    que caer en el adaptador del sandbox, no en Medusa."""
    (tmp_path / "promotions.json").write_text(json.dumps([PROMO]), encoding="utf-8")

    with installed_sandbox_ports(promotions_path=tmp_path / "promotions.json", catalog=_Catalog({})):
        from src.sdk.connectorkit import get_promotions_port

        assert get_promotions_port().__class__.__name__ == "FakePromotionsPort"
