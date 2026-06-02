from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Response

from src.platform.plugin_manifest import enumerate_manifest_workers, load_manifest

router = APIRouter()
logger = structlog.get_logger()

_PLUGINS_PYTHON_DIR = Path(__file__).resolve().parents[2]
# → hubara_agency/src/plugins/

_WORKSPACE_FILES = {
    "identity": "IDENTITY.md",
    "soul": "SOUL.md",
    "tools": "TOOLS.md",
    "agents": "AGENTS.md",
    "users": "USER.md",  # file USER.md maps to key "users"
}


def _require_internal(x_internal_dashboard: str | None = Header(default=None)) -> None:
    if x_internal_dashboard != "1":
        raise HTTPException(status_code=403, detail="Forbidden")


def _read_workspace(plugin_id: str, worker_name: str) -> dict:
    base = _PLUGINS_PYTHON_DIR / plugin_id / "agent" / worker_name / "workspace"
    logger.debug("read_workspace", plugin_id=plugin_id, worker_name=worker_name)
    content: dict = {}
    for key, filename in _WORKSPACE_FILES.items():
        filepath = base / filename
        if filepath.exists():
            try:
                content[key] = filepath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                logger.warning(
                    "workspace_file_read_error",
                    plugin_id=plugin_id,
                    worker_name=worker_name,
                    file=str(filepath),
                    error=str(exc),
                )
                content[key] = ""
        else:
            content[key] = ""
    skills = []
    skills_dir = base / "skills"
    if skills_dir.is_dir():
        for skill_dir in sorted(skills_dir.iterdir()):
            if skill_dir.is_dir():
                sf = skill_dir / "skill.md"
                if sf.exists():
                    try:
                        skills.append({"name": skill_dir.name, "content": sf.read_text(encoding="utf-8")})
                    except (UnicodeDecodeError, OSError) as exc:
                        logger.warning(
                            "skill_file_read_error",
                            plugin_id=plugin_id,
                            worker_name=worker_name,
                            skill=skill_dir.name,
                            error=str(exc),
                        )
    content["skills"] = skills
    return content


def _extract_name(identity_text: str, fallback: str) -> str:
    for line in identity_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback.capitalize()


def _extract_role(identity_text: str, fallback: str) -> str:
    in_body = False
    for line in identity_text.splitlines():
        if line.startswith("# "):
            in_body = True
            continue
        if in_body and line.strip() and not line.startswith("#"):
            return line.strip()[:120]
    return fallback.capitalize()


@router.get("")
def list_agents(
    _: None = Depends(_require_internal),
    response: Response = None,  # type: ignore[assignment]
) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    logger.info("list_agents.start")
    result = []
    seen: set[str] = set()
    for plugin_id, _worker_name, _ in enumerate_manifest_workers():
        if plugin_id in seen:
            continue
        manifest = load_manifest(plugin_id)
        if not manifest.get("agentic", False):
            continue
        seen.add(plugin_id)
        for worker in manifest.get("agent", {}).get("workers", []):
            w_name = worker.get("name", "")
            if not w_name:
                logger.warning("worker_missing_name", plugin_id=plugin_id, manifest_entry=worker)
                continue
            workspace = _read_workspace(plugin_id, w_name)
            result.append({
                "id": f"{plugin_id}:{w_name}",
                "plugin_id": plugin_id,
                "worker_name": w_name,
                "name": _extract_name(workspace["identity"], w_name),
                "role": _extract_role(workspace["identity"], w_name),
                "workspace": workspace,
            })
    return result
