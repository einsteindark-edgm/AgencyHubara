"""PR-B regression test (remarketing) — system prompt llega via workspace.

Verifica que el system prompt construido desde el workspace canonico de
Remarketing (`hubara_agency/src/remarketing_whatsapp/workspace/`)
contiene el mismo contenido de identidad / tono / mision proactiva /
catalogo que el sistema viejo (shared_brain/*.md inyectado via
`plugin_context` por `load_remarketing_brain_activity`).

Modelado sobre `tests/test_workspace_system_prompt.py` (Sales). No se
instancia `DefaultConversation.create` porque su firma exige un
`LLMProvider` real; el componente que construye el system prompt es
`ContextBuilder`, asi que lo instanciamos directo con el workspace.

Cubre:
- IDENTITY.md presente — Clara, Hubara, "minimamente invasiva".
- SOUL.md presente — BREVEDAD EXTREMA + doble salto de linea.
- USER.md presente — tenant defaults (Hubara, COP, Bogota).
- TOOLS.md presente — `transfer_to_sales_agent` documentada.
- AGENTS.md presente — mision proactiva (levantar, transferir).
- skill `hubara_catalog` (`always: true`) inyectada cada turno (catalogo +
  Cruz de Vida + $17,000).
- Politicas de pago contra entrega (POLICIES en `hubara_catalog`).
- Regla de "envio gratis solo si dijo caro" (AGENTS.md § Prohibicion de
  descuentos).
"""
from __future__ import annotations

from pathlib import Path

from exoclaw_conversation.context import ContextBuilder

WORKSPACE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "remarketing_whatsapp"
    / "workspace"
)


def _build_prompt() -> str:
    """Build the system prompt the way `build_prompt` activity will at runtime.

    Pasamos `extra_context=None` para simular el flujo PR-B donde el
    `plugin_context` del path Remarketing viaja como `None`.
    """
    builder = ContextBuilder(workspace=WORKSPACE)
    return builder.build_system_prompt(skill_names=None, extra_context=None)


def test_workspace_directory_exists() -> None:
    """Sanity: el workspace canonico esta committeado en el repo."""
    assert WORKSPACE.is_dir(), f"workspace missing at {WORKSPACE}"
    for required in ("IDENTITY.md", "SOUL.md", "USER.md", "TOOLS.md", "AGENTS.md"):
        assert (WORKSPACE / required).is_file(), f"missing {required} in workspace"
    assert (WORKSPACE / "skills" / "hubara_catalog" / "SKILL.md").is_file()


def test_identity_in_system_prompt() -> None:
    """IDENTITY.md cruza al system prompt — Clara, Hubara, minimamente invasiva."""
    prompt = _build_prompt()
    # Tokens unicos de IDENTITY.md: el nombre del agente y la marca.
    assert "Clara" in prompt
    assert "Hubara" in prompt
    # Personalidad de remarketing — distintiva vs Sales.
    assert "Mínimamente invasiva" in prompt or "mínimamente invasiva" in prompt.lower()


def test_soul_in_system_prompt() -> None:
    """SOUL.md cruza al system prompt — tono y reglas de formato."""
    prompt = _build_prompt()
    # Tokens de SOUL.md: BREVEDAD EXTREMA + regla del doble salto.
    assert "BREVEDAD" in prompt
    assert "DOBLE SALTO DE L" in prompt or "doble salto de l" in prompt.lower()


def test_user_in_system_prompt() -> None:
    """USER.md cruza al system prompt — tenant defaults."""
    prompt = _build_prompt()
    # Tokens de USER.md: COP y zona horaria.
    assert "COP" in prompt
    assert "America/Bogota" in prompt or "Hubara" in prompt


def test_tools_md_in_system_prompt() -> None:
    """TOOLS.md cruza al system prompt — `transfer_to_sales_agent` documentada."""
    prompt = _build_prompt()
    assert "transfer_to_sales_agent" in prompt


def test_agents_md_in_system_prompt() -> None:
    """AGENTS.md cruza al system prompt — la mision proactiva.

    Tokens distintivos de la mision: "levantar" una conversacion abandonada
    y "transferir" cuando el cliente responde.
    """
    prompt = _build_prompt()
    assert "levantar" in prompt.lower()
    assert "transfer" in prompt.lower()


def test_catalog_skill_loaded_always() -> None:
    """`hubara_catalog` skill (`always: true`) inyecta catalogo cada turno.

    `metadata.exoclaw.always = true` en el frontmatter de
    `skills/hubara_catalog/SKILL.md` (linea 3, single-line JSON inline).
    `SkillsLoader.get_always_skills()` la incluye automaticamente sin
    `load_skill`.
    """
    prompt = _build_prompt()
    # Primera vela del catalogo y precio en COP.
    assert "Cruz de Vida" in prompt
    assert "$17,000" in prompt or "17,000" in prompt


def test_pago_contra_entrega_in_system_prompt() -> None:
    """Politica `Pago Contra Entrega` cruza al system prompt via la skill."""
    prompt = _build_prompt()
    # Tokens especificos de POLICIES en hubara_catalog/SKILL.md.
    assert "Contra Entrega" in prompt or "contra entrega" in prompt.lower()
    assert "$45,000" in prompt or "45,000" in prompt


def test_envio_gratis_rule_in_system_prompt() -> None:
    """Regla critica de AGENTS.md: envio gratis SOLO si el cliente menciono "caro".

    Tokens especificos: la palabra "caro" entre comillas (gatillo) y "Envio
    Gratis" (la unica promocion permitida bajo esa condicion). La presencia
    de ambos prueba que la regla cruza al system prompt — antes vivia en
    `shared_brain/instructions.md` y se inyectaba via plugin_context.
    """
    prompt = _build_prompt()
    assert "caro" in prompt.lower()
    assert "Envío Gratis" in prompt or "envío gratis" in prompt.lower()
