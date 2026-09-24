"""Brazos del laboratorio (plan §3.1 y PR 15): el bot actual simulado (A1)
recibe la señal de hoy; los bots nuevos (B = Jev, C = OpenAI) reciben el modo
`on` y su perfil en el 4.º argumento de la señal, igual que un canary de
producción. Nada más cambia entre brazos."""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales_lab.arms import ARM_PROFILES, SIMULATED_ARMS, signal_meta


def test_the_current_bot_gets_the_signal_of_today() -> None:
    assert signal_meta("A1", {"text": "hola", "ts_ms": 5}) is None


@pytest.mark.parametrize(("arm", "profile"), [("B", "jev-v1"), ("C", "openai-lp-v1")])
def test_new_bots_get_mode_on_and_their_profile(arm: str, profile: str) -> None:
    meta = signal_meta(arm, {"text": "hola", "ts_ms": 1_790_000_000_000, "kind": "text", "wamid": "wamid.X"})

    assert meta == {"perception_mode": "on", "perception_profile": profile, "ts_ms": 1_790_000_000_000, "kind": "text"}
    assert ARM_PROFILES[arm] == profile


def test_a_message_without_time_still_carries_the_mode() -> None:
    assert signal_meta("B", {"text": "hola"}) == {"perception_mode": "on", "perception_profile": "jev-v1", "kind": "text"}


def test_an_unknown_arm_is_refused() -> None:
    assert SIMULATED_ARMS == ("A1", "B", "C")
    with pytest.raises(ValueError, match="Z"):
        signal_meta("Z", {"text": "hola"})
