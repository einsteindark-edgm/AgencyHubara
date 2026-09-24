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
    DiscountedUnits,
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


@dataclass
class KwargsPort:
    """Port que acepta el contrato completo (cupón incluido) y lo guarda."""

    calls: list[dict] = field(default_factory=list)

    async def register_order(self, **kwargs) -> OrderRegistrationResult:
        self.calls.append(kwargs)
        return _ok(order_id="draft_01CUPON")


@pytest.mark.asyncio
async def test_rebuild_order_args_keeps_coupon_and_discounted_lines(tmp_path):
    """El reintento de un registro con cupón NO pierde el descuento: el port
    recibe el mismo cupón y las mismas unidades con descuento que el intento
    original (sin eso, Medusa quedaría con el total de lista — pedido #44)."""
    record = _failed_record(
        "AUDIT-C",
        items=[
            {"handle": "cubo-love", "quantity": 2, "unit_price_cop": 21000},
            {"handle": "vela-x", "quantity": 1, "unit_price_cop": 17000},
        ],
        subtotal_cop=59000,
        shipping_cop=7900,
        total_cop=62700,
        coupon_code="AMOR26",
        discount_cop=4200,
        coupon_line_discounts=[{"index": 0, "units": 2, "discount_unit_cop": 2100}],
    )
    _write_metadata(tmp_path, "wa_57311", {"failed_order_registrations": [record]})
    port = KwargsPort()

    outcome = await reconcile_one(
        vault_dir=tmp_path, session_key="wa_57311", audit_id="AUDIT-C", port=port,
    )

    assert outcome.outcome == OUTCOME_RESOLVED
    (call,) = port.calls
    assert [it.discounted_units for it in call["items"]] == [
        (DiscountedUnits(units=2, discount_unit_cop=2100),),
        (),
    ]
    assert (call.get("coupon_code"), call.get("discount_cop")) == ("AMOR26", 4200)
    assert call["total_cop"] == 62700


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


@pytest.mark.asyncio
async def test_rebuild_order_args_keeps_the_quota_of_each_discounted_group(tmp_path):
    """El reintento conserva el cupo que consumió cada unidad con descuento:
    sin `quota_id` la línea no llevaría `coupon_quota_id` y la unidad NO
    contaría como vendida (el cupo vendería de más)."""
    record = _failed_record(
        "AUDIT-Q",
        items=[{"handle": "cubo-love", "quantity": 2, "unit_price_cop": 21000}],
        subtotal_cop=42000,
        shipping_cop=7900,
        total_cop=47800,
        coupon_code="AMOR26",
        discount_cop=2100,
        coupon_line_discounts=[
            {"index": 0, "units": 1, "discount_unit_cop": 2100, "quota_id": "q_rosado_cafe"}
        ],
    )
    _write_metadata(tmp_path, "wa_57312", {"failed_order_registrations": [record]})
    port = KwargsPort()

    await reconcile_one(vault_dir=tmp_path, session_key="wa_57312", audit_id="AUDIT-Q", port=port,
                        quota_recheck=_Recheck(left={"q_rosado_cafe": 1}))

    (call,) = port.calls
    assert call["items"][0].discounted_units == (
        DiscountedUnits(units=1, discount_unit_cop=2100, quota_id="q_rosado_cafe"),
    )



@dataclass
class _Recheck:
    """Re-chequeo del cupo al reintentar: cuántas quedan de cada cupo."""

    left: dict[str, int]
    held: list[str] = field(default_factory=list)

    def hold(self, code: str):
        import contextlib

        @contextlib.asynccontextmanager
        async def _cm():
            self.held.append(code)
            yield

        return _cm()

    async def units_left(self, quota_ids: set[str], *, own_order: tuple[str, str] | None = None) -> dict[str, int]:
        return {q: self.left.get(q, 0) for q in quota_ids}


def _quota_record(audit_id: str) -> dict:
    return _failed_record(
        audit_id,
        items=[{"handle": "cubo-love", "quantity": 1, "unit_price_cop": 21000}],
        subtotal_cop=21000, shipping_cop=7900, total_cop=26800,
        coupon_code="AMOR26", discount_cop=2100,
        coupon_line_discounts=[{"index": 0, "units": 1, "discount_unit_cop": 2100, "quota_id": "q_rosado_cafe"}],
    )


@pytest.mark.asyncio
async def test_reconcile_with_quota_units_gone_leaves_it_for_a_human(tmp_path):
    """Mientras Medusa estaba caído otro cliente se llevó la unidad: el
    reintento NO la vende dos veces; queda para registro manual."""
    _write_metadata(tmp_path, "wa_57313", {"failed_order_registrations": [_quota_record("AUDIT-QG")]})
    port = KwargsPort()
    recheck = _Recheck(left={"q_rosado_cafe": 0})

    outcome = await reconcile_one(vault_dir=tmp_path, session_key="wa_57313", audit_id="AUDIT-QG",
                                  port=port, quota_recheck=recheck)

    assert outcome.outcome == OUTCOME_ABANDONED
    assert "quota_changed" in (outcome.error_detail or "")
    assert port.calls == [] and recheck.held == ["AMOR26"]


