import json
from pathlib import Path
from pydantic import Field
from exoclaw.agent.tools import Tool, ToolContext
from src.core.temporal_client import get_temporal_client
from temporalio.exceptions import WorkflowAlreadyStartedError
from exoclaw_temporal.config import SessionInput


class TransferToSalesAgentTool(Tool):
    """Herramienta estricta para el Agente de Remarketing: Úsala SOLO cuando el usuario responde a tu mensaje de reactivación y debes pasarle la conversación al agente de Ventas principal."""
    
    name = "transfer_to_sales_agent"
    description = "Transfiere el control de la conversación al Agente de Ventas devolviendo la sesión."

    resumen: str = Field(..., description="Breve resumen de 1 sola línea sobre lo que dijo el usuario para contextualizar al agente de ventas.")
    
    workspace: str = Field("", description="Internal: Do not provide", exclude=True)

    def __init__(self, workspace: str, **kwargs):
        super().__init__(**kwargs)
        self.workspace = workspace

    async def execute(self, ctx: ToolContext = None, resumen: str = None, **kwargs) -> str:
        _resumen = resumen if isinstance(resumen, str) else kwargs.get('resumen', 'El cliente volvió a interactuar')
        
        vault = Path(self.workspace)
        metadata_file = vault / "metadata.json"
        
        data = {}
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
        
        data["active_route"] = "ventas"
        data["tag"] = "RETOMA_VENTA"
        data["motivo"] = _resumen
        
        if "status_history" not in data:
            data["status_history"] = []
            
        import time
        data["status_history"].append({
            "tag": "RETOMA_VENTA",
            "motivo": _resumen,
            "active_route": "ventas",
            "timestamp": time.time()
        })
        
        metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        
        # PROACTIVAMENTE Despertar al de Ventas!
        session_id = ctx.session_key if ctx else kwargs.get("session_id", vault.name)
        client = await get_temporal_client()
        workflow_id = f"session-{session_id}"
        
        # Importación diferida para evitar ciclos circulares
        from src.domains.sales_whatsapp.workflows.sales_session import HubaraSalesSessionWorkflow
        from src.core.registries import build_default_llm_config, build_workspace_config, get_base_tools_json, get_base_tools_registry
        from temporalio.client import WorkflowExecutionStatus
        
        try:
            handle = client.get_workflow_handle(workflow_id)
            desc = await handle.describe()  
            if desc.status != WorkflowExecutionStatus.RUNNING:
                raise RuntimeError("Ventas no corre")
        except Exception:
            llm = build_default_llm_config()
            ws = build_workspace_config(session_id)
            registry = get_base_tools_registry(Path(ws.path))
            
            try:
                handle = await client.start_workflow(
                    HubaraSalesSessionWorkflow.run,
                    SessionInput(
                        session_id=session_id,
                        channel="whatsapp",
                        chat_id=session_id,
                        llm=llm,
                        workspace=ws,
                        tool_definitions_json=get_base_tools_json(registry)
                    ),
                    id=workflow_id,
                    task_queue="queue-sales-agent",
                )
            except WorkflowAlreadyStartedError:
                handle = client.get_workflow_handle(workflow_id)

        # Enviamos señal interna simulada como si el Router hablara para que Ventas proceda
        plugin_context = None # El workflow cargará su propio context
        await handle.signal(
            HubaraSalesSessionWorkflow.send_message,
            args=[f"[SISTEMA INTERNO]: Remarketing acaba de recuperar al cliente. El usuario dijo: '{_resumen}'. TU TURNO. SALUDA AL CLIENTE Y RETOMA LA VENTA COMO SI NADA HUBIERA PASADO.", None, plugin_context]
        )
        
        return "El control ha sido transferido. NO generes más texto, responde vacío o con 'Ok' para finalizar."
