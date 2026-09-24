"""Composición del puerto de percepción: un adaptador por perfil.

`get_perception_port(perfil)` construye el adaptador que el perfil de
`profiles.yaml` declara (`openrouter_decisions` para Jev, `litellm` para
OpenAI con logprobs). `PERCEPTION_PROVIDER` fuerza uno para todo el proceso:

  fake          # adaptador falso (tests, humo del laboratorio)
  off / null    # interruptor: la percepción no corre (fail-open)

Un perfil desconocido da el adaptador nulo: el turno sigue como hoy.
Llaves: `OPENROUTER_API_KEY` (Jev) y la del proxy LiteLLM (OpenAI, cuyo alias
`openrouter-perception` usa la misma llave de OpenRouter en el proxy).
"""
from __future__ import annotations

import os
from functools import lru_cache

import structlog

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.platform.perception.adapters.litellm import LiteLLMLogprobsAdapter
from src.platform.perception.adapters.null import NullPerceptionAdapter
from src.platform.perception.adapters.openrouter_decisions import OpenRouterDecisionsAdapter
from src.platform.perception.ports import PerceptionPort
from src.platform.perception.profiles import load_profiles

logger = structlog.get_logger()


@lru_cache(maxsize=16)
def get_perception_port(profile_id: str) -> PerceptionPort:
    override = (os.getenv("PERCEPTION_PROVIDER") or "").strip().lower()
    if override == "fake":
        return FakePerceptionAdapter()
    if override in {"off", "null", "disabled"}:
        return NullPerceptionAdapter()
    profile = load_profiles().get(profile_id)
    if profile is None:
        logger.warning("perception.unknown_profile", profile=profile_id)
        return NullPerceptionAdapter()
    if profile.provider == "openrouter_decisions":
        return OpenRouterDecisionsAdapter.from_profile(profile)
    if profile.provider == "litellm":
        return LiteLLMLogprobsAdapter.from_profile(profile)
    if profile.provider == "fake":
        return FakePerceptionAdapter()
    return NullPerceptionAdapter()
