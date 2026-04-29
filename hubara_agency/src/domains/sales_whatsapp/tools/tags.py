import json
import time
from pathlib import Path
from pydantic import Field
from exoclaw.agent.tools import Tool, ToolContext

from src.core.constants import ROUTE_VENTAS


class ManageConversationTagTool(Tool):
    """Herramienta obligatoria para registrar la etiqueta comercial final de una conversación y el motivo de esa etiqueta."""

    name = "manage_conversation_tag"
    description = "Úsala al final de la venta, o si el usuario pierde el interés. Registra la etiqueta final ('INTERESADO', 'RECHAZO', 'COMPRA_EXITOSA') y un resumen del motivo."

    tag: str = Field(..., description="La etiqueta a aplicar (ej. 'INTERESADO', 'RECHAZO', 'COMPRA_EXITOSA').")
    motivo: str = Field(..., description="Resumen breve, de máximo 2 líneas, de por qué se puso esta etiqueta dado el contexto del char.")

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

        vault = Path(self.workspace)
        metadata_file = vault / "metadata.json"

        data = {}
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))

        data["tag"] = _tag
        data["motivo"] = _motivo

        if "status_history" not in data:
            data["status_history"] = []

        data["status_history"].append({
            "tag": _tag,
            "motivo": _motivo,
            "active_route": data.get("active_route", ROUTE_VENTAS),
            "timestamp": time.time()
        })

        metadata_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        session_id = ctx.session_key if ctx else kwargs.get("session_id", "wa_unknown")

        # ADR-001: la tool no abre temporal_client. Si el tag indica seguimiento,
        # devuelve una decision serializada para que el workflow programe el remarketing.
        response: dict = {
            "message": f"Éxito. Interacción etiquetada como '{_tag}'.",
        }
        if str(_tag).upper() == "INTERESADO":
            response["schedule_remarketing"] = {
                "session_id": session_id,
                "motivo": _motivo,
                "delay_seconds": 60,
            }
            response["message"] += " Se programó un ciclo de remarketing automáticamente."

        return json.dumps(response, ensure_ascii=False)
