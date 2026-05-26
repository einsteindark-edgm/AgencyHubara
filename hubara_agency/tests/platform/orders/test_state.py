"""Tests del módulo `platform.orders.state` — helpers puros de state machine.

Cobertura:
  * `read_stage`: defaults sanos, validación contra closed-list.
  * `validate_transition`: DAG, terminales, `force`, no-ops.
  * `transition_stage`: history append, idempotencia, raises en transición inválida.
  * `build_initial_stage_patch`: shape correcto para create flow.
  * `build_schedule_patch`: new→preparing + edit (reagendado) + stages avanzados.
  * `build_confirm_payment_patch`: idempotencia + history entry.
  * `build_cancel_patch`: force desde cualquier no-terminal, idempotente si ya cancelado.

Puros — no requieren fixtures, ni async, ni respx.
"""
from __future__ import annotations

import pytest

from src.platform.orders.state import (
    InvalidStageTransitionError,
    META_KEY_CANCELLED_REASON,
    META_KEY_HISTORY,
    META_KEY_HUMAN_NOTE,
    META_KEY_PAYMENT_CONFIRMED,
    META_KEY_PAYMENT_CONFIRMED_AT_MS,
    META_KEY_PAYMENT_CONFIRMED_BY,
    META_KEY_SCHEDULED_DELIVERY_ISO,
    META_KEY_SCHEDULED_DELIVERY_TIME,
    META_KEY_STAGE,
    STAGE_VALUES,
    build_cancel_patch,
    build_confirm_payment_patch,
    build_initial_stage_patch,
    build_schedule_patch,
    read_stage,
    transition_stage,
    validate_transition,
)


# ----------------------------------------------------------------------
# read_stage
# ----------------------------------------------------------------------


def test_read_stage_returns_new_for_none_metadata():
    assert read_stage(None) == "new"


def test_read_stage_returns_new_for_empty_dict():
    assert read_stage({}) == "new"


def test_read_stage_returns_new_when_key_missing():
    assert read_stage({"unrelated": "field"}) == "new"


def test_read_stage_returns_valid_stage():
    assert read_stage({META_KEY_STAGE: "preparing"}) == "preparing"


def test_read_stage_falls_back_for_invalid_value():
    """Defensive: si externo escribió un valor no-stage, devolvemos new."""
    assert read_stage({META_KEY_STAGE: "garbage"}) == "new"


# ----------------------------------------------------------------------
# validate_transition
# ----------------------------------------------------------------------


def test_validate_transition_allows_new_to_preparing():
    validate_transition("new", "preparing")  # no raise


def test_validate_transition_idempotent_same_stage():
    """from == to es no-op (no raisea)."""
    validate_transition("preparing", "preparing")


def test_validate_transition_rejects_skip():
    """No se puede saltar de new directo a delivered."""
    with pytest.raises(InvalidStageTransitionError) as exc:
        validate_transition("new", "delivered")
    assert exc.value.from_stage == "new"
    assert exc.value.to_stage == "delivered"


def test_validate_transition_rejects_from_terminal():
    """delivered es terminal."""
    with pytest.raises(InvalidStageTransitionError) as exc:
        validate_transition("delivered", "preparing")
    assert "terminal" in str(exc.value)


def test_validate_transition_allows_cancel_from_any_non_terminal():
    for stage in ("new", "preparing", "ready", "shipping"):
        validate_transition(stage, "cancelled")  # no raise


def test_validate_transition_rejects_from_cancelled():
    with pytest.raises(InvalidStageTransitionError):
        validate_transition("cancelled", "preparing")


def test_validate_transition_force_bypasses_check():
    """force=True permite cualquier transición (correctiva)."""
    validate_transition("delivered", "preparing", force=True)
    validate_transition("new", "delivered", force=True)


def test_validate_transition_rejects_unknown_stage():
    with pytest.raises(InvalidStageTransitionError):
        validate_transition("new", "garbage")  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# transition_stage
# ----------------------------------------------------------------------


