"""Tool: LoadSkillTool — carga on-demand de skills del workspace.

Bug de producción (runs 019f39ee/d8ee31a9, caso 573229041190, 2026-07-07):
TOOLS.md instruye `load_skill("hubara_catalog")` para consultar las políticas
estables (envíos, garantía, contra entrega, descuentos), pero la tool nunca
estuvo registrada en el worker de Sales — el LLM quemaba una iteración con
"Tool 'load_skill' not found" y las políticas eran INALCANZABLES (terminó
inventando el costo de envío a Medellín en una orden real).

Diseño:
  * **Closed-list**: solo skills que existen como `workspace/skills/<name>/
    SKILL.md`. Nombres con separadores de path o `..` se rechazan sin tocar
    el filesystem (anti path-traversal).
  * El contenido vuelve como tool result → entra al historial del turno
    (los mensajes assistant/tool SÍ se persisten) y el LLM lo cita literal.
  * El error de skill inexistente lista las disponibles — el prompt se
    auto-corrige en la siguiente iteración.
  * Se strippea el frontmatter YAML (metadata de carga, no es contenido
    para el LLM).

DEHA / ADR-001: tool inerte — lee el workspace y devuelve texto; no toca
Temporal ni muta estado.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext


def _strip_frontmatter(text: str) -> str:
    """Quita el bloque YAML `--- ... ---` inicial si existe."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


class LoadSkillTool(ToolBase):
    """Devuelve el contenido de una skill del workspace del agente."""

    name = "load_skill"
    description = (
        "Carga una skill de conocimiento del workspace y devuelve su "
        "contenido para que lo uses en esta conversación. Úsala SOLO cuando "
        "la necesites (ej. `hubara_catalog` si el cliente pregunta por "
        "políticas de envío, garantía, contra entrega o descuentos). El "
        "catálogo de productos NUNCA está en skills: para productos usa "
        "search_products / get_product_by_handle."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": (
                    "Nombre exacto de la skill (directorio bajo "
                    "workspace/skills/), ej. 'hubara_catalog'."
                ),
            }
        },
        "required": ["skill_name"],
    }

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)

    def _available(self) -> list[str]:
        skills_dir = self._workspace / "skills"
        if not skills_dir.is_dir():
            return []
        return sorted(
            d.name
            for d in skills_dir.iterdir()
            if d.is_dir() and (d / "SKILL.md").is_file()
        )

    async def execute_with_context(
        self, ctx: ToolContext, skill_name: str = ""
    ) -> str:
        name = (skill_name or "").strip()
        # Anti path-traversal: el nombre es un identificador plano, nunca un
        # path. Se valida ANTES de tocar el filesystem.
        if not name or "/" in name or "\\" in name or ".." in name:
            return (
                f"Error: nombre de skill inválido {name!r}. "
                f"Disponibles: {', '.join(self._available()) or '(ninguna)'}"
            )
        if name not in self._available():
            return (
                f"Error: la skill '{name}' no existe. "
                f"Disponibles: {', '.join(self._available()) or '(ninguna)'}"
            )
        content = (self._workspace / "skills" / name / "SKILL.md").read_text(
            encoding="utf-8"
        )
        return _strip_frontmatter(content)
