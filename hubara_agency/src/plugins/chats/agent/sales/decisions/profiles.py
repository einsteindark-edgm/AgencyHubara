"""Perfiles del motor de decisiones (`profiles.yaml` de este paquete).

Un perfil nombra su cuestionario, su política y sus umbrales, o toma el
turno del paquete activo (`turn: bundle`, PAQUETES_DE_DECISION.md F7): el
`turn.yaml` de la tienda trae el cuestionario, la política y los umbrales,
y el perfil los muestra (`questions`, `policy`, `thresholds`, `bundle`) para
la traza, el laboratorio y la sonda. Con qué corre el turno lo resuelve
`decisions/turn.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROFILES_PATH = Path(__file__).with_name("profiles.yaml")
#: `turn: bundle`: el turno del paquete activo.
BUNDLE_TURN = "bundle"


@dataclass(frozen=True)
class EngineProfile:
    id: str
    oracle: str
    questions: str
    policy: str
    thresholds: dict[str, float] = field(default_factory=dict)
    shadow: str | None = None
    calibrated_model: str | None = None
    #: `id@versión` del paquete cuyo turno corre el perfil; None = el
    #: cuestionario y la política que nombra el perfil.
    bundle: str | None = None


def _profile(profile_id: str, raw: dict[str, Any]) -> EngineProfile:
    shadow = raw.get("shadow")
    calibrated = raw.get("calibrated_model")
    common = dict(
        id=profile_id,
        oracle=str(raw["oracle"]),
        shadow=str(shadow) if shadow else None,
        calibrated_model=str(calibrated) if calibrated else None,
    )
    turn = raw.get("turn")
    if turn is not None:
        if turn != BUNDLE_TURN:
            raise KeyError(f"perfil {profile_id}: turn {turn!r} (solo {BUNDLE_TURN!r}: el del paquete activo)")
        from src.plugins.chats.agent.sales.decisions.registry import active_turn

        compiled = active_turn()
        return EngineProfile(
            questions=str(compiled.questionnaire["id"]),
            policy=compiled.policy,
            thresholds=compiled.thresholds,
            bundle=compiled.bundle,
            **common,
        )
    return EngineProfile(
        questions=str(raw["questions"]),
        policy=str(raw["policy"]),
        thresholds={str(k): float(v) for k, v in (raw.get("thresholds") or {}).items()},
        **common,
    )


@lru_cache(maxsize=1)
def _raw_profiles() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8")) or {}
    return {str(pid): dict(raw) for pid, raw in data.items()}


def load_engine_profiles() -> dict[str, EngineProfile]:
    """Los perfiles; los de `turn: bundle`, con el turno del paquete activo
    (no se cachean: el paquete activo es config del proceso)."""
    return {pid: _profile(pid, raw) for pid, raw in _raw_profiles().items()}


def get_engine_profile(profile_id: str) -> EngineProfile | None:
    return load_engine_profiles().get(profile_id)
