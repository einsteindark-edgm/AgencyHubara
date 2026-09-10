"""D2.3 — política PURA de rollout de Meta Business Agent.

Estamos en producción: MBA solo puede responderle a una LISTA CERRADA de
clientes (regla del operador). Esta política la hace cumplir del lado de
Meta también:

* La allowlist de Meta solo admite teléfonos E.164 que YA están en la lista
  cerrada de Hubara (``MBA_CUSTOMER_ALLOWLIST``): si no, MBA le respondería a
  alguien cuyos eventos ``standby`` Hubara descarta (D1.4).
* ``ai_audience = EVERYONE`` está vedado por política salvo knob explícito
  (``MBA_ALLOW_EVERYONE``) + confirmación, y SOLO con MBA apagado (abrirla
  con el rollout encendido lo abriría a todos en el acto: primero apagar,
  después cambiar, después encender); volver a ``ALLOWLISTED_ONLY`` siempre
  se permite.
* ``rollout.enabled = true`` exige TODOS los chequeos de ``readiness``:
  flag de Hubara encendida, último sync OK (D2.2), connector ACTIVE en Meta,
  audiencia cerrada, allowlist no vacía y contenida en la de Hubara.
* Apagar (``enabled = false``) y quitar teléfonos son el kill switch: nunca
  se bloquean.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

__all__ = [
    "AUDIENCES",
    "E164_RE",
    "Check",
    "RolloutFacts",
    "can_add_phone",
    "can_enable",
    "can_set_audience",
    "readiness",
]

AUDIENCES = ("ALLOWLISTED_ONLY", "EVERYONE")
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


@dataclass(frozen=True)
class RolloutFacts:
    flag_enabled: bool
    rollout_enabled: bool
    ai_audience: str | None
    allowlist: tuple[tuple[str, str], ...]  # (entry_id, phone E.164)
    hubara_allowed: Callable[[str], bool]
    last_sync_ok: bool
    connector_status: str | None
    everyone_knob: bool


@dataclass(frozen=True)
class Check:
    code: str
    ok: bool
    detail: str = ""


def _outside_hubara(facts: RolloutFacts) -> list[str]:
    return [phone for _, phone in facts.allowlist if not facts.hubara_allowed(phone)]


def readiness(facts: RolloutFacts) -> tuple[Check, ...]:
    outside = _outside_hubara(facts)
    return (
        Check("flag_enabled", facts.flag_enabled, "MBA_STANDBY_ENABLED en el API de Hubara"),
        Check("sync_ok", facts.last_sync_ok, "el último sync con Meta terminó OK (D2.2)"),
        Check(
            "connector_active",
            facts.connector_status == "ACTIVE",
            f"connector en Meta: {facts.connector_status or 'no registrado'}",
        ),
        Check("audience_allowlisted_only", facts.ai_audience == "ALLOWLISTED_ONLY", f"ai_audience: {facts.ai_audience or 'desconocida'}"),
        Check("allowlist_nonempty", len(facts.allowlist) > 0, f"{len(facts.allowlist)} teléfono(s) en Meta"),
        Check(
            "allowlist_within_hubara",
            not outside,
            "fuera de la lista cerrada de Hubara: " + ", ".join(outside) if outside else "todos los teléfonos de Meta están en la lista cerrada de Hubara",
        ),
    )


def can_enable(facts: RolloutFacts) -> tuple[str, ...]:
    """Códigos de los chequeos que fallan; vacío = se puede encender."""
    return tuple(c.code for c in readiness(facts) if not c.ok)


def can_add_phone(phone: str, facts: RolloutFacts) -> str | None:
    if not facts.flag_enabled:
        return "mba_disabled"
    if not E164_RE.fullmatch(phone or ""):
        return "invalid_phone"
    if not facts.hubara_allowed(phone):
        return "customer_not_in_hubara_allowlist"
    if any(p == phone for _, p in facts.allowlist):
        return "already_listed"
    return None


def can_set_audience(audience: str, *, confirm: bool, facts: RolloutFacts) -> str | None:
    if audience not in AUDIENCES:
        return "invalid_audience"
    if audience == "ALLOWLISTED_ONLY":
        return None
    if not facts.everyone_knob:
        return "everyone_not_allowed"
    if facts.rollout_enabled:
        return "disable_first"
    if not confirm:
        return "confirmation_required"
    return None
