"""Catálogo de diagnósticos del SDK — código → qué pasó → cómo se arregla.

La fuente ÚNICA de los mensajes del "compilador" (F-SDK-1). La consumen:

- ``src.sdk.testkit`` — cada check fallido referencia un código de acá;
- ``python -m src.sdk.cli explain P-16`` — imprime título + fix + ref;
- el catálogo (system_map) — tooltips de la cuarentena.

Es la tabla §3 de ARCHITECTURE_FINAL_fable.md ("si hacés esto… te frena…
fix") vuelta machine-readable. Regla de oro: gate nuevo ⇒ su entrada acá en
el MISMO PR (un código sin diagnóstico es un error críptico en potencia).

Formato de salida (estilo rustc)::

    error[P-16]: worker usa la task queue de OTRO plugin
      --> src/plugins/eta/workers/eta.py
      fix: get_task_queue("<tu-plugin>", "<tu-worker>") — la queue se declara
           en TU manifest
      ref: docs/_sdk/04-testkit-certificacion.md
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Diagnostic:
    """Definición estática de una regla diagnosticable."""

    code: str
    title: str
    severity: Severity
    fix: str
    ref: str


_D = Diagnostic

DIAGNOSTICS: dict[str, Diagnostic] = {
    d.code: d
    for d in (
        # ── Niveles de certificación (C0/C1 — forma y cargabilidad) ──────
        _D(
            "C0-SCHEMA",
            "el manifest no valida contra el modelo tipado (PluginManifest)",
            "error",
            "leé el detalle del path inválido; el contrato vive en "
            "plugin.schema.yaml y src/sdk/manifest_model.py (deben coincidir)",
            "docs/_sdk/02-manifest-tipado.md",
        ),
        _D(
            "C1-DEPS",
            "depends_on referencia un plugin que no existe en el repo",
            "error",
            "corregí el id en depends_on o creá el plugin provider; "
            "depends_on es SOLO para deps duras (P-DEPS)",
            "docs/_sdk/04-testkit-certificacion.md",
        ),
        _D(
            "C1-API-MODULE",
            "api.python_module / legacy_routers[].module no existe en disco",
            "error",
            "creá el módulo con `router = APIRouter()` o corregí el path "
            "declarado — manifest veraz (INV-3)",
            "docs/_sdk/04-testkit-certificacion.md",
        ),
        _D(
            "C1-WORKER-MODULE",
            "agent.workers[].module no existe en disco",
            "error",
            "creá el módulo del worker (async def main() con "
            "ensure_plugin_enabled primero) o corregí el path",
            "docs/_sdk/04-testkit-certificacion.md",
        ),
        _D(
            "C1-FRONTEND-ENTRY",
            "frontend.entry no existe en disco",
            "error",
            "creá <plugin>/frontend/index.ts que default-exporte el Page, "
            "o borrá el bloque frontend: del manifest",
            "docs/_sdk/04-testkit-certificacion.md",
        ),
        # ── P-rules (gates pre-existentes que el TestKit reporta per-plugin) ──
        _D(
            "P-6",
            "ENABLED_PLUGINS habilita el plugin sin sus depends_on",
            "error",
            "habilitá las deps o quitá el plugin del set (validate_enabled "
            "corre al boot de los 3 loaders)",
            "PLUGIN_CONTRACT.md §5.4",
        ),
        _D(
            "P-15",
            "dashboard.workspace declarado no existe en disco",
            "error",
            "corregí el path (repo-root-relative) o creá el workspace con "
            "IDENTITY/SOUL/TOOLS/AGENTS/USER.md",
            "docs/_sdk/05-arquetipos.md",
        ),
        _D(
            "P-16",
            "worker usa la task queue de OTRO plugin",
            "error",
            'get_task_queue("<tu-plugin>", "<tu-worker>") — la queue se '
            "declara en TU manifest y es única",
            "ARCHITECTURE_FINAL_fable.md §3",
        ),
        _D(
            "P-17",
            "agentic incoherente con los bloques dashboard: de los workers",
            "error",
            "agentic: true ⟺ ≥1 worker con bloque dashboard:. Corregí el "
            "flag o el bloque",
            "docs/_sdk/05-arquetipos.md",
        ),
        _D(
            "P-21",
            "worker sin ensure_plugin_enabled('<id>') como primera línea de main()",
            "error",
            "from src.sdk import ensure_plugin_enabled; llamalo PRIMERO en "
            "main() con TU plugin id (mata containers huérfanos)",
            "docs/_sdk/01-fachada-sdk.md",
        ),
        _D(
            "P-26",
            "directorio de plugin sin plugin.yaml (o manifest sin directorio)",
            "error",
            "todo plugin tiene su manifest en frontend_dashboard/src/plugins/"
            "<id>/plugin.yaml — creálo o borrá el directorio huérfano",
            "ARCHITECTURE_FINAL_fable.md §3",
        ),
        # ── Gates nuevos del SDK ──────────────────────────────────────────
        _D(
            "P-27",
            "plugin sin su archivo de conformance (TCK no instanciado)",
            "error",
            "creá tests/conformance/test_<id>_conformance.py con: "
            'globals().update(conformance_suite("<id>")) — 3 líneas, '
            "el TestKit hace el resto",
            "docs/_sdk/04-testkit-certificacion.md",
        ),
        _D(
            "P-28",
            "import `src.platform.*` NUEVO en un plugin (la fachada es src.sdk)",
            "error",
            "importá desde src.sdk / src.sdk.<kit>; si el símbolo no está en "
            "el SDK, agregalo AL SDK con su check (regla de oro) — la "
            "allowlist P-28 solo achica",
            "docs/_sdk/01-fachada-sdk.md",
        ),
        _D(
            "P-29",
            "la estructura interna no cumple el perfil del arquetipo declarado",
            "error",
            "mirá el detalle (dir faltante / regla de import violada) y el "
            "perfil en src/sdk/testkit/archetypes.py; si el plugin ya no es "
            "de ese tipo, cambiá archetype: en el manifest y pasá el perfil "
            "nuevo ENTERO",
            "docs/_sdk/05-arquetipos.md",
        ),
        _D(
            "P-29A",
            "manifest sin archetype: (o con un valor fuera del catálogo)",
            "error",
            "declará archetype: api_only | full_stack | agentic | notifier | "
            "sync. NO existe 'custom': si no encaja, se agrega un arquetipo "
            "al SDK con ADR",
            "docs/_sdk/05-arquetipos.md",
        ),
        _D(
            "P-31",
            "plugin importa un vendor/connector directo (Medusa, Meta, ...)",
            "error",
            "consumí el PORT del SDK (src.sdk.connectorkit) — el vendor es "
            "detalle de deployment; los imports legacy están congelados en "
            "la allowlist P-31 (solo achica)",
            "docs/_sdk/07-connectorkit.md",
        ),
    )
}


class UnknownDiagnosticError(KeyError):
    """Código no registrado — agregalo a DIAGNOSTICS (regla de oro)."""


def get_diagnostic(code: str) -> Diagnostic:
    try:
        return DIAGNOSTICS[code.upper()]
    except KeyError as exc:
        known = ", ".join(sorted(DIAGNOSTICS))
        raise UnknownDiagnosticError(
            f"código {code!r} no registrado en el catálogo. Conocidos: {known}"
        ) from exc


def format_diagnostic(code: str, detail: str = "", *, location: str = "") -> str:
    """Render estilo rustc de una ocurrencia del diagnóstico."""
    d = get_diagnostic(code)
    lines = [f"{d.severity}[{d.code}]: {d.title}"]
    if location:
        lines.append(f"  --> {location}")
    if detail:
        lines.append(f"  {detail}")
    lines.append(f"  fix: {d.fix}")
    lines.append(f"  ref: {d.ref}")
    return "\n".join(lines)
