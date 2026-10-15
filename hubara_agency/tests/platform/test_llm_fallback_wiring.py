"""Hubara le pone a ``llm_chat`` un tope y un respaldo coherentes con el proxy.

exoclaw es genérico: ``LLMConfig.call_deadline_s`` / ``fallback_model`` /
``fallback_deadline_s`` llegan en ``None`` (sin respaldo). Los valores los fija
``build_default_llm_config`` — esta prueba asierta que son los que corresponden.

Los números (2026-10-15):
  * PROD: 966 ``llm_chat`` sanas (ventas V2 + remarketing, 10-10 → 10-15, prompt
    p50 29.530 tokens, máx 44.952): p50 1,81 s, p99 2,89 s, máx 7,40 s; ninguna
    pasó de 20 s. Las 7 que no terminaron vencieron a los 294 s en el incidente.
  * El proxy corta a DeepSeek a los 30 s (45 s el alias ``-thinking``) y cae a
    ``gemini-backup``: el tope de la activity tiene que dejarle tiempo a ese
    respaldo del proxy antes de cortar, o se corta un turno que ya se salvaba.
  * Tope de la llamada + tope del respaldo caben holgados en el intento
    (``start_to_close`` de ``_LLM_OPTIONS``, 5 min).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.platform.registries import build_default_llm_config
from src.platform.temporal.retry_policies import _LLM_OPTIONS

_LITELLM_CONFIG = (
    Path(__file__).resolve().parents[3] / "exoclaw-temporal" / "litellm_config.yaml"
)
_PROXY_PREFIX = "litellm_proxy/"
#: La llamada sana más lenta medida en prod (ver docstring).
_SLOWEST_HEALTHY_PROD_CALL_S = 7.40
#: Lo que se le deja al respaldo del proxy después del timeout de DeepSeek
#: (Gemini contesta en ~1 s; el verificador de fotos, el más lento, 5,7 s).
_PROXY_BACKUP_ROOM_S = 10.0


def _proxy_config() -> dict:
    return yaml.safe_load(_LITELLM_CONFIG.read_text(encoding="utf-8"))


def _deepseek_timeouts(config: dict) -> list[float]:
    return [
        e["litellm_params"]["timeout"]
        for e in config["model_list"]
        if e["litellm_params"]["model"].startswith("deepseek/")
    ]


def test_the_agent_falls_back_to_the_same_backup_as_the_proxy_router() -> None:
    llm = build_default_llm_config()
    assert llm.fallback_model is not None, "el agente no tiene respaldo en llm_chat"
    assert llm.fallback_model.startswith(_PROXY_PREFIX), (
        "el respaldo va por el proxy (`litellm_proxy/<alias>`), como CUSTOMER_SUMMARY_MODEL"
    )
    alias = llm.fallback_model[len(_PROXY_PREFIX):]
    config = _proxy_config()
    assert alias in {e["model_name"] for e in config["model_list"]}, (
        f"'{alias}' no es un alias del proxy: el respaldo daría 400 justo cuando hace falta"
    )
    primary = llm.model.split("/", 1)[-1]
    router_backups = [
        backup
        for rule in config["router_settings"]["fallbacks"]
        for name, backups in rule.items()
        if name == primary
        for backup in backups
    ]
    assert router_backups == [alias], (
        f"el proxy cae de '{primary}' a {router_backups} y la activity a '{alias}': "
        "un solo respaldo, el mismo en las dos capas"
    )


def test_the_call_deadline_leaves_room_for_the_proxy_backup_first() -> None:
    llm = build_default_llm_config()
    proxy_cut = max(_deepseek_timeouts(_proxy_config()))
    assert llm.call_deadline_s is not None
    assert llm.call_deadline_s >= proxy_cut + _PROXY_BACKUP_ROOM_S, (
        f"tope de la activity {llm.call_deadline_s} s: el proxy corta a DeepSeek a los "
        f"{proxy_cut} s y necesita ~{_PROXY_BACKUP_ROOM_S:.0f} s para que conteste su "
        "respaldo; cortar antes tira un turno que ya se estaba salvando"
    )
    assert llm.call_deadline_s >= 3 * _SLOWEST_HEALTHY_PROD_CALL_S


def test_the_call_and_the_backup_fit_well_inside_the_activity_attempt() -> None:
    llm = build_default_llm_config()
    attempt_s = _LLM_OPTIONS["start_to_close_timeout"].total_seconds()
    assert llm.call_deadline_s is not None and llm.fallback_deadline_s is not None
    assert llm.call_deadline_s + llm.fallback_deadline_s <= attempt_s / 2, (
        f"{llm.call_deadline_s} + {llm.fallback_deadline_s} s no caben en la mitad del "
        f"intento de {attempt_s:.0f} s: Temporal mataría el intento antes que el respaldo"
    )
    assert llm.fallback_deadline_s >= 3 * _SLOWEST_HEALTHY_PROD_CALL_S
