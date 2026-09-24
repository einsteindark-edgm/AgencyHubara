"""Almacén del cupo por unidad (Fase 4) — contract suite fake / vault.

El cupo vive en Hubara (la API de Medusa no deja escribir metadata en una
promoción): `_promotions/quotas/<promotion_id>.json` en el vault, con
read-modify-write bajo `flock` (API y workers comparten el disco).
"""
from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from src.platform.promotions.quota_store import (
    FakePromoQuotaStore,
    PromoQuotaStore,
    QuotaSheet,
    VaultPromoQuotaStore,
)
from src.platform.promotions.quotas import PromoUnitQuota


def _q(qid: str, units: int = 5, *, created_by: str = "ana", created_at: str = "2026-09-23T17:00:00Z") -> PromoUnitQuota:
    return PromoUnitQuota(
        id=qid, promotion_id="promo_1", code="AMOR27", product_id="prod_cubo",
        handle="cubo-love", title="Cubo Love", color="Rosado", aroma=qid,
        units=units, created_at=created_at, created_by=created_by,
    )


@pytest.fixture(params=["fake", "vault"])
def store(request, tmp_path: Path) -> PromoQuotaStore:
    return FakePromoQuotaStore() if request.param == "fake" else VaultPromoQuotaStore(tmp_path)


def test_quota_store_contract(store: PromoQuotaStore) -> None:
    assert store.get("promo_1") == QuotaSheet(promotion_id="promo_1", code="", quotas=())

    saved = store.replace(
        "promo_1", "AMOR27", [_q("q1"), _q("q2", 3)],
        show_units_left=False, actor="ana", now_iso="2026-09-23T17:00:00Z",
    )
    assert saved.quotas == (_q("q1"), _q("q2", 3))
    assert saved.show_units_left is False
    assert (saved.updated_by, saved.updated_at) == ("ana", "2026-09-23T17:00:00Z")
    assert store.get("promo_1") == saved

    # Editar: q1 cambia unidades (conserva quién/cuándo la creó), q2 se va.
    edited = store.replace(
        "promo_1", "AMOR27",
        [_q("q1", 8, created_by="luis", created_at="2026-09-24T10:00:00Z"), _q("q3", 1, created_by="luis", created_at="2026-09-24T10:00:00Z")],
        show_units_left=True, actor="luis", now_iso="2026-09-24T10:00:00Z",
    )
    assert [(q.id, q.units, q.created_by) for q in edited.quotas] == [("q1", 8, "ana"), ("q3", 1, "luis")]
    assert [s.promotion_id for s in store.list_sheets()] == ["promo_1"]

    store.delete("promo_1")
    assert store.get("promo_1").quotas == ()
    assert store.list_sheets() == []


@pytest.mark.parametrize("bad", ["../etc", "promo/1", "", ".", "promo 1", "a" * 200])
def test_quota_store_rejects_unsafe_promotion_ids(tmp_path: Path, bad: str) -> None:
    store = VaultPromoQuotaStore(tmp_path)
    with pytest.raises(ValueError):
        store.get(bad)
    with pytest.raises(ValueError):
        store.replace(bad, "X", [], show_units_left=True, actor="a", now_iso="t")


def _writer(root: str, n: int) -> None:
    store = VaultPromoQuotaStore(Path(root))
    for i in range(n):
        store.update(
            "promo_1",
            lambda sheet, i=i: sheet.with_quotas(sheet.quotas + (_q(f"q{multiprocessing.current_process().pid}_{i}"),)),
        )


def test_vault_quota_store_concurrent_writes_keep_all_rows(tmp_path: Path) -> None:
    procs = [multiprocessing.get_context("spawn").Process(target=_writer, args=(str(tmp_path), 10)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    assert len(VaultPromoQuotaStore(tmp_path).get("promo_1").quotas) == 40
