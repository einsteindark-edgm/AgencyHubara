"""Fixtures y helpers comunes a la suite de tests de arquitectura DEHA.

Los tests bajo `tests/architecture/` validan las 5 reglas duras (R-DET, R-JSON,
R-STATELESS, R-HEARTBEAT, R-DIP) y los anti-patterns del layout multi-agente
del repo. Bloquean merge a main via `pytest -m architecture`.

Convenciones:
  * Todos los tests llevan `pytestmark = pytest.mark.architecture` (definido por
    el fixture autouse `architecture_marker` aquí abajo).
  * Las allow-lists vivien centralizadas en este archivo, no diseminadas por
    test. Cualquier excepción se documenta con el motivo.
  * `SRC_ROOT` es la raíz de `src/` resuelta dinámicamente para que los tests
    funcionen igual desde cwd=hubara_agency/ o cwd=repo-root.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


SRC_ROOT: Path = Path(__file__).resolve().parents[2] / "src"

# ----------------------------------------------------------------------------
# Allow-lists — qué excluimos del scope arquitectónico y por qué.
# ----------------------------------------------------------------------------

# El api/ del plugin chats es FastAPI puro (HTTP edge), no DEHA. Las reglas R-* no aplican.
# PR2: ex `src.dashboard` → `src.plugins.chats.api.*`. Mantenemos la exclusión semántica.
DASHBOARD_EXCLUDE = {"plugins.chats.api"}

# Agentes DEHA: cada entrada es un id lógico. Los paths se resuelven con
# `agent_paths()`. Post-PR5 todos los agentes viven bajo `src/plugins/`.
DEHA_AGENTS = ("chats.sales", "chats.remarketing", "catalog.sync")


def agent_paths(agent: str) -> dict[str, Path]:
    """Mapping de agent id a sus paths físicos (todos bajo src/plugins/<id>/)."""
    if agent == "chats.sales":
        return {
            "worker": SRC_ROOT / "plugins" / "chats" / "workers" / "sales.py",
            "root": SRC_ROOT / "plugins" / "chats" / "agent" / "sales",
        }
    if agent == "chats.remarketing":
        return {
            "worker": SRC_ROOT / "plugins" / "chats" / "workers" / "remarketing.py",
            "root": SRC_ROOT / "plugins" / "chats" / "agent" / "remarketing",
        }
    if agent == "catalog.sync":
        return {
            "worker": SRC_ROOT / "plugins" / "catalog" / "workers" / "sync.py",
            "root": SRC_ROOT / "plugins" / "catalog" / "agent",
        }
    raise ValueError(f"unknown agent id: {agent}")


# Packages cross-agent (no son agentes pero participan en las reglas R-DIP).
PLATFORM_PACKAGES = ("platform",)

# Sub-paths con cada rol — usados por los tests R-DET / R-JSON / R-HEARTBEAT.
# Cubren tanto el layout legacy (catalog_sync) como el de plugins (chats).
# Como `Path.glob` no soporta brace-expansion, los tests iteran ambos patrones.
AGENT_WORKFLOWS_GLOBS = (
    "src/*/workflows/*.py",
    "src/plugins/*/agent/*/workflows/*.py",
)
AGENT_ACTIVITIES_GLOBS = (
    "src/*/activities/*.py",
    "src/plugins/*/agent/*/activities/*.py",
)
AGENT_TOOLS_GLOBS = (
    "src/*/tools/*.py",
    "src/plugins/*/agent/*/tools/*.py",
)

# Backwards-compat aliases (mantienen el nombre singular hasta migrar callers).
AGENT_WORKFLOWS_GLOB = AGENT_WORKFLOWS_GLOBS[0]
AGENT_ACTIVITIES_GLOB = AGENT_ACTIVITIES_GLOBS[0]
AGENT_TOOLS_GLOB = AGENT_TOOLS_GLOBS[0]

# Paths "contracts.py" — DTOs de boundary.
CONTRACTS_GLOBS = (
    "src/*/contracts.py",
    "src/plugins/*/agent/*/contracts.py",
    "src/platform/contracts.py",
)

# Excepciones documentadas a R-JSON (clases que no son @dataclass(frozen=True)).
# Cada entrada incluye motivo + owner. Reducir esta lista es trabajo de refactor.
R_JSON_FROZEN_EXEMPTIONS: dict[str, str] = {
    # path:class_name -> motivo
    "src/platform/contracts.py:TransferDecision": (
        "Pre-existente. Migrar a frozen=True junto con el refactor de "
        "SessionInput DTOs a platform (ADR pendiente)."
    ),
    "src/platform/contracts.py:ScheduleRemarketingDecision": (
        "Pre-existente. Misma migración que TransferDecision."
    ),
    "src/plugins/chats/agent/remarketing/contracts.py:RemarketingSessionInput": (
        "Pre-existente. Sales ya está frozen=True; Remarketing pendiente."
    ),
}

# Activities long-running que NO requieren @with_heartbeat por su naturaleza.
# Heurística positiva (greppea httpx/litellm/medusa); los falsos positivos van aquí.
R_HEARTBEAT_EXEMPTIONS: set[str] = {
    # path:function_name — current allow-list.
    # Best-effort, llamada HTTP corta (timeout 4s) sin retry. Un heartbeat
    # agregaria overhead innecesario; si la API falla el LLM ya esta procesando
    # y el indicator seria stale.
    "src/platform/whatsapp/activities.py:send_typing_indicator_activity",
}

# Modulos top-level prohibidos bajo src/ (DEHA layout — agentes son siblings).
FORBIDDEN_TOP_LEVEL_PACKAGES: tuple[str, ...] = (
    "core",
    "shared",
    "common",
    "domains",
)

# Paths protegidos vs main (Capa 3 — meta-test).
# Cualquier PR que los modifique sin la env var ARCH_CHANGE_APPROVED=1 falla.
# Relativos a la raíz del repo (no a hubara_agency/).
#
# F8 (cierra N-8/PM-11 — "dos definiciones de PROTECTED que no coinciden"):
# la lista ya NO vive acá. La FUENTE ÚNICA es
# `hubara_agency/.hubara/spinal-files.yaml` (entries `protected: true`);
# este loader deriva los prefijos truncando cada glob en su primer `*`.
# El meta-gate frontend (src/test/architecture/helpers.ts) lee EL MISMO
# archivo — un solo lugar para agregar/sacar paths protegidos (con ADR).


def _load_protected_prefixes() -> tuple[str, ...]:
    import yaml as _yaml

    spinal = SRC_ROOT.parents[1] / "hubara_agency" / ".hubara" / "spinal-files.yaml"
    data = _yaml.safe_load(spinal.read_text(encoding="utf-8")) or {}
    prefixes: list[str] = []
    for entry in data.get("spinal_files") or []:
        if not isinstance(entry, dict) or not entry.get("protected"):
            continue
        raw = str(entry.get("path") or "")
        prefix = raw.split("*", 1)[0]
        if prefix:
            prefixes.append(prefix)
    if not prefixes:
        raise RuntimeError(
            f"spinal-files.yaml sin entries protected:true — el meta-gate quedaría "
            f"vacío (¿se movió {spinal}?)."
        )
    return tuple(sorted(set(prefixes)))


ARCHITECTURE_PROTECTED_PREFIXES: tuple[str, ...] = _load_protected_prefixes()


# ----------------------------------------------------------------------------
# Marker injection: todo test bajo tests/architecture/ recibe `@pytest.mark.architecture`
# automáticamente, así `pytest -m architecture` los filtra sin que cada archivo
# tenga que declarar `pytestmark`.
# ----------------------------------------------------------------------------

def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    arch_dir = str(Path(__file__).parent)
    for item in items:
        if str(item.fspath).startswith(arch_dir):
            item.add_marker(pytest.mark.architecture)


# ----------------------------------------------------------------------------
# AST helpers — usados por varios módulos de test.
# ----------------------------------------------------------------------------

def parse_file(path: Path) -> ast.Module:
    """Devuelve el AST de un .py. Falla rápido si el módulo no parsea."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def iter_agent_files(glob_pattern: str) -> list[Path]:
    """Lista archivos del repo que matchean `glob_pattern` (relativo a hubara_agency/)."""
    hub_root = SRC_ROOT.parent  # hubara_agency/
    return sorted(p for p in hub_root.glob(glob_pattern) if p.is_file())


def relative_to_hubara(path: Path) -> str:
    """Devuelve el path string relativo a hubara_agency/, slash-style."""
    hub_root = SRC_ROOT.parent
    return path.relative_to(hub_root).as_posix()
