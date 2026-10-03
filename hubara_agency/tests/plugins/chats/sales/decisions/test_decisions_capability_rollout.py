"""Encendido por capacidad (diseño v2 §08 y §11, fases F3 y F7).

Cada capacidad tiene su propio interruptor (`reglas` → `sombra` → canary →
`jev`), siempre dentro del techo de Terraform. Bajar o apagar nunca se
bloquea (interruptor de emergencia). Subir a canary o a encendido exige la
vara de la capacidad, medida en producción:

* 7 días o más en sombra, con al menos 100 decisiones medidas;
* menos del 1 % de caídas de Jev y p95 < 1,5 s;
* desacuerdos calificados por Claude Code (al menos 20) y Jev ganando.

El workflow V2 tiene el suyo: canary con números de prueba, un porcentaje y
todos; el encendido total exige haber pasado por canary.
"""
from __future__ import annotations

from pathlib import Path

from src.plugins.chats.agent.sales.decisions.capability_rollout import (
    CapabilityFacts,
    DecisionMetrics,
    can_set_capability,
    capability_facts,
)
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog

DAY = 86_400_000
NOW = 1_790_200_000_000


def _facts(**over) -> CapabilityFacts:
    base = dict(ceiling="on", current="shadow", days=8, decisions=400, fallback_rate=0.002, p95_ms=600,
                labeled=25, jev_wins=15, rule_wins=6)
    base.update(over)
    return CapabilityFacts(**base)


def test_lowering_or_turning_off_is_never_blocked() -> None:
    broken = _facts(days=0, decisions=0, fallback_rate=None, p95_ms=None, labeled=0, jev_wins=0, rule_wins=0)

    assert can_set_capability("off", broken) == ()
    assert can_set_capability("shadow", _facts(current="on", fallback_rate=0.5)) == ()


def test_shadow_only_needs_the_ceiling() -> None:
    assert can_set_capability("shadow", _facts(current="off", days=0, decisions=0)) == ()
    assert can_set_capability("shadow", _facts(current="off", ceiling="off")) == ("within_ceiling",)


def test_acting_needs_the_bar_of_the_capability() -> None:
    assert can_set_capability("canary", _facts()) == ()
    assert set(can_set_capability("on", _facts(days=3, decisions=40, fallback_rate=0.03, p95_ms=2000))) == {
        "shadow_days", "shadow_decisions", "fallbacks", "p95",
    }
    assert set(can_set_capability("canary", _facts(labeled=5, jev_wins=5, rule_wins=0))) == {"labeled"}
    assert set(can_set_capability("canary", _facts(jev_wins=5, rule_wins=15))) == {"jev_wins"}


def test_the_metrics_come_from_what_the_engine_measured(tmp_path: Path) -> None:
    metrics = DecisionMetrics(tmp_path)
    for day in (9, 8, 1):
        for _ in range(40):
            metrics.record(capability="baja", provider="sombra", ok=True, latency_ms=300, agree=True, at_ms=NOW - day * DAY)
    metrics.record(capability="baja", provider="sombra", ok=False, latency_ms=1600, agree=None, at_ms=NOW - DAY)
    metrics.record(capability="compra", provider="sombra", ok=True, latency_ms=900, agree=False, at_ms=NOW - DAY)
    log = DisagreementLog(tmp_path)
    item = log.record(capability="baja", state="x", rule=False, jev=True, model="m", answers=[])
    log.label(item, True)

    facts = capability_facts("baja", vault_dir=tmp_path, now_ms=NOW, ceiling="on", current="shadow")

    assert (facts.days, facts.decisions) == (3, 121)
    assert round(facts.fallback_rate, 4) == round(1 / 121, 4)
    assert facts.p95_ms == 300
    assert (facts.labeled, facts.jev_wins, facts.rule_wins) == (1, 1, 0)


def test_old_measurements_are_forgotten(tmp_path: Path) -> None:
    DecisionMetrics(tmp_path).record(capability="baja", provider="sombra", ok=True, latency_ms=100, agree=True,
                                     at_ms=NOW - 30 * DAY)

    assert capability_facts("baja", vault_dir=tmp_path, now_ms=NOW, ceiling="on", current="shadow").decisions == 0
