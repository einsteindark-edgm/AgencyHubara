"""Encendido del bot nuevo por etapas (plan del laboratorio §8.3, PR 16). PURO.

Patrón de `src/plugins/mba/domain/rollout_policy.py`: hechos → chequeos.

* El techo lo fija Terraform (`SALES_PERCEPTION_MODE_CEILING`); el control
  del dashboard mueve el modo DENTRO del techo y nunca lo pasa.
* Apagar y bajar NUNCA se bloquean: es el interruptor de emergencia.
* Subir exige que el modo llegue al worker (`SALES_SIGNAL_INBOUND_META`), no
  pasar el techo y la llave del clasificador (`OPENROUTER_API_KEY`).
* Canary y encendido exigen la vara de la sombra (§8.3): 7 días o más, menos
  del 1 % de caídas a "turno como hoy" y p95 de la percepción < 1,5 s.

Por conversación (`effective_mode`): en canary actúan los números de prueba
y un porcentaje estable de conversaciones (hash del id); las demás siguen en
sombra para seguir midiendo.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

MODES: tuple[str, ...] = ("off", "shadow", "canary", "on")
SHADOW_MIN_DAYS = 7
SHADOW_MAX_FALLBACK_RATE = 0.01
SHADOW_MAX_P95_MS = 1500


def _rank(mode: str) -> int:
    return MODES.index(mode) if mode in MODES else 0


@dataclass(frozen=True)
class RolloutState:
    """Lo que el control del dashboard guarda (`_rollout/perception.json`)."""

    mode: str = "off"
    canary_percent: int = 0
    test_numbers: tuple[str, ...] = ()
    updated_at_ms: int | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class RolloutFacts:
    ceiling: str
    current: str
    signal_meta_enabled: bool
    api_key_present: bool
    shadow_days: int
    shadow_turns: int
    shadow_fallback_rate: float | None
    shadow_p95_ms: int | None


@dataclass(frozen=True)
class Check:
    code: str
    ok: bool
    detail: str = ""


def readiness(target: str, facts: RolloutFacts) -> tuple[Check, ...]:
    checks = [
        Check("signal_meta_on", facts.signal_meta_enabled, "SALES_SIGNAL_INBOUND_META encendido: el modo llega al worker"),
        Check("within_ceiling", _rank(target) <= _rank(facts.ceiling), f"techo de Terraform: {facts.ceiling}"),
        Check("api_key", facts.api_key_present, "OPENROUTER_API_KEY cargada"),
    ]
    if _rank(target) >= _rank("canary"):
        fallback = facts.shadow_fallback_rate
        p95 = facts.shadow_p95_ms
        checks += [
            Check("shadow_days", facts.shadow_days >= SHADOW_MIN_DAYS,
                  f"{facts.shadow_days} días en sombra ({facts.shadow_turns} turnos); mínimo {SHADOW_MIN_DAYS}"),
            Check("shadow_fallbacks", fallback is not None and fallback < SHADOW_MAX_FALLBACK_RATE,
                  "caídas en sombra: " + (f"{fallback:.1%}" if fallback is not None else "sin datos") + " (menos de 1 %)"),
            Check("shadow_p95", p95 is not None and p95 < SHADOW_MAX_P95_MS,
                  f"p95 de la percepción: {p95 if p95 is not None else 'sin datos'} ms (menos de {SHADOW_MAX_P95_MS})"),
        ]
    return tuple(checks)


def can_set_mode(target: str, facts: RolloutFacts) -> tuple[str, ...]:
    """Códigos de los chequeos que fallan; vacío = se puede. Bajar o apagar
    siempre se puede."""
    if target not in MODES:
        return ("invalid_mode",)
    if _rank(target) <= _rank(facts.current):
        return ()
    return tuple(c.code for c in readiness(target, facts) if not c.ok)


def _bucket(session_id: str) -> int:
    return int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16) % 100


def effective_mode(state: RolloutState, *, ceiling: str, session_id: str) -> str:
    """El modo de ESTA conversación: el del control, sin pasar el techo; en
    canary, solo los números de prueba y el porcentaje estable actúan."""
    mode = state.mode if state.mode in MODES else "off"
    if _rank(mode) > _rank(ceiling if ceiling in MODES else "off"):
        mode = ceiling if ceiling in MODES else "off"
    if mode != "canary":
        return mode
    if session_id in state.test_numbers or _bucket(session_id) < max(0, min(100, state.canary_percent)):
        return "canary"
    return "shadow"
