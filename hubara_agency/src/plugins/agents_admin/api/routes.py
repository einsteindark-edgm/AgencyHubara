from pathlib import Path

from fastapi import APIRouter

from src.platform.plugin_manifest import enumerate_manifest_workers, load_manifest

router = APIRouter()

_PLUGINS_PYTHON_DIR = Path(__file__).resolve().parents[2]
# → hubara_agency/src/plugins/

_WORKSPACE_FILES = {
    "identity": "IDENTITY.md",
    "soul": "SOUL.md",
    "tools": "TOOLS.md",
    "agents": "AGENTS.md",
    "users": "USER.md",  # file USER.md maps to key "users"
}


def _read_workspace(plugin_id: str, worker_name: str) -> dict:
    base = _PLUGINS_PYTHON_DIR / plugin_id / "agent" / worker_name / "workspace"
    content: dict = {}
    for key, filename in _WORKSPACE_FILES.items():
        filepath = base / filename
        content[key] = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
    skills = []
    skills_dir = base / "skills"
    if skills_dir.is_dir():
        for skill_dir in sorted(skills_dir.iterdir()):
            if skill_dir.is_dir():
                sf = skill_dir / "skill.md"
                if sf.exists():
                    skills.append({"name": skill_dir.name, "content": sf.read_text(encoding="utf-8")})
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
        if in_body and line.strip():
            return line.strip()[:120]
    return fallback.capitalize()


@router.get("")
async def list_agents() -> list[dict]:
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
