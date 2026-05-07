import json
from pathlib import Path
from exoclaw.agent.tools.registry import ToolRegistry
from exoclaw_temporal.config import LLMConfig, WorkspaceConfig

from src.core.config import API_BASE_LLMLITE ,DEFAULT_LLM_MODEL, DEEPSEEK_API_KEY, WORKSPACE_VAULT_DIR

# Importaremos tools nativas o custom más adelante
from exoclaw_tools_workspace.filesystem import ReadFileTool, WriteFileTool
from src.domains.sales_whatsapp.tools.tags import ManageConversationTagTool

def build_default_llm_config() -> LLMConfig:
    """Configuración inyectable base para el motor LLM."""
    return LLMConfig(
        model=DEFAULT_LLM_MODEL,
        api_key=DEEPSEEK_API_KEY,
        api_base=API_BASE_LLMLITE,
        temperature=0.1,
        max_tokens=4096,
        max_iterations=10 # Cap the iterations for general tasks
    )

def build_workspace_config(session_id: str) -> WorkspaceConfig:
    """Aísla dinámicamente un workspace basado en la sesión."""
    session_vault = WORKSPACE_VAULT_DIR / session_id
    session_vault.mkdir(parents=True, exist_ok=True)
    return WorkspaceConfig(path=str(session_vault))

def get_base_tools_registry(workspace_path: Path) -> ToolRegistry:
    """Inicializa localmente las herramientas que el Agente usará en esta sesión."""
    registry = ToolRegistry()
    registry.register(WriteFileTool(workspace=workspace_path))
    registry.register(ReadFileTool(workspace=workspace_path))
    registry.register(ManageConversationTagTool(workspace=str(workspace_path)))
    # Para el proyecto serio, puedes registrar herramientas CRM aquí.
    return registry

def get_base_tools_json(registry: ToolRegistry) -> str:
    """Retorna la versión cruda determinista para inyectar en Temporal"""
    return json.dumps(registry.get_definitions())
