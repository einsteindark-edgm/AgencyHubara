"""Activities especificas del dominio Remarketing."""
from __future__ import annotations

from pathlib import Path

from temporalio import activity

from exoclaw_temporal.config import SessionInput

from src.core.brains import load_brain
from src.core.registries import (
    build_default_llm_config,
    build_workspace_config,
    get_base_tools_json,
    get_base_tools_registry,
)
from src.domains.remarketing_whatsapp.domain.policies.prompts import build_remarketing_trigger


# Definimos la dir del brain compartido aqui para que sea accesible tanto al workflow
# (solo via `imports_passed_through` para tipado) como a la activity de carga.
REMARKETING_BRAIN_DIR = Path(__file__).parent / "shared_brain"


@activity.defn(name="build_remarketing_trigger_activity")
async def build_remarketing_trigger_activity(motivo: str, memory_context: str) -> str:
    """Devuelve el prompt proactivo inicial del agente de Remarketing."""
    return build_remarketing_trigger(motivo, memory_context)


@activity.defn(name="bootstrap_remarketing_session_activity")
async def bootstrap_remarketing_session_activity(session_id: str, motivo: str) -> SessionInput:
    """Construye el `SessionInput` JSON-safe del agente de Remarketing.

    Saca del @workflow.run la I/O de filesystem (`build_workspace_config` hace
    `Path.mkdir`) y la construccion del registry de tools. Replica fuera del
    workflow lo que Sales ya hace en su composition root (service.py).

    Inputs son primitivos (str). El output es un dataclass plano
    JSON-serializable que cruza la frontera del workflow (R-JSON).
    """
    # `motivo` no se usa para construir el SessionInput; viaja por el DTO de boundary
    # y se consume mas adelante en el workflow (al armar el system trigger). Lo dejamos
    # explicito en la signature por simetria con el caller y para futura extensibilidad
    # (p.ej. tools que dependen del motivo).
    del motivo

    llm = build_default_llm_config()
    ws = build_workspace_config(session_id)  # mkdir OK aqui: estamos dentro de activity
    registry = get_base_tools_registry(Path(ws.path))

    return SessionInput(
        session_id=session_id,
        channel="whatsapp",
        chat_id=session_id,
        llm=llm,
        workspace=ws,
        tool_definitions_json=get_base_tools_json(registry),
    )


@activity.defn(name="load_remarketing_brain_activity")
async def load_remarketing_brain_activity() -> list[str]:
    """Carga el brain compartido del agente de Remarketing.

    Saca del @workflow.run la lectura de `identity.md`/`knowledge.md`/`instructions.md`
    (era el ultimo I/O dentro del workflow, deuda consciente de ADR-006).

    No recibe args: el `REMARKETING_BRAIN_DIR` es global del dominio. Si en el
    futuro se necesita parametrizar (p.ej. brains por subdominio), agregar
    `brain_dir: str` y resolver `Path(brain_dir)` aqui dentro.
    """
    return load_brain(REMARKETING_BRAIN_DIR)
