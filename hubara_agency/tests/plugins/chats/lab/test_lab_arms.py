"""Brazos del laboratorio (plan §3.1 y PR 15): el bot actual simulado (A1)
recibe la señal de hoy; el bot nuevo (B = Jev) recibe el modo `on` y su
perfil en el 4.º argumento de la señal, igual que un canary de producción.
Nada más cambia entre brazos. El brazo C (OpenAI) se quitó: 100 % Jev
(decisión del operador, 2026-09-28)."""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales_lab.arms import ARM_PROFILES, SIMULATED_ARMS, signal_meta


def test_the_current_bot_gets_the_signal_of_today() -> None:
    assert signal_meta("A1", {"text": "hola", "ts_ms": 5}) is None


def test_b0_is_the_new_workflow_with_todays_signal() -> None:
    """B0 = V2 con reglas y sin capas: la señal de hoy (lo único que cambia
    con A1 es el workflow, que el sandbox arranca por el registro de bots)."""
    assert signal_meta("B0", {"text": "hola", "ts_ms": 5}) is None
    assert "B0" not in ARM_PROFILES


@pytest.mark.parametrize(("arm", "profile"), [("B", "jev-v5")])
def test_new_bots_get_mode_on_and_their_profile(arm: str, profile: str) -> None:
    meta = signal_meta(arm, {"text": "hola", "ts_ms": 1_790_000_000_000, "kind": "text", "wamid": "wamid.X"})

    assert meta == {
        "perception_mode": "on", "perception_profile": profile, "ts_ms": 1_790_000_000_000, "kind": "text",
        # El motor saca la ráfaga del historial por su wamid (F1): en el sandbox
        # el historial viene cortado al inicio del turno, así queda igual que
        # en producción.
        "wamid": "wamid.X",
    }
    assert ARM_PROFILES[arm] == profile


def test_a_message_without_time_still_carries_the_mode() -> None:
    assert signal_meta("B", {"text": "hola"}) == {"perception_mode": "on", "perception_profile": "jev-v5", "kind": "text"}


def test_the_new_bot_runs_the_complete_engine_profile() -> None:
    """B es el bot nuevo completo: jev-v5 (contrato de herramientas, guía de
    etapas, lectura del hilo). Con jev-v1 el laboratorio medía el motor de F0,
    no el bot que se enciende en producción (el mismo perfil por defecto)."""
    from src.plugins.chats.agent.sales.decisions.bots import DEFAULT_PROFILE, bot_for_arm

    assert bot_for_arm("B").profile == "jev-v5" == DEFAULT_PROFILE


def test_an_unknown_arm_is_refused() -> None:
    assert SIMULATED_ARMS == ("A1", "B0", "B")
    with pytest.raises(ValueError, match="Z"):
        signal_meta("Z", {"text": "hola"})


def test_the_openai_arm_no_longer_exists() -> None:
    with pytest.raises(ValueError, match="C"):
        signal_meta("C", {"text": "hola"})


def test_jev_reads_the_raw_text_of_the_customer_like_in_production() -> None:
    """En producción el clasificador lee `inbound_meta.text` (lo que escribió
    el cliente) y el LLM el turno enriquecido. En el sandbox el mensaje de la
    señal es el turno enriquecido; el crudo viaja en el meta."""
    message = {"text": "[respondes a la campaña] quiero ese", "raw_text": "quiero ese", "wamid": "wamid.X"}

    assert signal_meta("B", message)["text"] == "quiero ese"
    assert "text" not in signal_meta("B", {"text": "hola"})


# ── Un brazo con paquete de decisión: `B@<paquete>` (PAQUETES_DE_DECISION.md F6) ──


def test_an_arm_with_a_bundle_is_the_same_bot_with_other_intelligence() -> None:
    from src.plugins.chats.agent.sales_lab.arms import arm_env, arm_profile, runnable_arm

    message = {"text": "hola", "ts_ms": 5}
    assert signal_meta("B@ventas", message) == signal_meta("B", message)
    assert arm_profile("B@ventas") == ARM_PROFILES["B"]
    assert arm_profile("B0@ventas") is None
    # El proceso del caso fija el paquete ANTES de importar la app (las tools
    # leen el vocabulario de la tienda al importarse).
    assert arm_env("B@ventas") == {"SALES_DECISIONS_BUNDLE": "ventas"}
    assert arm_env("B") == {}
    # Lo que puede correr el simulador (puro: lo usa el workflow de la corrida).
    assert runnable_arm("B@ventas-2") and runnable_arm("A1")
    assert not runnable_arm("Z") and not runnable_arm("B@") and not runnable_arm("C@ventas")
