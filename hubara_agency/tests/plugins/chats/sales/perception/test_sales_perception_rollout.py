"""Encendido del bot nuevo por etapas (plan del laboratorio §8.3 y PR 16). PURO.

El techo lo fija Terraform (`SALES_PERCEPTION_MODE_CEILING`); el control del
dashboard mueve el modo DENTRO del techo:

  * apagar y bajar NUNCA se bloquean (es el interruptor de emergencia);
  * subir exige que el modo llegue al worker (`SALES_SIGNAL_INBOUND_META`),
    no pasar el techo y tener la llave del clasificador;
  * canary y encendido exigen la vara de la sombra: 7 días o más, menos del
    1 % de caídas a "turno como hoy" y p95 de la percepción < 1,5 s.

Por conversación: en canary actúan los números de prueba y un porcentaje
estable de conversaciones (por hash); las demás siguen en sombra.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.perception.rollout import (
    RolloutFacts,
    RolloutState,
    can_set_mode,
    effective_mode,
    readiness,
)

TEST = "wa_573001234567"


def _facts(**over) -> RolloutFacts:
    base = dict(
        ceiling="on",
        current="shadow",
        signal_meta_enabled=True,
        api_key_present=True,
        shadow_days=8,
        shadow_turns=400,
        shadow_fallback_rate=0.004,
        shadow_p95_ms=900,
    )
    base.update(over)
    return RolloutFacts(**base)


def test_turning_off_or_lowering_is_never_blocked() -> None:
    broken = _facts(signal_meta_enabled=False, api_key_present=False, shadow_days=0, shadow_fallback_rate=0.5)

    assert can_set_mode("off", broken) == ()
    assert can_set_mode("shadow", _facts(current="on", api_key_present=False)) == ()


def test_raising_needs_the_signal_the_ceiling_and_the_key() -> None:
    assert can_set_mode("shadow", _facts(current="off", signal_meta_enabled=False)) == ("signal_meta_on",)
    assert can_set_mode("on", _facts(ceiling="shadow")) == ("within_ceiling",)
    assert can_set_mode("shadow", _facts(current="off", api_key_present=False)) == ("api_key",)


def test_canary_needs_the_shadow_bar() -> None:
    assert can_set_mode("canary", _facts()) == ()
    assert set(can_set_mode("canary", _facts(shadow_days=3, shadow_fallback_rate=0.02, shadow_p95_ms=2100))) == {
        "shadow_days", "shadow_fallbacks", "shadow_p95",
    }
    assert "shadow_days" in can_set_mode("on", _facts(shadow_days=2))


def test_readiness_explains_every_check() -> None:
    checks = readiness("canary", _facts(shadow_p95_ms=2100))

    by_code = {c.code: c for c in checks}
    assert by_code["shadow_p95"].ok is False and "2100" in by_code["shadow_p95"].detail
    assert by_code["within_ceiling"].ok is True


def test_the_mode_of_a_conversation_never_passes_the_ceiling() -> None:
    state = RolloutState(mode="on")

    assert effective_mode(state, ceiling="shadow", session_id=TEST) == "shadow"
    assert effective_mode(state, ceiling="off", session_id=TEST) == "off"
    assert effective_mode(RolloutState(mode="shadow"), ceiling="on", session_id=TEST) == "shadow"


def test_canary_acts_on_test_numbers_and_a_stable_share_the_rest_stays_in_shadow() -> None:
    only_tests = RolloutState(mode="canary", canary_percent=0, test_numbers=(TEST,))
    assert effective_mode(only_tests, ceiling="on", session_id=TEST) == "canary"
    assert effective_mode(only_tests, ceiling="on", session_id="wa_573007654321") == "shadow"

    half = RolloutState(mode="canary", canary_percent=50)
    sessions = [f"wa_57300000{i:04d}" for i in range(400)]
    acting = [s for s in sessions if effective_mode(half, ceiling="on", session_id=s) == "canary"]
    assert 150 < len(acting) < 250
    assert acting == [s for s in sessions if effective_mode(half, ceiling="on", session_id=s) == "canary"]  # estable


def test_an_unknown_mode_is_off() -> None:
    assert effective_mode(RolloutState(mode="encendido"), ceiling="on", session_id=TEST) == "off"
