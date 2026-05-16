from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# PR2: imports apuntan a la nueva ubicación bajo `src.plugins.chats.*`. PR3
# reemplazará estos imports estáticos por auto-discovery sobre los manifests
# en `frontend_dashboard/src/plugins/<id>/plugin.yaml`. Ver
# PLUGIN_REFACTOR_PLAN.md §3 PR2 / PR3.
from src.plugins.chats.api import sales as chats_sales_api
from src.plugins.chats.api import dashboard as chats_dashboard_api
from src.plugins.chats.api import handoff as chats_dashboard_handoff

app = FastAPI(
    title="Agency API",
    description="Entrada centralizada para todos los webhooks y canales asíncronos interactuando con Temporal Workers.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registramos los adaptadores de Canales basados en Dominio.
# PR2: prefijos y tags se preservan idénticos a antes para compat 1:1 con
# clientes (frontend, Meta webhooks). PR3 los moverá al `legacy_routers`
# bloque del manifest.
app.include_router(chats_sales_api.router, prefix="/api", tags=["WhatsApp_Sales_Domain"])
app.include_router(chats_dashboard_api.router, prefix="/api/dashboard", tags=["Dashboard"])
# Handoff humano: intervenir / mandar mensaje / devolver al bot. Mismo prefix
# `/api/dashboard` para que el frontend tenga un solo namespace.
app.include_router(chats_dashboard_handoff.router, prefix="/api/dashboard", tags=["Dashboard_Handoff"])

@app.get("/")
def health_check():
    """Liveness probe. Indica que la interfaz web está lista."""
    return {"status": "ok", "agency_agentic": "active", "temporal_connection": "delegated_to_routes"}