@pytest.mark.asyncio
async def test_reconcile_with_quota_units_left_registers_under_the_lock(tmp_path):
    _write_metadata(tmp_path, "wa_57314", {"failed_order_registrations": [_quota_record("AUDIT-QL")]})
    port = KwargsPort()
    recheck = _Recheck(left={"q_rosado_cafe": 1})

    outcome = await reconcile_one(vault_dir=tmp_path, session_key="wa_57314", audit_id="AUDIT-QL",
                                  port=port, quota_recheck=recheck)

    assert outcome.outcome == OUTCOME_RESOLVED
    assert len(port.calls) == 1 and recheck.held == ["AMOR26"]


# --- Premortem: el re-chequeo del cupo no tumba el barrido ni se cuenta a sí mismo ------


@dataclass
class _BrokenRecheck:
    """El re-chequeo no puede leer: candado ocupado, Medusa o el vault caídos."""

    error: Exception
    on_hold: bool = False

    def hold(self, code: str):
        import contextlib

        @contextlib.asynccontextmanager
        async def _cm():
            if self.on_hold:
                raise self.error
            yield

        return _cm()

    async def units_left(self, quota_ids: set[str], *, own_order: tuple[str, str] | None = None) -> dict[str, int]:
        raise self.error


def _broken(kind: str) -> _BrokenRecheck:
    from src.platform.promotions.port import PromotionsUnavailableError
    from src.platform.promotions.quota_lock import QuotaLockTimeout
    from src.platform.promotions.quota_store import QuotaStoreError

    return {
        "lock": _BrokenRecheck(QuotaLockTimeout("ocupado"), on_hold=True),
        "medusa": _BrokenRecheck(PromotionsUnavailableError("timeout")),
        "vault": _BrokenRecheck(QuotaStoreError("ilegible")),
    }[kind]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["lock", "medusa", "vault"])
async def test_reconcile_that_cannot_recheck_the_quota_stays_pending(tmp_path, kind):
    """Sin poder releer el cupo NO se registra (podría vender dos veces la
    última unidad) y tampoco explota: el intento cuenta y el pedido sigue
    pendiente para el próximo barrido."""
    path = _write_metadata(tmp_path, "wa_57316", {"failed_order_registrations": [_quota_record("AUDIT-QB")]})
    port = KwargsPort()

    outcome = await reconcile_one(vault_dir=tmp_path, session_key="wa_57316", audit_id="AUDIT-QB",
                                  port=port, quota_recheck=_broken(kind))

    assert outcome.outcome == OUTCOME_STILL_FAILING
    assert (outcome.error_detail or "").startswith("quota_unavailable")
    assert port.calls == []
    (rec,) = json.loads(path.read_text(encoding="utf-8"))["failed_order_registrations"]
    assert rec["status"] == STATUS_PENDING and len(rec["reconciliation_attempts"]) == 1


@dataclass
class _OwnDraftRecheck:
    """Medusa ya tiene el draft del intento original (respondió tarde): con
    él adentro no queda ninguna unidad; sin él, queda la que es suya."""

    own_orders: list[tuple[str, str] | None] = field(default_factory=list)

    def hold(self, code: str):
        import contextlib

        @contextlib.asynccontextmanager
        async def _cm():
            yield

        return _cm()

    async def units_left(self, quota_ids: set[str], *, own_order: tuple[str, str] | None = None) -> dict[str, int]:
        self.own_orders.append(own_order)
        return {q: 1 if own_order else 0 for q in quota_ids}


@pytest.mark.asyncio
async def test_reconcile_does_not_count_its_own_draft_as_sold(tmp_path):
    """L-28: el re-chequeo deja afuera el draft del MISMO pedido (misma sesión
    y mismo fingerprint): sin eso lo abandonaba por "quota_changed" aunque el
    pedido ya existía, y el humano lo registraba otra vez (duplicado)."""
    from src.platform.orders.medusa_order import _compute_order_fingerprint

    _write_metadata(tmp_path, "wa_57317", {"failed_order_registrations": [_quota_record("AUDIT-QP")]})
    port = KwargsPort()
    recheck = _OwnDraftRecheck()

    outcome = await reconcile_one(vault_dir=tmp_path, session_key="wa_57317", audit_id="AUDIT-QP",
                                  port=port, quota_recheck=recheck)

    assert outcome.outcome == OUTCOME_RESOLVED
    items = [OrderItem(handle="cubo-love", quantity=1, unit_price_cop=21000,
                       discounted_units=(DiscountedUnits(1, 2100, quota_id="q_rosado_cafe"),))]
    assert recheck.own_orders == [("wa_57317", _compute_order_fingerprint(items, 26800, "transfer"))]



