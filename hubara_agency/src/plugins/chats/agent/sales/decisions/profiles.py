"""Perfiles del motor de decisiones (`profiles.yaml` de este paquete)."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROFILES_PATH = Path(__file__).with_name("profiles.yaml")


@dataclass(frozen=True)
class EngineProfile:
    id: str
    oracle: str
    questions: str
    policy: str
    thresholds: dict[str, float] = field(default_factory=dict)
    shadow: str | None = None
    calibrated_model: str | None = None


def _profile(profile_id: str, raw: dict[str, Any]) -> EngineProfile:
    shadow = raw.get("shadow")
    calibrated = raw.get("calibrated_model")
    return EngineProfile(
        id=profile_id,
        oracle=str(raw["oracle"]),
        questions=str(raw["questions"]),
        policy=str(raw["policy"]),
        thresholds={str(k): float(v) for k, v in (raw.get("thresholds") or {}).items()},
        shadow=str(shadow) if shadow else None,
        calibrated_model=str(calibrated) if calibrated else None,
    )


@lru_cache(maxsize=1)
def load_engine_profiles() -> dict[str, EngineProfile]:
    data = yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8")) or {}
    return {str(pid): _profile(str(pid), raw) for pid, raw in data.items()}


def get_engine_profile(profile_id: str) -> EngineProfile | None:
    return load_engine_profiles().get(profile_id)
