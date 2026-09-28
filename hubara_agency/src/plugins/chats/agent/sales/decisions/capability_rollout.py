"""Encendido por capacidad (diseño v2 §08 y §11, fases F3 y F7). PURO salvo
el registro de métricas.

Cada capacidad tiene su interruptor (`off`=reglas → `shadow`=sombra →
`canary` → `on`=jev, el mismo vocabulario del control «Bot nuevo»), siempre
dentro del techo de Terraform `SALES_CAPABILITIES_CEILING`. Bajar o apagar
nunca se bloquea (interruptor de emergencia). Subir a canary o a encendido
exige la vara de la capacidad, medida en producción en las últimas dos
semanas:

* 7 días distintos o más en sombra y al menos 100 decisiones medidas;
* menos del 1 % de caídas de Jev (falla, tardanza o forma rara) y p95 < 1,5 s;
* al menos 20 desacuerdos calificados por Claude Code y Jev ganando más que
  la regla (la precisión en el banco de referencia se mide en el
  laboratorio).

`DecisionMetrics` guarda una línea por decisión de una capacidad en sombra o
en jev (`<vault>/_decisions/metrics/<AAAA-MM-DD>.jsonl`; `_*`: los
recorredores de sesiones lo ignoran). Sin Temporal.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog
from src.plugins.chats.agent.sales.decisions.rollout import MODES, Check

WINDOW_DAYS = 14
MIN_DAYS = 7
MIN_DECISIONS = 100
MAX_FALLBACK_RATE = 0.01
MAX_P95_MS = 1500
MIN_LABELED = 20
_DAY_MS = 86_400_000


def _rank(mode: str) -> int:
    return MODES.index(mode) if mode in MODES else 0


class DecisionMetrics:
    def __init__(self, vault_dir: Path) -> None:
        self._dir = Path(vault_dir) / "_decisions" / "metrics"

    def record(
        self,
        *,
        capability: str,
        provider: str,
        ok: bool,
        latency_ms: int,
        agree: bool | None,
        at_ms: int | None = None,
    ) -> None:
        at = int(at_ms if at_ms is not None else time.time() * 1000)
        day = datetime.fromtimestamp(at / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        path = self._dir / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"at_ms": at, "capability": capability, "provider": provider, "ok": ok, "latency_ms": latency_ms, "agree": agree}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def rows(self, capability: str, *, since_ms: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self._dir.glob("*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                at = row.get("at_ms") if isinstance(row, dict) else None
                if row.get("capability") == capability and isinstance(at, int) and at >= since_ms:
                    out.append(row)
        return out


@dataclass(frozen=True)
class CapabilityFacts:
    ceiling: str
    current: str
    days: int
    decisions: int
    fallback_rate: float | None
    p95_ms: int | None
    labeled: int
    jev_wins: int
    rule_wins: int


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def capability_facts(capability: str, *, vault_dir: Path, now_ms: int, ceiling: str, current: str) -> CapabilityFacts:
    rows = DecisionMetrics(vault_dir).rows(capability, since_ms=now_ms - WINDOW_DAYS * _DAY_MS)
    days = {r["at_ms"] // _DAY_MS for r in rows}
    fails = sum(1 for r in rows if not r.get("ok"))
    latencies = [int(r["latency_ms"]) for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    score = DisagreementLog(vault_dir).score(capability)
    return CapabilityFacts(
        ceiling=ceiling,
        current=current,
        days=len(days),
        decisions=len(rows),
        fallback_rate=(fails / len(rows)) if rows else None,
        p95_ms=_p95(latencies),
        labeled=score["labeled"],
        jev_wins=score["jev"],
        rule_wins=score["rule"],
    )


def readiness(target: str, facts: CapabilityFacts) -> tuple[Check, ...]:
    checks = [Check("within_ceiling", _rank(target) <= _rank(facts.ceiling), f"techo de Terraform: {facts.ceiling}")]
    if _rank(target) >= _rank("canary"):
        fallback, p95 = facts.fallback_rate, facts.p95_ms
        checks += [
            Check("shadow_days", facts.days >= MIN_DAYS, f"{facts.days} días en sombra; mínimo {MIN_DAYS}"),
            Check("shadow_decisions", facts.decisions >= MIN_DECISIONS,
                  f"{facts.decisions} decisiones medidas; mínimo {MIN_DECISIONS}"),
            Check("fallbacks", fallback is not None and fallback < MAX_FALLBACK_RATE,
                  "caídas de Jev: " + (f"{fallback:.1%}" if fallback is not None else "sin datos") + " (menos de 1 %)"),
            Check("p95", p95 is not None and p95 < MAX_P95_MS,
                  f"p95 de Jev: {p95 if p95 is not None else 'sin datos'} ms (menos de {MAX_P95_MS})"),
            Check("labeled", facts.labeled >= MIN_LABELED,
                  f"{facts.labeled} desacuerdos calificados; mínimo {MIN_LABELED}"),
            Check("jev_wins", facts.jev_wins > facts.rule_wins,
                  f"Jev gana {facts.jev_wins} y la regla {facts.rule_wins} de los desacuerdos calificados"),
        ]
    return tuple(checks)


def can_set_capability(target: str, facts: CapabilityFacts) -> tuple[str, ...]:
    """Códigos de los chequeos que fallan; vacío = se puede. Bajar o apagar
    siempre se puede."""
    if target not in MODES:
        return ("invalid_mode",)
    if _rank(target) <= _rank(facts.current):
        return ()
    return tuple(c.code for c in readiness(target, facts) if not c.ok)
