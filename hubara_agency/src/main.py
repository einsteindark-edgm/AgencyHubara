from fastapi import FastAPI
from src.domains.sales_whatsapp import api as whatsapp_api

app = FastAPI(
    title="Agency API",
    description="Entrada centralizada para todos los webhooks y canales asíncronos interactuando con Temporal Workers.",
    version="1.0.0"
)

# Registramos los adaptadores de Canales basados en Dominio.
app.include_router(whatsapp_api.router, prefix="/api", tags=["WhatsApp_Sales_Domain"])

@app.get("/")
def health_check():
    """Liveness probe. Indica que la interfaz web está lista."""
    return {"status": "ok", "agency_agentic": "active", "temporal_connection": "delegated_to_routes"}
