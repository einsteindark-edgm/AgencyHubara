import json
from pathlib import Path
from exoclaw.agent.tools.registry import ToolRegistry
from exoclaw_temporal.config import LLMConfig, WorkspaceConfig

from src.platform.config import API_BASE_LLMLITE ,DEFAULT_LLM_MODEL, DEEPSEEK_API_KEY, WORKSPACE_VAULT_DIR

# PR-G post-mortem (workflow session-wa_573125671604):
# Las tools `ReadFileTool` / `WriteFileTool` de `exoclaw_tools_workspace.filesystem`
# se quitaron del registry base porque son foot-guns: el LLM las usa para
# bypass-ear las tools de dominio. En aquel workflow el LLM, en vez de llamar
# `manage_conversation_tag(tag="INTERESADO", ...)` (que emite el envelope
# `schedule_remarketing` y dispara el RemarketingSessionWorkflow), escribió a
# mano `metadata.json` con `write_file` — la metadata quedó persistida pero
# NUNCA se programó el remarketing.
#
# Regla: el registry base es vacío por defecto. Cada dominio registra sus
# tools en su propio `worker.py` via `register_tool_extension(...)`. Si algún
# dominio genuinamente necesita filesystem access, lo registra explícito.

# PR-C: `ManageConversationTagTool` ya no se importa aqui (DIP fix). Su
# registracion vive en `src/sales_whatsapp/worker.py` via
# `register_tool_extension(...)`. `platform/` no debe conocer tools de dominios.

def build_default_llm_config() -> LLMConfig:
    """Configuración inyectable base para el motor LLM.

    Tuneada para DeepSeek V4 Pro (`deepseek-v4-pro` en litellm_config.yaml):
    priorizamos PRECISIÓN sobre velocidad/costo.

    - reasoning_effort="high": activa thinking mode de V4 Pro. La litellm 1.82.6
      colapsa cualquier valor graduado a `thinking:{"type":"enabled"}`
      (DeepSeekChatConfig.map_openai_params), así que el server corre Think High
      (su default con thinking on). "xhigh"/Think Max requiere upgrade de litellm.
      El round-trip de `reasoning_content` (requerido por V4 Pro en multi-turn,
      400 si falta) lo maneja exoclaw via LLMResponseData.to_assistant_message().
    - max_tokens=32768: el CoT de thinking cuenta contra el output; 4096 truncaba
      el razonamiento. Es un CAP, no un target — el texto final a WhatsApp lo
      gobierna el prompt, no este valor.
    - temperature=0.1: V4 Pro la ignora en thinking mode, pero la respeta el
      fallback `gemini-backup`.
    """
    return LLMConfig(
        model=DEFAULT_LLM_MODEL,
        api_key=DEEPSEEK_API_KEY,
        api_base=API_BASE_LLMLITE,
        temperature=0.1,
        max_tokens=32768,
        reasoning_effort="high",
        max_iterations=10 # Cap the iterations for general tasks
    )

def build_workspace_config(session_id: str) -> WorkspaceConfig:
    """Aísla dinámicamente un workspace basado en la sesión.

    DEPRECATED — PR-D global cleanup (ADR-2026-05-06-10): los callers de
    runtime (`bootstrap_sales_session_activity`, `bootstrap_remarketing_session_activity`)
    ya no usan esta funcion — instancian `WorkspaceConfig(path=runtime_workspace_path)`
    directo desde el campo del DTO. Sobrevive como dependencia estructural de
    `core/infrastructure/adapters/tool_registry_adapter.py:DefaultToolRegistry`
    y `core/ports/tool_registry.py:ToolRegistryPort` (orphan adapter/port —
    candidato a borrar en una limpieza futura del Protocol/Adapter triad).
    """
    session_vault = WORKSPACE_VAULT_DIR / session_id
    session_vault.mkdir(parents=True, exist_ok=True)
    return WorkspaceConfig(path=str(session_vault))

def get_base_tools_registry(workspace_path: Path) -> ToolRegistry:
    """Registry base — VACÍO por defecto.

    Tools de filesystem (`read_file`/`write_file` de `exoclaw_tools_workspace`)
    NO se registran globalmente porque son foot-guns: el LLM las usa para
    bypass-ear las tools de dominio (ver post-mortem arriba).

    Las tools de dominio se registran en `<agent>/worker.py` via
    `register_tool_extension(...)`. El dispatch lo hace
    `src/platform/temporal/activities.py:execute_tool` aplicando
    `apply_tool_extensions(registry, workspace_path)` sobre este registry vacío.

    Si algún dominio genuinamente necesita read/write file (ej: un agente que
    expone datos al LLM), debe registrar las tools explícitamente con scope
    restringido al subset de paths que su flow legítimo necesita — nunca
    el registry global sin restricciones.
    """
    return ToolRegistry()

def get_base_tools_json(registry: ToolRegistry) -> str:
    """Retorna la versión cruda determinista para inyectar en Temporal"""
    return json.dumps(registry.get_definitions())
