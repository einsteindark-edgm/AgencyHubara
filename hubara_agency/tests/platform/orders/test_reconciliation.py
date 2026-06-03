"""Tests del núcleo de reconciliación (`platform/orders/reconciliation.py`).

Cubre el cierre del loop Premortem F2+K1: reintentar registros que fallaron
contra Medusa, de forma idempotente, con estado terminal y cap de reintentos.

Todo con un FakePort en memoria — NO toca Medusa ni el vault real.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from src.platform.orders.port import (
    OrderItem,
    OrderRegistrationResult,
    OrderShipping,
)
from src.platform.orders.reconciliation import (
    DEFAULT_MAX_ATTEMPTS,
    OUTCOME_ABANDONED,
    OUTCOME_ALREADY_RESOLVED,
    OUTCOME_ERROR,
    OUTCOME_NOT_FOUND,
    OUTCOME_RESOLVED,
    OUTCOME_STILL_FAILING,
    STATUS_ABANDONED,
    STATUS_PENDING,
    STATUS_RESOLVED,
    mark_resolved_manually,
    reconcile_one,
)


# ----------------------------------------------------------------------
# Fakes / helpers
# ----------------------------------------------------------------------


@dataclass
class FakePort:
    """Port configurable. `results` se consume en orden; el último se repite."""

    results: list[OrderRegistrationResult]
    calls: list[dict] = field(default_factory=list)

    async def register_order(
        self,
        *,
        session_key: str,
        items: list[OrderItem],
        shipping: OrderShipping,
        payment_method: str,
        subtotal_cop: int,
        shipping_cop: int,
        total_cop: int,
        currency: str = "COP",
    ) -> OrderRegistrationResult:
        self.calls.append(
            {
                "session_key": session_key,
                "items": items,
                "shipping": shipping,
                "payment_method": payment_method,
                "total_cop": total_cop,
                "currency": currency,
            }
        )
        idx = min(len(self.calls) - 1, len(self.results) - 1)
        return self.results[idx]


def _ok(order_id="draft_01NEW", provider="medusa") -> OrderRegistrationResult:
    return OrderRegistrationResult(
        success=True, order_id=order_id, provider=provider,
        raw_payload={"id": order_id}, customer_id="cus_1",
    )


def _fail(detail="medusa_api_error: HTTP 503") -> OrderRegistrationResult:
    return OrderRegistrationResult(
        success=False, order_id=None, provider="medusa", error_detail=detail,
    )


def _stub(order_id="HUB-x") -> OrderRegistrationResult:
    return OrderRegistrationResult(
        success=True, order_id=order_id, provider="stub",
    )


def _failed_record(order_id="AUDIT-1", **overrides) -> dict:
    rec = {
        "order_id": order_id,
        "session_key": "wa_57311",
        "provider": "medusa",
        "success": False,
        "error_detail": "medusa_api_error: HTTP 503",
        "customer_id": None,
        "items": [
            {"handle": "vela-x", "quantity": 2, "unit_price_cop": 17000,
             "variant_label": "Lavanda"},
        ],
        "shipping": {
            "city": "Bogotá", "neighborhood": "Chapinero",
            "address": "Calle 100 #1-2", "phone": "+573110000000",
        },
        "payment_method": "transfer",
        "subtotal_cop": 34000,
        "shipping_cop": 5000,
        "total_cop": 39000,
        "currency": "COP",
        "registered_at_ms": 1779800400000,
        "status": STATUS_PENDING,
    }
    rec.update(overrides)
    return rec


def _write_metadata(vault: Path, session_key: str, data: dict) -> Path:
    d = vault / session_key
    d.mkdir(parents=True, exist_ok=True)
    p = d / "metadata.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# reconcile_one — happy path
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_one_success_marks_resolved(tmp_path):
    meta = _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [_failed_record("AUDIT-1")],
    })
    port = FakePort(results=[_ok(order_id="draft_01HXX")])

    outcome = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_57311",
        audit_id="AUDIT-1", port=port,
    )

    assert outcome.outcome == OUTCOME_RESOLVED
    assert outcome.is_resolved
    assert outcome.resolved_order_id == "draft_01HXX"
    assert outcome.provider == "medusa"
    assert outcome.attempts == 1
    assert len(port.calls) == 1
    # El port recibió los DTOs reconstruidos correctamente.
    call = port.calls[0]
    assert call["total_cop"] == 39000
    assert isinstance(call["items"][0], OrderItem)
    assert call["items"][0].handle == "vela-x"
    assert call["items"][0].variant_label == "Lavanda"
    assert isinstance(call["shipping"], OrderShipping)
    assert call["shipping"].city == "Bogotá"

    # Persistido en disco.
    rec = _read(meta)["failed_order_registrations"][0]
    assert rec["status"] == STATUS_RESOLVED
    assert rec["resolved_order_id"] == "draft_01HXX"
    assert rec["resolution"] == "auto"
    assert "resolved_at_ms" in rec
    assert len(rec["reconciliation_attempts"]) == 1
    assert rec["reconciliation_attempts"][0]["ok"] is True


@pytest.mark.asyncio
async def test_reconcile_one_failure_appends_attempt_keeps_pending(tmp_path):
    meta = _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [_failed_record("AUDIT-1")],
    })
    port = FakePort(results=[_fail("still down")])

    outcome = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_57311",
        audit_id="AUDIT-1", port=port,
    )

    assert outcome.outcome == OUTCOME_STILL_FAILING
    assert not outcome.is_resolved
    assert outcome.error_detail == "still down"
    assert outcome.attempts == 1

    rec = _read(meta)["failed_order_registrations"][0]
    assert rec["status"] == STATUS_PENDING
    assert len(rec["reconciliation_attempts"]) == 1
    assert rec["reconciliation_attempts"][0]["ok"] is False
    assert "resolved_order_id" not in rec


# ----------------------------------------------------------------------
# Idempotencia — la propiedad crítica
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_one_already_resolved_is_noop(tmp_path):
    """Un record ya resuelto NO se reintenta (no se llama al port)."""
    _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [
            _failed_record("AUDIT-1", status=STATUS_RESOLVED,
                           resolved_order_id="draft_OLD"),
        ],
    })
    port = FakePort(results=[_ok()])

    outcome = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_57311",
        audit_id="AUDIT-1", port=port,
    )

    assert outcome.outcome == OUTCOME_ALREADY_RESOLVED
    assert outcome.is_resolved
    assert outcome.resolved_order_id == "draft_OLD"
    assert len(port.calls) == 0  # NO tocó el port


@pytest.mark.asyncio
async def test_reconcile_one_idempotent_double_call(tmp_path):
    """Llamar 2 veces seguidas: el segundo es already_resolved, no duplica."""
    meta = _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [_failed_record("AUDIT-1")],
    })
    port = FakePort(results=[_ok(order_id="draft_FIRST"), _ok(order_id="draft_SECOND")])

    first = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-1", port=port,
    )
    second = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-1", port=port,
    )

    assert first.outcome == OUTCOME_RESOLVED
    assert first.resolved_order_id == "draft_FIRST"
    assert second.outcome == OUTCOME_ALREADY_RESOLVED
    assert second.resolved_order_id == "draft_FIRST"  # NO el SECOND
    assert len(port.calls) == 1  # solo el primer intento tocó el port

    rec = _read(meta)["failed_order_registrations"][0]
    assert rec["resolved_order_id"] == "draft_FIRST"
    assert len(rec["reconciliation_attempts"]) == 1


# ----------------------------------------------------------------------
# Stub → no resuelve (evita falso-resuelto)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_one_stub_result_does_not_resolve(tmp_path):
    """Si el reintento cae al stub (Medusa aún sin config), NO resuelve."""
    meta = _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [_failed_record("AUDIT-1")],
    })
    port = FakePort(results=[_stub(order_id="HUB-retry")])

    outcome = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-1", port=port,
    )

    assert outcome.outcome == OUTCOME_STILL_FAILING
    assert not outcome.is_resolved
    rec = _read(meta)["failed_order_registrations"][0]
    assert rec["status"] == STATUS_PENDING
    # El intento quedó registrado (con ok=False porque fue stub).
    assert rec["reconciliation_attempts"][0]["ok"] is False
    assert rec["reconciliation_attempts"][0]["provider"] == "stub"


@pytest.mark.asyncio
async def test_reconcile_one_migrates_stub_order_to_medusa(tmp_path):
    """Un registered_order=stub que ahora SÍ va a Medusa queda resuelto."""
    meta = _write_metadata(tmp_path, "wa_stub", {
        "registered_order": {
            **_failed_record("HUB-stub-1", provider="stub", success=True),
            "status": STATUS_PENDING,
        },
    })
    port = FakePort(results=[_ok(order_id="draft_MIGRATED")])

    outcome = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_stub", audit_id="HUB-stub-1", port=port,
    )

    assert outcome.outcome == OUTCOME_RESOLVED
    assert outcome.resolved_order_id == "draft_MIGRATED"
    rec = _read(meta)["registered_order"]
    assert rec["status"] == STATUS_RESOLVED
    assert rec["resolved_order_id"] == "draft_MIGRATED"


# ----------------------------------------------------------------------
# Cap de reintentos → abandoned
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_one_reaches_cap_becomes_abandoned(tmp_path):
    meta = _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [_failed_record("AUDIT-1")],
    })
    port = FakePort(results=[_fail()])

    last = None
    for _ in range(DEFAULT_MAX_ATTEMPTS):
        last = await reconcile_one(
            vault_dir=tmp_path, session_key="wa_57311",
            audit_id="AUDIT-1", port=port, max_attempts=DEFAULT_MAX_ATTEMPTS,
        )

    assert last.outcome == OUTCOME_ABANDONED
    assert last.attempts == DEFAULT_MAX_ATTEMPTS
    rec = _read(meta)["failed_order_registrations"][0]
    assert rec["status"] == STATUS_ABANDONED
    assert "abandoned_at_ms" in rec

    # Un reintento más NO toca el port (ya es terminal).
    calls_before = len(port.calls)
    again = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-1", port=port,
    )
    assert again.outcome == OUTCOME_ABANDONED
    assert len(port.calls) == calls_before


@pytest.mark.asyncio
async def test_reconcile_one_custom_max_attempts(tmp_path):
    _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [_failed_record("AUDIT-1")],
    })
    port = FakePort(results=[_fail()])

    o1 = await reconcile_one(vault_dir=tmp_path, session_key="wa_57311",
                             audit_id="AUDIT-1", port=port, max_attempts=2)
    assert o1.outcome == OUTCOME_STILL_FAILING
    o2 = await reconcile_one(vault_dir=tmp_path, session_key="wa_57311",
                             audit_id="AUDIT-1", port=port, max_attempts=2)
    assert o2.outcome == OUTCOME_ABANDONED


@pytest.mark.asyncio
async def test_reconcile_one_recovers_before_cap(tmp_path):
    """Falla 2 veces y a la 3ra Medusa vuelve → resuelto."""
    meta = _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [_failed_record("AUDIT-1")],
    })
    port = FakePort(results=[_fail(), _fail(), _ok(order_id="draft_RECOVERED")])

    o1 = await reconcile_one(vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-1", port=port)
    o2 = await reconcile_one(vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-1", port=port)
    o3 = await reconcile_one(vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-1", port=port)

    assert (o1.outcome, o2.outcome, o3.outcome) == (
        OUTCOME_STILL_FAILING, OUTCOME_STILL_FAILING, OUTCOME_RESOLVED,
    )
    rec = _read(meta)["failed_order_registrations"][0]
    assert rec["status"] == STATUS_RESOLVED
    assert len(rec["reconciliation_attempts"]) == 3


# ----------------------------------------------------------------------
# Not found / malformed
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_one_session_not_found(tmp_path):
    port = FakePort(results=[_ok()])
    outcome = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_ghost", audit_id="AUDIT-1", port=port,
    )
    assert outcome.outcome == OUTCOME_NOT_FOUND
    assert len(port.calls) == 0


@pytest.mark.asyncio
async def test_reconcile_one_audit_id_not_found(tmp_path):
    _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [_failed_record("AUDIT-1")],
    })
    port = FakePort(results=[_ok()])
    outcome = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-NOPE", port=port,
    )
    assert outcome.outcome == OUTCOME_NOT_FOUND
    assert len(port.calls) == 0


@pytest.mark.asyncio
async def test_reconcile_one_malformed_record_returns_error(tmp_path):
    """Un record sin items reconstruibles → OUTCOME_ERROR, no crashea."""
    _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [
            _failed_record("AUDIT-1", items=[]),  # items vacío → ValueError
        ],
    })
    port = FakePort(results=[_ok()])
    outcome = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-1", port=port,
    )
    assert outcome.outcome == OUTCOME_ERROR
    assert len(port.calls) == 0


@pytest.mark.asyncio
async def test_reconcile_one_legacy_record_without_status(tmp_path):
    """Record legacy sin campo `status` se trata como pending y se reintenta."""
    rec = _failed_record("AUDIT-LEGACY")
    del rec["status"]
    _write_metadata(tmp_path, "wa_57311", {"failed_order_registrations": [rec]})
    port = FakePort(results=[_ok(order_id="draft_LEGACY")])

    outcome = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-LEGACY", port=port,
    )
    assert outcome.outcome == OUTCOME_RESOLVED


# ----------------------------------------------------------------------
# mark_resolved_manually
# ----------------------------------------------------------------------


def test_mark_resolved_manually_sets_status(tmp_path):
    meta = _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [_failed_record("AUDIT-1")],
    })
    outcome = mark_resolved_manually(
        vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-1",
        note="registrado a mano en Medusa Admin", resolved_order_id="order_MANUAL",
    )
    assert outcome.outcome == OUTCOME_RESOLVED
    rec = _read(meta)["failed_order_registrations"][0]
    assert rec["status"] == STATUS_RESOLVED
    assert rec["resolution"] == "manual"
    assert rec["resolution_note"] == "registrado a mano en Medusa Admin"
    assert rec["resolved_order_id"] == "order_MANUAL"


def test_mark_resolved_manually_idempotent(tmp_path):
    _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [
            _failed_record("AUDIT-1", status=STATUS_RESOLVED, resolved_order_id="x"),
        ],
    })
    outcome = mark_resolved_manually(
        vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-1",
    )
    assert outcome.outcome == OUTCOME_ALREADY_RESOLVED


def test_mark_resolved_manually_not_found(tmp_path):
    _write_metadata(tmp_path, "wa_57311", {"failed_order_registrations": []})
    outcome = mark_resolved_manually(
        vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-NOPE",
    )
    assert outcome.outcome == OUTCOME_NOT_FOUND


# ----------------------------------------------------------------------
# Persistencia atómica — no deja tmp huérfano
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_one_atomic_write_no_tmp_leftover(tmp_path):
    _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [_failed_record("AUDIT-1")],
    })
    port = FakePort(results=[_ok()])
    await reconcile_one(vault_dir=tmp_path, session_key="wa_57311",
                        audit_id="AUDIT-1", port=port)
    leftovers = list((tmp_path / "wa_57311").glob("*.tmp"))
    assert leftovers == []
