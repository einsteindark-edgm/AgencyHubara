"""Scaffolder — ``hubara create plugin`` genera DESDE los perfiles (F-SDK-3).

INV-5: el skeleton sale del MISMO ``ArchetypeProfile`` que el TCK audita —
por construcción, un plugin generado **nace C2** (el golden test
``tests/architecture/test_cli_scaffold_golden.py`` lo exige en CI; si un
template degenera, el gate lo caza antes que un usuario).

v1 implementa completos los arquetipos sin workers (``api_only`` y
``full_stack``). Los arquetipos con workers Temporal (``agentic`` /
``notifier`` / ``sync``) exigen además queue + k8s yaml + compose + workspace
— scaffold pendiente (F-SDK-3b); el CLI lo dice explícito en vez de generar
un worker a medias que rompa la paridad k8s (premortem).
"""
from __future__ import annotations

import re
from pathlib import Path

SCAFFOLDABLE: tuple[str, ...] = ("api_only", "full_stack")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ScaffoldError(ValueError):
    """Input inválido o colisión — nada se escribió a disco."""


def _manifest_yaml(plugin_id: str, archetype: str, display_name: str) -> str:
    frontend_block = ""
    if archetype == "full_stack":
        frontend_block = f"""
# ── Frontend ───────────────────────────────────────────────────────────
frontend:
  entry: ./frontend
  contributes:
    sections:
      - {{ key: {plugin_id}, label: {display_name}, order: 90, icon: bot }}
    sidebar:
      - {{ route: /{plugin_id}, label: {display_name}, icon: bot }}
"""
    return f"""# Manifest de {plugin_id} — generado por `hubara create plugin` (F-SDK-3).
# Contrato: PLUGIN_CONTRACT.md · schema: ../_schema/plugin.schema.yaml
# Regla de oro: campo nuevo ⇒ schema + modelo tipado + check (mismo PR).
id: {plugin_id}
version: 0.1.0
archetype: {archetype}  # P-29: el TCK audita esta identidad de por vida
display_name: {display_name}
description: |
  TODO: una frase honesta de qué hace este plugin.
{frontend_block}
# ── API REST ────────────────────────────────────────────────────────────
api:
  python_module: src.plugins.{plugin_id}.api
  prefix: /api/{plugin_id}
  tags: [{display_name}]
"""


def _api_init(plugin_id: str) -> str:
    return f'''"""API del plugin {plugin_id} — routers DELGADOS (el perfil lo audita).

La lógica vive en ``domain/`` (pura, sin I/O); este módulo solo traduce
HTTP ↔ dominio. Los datos de OTROS plugins entran por cast declarado
(``consumes:`` del manifest), jamás importando a un sibling (P-3).
"""
from fastapi import APIRouter

from src.plugins.{plugin_id}.domain.logic import health_payload

router = APIRouter()


@router.get("/health")
def health() -> dict:
    """Smoke del plugin — reemplazar por endpoints reales."""
    return health_payload()
'''


def _domain_logic(plugin_id: str) -> str:
    return f'''"""Dominio de {plugin_id} — lógica PURA (sin I/O, sin vendors, sin fastapi).

El perfil del arquetipo (P-29) recomienda que la lógica viva acá y los
routers queden delgados. I/O hacia sistemas externos: SIEMPRE vía ports del
SDK (``src.sdk.connectorkit``) desde adapters — jamás un client de vendor
directo (P-31).
"""
from __future__ import annotations


def health_payload() -> dict:
    """Ejemplo mínimo testeable sin red — reemplazar por dominio real."""
    return {{"plugin": "{plugin_id}", "status": "ok"}}
'''


def _domain_test(plugin_id: str) -> str:
    return f'''"""Tests de DOMINIO de {plugin_id} (los de arquitectura los da el TCK)."""
from src.plugins.{plugin_id}.domain.logic import health_payload


def test_health_payload_shape() -> None:
    payload = health_payload()
    assert payload["plugin"] == "{plugin_id}"
    assert payload["status"] == "ok"
'''


def _frontend_index() -> str:
    return '''/**
 * Entry del plugin — el registry generado exige default-export del Page
 * (assertPluginModule lo verifica EN COMPILACIÓN).
 */
export { default } from "./pages/Page";
'''


