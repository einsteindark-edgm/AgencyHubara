"""Activities especificas del dominio Sales.

Aqui viven los "puentes" entre el workflow y los prompts puros (``prompts.py``).
El proposito es mantener los workflows finos (driving adapters) y permitir que
los prompts evolucionen sin tocar la shape de history (la activity stub se
mantiene).

PR-E: este archivo se movio de ``activities.py`` (top-level) a
``activities/bootstrap_session.py`` (sub-folder) para alinear con el patron
de un modulo por activity. El re-export en ``activities/__init__.py``
preserva el import path publico.
"""
from __future__ import annotations

from pathlib import Path

from temporalio import activity

from exoclaw_temporal.config import SessionInput, WorkspaceConfig

from src.platform.registries import (
    build_default_llm_config,
    get_base_tools_json,
    get_base_tools_registry,
)
from src.platform.tool_extensions import apply_tool_extensions
from src.sales_whatsapp.contracts import SalesSessionInput
from src.sales_whatsapp.prompts import build_ghosting_prompt


@activity.defn(name="decide_ghosting_action")
async def decide_ghosting_action() -> str:
    """Devuelve el prompt inyectado cuando se detecta ghosting.

    No tiene side effects ni I/O. Existe como activity (no como llamada directa
    en el workflow) para mantener los strings de negocio fuera del workflow code.
    """
    return build_ghosting_prompt()


@activity.defn(name="bootstrap_sales_session_activity")
async def bootstrap_sales_session_activity(input: SalesSessionInput) -> SessionInput:
    """Construye el `SessionInput` JSON-safe del agente de Sales.

    Saca del @workflow.run la I/O de filesystem (`build_workspace_config` hace
    `Path.mkdir`) y la construccion del registry de tools. Replica el patron
    aplicado a Remarketing (PR F6.1) — los callers (service.py, dispatcher
    activities) ya no construyen `SessionInput` antes de `start_workflow`,
    solo pasan `SalesSessionInput(session_id=..., runtime_workspace_path=...)`
    y el bootstrap se ejecuta como primera activity dentro del workflow
    (R-DET / R-JSON).

    Input: `SalesSessionInput` plano (R-JSON). Pasamos el dataclass completo
    (en lugar de `session_id: str`) para no romper el shape de la activity al
    agregar campos en futuras iteraciones — solo se anaden campos al DTO con
    defaults.

    `input.runtime_workspace_path` (PR-B): el path canonico del workspace del
    agente de Sales (donde viven IDENTITY.md, SOUL.md, USER.md, TOOLS.md,
    AGENTS.md, memory/* y skills/*). **A partir de PR-B este path es el que
    `SessionInput.workspace` reporta**, asi que `ContextBuilder.build_system_prompt`
    (en `build_prompt` activity) lee identidad/tono/catalogo desde el workspace
    canonico — NO mas desde `shared_brain/*.md` via `plugin_context`. El vault
    per-session (`WORKSPACE_VAULT_DIR / session_id`) sigue siendo la home de
    `MessageHistoryStore` y `MetadataStore` (JSONL de la conversacion,
    metadata.json), pero esos los maneja el filesystem adapter, no este
    workspace.

    Es responsabilidad del composition root (PR-A) cablear el path:
    `composition.py` lee `EXOCLAW_WORKSPACE_SALES` via `config/env.py` e
    instancia `WorkspaceConfig(path=...)`, propagado como string en
    `SalesSessionInput.runtime_workspace_path` (R-JSON).
    """
    session_id = input.session_id
    llm = build_default_llm_config()

    # PR-B: el workspace que ve el runtime es el canonico del agente.
    # Sin path nadie cabledo el composition root correctamente — failfast
    # antes de llegar al `build_prompt` y emitir un system prompt vacio.
    if input.runtime_workspace_path is None:
        raise RuntimeError(
            "runtime_workspace_path missing — composition.py debe wirearlo (PR-A). "
            "Reviser src/domains/sales_whatsapp/composition.py y "
            "src/core/infrastructure/temporal/dispatcher_activities.py."
        )

    ws = WorkspaceConfig(path=input.runtime_workspace_path)
    activity.logger.info(
        "bootstrap_sales_session_activity: workspace=%s",
        input.runtime_workspace_path,
    )

    # El registry de tools sigue apuntando al workspace canonico tambien;
    # PR-C revisara si las tools deben usar otro path (ej: per-session vault
    # para escrituras durables). Por ahora alineado con `ws.path`.
    registry = get_base_tools_registry(Path(ws.path))

    # POST-MORTEM workflow session-wa_573125671604: el bootstrap NO aplicaba
    # las extensions registradas en `worker.py` (`register_tool_extension`).
    # Resultado: `tool_definitions_json` viajaba VACIO al LLM, asi que el LLM
    # no veia ni `manage_conversation_tag` ni `transfer_to_sales_agent`. Antes
    # de Opcion A esto quedaba oculto porque `read_file`/`write_file` venian
    # en el base registry — el LLM "tenia algo" pero abusaba para bypass-ear
    # las tools de dominio. Con base vacio + sin extensions, el LLM no veia
    # nada. Fix: aplicar las extensions ANTES de generar el JSON, igual que
    # `execute_tool` lo hace al despachar (platform/temporal/activities.py:33).
    apply_tool_extensions(registry, Path(ws.path))

    return SessionInput(
        session_id=session_id,
        channel="whatsapp",
        chat_id=session_id,
        llm=llm,
        workspace=ws,
        tool_definitions_json=get_base_tools_json(registry),
    )
