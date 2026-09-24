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


def test_corrupt_quota_file_fails_closed_instead_of_meaning_no_quota(tmp_path: Path) -> None:
    """Un archivo roto NO es "sin cupo" (eso aplicaría el cupón sin límite)."""
    from src.platform.promotions.quota_store import QuotaStoreError

    store = VaultPromoQuotaStore(tmp_path)
    store.replace("promo_1", "AMOR27", [_q("q1")], show_units_left=True, actor="a", now_iso="t")
    (tmp_path / "_promotions" / "quotas" / "promo_1.json").write_text("{roto", encoding="utf-8")

    with pytest.raises(QuotaStoreError):
        store.get("promo_1")


def test_counting_since_is_fixed_at_the_first_save_and_never_moves(store: PromoQuotaStore) -> None:
    first = store.replace("promo_1", "AMOR27", [_q("q1")], show_units_left=True, actor="a",
                          now_iso="2026-09-23T17:00:00Z")
    # Borrar la única fila y volver a crearla NO adelanta desde cuándo se cuenta.
    store.replace("promo_1", "AMOR27", [], show_units_left=True, actor="a", now_iso="2026-09-24T09:00:00Z")
    again = store.replace("promo_1", "AMOR27", [_q("q1", created_at="2026-09-25T09:00:00Z")],
                          show_units_left=True, actor="a", now_iso="2026-09-25T09:00:00Z")

    assert first.counting_since == "2026-09-23T17:00:00Z"
    assert again.counting_since == "2026-09-23T17:00:00Z"


# --- Premortem: formato nuevo, edición concurrente, productos que salen ----------


def _sheet_file(root: Path) -> Path:
    return root / "_promotions" / "quotas" / "promo_1.json"


def test_unknown_keys_of_a_newer_format_are_ignored_not_fatal(tmp_path: Path) -> None:
    """Una clave que este código no conoce (formato más nuevo, edición a mano)
    no vuelve ilegible el cupo: sin esto la central quedaba inusable (detalle
    503, guardar 500, lista vacía en silencio) y el bot fallaba cerrado."""
    import json

    store = VaultPromoQuotaStore(tmp_path)
    store.replace("promo_1", "AMOR27", [_q("q1")], show_units_left=True, actor="ana", now_iso="t1")
    data = json.loads(_sheet_file(tmp_path).read_text(encoding="utf-8"))
    data["schema_version"] = 2
    data["quotas"][0]["note"] = "fila editada a mano"
    _sheet_file(tmp_path).write_text(json.dumps(data), encoding="utf-8")

    assert store.get("promo_1").quotas == (_q("q1"),)
    assert [s.promotion_id for s in store.list_sheets()] == ["promo_1"]
    saved = store.replace("promo_1", "AMOR27", [_q("q1", 9)], show_units_left=True, actor="ana", now_iso="t2")
    assert saved.quotas[0].units == 9


@pytest.mark.parametrize(
    "row",
    [
        {"id": "q1", "promotion_id": "promo_1"},  # le faltan campos obligatorios
        {**{f: v for f, v in vars(_q("q1")).items()}, "units": "cinco"},  # unidades no enteras
    ],
)
def test_a_row_that_cannot_be_trusted_still_fails_closed(tmp_path: Path, row: dict) -> None:
    import json

    from src.platform.promotions.quota_store import QuotaStoreError

    _sheet_file(tmp_path).parent.mkdir(parents=True)
    _sheet_file(tmp_path).write_text(json.dumps({"code": "AMOR27", "quotas": [row]}), encoding="utf-8")

    with pytest.raises(QuotaStoreError):
        VaultPromoQuotaStore(tmp_path).get("promo_1")


def test_replace_over_an_unreadable_sheet_refuses_and_keeps_the_file(tmp_path: Path) -> None:
    """Pisar un cupo ilegible perdería desde cuándo se cuentan las vendidas
    (y vendería de más): se rechaza con un error claro y el archivo queda."""
    from src.platform.promotions.quota_store import QuotaStoreError

    store = VaultPromoQuotaStore(tmp_path)
    _sheet_file(tmp_path).parent.mkdir(parents=True)
    _sheet_file(tmp_path).write_text("{roto", encoding="utf-8")

    with pytest.raises(QuotaStoreError) as err:
        store.replace("promo_1", "AMOR27", [_q("q1")], show_units_left=True, actor="ana", now_iso="t")

    assert err.value.reason == "unreadable"
    assert _sheet_file(tmp_path).read_text(encoding="utf-8") == "{roto"


