"""D2.3 — ``RolloutControl``: allowlist, audiencia y ``rollout.enabled`` de
Meta Business Agent desde la tab, con la política pura y el estado del sync
(D2.2) como evidencia. Sobre el ``FakeMbaAdmin`` de D2.1.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.plugins.mba.adapters.meta_admin import FakeMbaAdmin, MbaAdminError
from src.plugins.mba.adapters.sync_state import SyncStateStore
from src.plugins.mba.domain.config import AgentFiles, build_agent_config
from src.plugins.mba.use_cases.rollout_control import RolloutControl
from tests.plugins.mba.test_sync_diff import _SKILLS, _YAML

ENTITY = "PHONE_777"
HUBARA_LIST = {"573001234567", "573009876543"}


def _cfg(yaml_text: str = _YAML):
    return build_agent_config(AgentFiles(agent_yaml=yaml_text, skills=_SKILLS))


def _ready_fake() -> FakeMbaAdmin:
    fake = FakeMbaAdmin()
    fake.settings[ENTITY] = {"agent_id": "ag_1", "channel": "whatsapp", "rollout": {"enabled": False}, "ai_audience": "ALLOWLISTED_ONLY"}
    fake.connectors[ENTITY] = [{"id": "c-1", "name": "hubara-commerce", "connection_status": {"status": "ACTIVE"}}]
    fake.allowlist[ENTITY] = [{"id": "e-1", "consumer_phone_number": "+573001234567"}]
    return fake


def _control(tmp_path: Path, *, fake: FakeMbaAdmin | None = None, enabled: bool = True, everyone: bool = False, synced: bool = True):
    fake = fake or _ready_fake()
    store = SyncStateStore(tmp_path)
    if synced:
        store.write("sales", {"last_apply": {"status": "ok", "at_ms": 1}})
    uc = RolloutControl(
        admin=fake,
        state_store=store,
        load_config=lambda agent_id: _cfg() if agent_id == "sales" else None,
        is_enabled=lambda: enabled,
        hubara_allowed=lambda phone: phone.lstrip("+") in HUBARA_LIST,
        everyone_allowed=lambda: everyone,
        now_ms=lambda: 1_700_000_000_000,
    )
    return uc, fake, store


async def test_status_reads_meta_and_reports_readiness(tmp_path: Path) -> None:
    uc, fake, _ = _control(tmp_path)
    st = await uc.status("sales")
    assert st.rollout_enabled is False and st.ai_audience == "ALLOWLISTED_ONLY"
    assert st.allowlist == [{"id": "e-1", "phone": "+573001234567", "in_hubara": True}]
    assert st.can_enable is True and all(c["ok"] for c in st.checks)
    assert st.everyone_allowed is False
    assert not [c for c in fake.calls if c[0].startswith(("create", "put", "add", "remove", "delete"))]
    assert await uc.status("nope") is None


async def test_status_without_sync_or_with_a_foreign_phone_is_not_ready(tmp_path: Path) -> None:
    fake = _ready_fake()
    fake.allowlist[ENTITY].append({"id": "e-2", "consumer_phone_number": "+573000000000"})
    uc, _, _ = _control(tmp_path, fake=fake, synced=False)
    st = await uc.status("sales")
    assert st.can_enable is False
    failed = {c["code"] for c in st.checks if not c["ok"]}
    assert failed == {"sync_ok", "allowlist_within_hubara"}
    assert st.allowlist[1] == {"id": "e-2", "phone": "+573000000000", "in_hubara": False}


async def test_status_surfaces_a_remote_outage_instead_of_pretending(tmp_path: Path) -> None:
    uc, fake, _ = _control(tmp_path)
    fake.fail_with = MbaAdminError("unavailable", status=503, detail="down", attempts=3)
    with pytest.raises(MbaAdminError):
        await uc.status("sales")


async def test_adding_a_phone_goes_through_the_policy_and_is_recorded(tmp_path: Path) -> None:
    uc, fake, store = _control(tmp_path)
    out = await uc.add_phone("sales", "+573009876543")
    assert out.applied is True and out.reason == "applied"
    assert [e["consumer_phone_number"] for e in fake.allowlist[ENTITY]] == ["+573001234567", "+573009876543"]
    history = store.read("sales")["rollout_history"]
    assert history[-1] == {"at_ms": 1_700_000_000_000, "action": "allowlist_add", "value": "+573009876543", "ok": True}

    for phone, reason in (("+573000000000", "customer_not_in_hubara_allowlist"), ("573009876543", "invalid_phone"), ("+573009876543", "already_listed")):
        out = await uc.add_phone("sales", phone)
        assert out.applied is False and out.reason == reason, phone
    assert len(fake.allowlist[ENTITY]) == 2

    uc_off, fake_off, _ = _control(tmp_path, enabled=False)
    assert (await uc_off.add_phone("sales", "+573009876543")).reason == "mba_disabled"
    assert len(fake_off.allowlist[ENTITY]) == 1


async def test_removing_a_phone_is_always_allowed_even_with_the_flag_off(tmp_path: Path) -> None:
    uc, fake, store = _control(tmp_path, enabled=False)
    out = await uc.remove_phone("sales", "e-1")
    assert out.applied is True and fake.allowlist[ENTITY] == []
    assert store.read("sales")["rollout_history"][-1]["action"] == "allowlist_remove"
    out = await uc.remove_phone("sales", "e-404")
    assert out.applied is False and out.reason == "rejected" and out.error["status"] == 404


async def test_audience_everyone_is_refused_by_policy_and_allowlisted_only_always_works(tmp_path: Path) -> None:
    uc, fake, _ = _control(tmp_path)
    out = await uc.set_audience("sales", "EVERYONE", confirm=True)
    assert out.applied is False and out.reason == "everyone_not_allowed"
    assert fake.settings[ENTITY]["ai_audience"] == "ALLOWLISTED_ONLY"

    uc, fake, store = _control(tmp_path, everyone=True)
    assert (await uc.set_audience("sales", "EVERYONE", confirm=False)).reason == "confirmation_required"
    out = await uc.set_audience("sales", "EVERYONE", confirm=True)
    assert out.applied is True and fake.settings[ENTITY]["ai_audience"] == "EVERYONE"
    assert fake.settings[ENTITY]["rollout"] == {"enabled": False}  # PUT parcial: solo la audiencia
    assert fake.calls[-1][0] == "put_settings" and fake.calls[-1][2] == {"ai_audience": "EVERYONE"} and fake.calls[-1][3] == "ag_1"
    out = await uc.set_audience("sales", "ALLOWLISTED_ONLY", confirm=False)
    assert out.applied is True and fake.settings[ENTITY]["ai_audience"] == "ALLOWLISTED_ONLY"
    assert [h["value"] for h in store.read("sales")["rollout_history"]] == ["EVERYONE", "ALLOWLISTED_ONLY"]


async def test_enabling_requires_every_check_and_a_confirmation_but_disabling_never_does(tmp_path: Path) -> None:
    uc, fake, store = _control(tmp_path, synced=False)
    out = await uc.set_enabled("sales", True, confirm=True)
    assert out.applied is False and out.reason == "not_ready" and "sync_ok" in out.blocked
    assert fake.settings[ENTITY]["rollout"] == {"enabled": False}

    uc, fake, store = _control(tmp_path)
    assert (await uc.set_enabled("sales", True, confirm=False)).reason == "confirmation_required"
    out = await uc.set_enabled("sales", True, confirm=True)
    assert out.applied is True and fake.settings[ENTITY]["rollout"] == {"enabled": True}
    assert fake.calls[-1][2] == {"rollout": {"enabled": True}}
    assert store.read("sales")["rollout_history"][-1] == {"at_ms": 1_700_000_000_000, "action": "rollout_enabled", "value": "true", "ok": True}

    # kill switch: flag apagada, sin sync, sin confirmación → igual apaga
    uc_off, _, _ = _control(tmp_path, fake=fake, enabled=False, synced=False)
    out = await uc_off.set_enabled("sales", False, confirm=False)
    assert out.applied is True and fake.settings[ENTITY]["rollout"] == {"enabled": False}


async def test_a_meta_rejection_is_reported_and_recorded_as_failed(tmp_path: Path) -> None:
    class _Rejects(FakeMbaAdmin):
        async def put_settings(self, entity_id, body, *, agent_id=None):
            raise MbaAdminError("rejected", status=400, detail="billing required")

    fake = _Rejects()
    fake.settings[ENTITY] = _ready_fake().settings[ENTITY]
    fake.connectors[ENTITY] = _ready_fake().connectors[ENTITY]
    fake.allowlist[ENTITY] = _ready_fake().allowlist[ENTITY]
    uc, _, store = _control(tmp_path, fake=fake)
    out = await uc.set_enabled("sales", True, confirm=True)
    assert out.applied is False and out.reason == "rejected" and out.error == {"kind": "rejected", "detail": "billing required", "status": 400}
    assert store.read("sales")["rollout_history"][-1]["ok"] is False
    assert "billing required" in json.dumps(store.read("sales"))


async def test_guards_without_entity_or_agent(tmp_path: Path) -> None:
    uc, _, _ = _control(tmp_path)
    assert (await uc.set_enabled("nope", True, confirm=True)).reason == "agent_unknown"
    no_entity = build_agent_config(AgentFiles(agent_yaml=_YAML.replace('entity_id: "PHONE_777"', "entity_id: null"), skills=_SKILLS))
    uc2 = RolloutControl(
        admin=_ready_fake(), state_store=SyncStateStore(tmp_path), load_config=lambda _: no_entity,
        is_enabled=lambda: True, hubara_allowed=lambda _: True, everyone_allowed=lambda: False,
    )
    assert (await uc2.set_enabled("sales", True, confirm=True)).reason == "entity_id_missing"
    assert await uc2.status("sales") is not None and (await uc2.status("sales")).entity_id is None
