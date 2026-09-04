"""I/O del plugin ``mba``: descubre los agentes autorados en ``agents/<id>/`` y
arma su configuración con el dominio puro. Nada de otros plugins (P-3)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from loguru import logger

from src.plugins.mba.domain.config import AgentFiles, MbaConfigDTO, build_agent_config

_PLUGIN_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PLUGIN_DIR.parents[3]  # mba → plugins → src → hubara_agency → raíz
AGENTS_DIR = _PLUGIN_DIR / "agents"


@dataclass(frozen=True)
class MbaAgentDTO:
    id: str
    display_name: str
    role: str
    channel: str
    icon: str
    color: str
    entity_id: str | None


def _agent_dirs() -> list[Path]:
    if not AGENTS_DIR.is_dir():
        return []
    return sorted(p for p in AGENTS_DIR.iterdir() if p.is_dir() and (p / "agent.yaml").is_file())


def list_agents() -> list[MbaAgentDTO]:
    out: list[MbaAgentDTO] = []
    for d in _agent_dirs():
        try:
            spec = yaml.safe_load((d / "agent.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.error("[mba] agent.yaml inválido en {}: {}", d, exc)
            continue
        out.append(
            MbaAgentDTO(
                id=str(spec.get("id") or d.name),
                display_name=str(spec.get("display_name") or d.name),
                role=str(spec.get("role") or ""),
                channel=str(spec.get("channel") or "whatsapp"),
                icon=str(spec.get("icon") or "bot"),
                color=str(spec.get("color") or "violet"),
                entity_id=(str(spec["entity_id"]) if spec.get("entity_id") else None),
            )
        )
    return out


def load_agent(agent_id: str) -> MbaConfigDTO | None:
    d = AGENTS_DIR / agent_id
    if agent_id != Path(agent_id).name or not (d / "agent.yaml").is_file():
        return None
    agent_yaml = (d / "agent.yaml").read_text(encoding="utf-8")
    skills: dict[str, str] = {}
    skills_dir = d / "skills"
    if skills_dir.is_dir():
        for md in sorted(skills_dir.glob("*.md")):
            try:
                skills[f"skills/{md.name}"] = md.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("[mba] no se pudo leer {}: {}", md, exc)
    workspace = d.relative_to(_REPO_ROOT).as_posix()
    return build_agent_config(AgentFiles(agent_yaml=agent_yaml, skills=skills), workspace=workspace)
