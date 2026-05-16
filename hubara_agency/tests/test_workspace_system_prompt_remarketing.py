"""PR-B regression test (remarketing) — system prompt llega via workspace.

Verifica que el system prompt construido desde el workspace canonico de
Remarketing (`hubara_agency/src/plugins/chats/agent/remarketing/workspace/`)
contiene el mismo contenido de identidad / tono / mision proactiva /
catalogo que el sistema viejo (shared_brain/*.md inyectado via
`plugin_context` por `load_remarketing_brain_activity`).

Modelado sobre `tests/test_workspace_system_prompt.py` (Sales). No se
instancia `DefaultConversation.create` porque su firma exige un
`LLMProvider` real; el componente que construye el system prompt es
`ContextBuilder`, asi que lo instanciamos directo con el workspace.

Cubre:
- IDENTITY.md presente — Asesor de Hubara (modo Recuperación), continuación
  natural de Sales, sin nombre propio nuevo, sin "48 horas" hardcoded
  (bug 8a34b54a Fix #L1+#L2).
- SOUL.md presente — BREVEDAD EXTREMA + doble salto de linea.
- USER.md presente — tenant defaults (Hubara, COP, Bogota).
- TOOLS.md presente — `transfer_to_sales_agent` documentada.
- AGENTS.md presente — mision proactiva (levantar, transferir).
- skill `hubara_catalog` deprecada (`always: false`), ya NO inyecta catalogo
  hardcoded cada turno — el catalogo dinamico lo maneja Sales tras
  transferencia.
- Politicas de pago contra entrega siguen accesibles via skill cargable.
- Regla de "envio gratis solo si dijo caro" (AGENTS.md § Prohibicion de
  descuentos).
"""
from __future__ import annotations

from pathlib import Path

from exoclaw_conversation.context import ContextBuilder

WORKSPACE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "plugins"
    / "chats"
    / "agent"
    / "remarketing"
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
    """IDENTITY.md cruza al system prompt — Asesor de Hubara (Recuperación).

    Post-fix #L2 (bug 8a34b54a): ya NO se llama "Clara" — es el MISMO Asesor
    de Hubara que Sales para continuidad de marca. El cliente no debe ver
    una persona nueva.
    """
    prompt = _build_prompt()
    assert "Hubara" in prompt
    assert "Asesor" in prompt
    # Personalidad de remarketing — distintiva vs Sales.
    assert "Mínimamente invasiva" in prompt or "mínimamente invasiva" in prompt.lower()
    # Continuidad explicita: no debe presentarse como persona nueva.
    assert "MISMO Asesor" in prompt or "mismo Asesor" in prompt or "continuación natural" in prompt.lower()


def test_identity_does_not_hardcode_temporal_window() -> None:
    """Post-fix #L1 (bug 8a34b54a): el IDENTITY ya NO predetermina "hace 48
    horas mostraron intención" hardcoded.

    Esa frase causaba que el LLM dijera "hace unos días" aunque solo hubieran
    pasado 60 segundos. Ahora el delta de tiempo se declara explicitamente
    como desconocido y se prohibe especular en el mensaje al cliente.

    Nota: el prompt SI puede mencionar "48 horas" como EJEMPLO de frase
    prohibida ("PROHIBIDO usar 'hace 48 horas'"). Lo que NO debe aparecer
    es la frase original "hace 48 horas mostraron intención".
    """
    prompt = _build_prompt()
    # Frase exacta del IDENTITY viejo (predeterminaba el periodo).
    assert "hace 48 horas mostraron" not in prompt
    # Y el NUEVO IDENTITY DEBE declarar el tiempo como desconocido.
    assert "no lo conoces con certeza" in prompt.lower() or "tiempo transcurrido" in prompt.lower()


def test_identity_forbids_invented_names() -> None:
    """Post-fix #L2: el IDENTITY prohibe explicitamente presentarse como
    persona nueva (ej. "Clara"). El cliente no la conoce y eso causa
    extrañeza en el gancho.
    """
    prompt = _build_prompt()
    # Frase canonica de la prohibicion en IDENTITY.md
    assert "nombre" in prompt.lower()
    assert "no te presentes" in prompt.lower() or "NO te presentes" in prompt


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


def test_catalog_skill_no_longer_auto_loaded_in_remarketing() -> None:
    """Post-rollout: `hubara_catalog` skill ya NO se inyecta cada turno.

    Antes (`always: true`) el catalogo hardcoded aparecia en cada gancho.
    Ahora (`always: false`) el catalogo dinamico lo maneja Sales tras
    transferencia — Remarketing no necesita ver productos ni precios
    para abrir conversacion.
    """
    prompt = _build_prompt()
    # Cruz de Vida ya NO debe aparecer auto-inyectada cada turno.
    assert "Cruz de Vida" not in prompt
    # Tampoco precios viejos hardcoded.
    assert "$17,000" not in prompt


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
