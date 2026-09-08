"""D1.6 — `ReleaseThread`: política + puerto + registro en la sesión.

Vault real (`metadata.json`): lee `control_owner` (D1.5) y `phone_number_id`,
escribe `thread_control` (último pedido / error) y un evento en
`control_history` con `source="thread_control"`; NUNCA toca `control_owner`
(eso lo confirma Meta por `messaging_handovers`).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.plugins.mba.adapters.thread_control import FakeThreadControl, ThreadControlError
from src.plugins.mba.domain.release_policy import ReleaseTrigger
from src.plugins.mba.use_cases.release_thread import ReleaseOutcome, ReleaseThread
from src.sdk.runtime import FilesystemMetadataStore

CUSTOMER = "573001234567"
SESSION = f"wa_{CUSTOMER}"
NOW_MS = 1_757_400_000_000


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


def _seed(vault: Path, **extra: Any) -> None:
    FilesystemMetadataStore(vault).write(SESSION, {
        "tag": "HUMANO", "active_route": "humano", "phone_number_id": "PHONE_777",
        "control_owner": "hubara", "control_owner_updated_at_ms": NOW_MS - 60_000,
        "control_history": [{"owner": "hubara", "kind": "control_taken", "at_ms": NOW_MS - 60_000}],
        **extra,
    })


def _meta(vault: Path) -> dict[str, Any]:
    return json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))


def _uc(vault: Path, port: FakeThreadControl | None = None, *, enabled: bool = True, allowed: bool = True,
        fallback_phone: str = "", now_ms: int = NOW_MS) -> tuple[ReleaseThread, FakeThreadControl]:
    port = port or FakeThreadControl()
    uc = ReleaseThread(
        metadata_store=FilesystemMetadataStore(vault),
        port=port,
        is_customer_allowed=lambda c: allowed,
        is_enabled=lambda: enabled,
        phone_number_id_fallback=lambda: fallback_phone,
        now_ms=lambda: now_ms,
    )
    return uc, port


async def test_handoff_resolved_releases_the_thread_and_records_it_without_touching_the_owner(vault: Path) -> None:
    _seed(vault)
    uc, port = _uc(vault)
    out = await uc.execute(SESSION, ReleaseTrigger.HANDOFF_RESOLVED, metadata="caso #12 cerrado")
    assert out == ReleaseOutcome(session_key=SESSION, released=True, reason="handoff_resolved", action_at_ms=NOW_MS)
    assert port.calls == [("release", "PHONE_777", CUSTOMER, "hubara:handoff_resolved caso #12 cerrado")]
    m = _meta(vault)
    assert m["control_owner"] == "hubara"  # lo cambia Meta por messaging_handovers, no nosotros
    assert m["thread_control"] == {
        "last_action": "release", "last_action_at_ms": NOW_MS, "trigger": "handoff_resolved",
        "metadata": "caso #12 cerrado", "last_ok": True, "last_error": None,
    }
    assert m["control_history"][-1] == {
        "owner": "hubara", "kind": "release_requested", "at_ms": NOW_MS, "trigger": "handoff_resolved",
        "metadata": "caso #12 cerrado", "source": "thread_control",
    }
    assert m["tag"] == "HUMANO" and m["active_route"] == "humano"


async def test_a_release_is_not_repeated_until_meta_confirms_the_new_owner(vault: Path) -> None:
    _seed(vault)
    uc, port = _uc(vault)
    assert (await uc.execute(SESSION, ReleaseTrigger.MANUAL)).released
    uc2, port2 = _uc(vault, now_ms=NOW_MS + 5_000)
    out = await uc2.execute(SESSION, ReleaseTrigger.MANUAL)
    assert out == ReleaseOutcome(session_key=SESSION, released=False, reason="release_pending")
    assert port2.calls == []
    # Meta confirmó (D1.5 escribió control_owner=mba con updated_at posterior) → ya no hay nada que soltar
    FilesystemMetadataStore(vault).update(SESSION, lambda d: d | {"control_owner": "mba", "control_owner_updated_at_ms": NOW_MS + 1_000})
    out = await _uc(vault, now_ms=NOW_MS + 9_000)[0].execute(SESSION, ReleaseTrigger.MANUAL)
    assert out.reason == "already_mba" and not out.released
    # y si Meta nos devolvió el hilo después (take nuevo), se puede volver a soltar
    FilesystemMetadataStore(vault).update(SESSION, lambda d: d | {"control_owner": "hubara", "control_owner_updated_at_ms": NOW_MS + 8_000})
    uc3, port3 = _uc(vault, now_ms=NOW_MS + 9_000)
    assert (await uc3.execute(SESSION, ReleaseTrigger.MANUAL)).released and len(port3.calls) == 1


async def test_policy_says_no_then_nothing_is_called_nor_written(vault: Path) -> None:
    _seed(vault)
    before = _meta(vault)
    uc, port = _uc(vault)
    out = await uc.execute(SESSION, ReleaseTrigger.REMARKETING_REPLY, order_registered=True)
    assert out == ReleaseOutcome(session_key=SESSION, released=False, reason="order_in_progress")
    assert port.calls == [] and _meta(vault) == before


async def test_guards_are_fail_closed(vault: Path) -> None:
    _seed(vault)
    uc, port = _uc(vault, enabled=False)
    assert (await uc.execute(SESSION, ReleaseTrigger.MANUAL)).reason == "mba_disabled"
    uc, port = _uc(vault, allowed=False)
    assert (await uc.execute(SESSION, ReleaseTrigger.MANUAL)).reason == "customer_not_enabled"
    uc, port = _uc(vault)
    assert (await uc.execute("wa_573009999999", ReleaseTrigger.MANUAL)).reason == "session_unknown"
    assert port.calls == [] and "thread_control" not in _meta(vault)


async def test_phone_number_id_comes_from_the_session_or_the_configured_fallback(vault: Path) -> None:
    _seed(vault, phone_number_id=None)
    uc, port = _uc(vault)
    assert (await uc.execute(SESSION, ReleaseTrigger.MANUAL)).reason == "phone_number_id_missing" and port.calls == []
    uc, port = _uc(vault, fallback_phone="PHONE_ENV")
    assert (await uc.execute(SESSION, ReleaseTrigger.MANUAL)).released
    assert port.calls[0][1] == "PHONE_ENV"


async def test_a_meta_error_is_recorded_and_reported_but_never_raised(vault: Path) -> None:
    _seed(vault)
    port = FakeThreadControl()
    port.fail_with = ThreadControlError("unavailable", status=503, detail="Service Unavailable", attempts=3)
    uc, _ = _uc(vault, port)
    out = await uc.execute(SESSION, ReleaseTrigger.HANDOFF_RESOLVED)
    assert out == ReleaseOutcome(session_key=SESSION, released=False, reason="unavailable", action_at_ms=NOW_MS,
                                 error="503 Service Unavailable")
    tc = _meta(vault)["thread_control"]
    assert tc["last_ok"] is False and tc["last_action"] == "release"
    assert tc["last_error"] == {"kind": "unavailable", "status": 503, "detail": "Service Unavailable", "at_ms": NOW_MS,
                               "trigger": "handoff_resolved"}
    assert _meta(vault)["control_history"][-1]["kind"] == "control_taken"  # sin evento: no se soltó
    # un fallo NO deja el release como pendiente: se vuelve a intentar
    port.fail_with = None
    uc2, port2 = _uc(vault, port, now_ms=NOW_MS + 1_000)
    assert (await uc2.execute(SESSION, ReleaseTrigger.HANDOFF_RESOLVED)).released
    assert _meta(vault)["thread_control"]["last_ok"] is True and _meta(vault)["thread_control"]["last_error"] is None


async def test_a_rejection_because_we_do_not_hold_the_thread_is_reported_as_rejected(vault: Path) -> None:
    _seed(vault)
    port = FakeThreadControl()
    port.fail_with = ThreadControlError("rejected", status=400, detail="(#100) You must hold thread control", attempts=1)
    uc, _ = _uc(vault, port)
    out = await uc.execute(SESSION, ReleaseTrigger.MANUAL)
    assert (out.released, out.reason, out.error) == (False, "rejected", "400 (#100) You must hold thread control")
