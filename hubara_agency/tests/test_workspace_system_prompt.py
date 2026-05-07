"""PR-B regression test — system prompt diff = 0 vs PR-A.

Verifica que el system prompt construido desde el workspace canonico de Sales
(`hubara_agency/src/sales_whatsapp/workspace/`) contiene el mismo
contenido de identidad / tono / catalogo que el sistema viejo (shared_brain/*.md
inyectado via `plugin_context`).

No instanciamos `DefaultConversation.create` porque su firma exige
`provider`/`model` (un LLMProvider real). El componente que efectivamente
construye el system prompt es `ContextBuilder`, asi que lo instanciamos
directamente con el workspace.

Cubre:
- IDENTITY.md presente en el system prompt (asegura que el rol del agente cruzo
  el switchover).
- SOUL.md presente (tono / formato — la regla del doble salto de linea sigue
  llegando al LLM).
- AGENTS.md presente (operativa, escalacion, ghosting).
- TOOLS.md presente (taxonomia de tags + reglas de cierre).
- USER.md presente (tenant defaults — Hubara, COP, envios nacionales).
- skill `hubara_catalog` con `metadata.exoclaw.always = true` se inyecta cada
  turno (catalogo de productos visible sin tener que llamar `load_skill`).
"""
from __future__ import annotations

from pathlib import Path

from exoclaw_conversation.context import ContextBuilder

WORKSPACE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "sales_whatsapp"
    / "workspace"
)


def _build_prompt() -> str:
    """Build the system prompt the way `build_prompt` activity will at runtime.

    Importante: pasamos `extra_context=None` para simular el flujo PR-B donde
    el `plugin_context` del path Sales viaja como `None`. Si la suite tuviera
    que sumar memory/A-MEM, vendria via `turn_context`, no `extra_context`.
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
    """IDENTITY.md cruza al system prompt."""
    prompt = _build_prompt()
    # Tokens unicos de IDENTITY.md ("Asesor Exclusivo de Ventas de Hubara").
    assert "Asesor" in prompt
    assert "Hubara" in prompt


def test_soul_in_system_prompt() -> None:
    """SOUL.md cruza al system prompt — tono y reglas de formato."""
    prompt = _build_prompt()
    # Token de SOUL.md (la regla del doble salto de linea para fragmentar
    # mensajes en WhatsApp).
    assert "WhatsApp" in prompt
    assert "DOBLE SALTO DE L" in prompt or "doble salto de l" in prompt.lower()


def test_agents_in_system_prompt() -> None:
    """AGENTS.md cruza al system prompt — operativa y escalacion."""
    prompt = _build_prompt()
    # Tokens de AGENTS.md: ghosting, escalacion.
    assert "ghosting" in prompt.lower() or "Ghosting" in prompt
    assert "Escalaci" in prompt  # "Escalación"


def test_tools_in_system_prompt() -> None:
    """TOOLS.md cruza al system prompt — tag taxonomy + closing rules."""
    prompt = _build_prompt()
    # Tokens de TOOLS.md: la taxonomia de tags es OBLIGATORIA al cierre.
    assert "INTERESADO" in prompt
    assert "COMPRA_EXITOSA" in prompt or "RECHAZO" in prompt


def test_user_profile_in_system_prompt() -> None:
    """USER.md cruza al system prompt — tenant defaults."""
    prompt = _build_prompt()
    # Tokens de USER.md.
    assert "America/Bogota" in prompt or "Hubara" in prompt
    assert "COP" in prompt


def test_catalog_skill_loads_always() -> None:
    """Cruz de Vida (primera vela del catalogo) y precio aparecen en cada turno.

    `skills/hubara_catalog/SKILL.md` esta marcada `metadata.exoclaw.always = true`
    (ADR-2026-05-06-02), asi que `SkillsLoader.get_always_skills()` la incluye
    automaticamente sin pasar por `load_skill`.
    """
    prompt = _build_prompt()
    # La primera vela del catalogo y su precio en COP.
    assert "Cruz de Vida" in prompt
    assert "$17,000" in prompt or "17,000" in prompt


def test_catalog_policies_in_system_prompt() -> None:
    """Politicas de envio/pago del catalogo aparecen sin `load_skill`."""
    prompt = _build_prompt()
    # Pago contra entrega solo > $45,000 COP.
    assert "Contra Entrega" in prompt or "contra entrega" in prompt.lower()
    assert "$45,000" in prompt or "45,000" in prompt
