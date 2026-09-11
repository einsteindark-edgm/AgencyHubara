"""D1.9 — `EmitAgentEvent`: contarle a Meta Business Agent una novedad del
pedido para que se la transmita al cliente SIN que Hubara tome el hilo.

Vault real (`metadata.json`): lee `phone_number_id` (= entity_id de MBA) y
el dueño del hilo, escribe `agent_events[]` (auditoría + dedupe). Guardas
fail-closed iguales a D1.6 + "MBA controla el hilo" (si responde Hubara o un
humano, el aviso lo manda Hubara como siempre).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.plugins.mba.adapters.agent_event import AgentEventError, FakeAgentEvent
from src.plugins.mba.domain.agent_events import (
    AGENT_EVENT_TYPES,
    agent_event_type_for_stage,
    build_description,
)
from src.plugins.mba.use_cases.emit_agent_event import (
    AGENT_EVENTS_CAP,
    PENDING_TTL_MS,
    AgentEventOutcome,
    EmitAgentEvent,
)
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
    FilesystemMetadataStore(vault).write(
        SESSION,
        {
            "tag": "COMPRA_EXITOSA",
            "active_route": "humano",
            "phone_number_id": "PHONE_777",
            "control_owner": "mba",
            "control_owner_updated_at_ms": NOW_MS - 60_000,
            **extra,
        },
    )


def _meta(vault: Path) -> dict[str, Any]:
    return json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))


def _uc(
    vault: Path,
    port: FakeAgentEvent | None = None,
    *,
    enabled: bool = True,
    allowed: bool = True,
    controls: bool = True,
    fallback_entity: str = "",
    now_ms: int = NOW_MS,
) -> tuple[EmitAgentEvent, FakeAgentEvent]:
    port = port or FakeAgentEvent()
    uc = EmitAgentEvent(
        metadata_store=FilesystemMetadataStore(vault),
        port=port,
        is_customer_allowed=lambda c: allowed,
        is_enabled=lambda: enabled,
        controls_thread=lambda metadata, session_id: controls,
        entity_id_fallback=lambda: fallback_entity,
        now_ms=lambda: now_ms,
    )
    return uc, port


# ── dominio puro ──────────────────────────────────────────────────────────────


def test_event_types_are_a_closed_catalogue_mapped_from_the_order_stages() -> None:
    assert set(AGENT_EVENT_TYPES) >= {
        "payment_received",
        "order_preparing",
        "order_ready",
        "order_shipped",
        "order_delivered",
        "order_cancelled",
        "episode_closed",
    }
    assert (
        agent_event_type_for_stage("preparing", payment_confirmed=True)
        == "payment_received"
    )
    assert (
        agent_event_type_for_stage("preparing", payment_confirmed=False)
        == "order_preparing"
    )
    assert agent_event_type_for_stage("ready", payment_confirmed=True) == "order_ready"
    assert (
        agent_event_type_for_stage("shipping", payment_confirmed=False)
        == "order_shipped"
    )
    assert (
        agent_event_type_for_stage("delivered", payment_confirmed=True)
        == "order_delivered"
    )
    assert (
        agent_event_type_for_stage("cancelled", payment_confirmed=False)
        == "order_cancelled"
    )
    assert agent_event_type_for_stage("new", payment_confirmed=False) is None


def test_the_description_tells_mba_what_to_relay_and_forbids_inventing() -> None:
    d = build_description("order_shipped", "Tu pedido #12 ya va en camino 🚚.")
    assert "Tu pedido #12 ya va en camino 🚚." in d
    assert "no inventes" in d.lower() and len(d) <= 2000
    assert len(build_description("order_shipped", "x" * 5000)) <= 2000


# ── use case ──────────────────────────────────────────────────────────────────


async def test_with_mba_in_front_the_event_goes_to_meta_and_is_recorded(
    vault: Path,
) -> None:
    _seed(vault)
    uc, port = _uc(vault)
    out = await uc.execute(
        SESSION,
        "order_shipped",
        order_id="order_1",
        message="Tu pedido #12 ya va en camino 🚚.",
        payload={"stage": "shipping"},
        source="eta",
    )
    assert out == AgentEventOutcome(
        session_key=SESSION,
        emitted=True,
        reason="accepted",
        agent_event_id="fake-1",
        at_ms=NOW_MS,
    )
    assert len(port.calls) == 1
    entity, to, event_type, description, payload = port.calls[0]
    assert (entity, to, event_type) == ("PHONE_777", f"+{CUSTOMER}", "order_shipped")
    assert description == build_description(
        "order_shipped", "Tu pedido #12 ya va en camino 🚚."
    )
    assert payload == {"stage": "shipping", "order_id": "order_1"}
    m = _meta(vault)
    assert (
        m["control_owner"] == "mba" and m["tag"] == "COMPRA_EXITOSA"
    )  # nada más se toca
    assert m["agent_events"] == [
        {
            "type": "order_shipped",
            "order_id": "order_1",
            "at_ms": NOW_MS,
            "status": "accepted",
            "agent_event_id": "fake-1",
            "error": None,
            "source": "eta",
        }
    ]


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"enabled": False}, "mba_disabled"),
        ({"allowed": False}, "customer_not_enabled"),
        ({"controls": False}, "hubara_controls"),
    ],
)
async def test_the_guards_fail_closed_and_nothing_is_called_nor_written(
    vault: Path, kw: dict, reason: str
) -> None:
    _seed(vault)
    uc, port = _uc(vault, **kw)
    out = await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    assert (out.emitted, out.reason) == (False, reason)
    assert port.calls == [] and "agent_events" not in _meta(vault)


async def test_an_unknown_session_or_key_or_type_is_refused(vault: Path) -> None:
    uc, port = _uc(vault)
    assert (
        await uc.execute(SESSION, "order_shipped", message="m")
    ).reason == "session_unknown"
    assert (
        await uc.execute("573001234567", "order_shipped", message="m")
    ).reason == "session_unknown"
    _seed(vault)
    assert (
        await uc.execute(SESSION, "whatever", message="m")
    ).reason == "unknown_event_type"
    assert port.calls == []


async def test_the_same_event_for_the_same_order_is_emitted_once(vault: Path) -> None:
    _seed(vault)
    uc, port = _uc(vault)
    assert (
        await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    ).emitted is True
    again = await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    assert (again.emitted, again.reason, again.agent_event_id) == (
        False,
        "already_emitted",
        "fake-1",
    )
    # otro pedido, mismo tipo → evento nuevo; otro tipo, mismo pedido → evento nuevo
    assert (
        await uc.execute(SESSION, "order_shipped", order_id="order_2", message="m")
    ).emitted is True
    assert (
        await uc.execute(SESSION, "order_delivered", order_id="order_1", message="m")
    ).emitted is True
    # sin order_id no hay clave de dedupe (p.ej. episode_closed se repite por episodio)
    assert (await uc.execute(SESSION, "episode_closed", message="m")).emitted is True
    assert (await uc.execute(SESSION, "episode_closed", message="m")).emitted is True
    assert len(port.calls) == 5


async def test_a_meta_rejection_is_recorded_and_does_not_block_a_retry(
    vault: Path,
) -> None:
    _seed(vault)
    uc, port = _uc(
        vault,
        FakeAgentEvent(
            fail_with=AgentEventError("rejected", status=400, detail="Unknown entity")
        ),
    )
    out = await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    assert out == AgentEventOutcome(
        session_key=SESSION,
        emitted=False,
        reason="rejected",
        agent_event_id=None,
        at_ms=NOW_MS,
        error="400 Unknown entity",
    )
    assert _meta(vault)["agent_events"][-1] == {
        "type": "order_shipped",
        "order_id": "order_1",
        "at_ms": NOW_MS,
        "status": "rejected",
        "agent_event_id": None,
        "error": "400 Unknown entity",
        "source": None,
    }
    port.fail_with = None
    assert (
        await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    ).emitted is True


async def test_an_ambiguous_timeout_counts_as_possibly_delivered_so_it_is_not_repeated(
    vault: Path,
) -> None:
    _seed(vault)
    uc, port = _uc(
        vault,
        FakeAgentEvent(
            fail_with=AgentEventError("ambiguous", detail="ReadTimeout: slow")
        ),
    )
    out = await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    assert (out.emitted, out.reason, out.error) == (
        False,
        "ambiguous",
        "ReadTimeout: slow",
    )
    port.fail_with = None
    again = await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    assert (again.emitted, again.reason) == (False, "already_emitted")


async def test_the_entity_id_falls_back_to_the_configured_phone_number_id(
    vault: Path,
) -> None:
    _seed(vault, phone_number_id=None)
    uc, port = _uc(vault, fallback_entity="PHONE_FALLBACK")
    assert (
        await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    ).emitted is True
    assert port.calls[0][0] == "PHONE_FALLBACK"
    uc, port = _uc(vault)
    assert (
        await uc.execute(SESSION, "order_delivered", order_id="order_1", message="m")
    ).reason == "entity_id_missing"
    assert port.calls == []


async def test_the_audit_trail_is_capped(vault: Path) -> None:
    _seed(
        vault,
        agent_events=[
            {
                "type": "order_ready",
                "order_id": f"o{i}",
                "at_ms": i,
                "status": "accepted",
                "agent_event_id": None,
                "error": None,
                "source": None,
            }
            for i in range(AGENT_EVENTS_CAP)
        ],
    )
    uc, _ = _uc(vault)
    assert (
        await uc.execute(SESSION, "order_shipped", order_id="order_new", message="m")
    ).emitted is True
    events = _meta(vault)["agent_events"]
    assert (
        len(events) == AGENT_EVENTS_CAP
        and events[-1]["order_id"] == "order_new"
        and events[0]["order_id"] == "o1"
    )


async def test_if_meta_accepted_but_the_vault_could_not_record_it_the_outcome_says_so(
    vault: Path, monkeypatch
) -> None:
    _seed(vault)
    uc, port = _uc(vault)
    real_update = uc._store.update
    calls = {"n": 0}

    def _second_fails(session_id: str, mutator: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:  # la reserva pasa, el cierre de la reserva falla
            raise OSError("disk full")
        return real_update(session_id, mutator)

    monkeypatch.setattr(uc._store, "update", _second_fails)
    out = await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    assert (out.emitted, out.recorded, out.agent_event_id) == (True, False, "fake-1")
    assert len(port.calls) == 1
    assert (
        _meta(vault)["agent_events"][-1]["status"] == "pending"
    )  # vence sola a los PENDING_TTL_MS


async def test_without_a_reservation_in_the_vault_nothing_is_sent(
    vault: Path, monkeypatch
) -> None:
    """Sin reserva no hay dedupe ni auditoría: un retry duplicaría el aviso."""
    _seed(vault)
    uc, port = _uc(vault)

    def _boom(session_id: str, mutator: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(uc._store, "update", _boom)
    out = await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    assert (out.emitted, out.reason, out.recorded) == (
        False,
        "vault_unavailable",
        False,
    )
    assert port.calls == []


async def test_the_check_and_the_reservation_are_one_locked_write_so_a_concurrent_twin_is_deduped(
    vault: Path,
) -> None:
    """La activity del ETA vencida y su retry corren en paralelo: la segunda
    emisión ve la reserva ``pending`` de la primera y no llama a Meta."""
    _seed(vault)
    uc1, port1 = _uc(vault)
    uc2, port2 = _uc(vault, now_ms=NOW_MS + 5_000)

    class _Slow(FakeAgentEvent):
        async def emit(self, **kw: Any):
            # mientras la 1ª espera a Meta, la 2ª intenta lo mismo
            twin = await uc2.execute(
                SESSION, "order_shipped", order_id="order_1", message="m"
            )
            assert (twin.emitted, twin.reason) == (False, "already_emitted")
            return await super().emit(**kw)

    slow = _Slow()
    uc1._port = slow
    out = await uc1.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    assert out.emitted is True and len(slow.calls) == 1 and port2.calls == []
    events = _meta(vault)["agent_events"]
    assert (
        len(events) == 1
        and events[0]["status"] == "accepted"
        and events[0]["agent_event_id"] == "fake-1"
    )


async def test_a_stale_pending_reservation_does_not_block_forever(vault: Path) -> None:
    _seed(
        vault,
        agent_events=[
            {
                "type": "order_shipped",
                "order_id": "order_1",
                "at_ms": NOW_MS - PENDING_TTL_MS - 1,
                "status": "pending",
                "agent_event_id": None,
                "error": None,
                "source": "eta",
            }
        ],
    )
    uc, port = _uc(vault)
    assert (
        await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    ).emitted is True
    assert len(port.calls) == 1
    _seed(
        vault,
        agent_events=[
            {
                "type": "order_shipped",
                "order_id": "order_1",
                "at_ms": NOW_MS - 1_000,
                "status": "pending",
                "agent_event_id": None,
                "error": None,
                "source": "eta",
            }
        ],
    )
    assert (
        await uc.execute(SESSION, "order_shipped", order_id="order_1", message="m")
    ).reason == "already_emitted"
