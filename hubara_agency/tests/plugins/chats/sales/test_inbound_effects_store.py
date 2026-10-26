"""El diario de efectos de la entrada durable (ADR 2026-10-15 §6): lo que un
reintento del ingest no puede repetir — la línea del historial, el mensaje a
la sesión, un envío directo al cliente — queda marcado por `wamid` en el vault
y sobrevive al proceso que lo marcó."""
from __future__ import annotations

from src.plugins.chats.agent.sales.inbound_effects_store import FilesystemInboundEffects

_DAY_MS = 86_400_000
_NOW = 1_789_992_000_000


def _effects(tmp_path, now_ms: int = _NOW) -> FilesystemInboundEffects:
    return FilesystemInboundEffects(tmp_path / "inbound_effects", now_ms=lambda: now_ms)


def test_a_send_is_claimed_once_per_message(tmp_path) -> None:
    effects = _effects(tmp_path)

    assert effects.claim("wamid.A", "send:abc") is True
    assert effects.claim("wamid.A", "send:abc") is False


def test_a_marked_effect_is_seen_by_a_new_process(tmp_path) -> None:
    _effects(tmp_path).mark("wamid.A", "turn")

    again = _effects(tmp_path)

    assert again.done("wamid.A", "turn") is True
    assert again.claim("wamid.A", "turn") is False


def test_effects_and_messages_are_independent(tmp_path) -> None:
    effects = _effects(tmp_path)
    effects.mark("wamid.A", "history")

    assert effects.done("wamid.A", "turn") is False
    assert effects.done("wamid.B", "history") is False


def test_a_retry_after_midnight_still_sees_the_mark(tmp_path) -> None:
    _effects(tmp_path, now_ms=_NOW).mark("wamid.A", "turn")

    assert _effects(tmp_path, now_ms=_NOW + _DAY_MS).done("wamid.A", "turn") is True


def test_a_message_without_wamid_is_never_journaled(tmp_path) -> None:
    effects = _effects(tmp_path)
    effects.mark("", "turn")

    assert effects.done("", "turn") is False
    assert effects.claim("", "send:abc") is True
    assert effects.claim("", "send:abc") is True


def test_days_past_the_temporal_retention_are_pruned_when_a_new_day_starts(tmp_path) -> None:
    _effects(tmp_path, now_ms=_NOW - 40 * _DAY_MS).mark("wamid.OLD", "turn")

    _effects(tmp_path, now_ms=_NOW).mark("wamid.NEW", "turn")

    days = sorted(p.name for p in (tmp_path / "inbound_effects").iterdir())
    assert len(days) == 1
    assert _effects(tmp_path).done("wamid.NEW", "turn") is True


def test_a_workflow_reset_days_later_still_sees_the_marks(tmp_path) -> None:
    """Premortem PM-007: el ADR ofrece resetear un workflow fallido desde la UI
    de Temporal (retención de 30 días). Un reset 10 días después no puede
    duplicar la línea del historial ni la respuesta del bot."""
    _effects(tmp_path, now_ms=_NOW - 10 * _DAY_MS).mark("wamid.A", "turn")

    assert _effects(tmp_path, now_ms=_NOW).done("wamid.A", "turn") is True