@pytest.mark.asyncio
async def test_rebuild_order_args_keeps_the_color_and_aroma_of_each_line(tmp_path):
    """El reintento escribe en Medusa el MISMO color/aroma (y el mismo
    fingerprint) que el intento original."""
    record = _failed_record(
        "AUDIT-CA",
        items=[{"handle": "cubo-love", "quantity": 1, "unit_price_cop": 21000, "color": "rosado"}],
        item_variants=[{"color": "Rosado", "aroma": "Café"}],
        subtotal_cop=21000, shipping_cop=7900, total_cop=28900,
    )
    _write_metadata(tmp_path, "wa_57318", {"failed_order_registrations": [record]})
    port = KwargsPort()

    await reconcile_one(vault_dir=tmp_path, session_key="wa_57318", audit_id="AUDIT-CA", port=port)

    (call,) = port.calls
    assert (call["items"][0].color, call["items"][0].aroma) == ("Rosado", "Café")



# --- C3: la reconciliación no escribe una copia vieja de la sesión --------------------


@pytest.mark.asyncio
async def test_reconcile_keeps_what_others_wrote_to_the_session_meanwhile(tmp_path):
    """Mientras se reintentaba (candado + Medusa), un humano tomó la
    conversación: el reintento guarda SU record sin revertir el resto."""
    path = _write_metadata(tmp_path, "wa_57319", {"failed_order_registrations": [_failed_record("AUDIT-W")]})

    @dataclass
    class _PortThatSeesAHandoff:
        calls: list[dict] = field(default_factory=list)

        async def register_order(self, **kwargs) -> OrderRegistrationResult:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["active_route"] = "humano"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.calls.append(kwargs)
            return _ok()

    outcome = await reconcile_one(vault_dir=tmp_path, session_key="wa_57319", audit_id="AUDIT-W",
                                  port=_PortThatSeesAHandoff())

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert outcome.outcome == OUTCOME_RESOLVED
    assert saved["active_route"] == "humano"
    assert saved["failed_order_registrations"][0]["status"] == STATUS_RESOLVED


@pytest.mark.asyncio
async def test_two_reconciliations_of_the_same_quota_order_do_not_undo_each_other(tmp_path):
    """El barrido y el botón "Reintentar" sobre el mismo pedido con cupo: el
    segundo espera el candado y, al entrar, ve que el primero ya lo resolvió
    (no lo abandona por "quota_changed" contando el draft recién creado)."""
    import asyncio
    import contextlib

    path = _write_metadata(tmp_path, "wa_57320", {"failed_order_registrations": [_quota_record("AUDIT-2X")]})
    sold: dict[str, int] = {}
    gate = asyncio.Lock()

    class _Recheck2:
        def hold(self, code: str):
            @contextlib.asynccontextmanager
            async def _cm():
                async with gate:
                    yield
            return _cm()

        async def units_left(self, quota_ids, *, own_order=None):
            return {q: 1 - sold.get(q, 0) for q in quota_ids}

    class _SellingPort:
        async def register_order(self, **kwargs) -> OrderRegistrationResult:
            await asyncio.sleep(0.01)
            for item in kwargs["items"]:
                for group in item.discounted_units:
                    sold[group.quota_id] = sold.get(group.quota_id, 0) + group.units
            return _ok(order_id="draft_q")

    outcomes = await asyncio.gather(*[
        reconcile_one(vault_dir=tmp_path, session_key="wa_57320", audit_id="AUDIT-2X",
                      port=_SellingPort(), quota_recheck=_Recheck2())
        for _ in range(2)
    ])

    assert sorted(o.outcome for o in outcomes) == [OUTCOME_ALREADY_RESOLVED, OUTCOME_RESOLVED]
    (rec,) = json.loads(path.read_text(encoding="utf-8"))["failed_order_registrations"]
    assert (rec["status"], rec["resolved_order_id"]) == (STATUS_RESOLVED, "draft_q")



@pytest.mark.asyncio
async def test_retrying_an_order_abandoned_for_quota_says_why(tmp_path):
    """C5: "Reintentar" sobre un pedido abandonado porque se acabaron las
    unidades dice ESO, no "max_attempts alcanzado"."""
    rec = _quota_record("AUDIT-QA")
    rec.update(status=STATUS_ABANDONED, abandon_reason="quota_changed")
    _write_metadata(tmp_path, "wa_57321", {"failed_order_registrations": [rec]})

    outcome = await reconcile_one(vault_dir=tmp_path, session_key="wa_57321", audit_id="AUDIT-QA",
                                  port=KwargsPort(), quota_recheck=_Recheck(left={}))

    assert outcome.outcome == OUTCOME_ABANDONED
    assert "quota_changed" in (outcome.error_detail or "")