def test_replace_with_a_stale_version_is_refused_without_writing(store: PromoQuotaStore) -> None:
    """C-5: quien editó sobre una versión vieja no pisa lo que otra persona
    guardó después (la comparación va bajo el candado del cupo)."""
    from src.platform.promotions.quota_store import QuotaStoreError

    first = store.replace("promo_1", "AMOR27", [_q("q1")], show_units_left=True, actor="ana",
                          now_iso="2026-09-23T17:00:00.000001Z", expected_updated_at=None)
    luis = store.replace("promo_1", "AMOR27", [_q("q1", 3)], show_units_left=True, actor="luis",
                         now_iso="2026-09-23T17:05:00.000002Z", expected_updated_at=first.updated_at)

    with pytest.raises(QuotaStoreError) as err:  # Ana todavía veía la primera versión
        store.replace("promo_1", "AMOR27", [_q("q1", 8)], show_units_left=True, actor="ana",
                      now_iso="2026-09-23T17:06:00.000003Z", expected_updated_at=first.updated_at)
    with pytest.raises(QuotaStoreError) as never:  # "nunca se guardó" ya no es cierto
        store.replace("promo_1", "AMOR27", [_q("q1", 8)], show_units_left=True, actor="ana",
                      now_iso="2026-09-23T17:06:00.000004Z", expected_updated_at=None)

    assert (err.value.reason, never.value.reason) == ("changed", "changed")
    assert type(err.value).__name__ == "QuotaSheetChangedError"
    assert store.get("promo_1") == luis
    # Sin `expected_updated_at` (cliente viejo) no hay chequeo.
    assert store.replace("promo_1", "AMOR27", [_q("q1", 4)], show_units_left=True, actor="ana",
                         now_iso="2026-09-23T17:07:00Z").quotas[0].units == 4


def test_prune_to_products_drops_rows_of_products_the_coupon_no_longer_has(store: PromoQuotaStore) -> None:
    """A15: sacar un producto del cupón saca sus filas del cupo (el bot ya no
    puede venderlas; sumarlas inflaba los totales de la central)."""
    other = PromoUnitQuota(
        id="q9", promotion_id="promo_1", code="AMOR27", product_id="prod_vaso", handle="vaso",
        title="Vaso", color=None, aroma=None, units=4, created_at="2026-09-23T17:00:00Z", created_by="ana",
    )
    saved = store.replace("promo_1", "AMOR27", [_q("q1"), other], show_units_left=False, actor="ana",
                          now_iso="2026-09-23T17:00:00Z")

    removed = store.prune_to_products("promo_1", ("prod_cubo",), actor="luis", now_iso="2026-09-24T10:00:00Z")
    nothing = store.prune_to_products("promo_1", ("prod_cubo",), actor="luis", now_iso="2026-09-24T10:01:00Z")
    after = store.get("promo_1")

    assert removed == (other,)
    assert nothing == ()
    assert after.quotas == (_q("q1"),)
    assert (after.show_units_left, after.counting_since) == (False, saved.counting_since)
    assert after.updated_by == "luis"


def test_two_saves_at_the_same_instant_get_strictly_increasing_versions(store: PromoQuotaStore) -> None:
    """C-5: dos guardados en el mismo instante (o con el reloj atrás) no
    repiten versión — si no, quien cargó entre los dos pasaría el chequeo."""
    from datetime import datetime

    from src.platform.promotions.quota_store import QuotaStoreError

    same = "2026-09-23T17:00:00.000000Z"
    first = store.replace("promo_1", "AMOR27", [_q("q1")], show_units_left=True, actor="ana", now_iso=same)
    second = store.replace("promo_1", "AMOR27", [_q("q1", 3)], show_units_left=True, actor="luis",
                           now_iso=same, expected_updated_at=first.updated_at)
    pruned_at = store.replace("promo_1", "AMOR27", [_q("q1", 3)], show_units_left=True, actor="luis",
                              now_iso="2026-09-23T16:59:59.000000Z")  # reloj atrás

    def instant(raw: str) -> datetime:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))

    assert instant(first.updated_at) < instant(second.updated_at) < instant(pruned_at.updated_at)
    with pytest.raises(QuotaStoreError) as err:  # quien cargó la primera versión
        store.replace("promo_1", "AMOR27", [_q("q1", 8)], show_units_left=True, actor="ana",
                      now_iso=same, expected_updated_at=first.updated_at)
    assert err.value.reason == "changed"
    assert first.counting_since == pruned_at.counting_since == same  # desde cuándo se cuenta no se mueve