def _frontend_page(plugin_id: str, display_name: str) -> str:
    return f'''/**
 * Page de {plugin_id} — los Pages NO reciben props (PluginHost, F5):
 * estado de selección vía useSelection("{plugin_id}"); datos vía la entity
 * local (Zod en el boundary) llamando SOLO /api/{plugin_id}/*.
 */
import {{ useSelection }} from "@/shared/sdk";

export default function Page() {{
  const [selectedId] = useSelection("{plugin_id}");
  return (
    <section className="flex flex-col gap-2 p-4">
      <h1 className="text-lg font-semibold">{display_name}</h1>
      <p className="text-sm opacity-70">
        Plugin generado por <code>hubara create</code>. Reemplazá este Page
        por tu mini-FSD (entities → features → pages).
      </p>
      {{selectedId ? <p className="text-sm">Seleccionado: {{selectedId}}</p> : null}}
    </section>
  );
}}
'''


def _conformance(plugin_id: str) -> str:
    return f'''"""TCK del plugin ``{plugin_id}`` — instanciado, no copiado (P-27, F-SDK-2).

Generado por `hubara create plugin`. NO agregar checks acá (viven en
src/sdk/testkit/ — L-3); los tests de dominio van en tests/plugins/{plugin_id}/.
"""
from src.sdk.testkit import conformance_suite

globals().update(conformance_suite("{plugin_id}"))
'''


def default_repo_root() -> Path:
    """Raíz del monorepo: el dir que contiene `hubara_agency/` y `frontend_dashboard/`.

    Este archivo vive en `hubara_agency/src/sdk/cli/scaffold.py` → 4 niveles
    arriba. (Antes era `parents[5]`, que apuntaba al PADRE del repo y escribía
    el scaffold fuera del checkout en worktrees.)
    """
    return Path(__file__).resolve().parents[4]


def create_plugin(
    plugin_id: str,
    archetype: str,
    repo_root: Path | None = None,
    display_name: str | None = None,
) -> list[Path]:
    """Genera el plugin completo. Devuelve los paths creados (para el log)."""
    if repo_root is None:
        repo_root = default_repo_root()
    if not _ID_RE.match(plugin_id):
        raise ScaffoldError(
            f"id inválido: {plugin_id!r} (regex ^[a-z][a-z0-9_]*$ — es segmento "
            f"de import Python, sin guiones medios)"
        )
    if archetype not in SCAFFOLDABLE:
        raise ScaffoldError(
            f"archetype {archetype!r} todavía no es scaffoldeable (v1: "
            f"{', '.join(SCAFFOLDABLE)}). Los arquetipos con workers Temporal "
            f"(agentic/notifier/sync) requieren queue + k8s + compose + "
            f"workspace (F-SDK-3b) — mientras tanto: copiá el plugin modelo "
            f"del perfil (docs/_sdk/05-arquetipos.md) y corré `hubara check`."
        )
    display_name = display_name or plugin_id.replace("_", " ").title()

    manifest_dir = repo_root / "frontend_dashboard" / "src" / "plugins" / plugin_id
    backend_dir = repo_root / "hubara_agency" / "src" / "plugins" / plugin_id
    conformance = (
        repo_root
        / "hubara_agency"
        / "tests"
        / "conformance"
        / f"test_{plugin_id}_conformance.py"
    )
    domain_tests_dir = repo_root / "hubara_agency" / "tests" / "plugins" / plugin_id
    if manifest_dir.exists() or backend_dir.exists():
        raise ScaffoldError(
            f"el plugin {plugin_id!r} ya existe ({manifest_dir} / {backend_dir})"
        )

    files: dict[Path, str] = {
        manifest_dir / "plugin.yaml": _manifest_yaml(plugin_id, archetype, display_name),
        backend_dir / "__init__.py": "",
        backend_dir / "api" / "__init__.py": _api_init(plugin_id),
        backend_dir / "domain" / "__init__.py": "",
        backend_dir / "domain" / "logic.py": _domain_logic(plugin_id),
        conformance: _conformance(plugin_id),
        domain_tests_dir / "__init__.py": "",
        domain_tests_dir / "test_domain.py": _domain_test(plugin_id),
    }
    if archetype == "full_stack":
        files[manifest_dir / "frontend" / "index.ts"] = _frontend_index()
        files[manifest_dir / "frontend" / "pages" / "Page.tsx"] = _frontend_page(
            plugin_id, display_name
        )

    created: list[Path] = []
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(path)
    return created
