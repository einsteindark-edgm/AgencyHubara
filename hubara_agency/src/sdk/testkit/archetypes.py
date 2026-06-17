"""Perfiles de arquetipo — la arquitectura interna obligatoria (P-29, F-SDK-2).

INV-5 ("el que genera, audita"): cada perfil es la fuente ÚNICA de la que
derivan (a) el scaffolder del CLI (genera el skeleton), (b) el TestKit (lo
audita de por vida vía ``check_p29_profile``), (c) el catálogo (lo dibuja).

Un perfil declara FORMA, no implementación: qué superficies del manifest debe
(o no debe) tener el plugin, qué directorios prueban que la estructura
interna existe, y qué está prohibido para su tipo. Las reglas ``required_*``
son ERROR (la realidad actual las cumple — se verificó al clasificar);
las ``advisory_*`` son WARNING (la dirección de migración: visibles en el
reporte sin romper CI hasta que el plugin migre).

NO existe ``archetype: custom``: si un plugin no encaja en ningún perfil, se
agrega/extiende un arquetipo ACÁ (con ADR + label architecture-change) — el
mecanismo se arregla, el contrato no se perfora.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Presence = Literal["required", "forbidden", "optional"]


@dataclass(frozen=True)
class GlobRule:
    """Un glob (relativo a ``hubara_agency/src/plugins/<id>/``) que debe matchear."""

    pattern: str
    why: str


@dataclass(frozen=True)
class ArchetypeProfile:
    name: str
    description: str
    # Superficies del manifest:
    frontend: Presence
    api: Presence
    workers: Presence
    # Flags de coherencia:
    agentic_flag: bool | None = None  # None = cualquiera
    require_worker_dashboard: bool = False  # agentic: ≥1 worker visible en Agents
    forbid_worker_dashboard: bool = False  # sync: un pipeline no es un agente
    forbid_owns_route: bool = False  # notifier/sync: jamás poseen conversación (L-4)
    # Estructura interna (backend), severidad error:
    required_globs: tuple[GlobRule, ...] = field(default_factory=tuple)
    # Dirección de migración, severidad warning:
    advisory_globs: tuple[GlobRule, ...] = field(default_factory=tuple)


PROFILES: dict[str, ArchetypeProfile] = {
    "api_only": ArchetypeProfile(
        name="api_only",
        description=(
            "Backend puro: router FastAPI propio, sin bloque frontend (su UI, "
            "si existe, vive en una app aparte). Modelo: system_map."
        ),
        frontend="forbidden",
        api="required",
        workers="forbidden",
        agentic_flag=False,
        advisory_globs=(
            GlobRule("domain", "lógica pura separada de los routers (api delgada)"),
        ),
    ),
    "full_stack": ArchetypeProfile(
        name="full_stack",
        description=(
            "Sección de dashboard + API propia; workers de background "
            "permitidos (NO conversacionales). Modelo: orders, ads."
        ),
        frontend="required",
        api="required",
        workers="optional",
        agentic_flag=False,
        advisory_globs=(
            GlobRule("domain", "lógica pura separada de los routers (api delgada)"),
        ),
    ),
    "agentic": ArchetypeProfile(
        name="agentic",
        description=(
            "Workers conversacionales con workspace de prompts (IDENTITY/SOUL/"
            "TOOLS) visibles en la sección Agents. Modelo: chats."
        ),
        frontend="optional",
        api="optional",
        workers="required",
        agentic_flag=True,
        require_worker_dashboard=True,
        required_globs=(
            GlobRule(
                "agent/*/workspace",
                "workspace de prompts por agente conversacional (PM-7)",
            ),
        ),
        advisory_globs=(
            GlobRule("agent/*/use_cases", "use cases puros orquestando ports (DEHA)"),
        ),
    ),
    "notifier": ArchetypeProfile(
        name="notifier",
        description=(
            "Push puro: notifica SIN poseer la conversación (L-4 horneada: "
            "sin owns_route; jamás escribe active_route). Modelo: eta."
        ),
        frontend="optional",
        api="optional",
        workers="required",
        forbid_owns_route=True,
        required_globs=(
            GlobRule("agent/*/activities", "los side effects del push viven en activities"),
        ),
    ),
    "sync": ArchetypeProfile(
        name="sync",
        description=(
            "Pipeline source→sink entre sistemas externos con read model "
            "local; reconciliación idempotente, sin conversación ni LLM. "
            "Modelo: catalog."
        ),
        frontend="optional",
        api="optional",
        workers="required",
        agentic_flag=False,
        forbid_owns_route=True,
        forbid_worker_dashboard=True,
        required_globs=(
            GlobRule("agent/**/workflows", "orquestación pull→diff→apply (R-DET)"),
            GlobRule("agent/**/activities", "el único lugar con I/O, vía ports"),
            GlobRule("agent/**/use_cases", "el diff plan se computa PURO (testeable sin red)"),
        ),
    ),
}
