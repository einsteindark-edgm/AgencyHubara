from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.sales_whatsapp import api as whatsapp_api
from src.dashboard import api as dashboard_api
from src.dashboard import handoff as dashboard_handoff

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
app.include_router(whatsapp_api.router, prefix="/api", tags=["WhatsApp_Sales_Domain"])
app.include_router(dashboard_api.router, prefix="/api/dashboard", tags=["Dashboard"])
# Handoff humano: intervenir / mandar mensaje / devolver al bot. Mismo prefix
# `/api/dashboard` para que el frontend tenga un solo namespace.
app.include_router(dashboard_handoff.router, prefix="/api/dashboard", tags=["Dashboard_Handoff"])

@app.get("/")
def health_check():
    """Liveness probe. Indica que la interfaz web está lista."""
    return {"status": "ok", "agency_agentic": "active", "temporal_connection": "delegated_to_routes"}