def test_transition_stage_appends_to_history():
    # Post-2026-05-26: new→preparing requiere scheduled_delivery_iso. El test
    # incluye el campo en metadata para validar la transición canónica.
    meta = {
        META_KEY_STAGE: "new",
        META_KEY_HISTORY: [{"from": None, "to": "new", "at_ms": 100, "by": "agent"}],
        "hubara_scheduled_delivery_iso": "2026-05-27",
    }
    patch = transition_stage(
        meta, to_stage="preparing", by="human", note="agendado", now_ms=200
    )
    assert patch[META_KEY_STAGE] == "preparing"
    history = patch[META_KEY_HISTORY]
    assert len(history) == 2
    assert history[1] == {
        "from": "new",
        "to": "preparing",
        "at_ms": 200,
        "by": "human",
        "note": "agendado",
    }


def test_transition_stage_idempotent_no_op():
    """from == to devuelve patch vacío {}, no escribe history."""
    meta = {META_KEY_STAGE: "preparing"}
    patch = transition_stage(meta, to_stage="preparing", by="human", now_ms=200)
    assert patch == {}


def test_transition_stage_does_not_mutate_input():
    meta = {
        META_KEY_STAGE: "new",
        META_KEY_HISTORY: [{"from": None, "to": "new", "at_ms": 100, "by": "agent"}],
        "hubara_scheduled_delivery_iso": "2026-05-27",
    }
    meta_snapshot = {**meta, META_KEY_HISTORY: list(meta[META_KEY_HISTORY])}
    transition_stage(meta, to_stage="preparing", by="human", now_ms=200)
    assert meta == meta_snapshot


def test_transition_new_to_preparing_requires_scheduled_date():
    """Bug fix 2026-05-26: drag-and-drop new→preparing exige fecha de entrega
    agendada. Sin scheduled_delivery_iso, levanta InvalidStageTransitionError.
    """
    meta = {META_KEY_STAGE: "new"}
    with pytest.raises(InvalidStageTransitionError) as exc:
        transition_stage(meta, to_stage="preparing", by="human", now_ms=200)
    assert "agendar la fecha" in str(exc.value)


def test_transition_new_to_preparing_force_bypasses_schedule_check():
    """`force=True` bypassa el check de scheduled_delivery_iso (operación
    correctiva del operador, ej. reprocessing manual)."""
    meta = {META_KEY_STAGE: "new"}
    patch = transition_stage(
        meta, to_stage="preparing", by="human", force=True, now_ms=200
    )
    assert patch[META_KEY_STAGE] == "preparing"


def test_transition_stage_raises_on_invalid_transition():
    meta = {META_KEY_STAGE: "new"}
    with pytest.raises(InvalidStageTransitionError):
        transition_stage(meta, to_stage="delivered", by="human")


def test_transition_stage_force_bypass():
    meta = {META_KEY_STAGE: "delivered"}
    patch = transition_stage(
        meta, to_stage="preparing", by="human", force=True, now_ms=300
    )
    assert patch[META_KEY_STAGE] == "preparing"
    assert patch[META_KEY_HISTORY][-1]["forced"] is True


# ----------------------------------------------------------------------
# build_initial_stage_patch
# ----------------------------------------------------------------------


def test_build_initial_stage_patch_creates_history_with_null_from():
    patch = build_initial_stage_patch(by="agent", now_ms=1000)
    assert patch[META_KEY_STAGE] == "new"
    assert len(patch[META_KEY_HISTORY]) == 1
    entry = patch[META_KEY_HISTORY][0]
    assert entry["from"] is None
    assert entry["to"] == "new"
    assert entry["by"] == "agent"
    assert entry["at_ms"] == 1000


# ----------------------------------------------------------------------
# build_schedule_patch
# ----------------------------------------------------------------------


