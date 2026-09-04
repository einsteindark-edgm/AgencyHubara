"""Descubrimiento de agentes para la sección *Agents* del dashboard.

El plugin ``agents_admin`` NO tiene workers Temporal propios — su responsabilidad
es exponer, en modo lectura, los AGENTES reales del sistema y el contenido REAL
de sus archivos de workspace (``IDENTITY.md`` / ``SOUL.md`` / ``AGENTS.md`` /
``USER.md`` / ``TOOLS.md``).

Fuente de verdad: los ``plugin.yaml`` de cada plugin. Un worker es "un agente
visible en el dashboard" si su entry en ``agent.workers[]`` declara un bloque
``dashboard:`` (ver ``frontend_dashboard/src/plugins/chats/plugin.yaml``). Este
módulo escanea TODOS los manifests genéricamente — nunca nombra a ``chats`` — de
modo que un agente nuevo aparece en la UI con solo declararlo en su manifest.

R-DIP: aquí NO se importa ningún módulo de otro plugin. Los workspaces y los
manifests se leen como ARCHIVOS (datos), igual que hace ``src/main.py`` con los
manifests al boot. La ruta del workspace viaja por el manifest, no por un import.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from loguru import logger

from src.platform.plugin_manifest import enabled_plugins
from src.plugins.agents_admin.mba_config import (
    MbaConfigDTO,
    WorkspaceSkill,
    WorkspaceSources,
    normalize_mba_config,
)

# ---------------------------------------------------------------------------
# Localización del repo root + manifests (mismo árbol que src/main.py lee).
# ---------------------------------------------------------------------------


def _find_repo_root(start: Path) -> Path:
    """Sube hasta el dir que contiene ``hubara_agency/`` y ``frontend_dashboard/``.

    Robusto ante movimientos de este archivo (no depende de un ``parents[N]``
    fijo). Fallback al ancestro razonable si no se encuentra.
    """
    for candidate in (start, *start.parents):
        if (candidate / "hubara_agency").is_dir() and (candidate / "frontend_dashboard").is_dir():
            return candidate
    return start.parents[4]


_REPO_ROOT = _find_repo_root(Path(__file__).resolve())
_PLUGINS_MANIFEST_DIR = _REPO_ROOT / "frontend_dashboard" / "src" / "plugins"

# Mapa key (sección UI) → archivo del workspace. El orden define el orden de
# render en la UI y espeja `PROMPT_SECTIONS` del frontend. Es la convención de
# workspace de exoclaw (USER.md es singular aunque la sección se llame "users").
_PROMPT_FILES: tuple[tuple[str, str], ...] = (
    ("agents", "AGENTS.md"),
    ("identity", "IDENTITY.md"),
    ("soul", "SOUL.md"),
    ("tools", "TOOLS.md"),
    ("users", "USER.md"),
)


# ---------------------------------------------------------------------------
# DTOs (R-JSON: frozen + JSON-serializable; se serializan con `asdict`).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentCapabilityDTO:
    """Una capacidad real del agente (deriva de sus tools, declarada en manifest)."""

    label: str
    icon: str


@dataclass(frozen=True)
class AgentPromptDTO:
    """Contenido REAL de un archivo de workspace del agente."""

    key: str  # "agents" | "identity" | "soul" | "tools" | "users"
    filename: str  # "AGENTS.md", ...
    content: str
    word_count: int


@dataclass(frozen=True)
class AgentDTO:
    """Un agente del sistema, tal como lo consume la sección Agents."""

    id: str  # nombre del worker, e.g. "sales"
    name: str
    role: str
    model: str | None
    category: str
    icon: str
    color: str
    workspace: str  # ruta declarada (para trazabilidad / debug)
    capabilities: tuple[AgentCapabilityDTO, ...] = field(default_factory=tuple)
    prompts: tuple[AgentPromptDTO, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Lectura de workspace.
# ---------------------------------------------------------------------------


def _resolve_workspace_dir(workspace_rel: str) -> Path | None:
    """Resuelve ``workspace_rel`` (repo-root-relative) a un dir existente.

    Defensa: rechaza rutas que escapen del repo root (un manifest malicioso no
    puede hacer que el endpoint sirva ``/etc/passwd``).
    """
    ws_dir = (_REPO_ROOT / workspace_rel).resolve()
    if _REPO_ROOT != ws_dir and _REPO_ROOT not in ws_dir.parents:
        logger.warning(
            "[agents_admin] workspace path escapes repo root, ignorado: {}", ws_dir
        )
        return None
    if not ws_dir.is_dir():
        logger.warning("[agents_admin] workspace dir no existe: {}", ws_dir)
        return None
    return ws_dir


def _read_workspace_prompts(workspace_rel: str) -> tuple[AgentPromptDTO, ...]:
    """Lee los .md del workspace ``workspace_rel`` (repo-root-relative).

    Devuelve solo los archivos que existen; loguea un warning por cada faltante.
    """
    ws_dir = _resolve_workspace_dir(workspace_rel)
    if ws_dir is None:
        return ()

    out: list[AgentPromptDTO] = []
    for key, filename in _PROMPT_FILES:
        path = ws_dir / filename
        if not path.is_file():
            logger.warning("[agents_admin] falta {} en {}", filename, ws_dir)
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("[agents_admin] no se pudo leer {}: {}", path, exc)
            continue
        out.append(
            AgentPromptDTO(
                key=key,
                filename=filename,
                content=content,
                word_count=len(content.split()),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Fuentes para la configuración Meta Business Agent (bootstrap + skills).
# ---------------------------------------------------------------------------

_FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)


def _parse_skill(name: str, text: str) -> WorkspaceSkill:
    """Separa el front-matter YAML de un ``SKILL.md`` (``description`` +
    ``metadata.exoclaw.always``) de su cuerpo markdown."""
    description = ""
    always = False
    body = text
    m = _FRONT_MATTER.match(text)
    if m:
        body = text[m.end():]
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as exc:
            logger.warning("[agents_admin] front-matter inválido en skill {}: {}", name, exc)
            meta = {}
        if isinstance(meta, dict):
            description = str(meta.get("description") or "").strip()
            exo = (meta.get("metadata") or {}).get("exoclaw") if isinstance(meta.get("metadata"), dict) else None
            always = bool(exo.get("always")) if isinstance(exo, dict) else False
    return WorkspaceSkill(name=name, description=description, always=always, body=body)


def read_workspace_sources(workspace_rel: str) -> WorkspaceSources:
    """Lee bootstrap files + ``skills/*/SKILL.md`` de un workspace.

    NUNCA lee ``memory/`` (datos dinámicos y potencialmente personales): la
    configuración de MBA se deriva solo de la personalidad estática.
    """
    ws_dir = _resolve_workspace_dir(workspace_rel)
    if ws_dir is None:
        return WorkspaceSources(files={}, skills=())

    files: dict[str, str] = {}
    for _, filename in _PROMPT_FILES:
        path = ws_dir / filename
        if path.is_file():
            try:
                files[filename] = path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("[agents_admin] no se pudo leer {}: {}", path, exc)

    skills: list[WorkspaceSkill] = []
    skills_dir = ws_dir / "skills"
    if skills_dir.is_dir():
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            try:
                skills.append(_parse_skill(skill_dir.name, skill_md.read_text(encoding="utf-8")))
            except OSError as exc:
                logger.warning("[agents_admin] no se pudo leer {}: {}", skill_md, exc)
    return WorkspaceSources(files=files, skills=tuple(skills))


def build_mba_config(agent_id: str) -> MbaConfigDTO | None:
    """Configuración MBA normalizada del agente ``agent_id`` (None si no existe)."""
    agent = next((a for a in discover_agents() if a.id == agent_id), None)
    if agent is None or not agent.workspace:
        return None
    return normalize_mba_config(
        agent_id, read_workspace_sources(agent.workspace), workspace=agent.workspace
    )


def _build_agent(worker_name: str, dash: dict) -> AgentDTO:
    workspace_rel = str(dash.get("workspace") or "")
    capabilities = tuple(
        AgentCapabilityDTO(label=str(c.get("label", "")), icon=str(c.get("icon", "bolt")))
        for c in (dash.get("capabilities") or [])
        if c.get("label")
    )
    prompts = _read_workspace_prompts(workspace_rel) if workspace_rel else ()
    return AgentDTO(
        id=worker_name,
        name=str(dash.get("display_name") or worker_name),
        role=str(dash.get("role") or ""),
        model=(str(dash["model"]) if dash.get("model") else None),
        category=str(dash.get("category") or "Agentes"),
        icon=str(dash.get("icon") or "bot"),
        color=str(dash.get("color") or "blue"),
        workspace=workspace_rel,
        capabilities=capabilities,
        prompts=prompts,
    )


def discover_agents() -> list[AgentDTO]:
    """Escanea los manifests y devuelve los agentes con bloque ``dashboard:``.

    Genérico: no nombra ningún plugin. Ordena por (categoría, id) para una UI
    estable.
    """
    if not _PLUGINS_MANIFEST_DIR.exists():
        logger.warning(
            "[agents_admin] manifest dir no encontrado: {}", _PLUGINS_MANIFEST_DIR
        )
        return []

    # PM-4 / AP-8: el plano de gestión respeta el toggle — un plugin apagado
    # no muestra sus agentes (antes escaneaba TODOS los manifests del disco).
    enabled = enabled_plugins()

    agents: list[AgentDTO] = []
    for plugin_dir in sorted(_PLUGINS_MANIFEST_DIR.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith(("_", ".")):
            continue
        if enabled is not None and plugin_dir.name not in enabled:
            continue
        manifest_path = plugin_dir / "plugin.yaml"
        if not manifest_path.is_file():
            continue
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.error("[agents_admin] YAML inválido en {}: {}", manifest_path, exc)
            continue
        # PM-3 / AP-11: el schema promete que `agentic: true` gatea esta lista;
        # ahora el código lo honra (P-17 mantiene flag ⟺ dashboard coherentes).
        if not manifest.get("agentic"):
            continue
        agent_cfg = manifest.get("agent") or {}
        for worker in agent_cfg.get("workers") or []:
            dash = worker.get("dashboard")
            name = worker.get("name")
            if not dash or not name:
                continue
            agents.append(_build_agent(str(name), dash))

    agents.sort(key=lambda a: (a.category, a.id))
    logger.info("[agents_admin] descubiertos {} agente(s)", len(agents))
    return agents
