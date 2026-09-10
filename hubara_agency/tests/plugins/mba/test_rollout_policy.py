"""D2.3 — política PURA de rollout de Meta Business Agent (allowlist,
audiencia, ``rollout.enabled``). Estamos en producción: MBA solo puede
responderle a una LISTA CERRADA, y encenderlo exige que todo lo anterior esté
en su lugar. Apagarlo es el kill switch y nunca se bloquea.
"""
from __future__ import annotations

from src.plugins.mba.domain.rollout_policy import (
    E164_RE,
    Check,
    RolloutFacts,
    can_add_phone,
    can_enable,
    can_set_audience,
    readiness,
)

HUBARA_LIST = {"573001234567", "573009876543"}


def _facts(**over) -> RolloutFacts:
    base = dict(
        flag_enabled=True,
        rollout_enabled=False,
        ai_audience="ALLOWLISTED_ONLY",
        allowlist=(("e1", "+573001234567"),),
        hubara_allowed=lambda phone: phone.lstrip("+") in HUBARA_LIST,
        last_sync_ok=True,
        connector_status="ACTIVE",
        everyone_knob=False,
    )
    base.update(over)
    return RolloutFacts(**base)


def test_readiness_lists_every_guard_with_its_verdict() -> None:
    checks = readiness(_facts())
    assert [c.code for c in checks] == [
        "flag_enabled", "sync_ok", "connector_active", "audience_allowlisted_only", "allowlist_nonempty", "allowlist_within_hubara",
    ]
    assert all(c.ok for c in checks)
    assert can_enable(_facts()) == ()


def test_enabling_is_blocked_by_any_failed_guard() -> None:
    assert can_enable(_facts(flag_enabled=False)) == ("flag_enabled",)
    assert can_enable(_facts(last_sync_ok=False)) == ("sync_ok",)
    assert can_enable(_facts(connector_status="ERROR")) == ("connector_active",)
    assert can_enable(_facts(connector_status=None)) == ("connector_active",)
    assert can_enable(_facts(ai_audience="EVERYONE")) == ("audience_allowlisted_only",)
    assert can_enable(_facts(allowlist=())) == ("allowlist_nonempty",)
    # un teléfono en Meta que Hubara NO acepta: MBA le respondería a alguien
    # cuyos eventos Hubara descarta (D1.4) → bloqueado
    assert can_enable(_facts(allowlist=(("e1", "+573001234567"), ("e2", "+573000000000")))) == ("allowlist_within_hubara",)
    bad = next(c for c in readiness(_facts(allowlist=(("e2", "+573000000000"),))) if c.code == "allowlist_within_hubara")
    assert isinstance(bad, Check) and not bad.ok and "+573000000000" in bad.detail


def test_only_hubara_allowed_e164_phones_can_join_metas_allowlist() -> None:
    assert E164_RE.fullmatch("+573009876543")
    assert can_add_phone("+573009876543", _facts()) is None
    assert can_add_phone("573009876543", _facts()) == "invalid_phone"  # E.164 con +
    assert can_add_phone("+57 300 987", _facts()) == "invalid_phone"
    assert can_add_phone("+573000000000", _facts()) == "customer_not_in_hubara_allowlist"
    assert can_add_phone("+573001234567", _facts()) == "already_listed"
    assert can_add_phone("+573009876543", _facts(flag_enabled=False)) == "mba_disabled"


def test_everyone_needs_the_policy_knob_and_an_explicit_confirmation() -> None:
    assert can_set_audience("EVERYONE", confirm=True, facts=_facts()) == "everyone_not_allowed"
    assert can_set_audience("EVERYONE", confirm=False, facts=_facts(everyone_knob=True)) == "confirmation_required"
    assert can_set_audience("EVERYONE", confirm=True, facts=_facts(everyone_knob=True)) is None
    assert can_set_audience("ALLOWLISTED_ONLY", confirm=False, facts=_facts()) is None
    assert can_set_audience("ALLOWLISTED_ONLY", confirm=False, facts=_facts(flag_enabled=False)) is None  # volver a cerrado siempre
    assert can_set_audience("FRIENDS", confirm=True, facts=_facts(everyone_knob=True)) == "invalid_audience"
