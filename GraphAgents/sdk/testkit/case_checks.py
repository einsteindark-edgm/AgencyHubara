"""Los checks de un **CASO** — única fuente de verdad. Un caso es data (el triple input + su
golden); estos checks lo hacen CONFIABLE para que el viewer muestre ✓ sin que nadie lea código:

- CASE-REF    — todo `$ref` (seed/ports/golden) resuelve a un archivo existente.
- CASE-TARGET — el `target` (tool:/agent:/flow:<id>) existe en el catálogo / manifests.
- CASE-PORT   — todo port inyectado tiene su fixture vendor (replayeable sin red).
- CASE-GOLDEN — el REPLAY del caso == su golden (el guard contra un golden desactualizado).

Devuelve `{'errors': [...], 'warnings': [...]}` — el mismo reporte que los checks de manifests
y tools. Corta temprano: sin refs no se puede replayear, sin target tampoco.
"""
from __future__ import annotations

from pathlib import Path

from sdk.case_model import Case, iter_refs, resolve
from sdk.connectorkit.ports import FIXTURE_VENDORS
from sdk.registry import discover_tool_ids


def _check_refs(case: Case, ga_root: Path) -> list[str]:
    errs: list[str] = []
    for ref in iter_refs(case.seed) + iter_refs(case.ports) + iter_refs(case.golden):
        if not (ga_root / ref).exists():
            errs.append(f"[CASE-REF] el caso '{case.id}' referencia '{ref}' que no existe (relativo a GraphAgents/)")
    return errs


def _check_target(case: Case, ga_root: Path) -> list[str]:
    if case.kind == "tool":
        if case.target_id not in discover_tool_ids(ga_root):
            return [f"[CASE-TARGET] el caso '{case.id}' apunta a tool '{case.target_id}' que no está en el catálogo (tools/)"]
        return []
    suffix = "agent.yaml" if case.kind == "agent" else "taskgraph.yaml"
    p = ga_root / "manifests" / f"{case.target_id}.{suffix}"
    if not p.exists():
        return [f"[CASE-TARGET] el caso '{case.id}' apunta a {case.kind} '{case.target_id}' inexistente ({p.name})"]
    return []


def _check_ports(case: Case) -> list[str]:
    return [
        f"[CASE-PORT] el caso '{case.id}' inyecta el port '{pk}' sin fixture vendor "
        f"(conocidos: {sorted(FIXTURE_VENDORS)})"
        for pk in case.ports
        if pk not in FIXTURE_VENDORS
    ]


def _check_golden(case: Case, ga_root: Path) -> list[str]:
    """El corazón: replayea el caso y compara con su golden. Import local de `replay_case`
    para no acoplar el TestKit al loader salvo cuando se chequea de verdad (y evita ciclos)."""
    from sdk.replay import replay_case

    try:
        out = replay_case(case, ga_root)
    except Exception as e:  # noqa: BLE001 — replay roto = caso no confiable
        return [f"[CASE-GOLDEN] el caso '{case.id}' no replayea: {e}"]
    if out != resolve(case.golden, ga_root):
        return [f"[CASE-GOLDEN] el caso '{case.id}': el replay NO matchea el golden (¿golden desactualizado?)"]
    return []


def run_case_checks(case: Case, ga_root: Path) -> dict:
    refs = _check_refs(case, ga_root)
    if refs:
        return {"errors": refs, "warnings": []}  # sin los refs no se puede resolver ni replayear
    target = _check_target(case, ga_root)
    if target:
        return {"errors": target, "warnings": []}
    ports = _check_ports(case)
    if ports:
        return {"errors": ports, "warnings": []}
    return {"errors": _check_golden(case, ga_root), "warnings": []}
