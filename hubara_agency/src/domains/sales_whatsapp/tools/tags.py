import json
from pathlib import Path
from datetime import timedelta
from pydantic import Field
from exoclaw.agent.tools import Tool, ToolContext

from src.core.temporal_client import get_temporal_client
from temporalio.exceptions import WorkflowAlreadyStartedError

class ManageConversationTagTool(Tool):
    """Herramienta obligatoria para registrar la etiqueta comercial final de una conversación y el motivo de esa etiqueta."""
    
    name = "manage_conversation_tag"
    description = "Úsala al final de la venta, o si el usuario pierde el interés. Registra la etiqueta final ('INTERESADO', 'RECHAZO', 'COMPRA_EXITOSA') y un resumen del motivo."

    tag: str = Field(..., description="La etiqueta a aplicar (ej. 'INTERESADO', 'RECHAZO', 'COMPRA_EXITOSA').")
    motivo: str = Field(..., description="Resumen breve, de máximo 2 líneas, de por qué se puso esta etiqueta dado el contexto del char.")
    
    # Internal fields no longer populated by Pydantic directly for Tool parameters. 
    # Because Exoclaw Tool maps Pydantic fields to the LLM json schema.
    # We must use `ctx` for dynamic variables!
    workspace: str = Field("", description="Internal: Do not provide", exclude=True)

    def __init__(self, workspace: str, **kwargs):
        super().__init__(**kwargs)
        self.workspace = workspace

    async def execute(self, ctx: ToolContext = None, tag: str = None, motivo: str = None, **kwargs) -> str:
        _tag = tag if isinstance(tag, str) else kwargs.get('tag')
        if not isinstance(_tag, str):
            _tag = 'INTERESADO'
            
        _motivo = motivo if isinstance(motivo, str) else kwargs.get('motivo')
        if not isinstance(_motivo, str):
            _motivo = 'Cierre automatico por inactividad'
        
        # 1. Guardar metadatos en JSON en el PVC particular de esta sesion
        vault = Path(self.workspace)
        metadata_file = vault / "metadata.json"
        
        data = {}
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
        
        data["tag"] = _tag
        data["motivo"] = _motivo
        metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        
        session_id = ctx.session_key if ctx else kwargs.get("session_id", "wa_unknown")
        
        # 2. Trigger Remarketing Workflow if tag indicates need for follow-up
        if str(_tag).upper() == "INTERESADO":
            client = await get_temporal_client()
            
            # En entorno de dev agendamos para dentro de 60 segundos por facilidad de pruebas. En pro se usarian `timedelta(days=2)`
            delay = timedelta(seconds=60)
            
            try:
                await client.start_workflow(
                    "RemarketingWorkflow",
                    args=[session_id, _motivo],
                    id=f"remarketing-{session_id}",
                    task_queue="queue-remarketing-agent",
                    start_delay=delay
                )
            except WorkflowAlreadyStartedError:
                pass
            
            
        return f"Éxito. Interacción etiquetada como '{_tag}'. Si fue 'INTERESADO', se programó un ciclo de remarketing automáticamente."
