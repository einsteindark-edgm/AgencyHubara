"""Perfiles del motor de decisiones (diseño v2 §03).

Un perfil del MOTOR junta lo que cambia seguido: cuestionario, política,
umbrales, el perfil que corre en sombra y el snapshot de Jev con el que se
calibró. El ORÁCULO (proveedor, modelo fijo, tiempo máximo, anonimización)
vive en la plataforma (`platform/perception/profiles.yaml`) y casi nunca
cambia. El id del perfil del motor (`jev-v1`) es el que viaja en la señal y en
Terraform (`tenants.*.lab.perception_profile`).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.perception.profiles import load_profiles as load_oracle_profiles
from src.plugins.chats.agent.sales.decisions.policies import get_policy
from src.plugins.chats.agent.sales.decisions.profiles import get_engine_profile, load_engine_profiles
from src.plugins.chats.agent.sales.decisions.turn import turn_of


def test_jev_v1_is_the_classifier_of_today() -> None:
    profile = get_engine_profile("jev-v1")

    assert profile is not None
    assert (profile.oracle, profile.questions, profile.policy) == ("jev-1.13", "rafaga-v1", "turno-v1")
    assert profile.thresholds == {"detect": 0.70, "covered": 0.70}


def test_every_engine_profile_resolves_its_oracle_questionnaire_and_policy() -> None:
    profiles = load_engine_profiles()
    oracles = load_oracle_profiles()

    assert profiles
    for profile in profiles.values():
        assert profile.oracle in oracles, profile.id
        # Su cuestionario y su política, o los del turno del paquete activo (F7).
        turn = turn_of(profile)
        assert turn.questionnaire.id == profile.questions, profile.id
        assert turn.policy is get_policy(profile.policy), profile.id
        if profile.shadow is not None:
            assert profile.shadow in profiles and profile.shadow != profile.id, profile.id


def test_the_openai_rival_profile_is_gone() -> None:
    assert get_engine_profile("openai-lp-v1") is None


def test_an_unknown_policy_is_an_error() -> None:
    with pytest.raises(KeyError, match="turno-v0"):
        get_policy("turno-v0")


def test_jev_v4_is_jev_v3_with_broader_topics() -> None:
    """Caso del laboratorio (2026-09-28): «¿tienen religiosas?» y «más
    información sobre la colección de Halloween» (con el anuncio adelante) le
    dieron a «catálogo» 0,11–0,32 con `rafaga-v3`: el plan del turno quedó
    solo con «saludo» y nada obligaba a mostrar el catálogo. `jev-v4` cambia
    SOLO el cuestionario (mismas política y umbrales): así el laboratorio
    compara v3 contra v4 sabiendo qué se movió."""
    v3, v4 = get_engine_profile("jev-v3"), get_engine_profile("jev-v4")

    assert v4 is not None and v3 is not None
    assert (v4.oracle, v4.questions, v4.policy) == ("jev-1.13", "rafaga-v4", "turno-v3")
    assert (v4.thresholds, v4.shadow, v4.calibrated_model) == (v3.thresholds, v3.shadow, v3.calibrated_model)


def test_jev_v5_is_jev_v4_with_the_shipping_cost_question() -> None:
    """Laboratorio caso-fotos-0929-r5 (6543, turno 6): con la segunda puerta
    también en `send_reply`, «¿cuánto se demora el envío?» salió solo con la
    tarjeta de tarifas. `jev-v5` cambia SOLO el cuestionario (`rafaga-v5`: la
    pregunta `envio.costo`), con la misma política y los mismos umbrales: el
    laboratorio compara v4 contra v5 sabiendo qué se movió.

    Desde F7 (PAQUETES_DE_DECISION.md) su turno sale del paquete activo
    (`ventas@1`). Ningún perfil trae ya `confidence`: ninguna política la leía."""
    v4, v5 = get_engine_profile("jev-v4"), get_engine_profile("jev-v5")

    assert v5 is not None and v4 is not None
    assert (v5.oracle, v5.questions, v5.policy, v5.bundle) == ("jev-1.13", "rafaga-v5", "turno-v3", "ventas@1")
    assert v5.thresholds == v4.thresholds
    assert (v5.shadow, v5.calibrated_model) == (v4.shadow, v4.calibrated_model)


#: Las políticas que lee cada una (una política nueva arrastra lo que hereda).
_POLICY_CHAIN = {"turno-v1": ("turno_v1",), "turno-v2": ("turno_v1", "turno_v2"), "turno-v3": ("turno_v1", "turno_v2", "turno_v3")}


def _thresholds_read(policy_id: str) -> set[str]:
    """Los umbrales que el código de la política (y lo que hereda) lee: `th["…"]`."""
    import re

    root = Path(__file__).resolve().parents[5] / "src/plugins/chats/agent/sales/decisions/policies"
    return {
        key
        for module in _POLICY_CHAIN[policy_id]
        for key in re.findall(r'th\["([a-z_]+)"\]', (root / f"{module}.py").read_text(encoding="utf-8"))
    }


def test_no_profile_or_policy_carries_a_threshold_nobody_reads() -> None:
    """Un umbral que nadie lee engaña: parece que calibra algo y no hace nada
    (`confidence: 0.60` estuvo así desde jev-v1, PAQUETES_DE_DECISION.md §1).
    Lo mismo que el certificador exige a un `turn.yaml` (DB015)."""
    import yaml

    from src.plugins.chats.shared.store_pack import CATALOG_PATH

    for policy_id in _POLICY_CHAIN:
        policy = get_policy(policy_id)
        assert set(policy.DEFAULT_THRESHOLDS) == _thresholds_read(policy_id), policy_id
    for profile in load_engine_profiles().values():
        assert set(profile.thresholds) <= _thresholds_read(profile.policy), profile.id
    declared = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))["turn"]["policies"]["turno-v3"]["thresholds"]
    assert set(declared) == _thresholds_read("turno-v3")
