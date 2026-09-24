"""Carga de `profiles.yaml` (perfiles versionados del puerto de percepción)."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROFILES_PATH = Path(__file__).with_name("profiles.yaml")


@dataclass(frozen=True)
class PerceptionProfile:
    id: str
    provider: str  # fake | null | litellm | openrouter_decisions
    model: str
    question_set: str
    timeout_s: float = 3.0
    thresholds: dict[str, float] = field(default_factory=dict)
    anonymize: bool = True
    params: dict[str, Any] = field(default_factory=dict)
    provider_prefs: dict[str, Any] = field(default_factory=dict)
    # Calibración por pregunta (temperature scaling): {pregunta: {"temperature": T}}.
    calibration: dict[str, dict[str, float]] = field(default_factory=dict)


def _profile(profile_id: str, raw: dict[str, Any]) -> PerceptionProfile:
    return PerceptionProfile(
        id=profile_id,
        provider=str(raw["provider"]),
        model=str(raw["model"]),
        question_set=str(raw.get("question_set") or ""),
        timeout_s=float(raw.get("timeout_s") or 3.0),
        thresholds={str(k): float(v) for k, v in (raw.get("thresholds") or {}).items()},
        anonymize=bool(raw.get("anonymize", True)),
        params=dict(raw.get("params") or {}),
        provider_prefs=dict(raw.get("provider_prefs") or {}),
        calibration={str(k): dict(v) for k, v in (raw.get("calibration") or {}).items()},
    )


@lru_cache(maxsize=1)
def load_profiles() -> dict[str, PerceptionProfile]:
    data = yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8")) or {}
    return {str(pid): _profile(str(pid), raw) for pid, raw in data.items()}
