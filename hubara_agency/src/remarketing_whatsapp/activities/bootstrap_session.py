"""Activities especificas del dominio Remarketing.

Aqui viven los "puentes" entre el workflow y los prompts puros (``prompts.py``).
El proposito es mantener el workflow fino (driving adapter) y permitir que los
prompts evolucionen sin tocar la shape de history (la activity stub se
mantiene).

PR-E (ADR-2026-05-06-11): este archivo se movio de ``activities.py``
(top-level) a ``activities/bootstrap_session.py`` (sub-folder), espejo de
sales_whatsapp PR-E (ADR-2026-05-06-07). El re-export en
``activities/__init__.py`` preserva el import path publico
``from src.remarketing_whatsapp.activities import ...``.

PR-D global cleanup (ADR-2026-05-06-10): ya no existen `REMARKETING_BRAIN_DIR`,
el import de `load_brain` ni la `@activity.defn load_remarketing_brain_activity`.
Tras la migracion DEHA workspace (PR-B), el workflow ya no las invoca; las
fixtures se regeneraron a v3 (sin event `load_remarketing_brain_activity`).
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
from src.remarketing_whatsapp.contracts import RemarketingSessionInput
from src.remarketing_whatsapp.prompts import build_remarketing_trigger


@activity.defn(name="build_remarketing_trigger_activity")
async def build_remarketing_trigger_activity(motivo: str, memory_context: str) -> str:
    """Devuelve el prompt proactivo inicial del agente de Remarketing."""
    return build_remarketing_trigger(motivo, memory_context)


@activity.defn(name="bootstrap_remarketing_session_activity")
async def bootstrap_remarketing_session_activity(
    input: RemarketingSessionInput,
) -> SessionInput:
    """Construye el `SessionInput` JSON-safe del agente de Remarketing.

    Saca del @workflow.run la I/O de filesystem y la construccion del registry
    de tools. Replica fuera del workflow lo que Sales ya hace en su composition
    root (service.py).

    PR-A workspace refactor: la signature cambia a aceptar el
    `RemarketingSessionInput` completo (en lugar de `(session_id, motivo)`)
    para (a) hacer la activity extensible y (b) llevar
    `runtime_workspace_path` a traves del boundary (R-JSON).

    PR-B switchover (analogo a sales_whatsapp PR-B / ADR-2026-05-06-04): el
    body ahora construye `WorkspaceConfig(path=input.runtime_workspace_path)` —
    el workspace canonico del agente de Remarketing (donde viven IDENTITY.md,
    SOUL.md, USER.md, TOOLS.md, AGENTS.md, memory/* y skills/*) es el unico
    canal por el que la identidad / tono / catalogo entran al workflow. Failfast
    con `RuntimeError` si `runtime_workspace_path` falta — surface composition-
    root miswires antes del `build_prompt` activity (que produciria un system
    prompt vacio). El per-session vault (`WORKSPACE_VAULT_DIR / session_id`)
    sigue siendo home de `MessageHistoryStore` JSONL y `metadata.json`, pero
    esos los maneja el filesystem adapter, no este workspace.

    Es responsabilidad del composition root (PR-A) cablear el path:
    `dispatcher_activities.py:schedule_remarketing_workflow_activity` lee
    `EXOCLAW_WORKSPACE_REMARKETING` via `config/env.py:get_workspace_path()`
    y propaga el path como string en `RemarketingSessionInput.runtime_workspace_path`
    (R-JSON).

    Inputs cruzan el boundary como dataclass plano (R-JSON). El output tambien.
    """
    session_id = input.session_id

    # PR-B: el workspace que ve el runtime es el canonico del agente.
    # Sin path nadie cabledo el composition root correctamente — failfast
    # antes de llegar al `build_prompt` y emitir un system prompt vacio.
    runtime_path = input.runtime_workspace_path
    if not runtime_path:
        raise RuntimeError(
            "runtime_workspace_path missing on RemarketingSessionInput — "
            "schedule_remarketing_workflow_activity must wire it via "
            "remarketing_whatsapp.config.env.get_workspace_path() (PR-A)."
        )

    llm = build_default_llm_config()
    ws = WorkspaceConfig(path=runtime_path)
    activity.logger.info(
        "bootstrap_remarketing_session_activity: workspace=%s",
        runtime_path,
    )

    # El registry de tools sigue apuntando al workspace canonico tambien.
    registry = get_base_tools_registry(Path(ws.path))

    # POST-MORTEM (mismo que Sales): aplicar tool extensions ANTES de generar
    # el `tool_definitions_json`. Sin esto, las tools registradas en
    # `worker.py` (`register_tool_extension`) no llegan al LLM via system
    # prompt — viajan vacias y el LLM no sabe que existen. `execute_tool`
    # las aplica al despachar (`platform/temporal/activities.py:33`), pero
    # el LLM nunca las llamaria si el bootstrap no se las anuncia.
    apply_tool_extensions(registry, Path(ws.path))

    return SessionInput(
        session_id=session_id,
        channel="whatsapp",
        chat_id=session_id,
        llm=llm,
        workspace=ws,
        tool_definitions_json=get_base_tools_json(registry),
    )


# PR-D global cleanup (ADR-2026-05-06-10): la `@activity.defn load_remarketing_brain_activity`
# fue eliminada. PR-B la mantuvo viva temporalmente como salvavidas de replay
# para histories v2 que aun la referenciaban; tras regenerar las fixtures a v3
# (workflow secuencia sin ese event), el codigo no tiene callers. Operacionalmente:
# antes de deployar, drenar la queue de Remarketing o usar versioned worker.