def test_build_schedule_patch_transitions_new_to_preparing():
    meta = {META_KEY_STAGE: "new"}
    patch = build_schedule_patch(
        meta,
        delivery_iso="2026-05-26",
        delivery_time="09:00",
        note="cliente prefiere mañana",
        now_ms=500,
    )
    assert patch[META_KEY_SCHEDULED_DELIVERY_ISO] == "2026-05-26"
    assert patch[META_KEY_SCHEDULED_DELIVERY_TIME] == "09:00"
    assert patch[META_KEY_HUMAN_NOTE] == "cliente prefiere mañana"
    assert patch[META_KEY_STAGE] == "preparing"


def test_build_schedule_patch_reschedule_keeps_stage():
    """Si ya está en preparing y reagendan, NO transition (idempotente para stage)
    pero actualiza scheduled_iso + entry de history `schedule_updated`."""
    meta = {
        META_KEY_STAGE: "preparing",
        META_KEY_HISTORY: [
            {"from": None, "to": "new", "at_ms": 100, "by": "agent"},
            {"from": "new", "to": "preparing", "at_ms": 200, "by": "human"},
        ],
        META_KEY_SCHEDULED_DELIVERY_ISO: "2026-05-26",
    }
    patch = build_schedule_patch(
        meta, delivery_iso="2026-05-27", note="reagendado", now_ms=300
    )
    assert patch[META_KEY_SCHEDULED_DELIVERY_ISO] == "2026-05-27"
    # NO stage change
    assert META_KEY_STAGE not in patch
    # History tiene la entry de update
    history = patch[META_KEY_HISTORY]
    assert history[-1]["event"] == "schedule_updated"
    assert history[-1]["from"] == "preparing"
    assert history[-1]["to"] == "preparing"


# ----------------------------------------------------------------------
# build_confirm_payment_patch
# ----------------------------------------------------------------------


def test_build_confirm_payment_patch_sets_flag_and_logs_history():
    meta = {META_KEY_STAGE: "preparing"}
    patch = build_confirm_payment_patch(meta, by="human", now_ms=400)
    assert patch[META_KEY_PAYMENT_CONFIRMED] is True
    assert patch[META_KEY_PAYMENT_CONFIRMED_AT_MS] == 400
    assert patch[META_KEY_PAYMENT_CONFIRMED_BY] == "human"
    # History entry has event=payment_confirmed
    history = patch[META_KEY_HISTORY]
    assert history[-1]["event"] == "payment_confirmed"
    # NO stage change in history
    assert history[-1]["from"] == history[-1]["to"] == "preparing"


def test_build_confirm_payment_patch_idempotent():
    meta = {META_KEY_PAYMENT_CONFIRMED: True}
    patch = build_confirm_payment_patch(meta, by="human", now_ms=400)
    assert patch == {}


# ----------------------------------------------------------------------
# build_cancel_patch
# ----------------------------------------------------------------------


def test_build_cancel_patch_from_preparing():
    meta = {META_KEY_STAGE: "preparing"}
    patch = build_cancel_patch(
        meta, reason="Cliente arrepentido", by="human", now_ms=500
    )
    assert patch[META_KEY_STAGE] == "cancelled"
    assert patch[META_KEY_CANCELLED_REASON] == "Cliente arrepentido"
    assert patch[META_KEY_HISTORY][-1]["forced"] is True  # default force=True


def test_build_cancel_patch_from_shipping_with_force():
    """Cancelar desde shipping (paquete devuelto) — usa force=True."""
    meta = {META_KEY_STAGE: "shipping"}
    patch = build_cancel_patch(meta, reason="Devuelto en logística", now_ms=600)
    assert patch[META_KEY_STAGE] == "cancelled"


def test_build_cancel_patch_idempotent():
    meta = {META_KEY_STAGE: "cancelled"}
    patch = build_cancel_patch(meta, reason="X", now_ms=700)
    assert patch == {}


# ----------------------------------------------------------------------
# STAGE_VALUES integrity
# ----------------------------------------------------------------------


def test_stage_values_match_ui_status_enum():
    """Match exacto con `OrderUiStatus` del query_port (el frontend espera
    los mismos strings)."""
    from src.platform.orders.query_port import OrderUiStatus
    from typing import get_args

    assert set(STAGE_VALUES) == set(get_args(OrderUiStatus))
