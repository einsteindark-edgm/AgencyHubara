"""LoadSkillTool — carga on-demand de skills del workspace (caso 573229041190).

Bug de producción (runs 019f39ee/d8ee31a9, 2026-07-07): TOOLS.md instruye
`load_skill("hubara_catalog")` para consultar políticas estables (envíos,
garantía, contra entrega), pero la tool NUNCA estuvo registrada en el worker
de Sales → el LLM quemaba una iteración con "Tool 'load_skill' not found" y
las políticas eran INALCANZABLES (terminó inventando el costo de envío a
Medellín en una orden real).

Contrato:
  * closed-list: solo skills que existen como `workspace/skills/<name>/SKILL.md`;
  * anti path-traversal: nombres con separadores / `..` se rechazan;
  * el error de skill inexistente lista las disponibles (self-healing prompt);
  * registrada en el composition root del worker Sales con el import al TOP
    del archivo (gotcha #6: el lambda evalúa lazy y el NameError explotaría
    en runtime de la activity, no al boot).
"""
from __future__ import annotations

import ast
from pathlib import Path

from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.skills import LoadSkillTool

_REPO = Path(__file__).resolve().parents[4]
_SALES_WORKER = _REPO / "src/plugins/chats/workers/sales.py"


def _ctx(session: str = "wa_test") -> ToolContext:
    return ToolContext(session_key=session, channel="whatsapp", chat_id=session)


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    skill_dir = ws / "skills" / "hubara_catalog"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: políticas\n---\n\n# Políticas\n\n"
        "- Envíos a Bogotá: $12.000 a $15.000.\n",
        encoding="utf-8",
    )
    return ws


async def test_load_skill_returns_skill_content(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    tool = LoadSkillTool(workspace=str(ws))
    result = await tool.execute_with_context(_ctx(), skill_name="hubara_catalog")
    assert "Envíos a Bogotá" in result
    assert "$12.000" in result


async def test_load_skill_unknown_lists_available(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    tool = LoadSkillTool(workspace=str(ws))
    result = await tool.execute_with_context(_ctx(), skill_name="no_existe")
    assert "no_existe" in result
    assert "hubara_catalog" in result  # el error enseña las disponibles


async def test_load_skill_rejects_path_traversal(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    outside = tmp_path / "secreto"
    outside.mkdir()
    (outside / "SKILL.md").write_text("no debería leerse", encoding="utf-8")
    tool = LoadSkillTool(workspace=str(ws))
    for evil in ("../secreto", "..", "a/b", "skills/../../secreto"):
        result = await tool.execute_with_context(_ctx(), skill_name=evil)
        assert "no debería leerse" not in result
        assert "Error" in result or "inválido" in result


def test_load_skill_registered_in_sales_worker_with_top_import() -> None:
    """Registro en el composition root + import al TOP (gotcha #6 del repo:
    `register_tool_extension(name, lambda ws: XxxTool(...))` carga limpio
    aunque XxxTool no esté importado — el NameError aparece en runtime de la
    activity tumbando conversaciones reales)."""
    src = _SALES_WORKER.read_text(encoding="utf-8")
    assert '"sales.load_skill"' in src, (
        "workers/sales.py no registra 'sales.load_skill' — TOOLS.md la "
        "instruye y sin registro el LLM quema una iteración con "
        "'Tool not found' (caso run 019f39ee)."
    )
    tree = ast.parse(src)
    top_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            top_imports.update(alias.name for alias in node.names)
    assert "LoadSkillTool" in top_imports, (
        "LoadSkillTool usada en un lambda de register_tool_extension pero "
        "sin import al top de workers/sales.py (gotcha #6)."
    )
