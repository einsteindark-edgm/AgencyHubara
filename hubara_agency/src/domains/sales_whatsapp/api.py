from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from src.core.config import WHATSAPP_VERIFY_TOKEN
from src.domains.sales_whatsapp import service as whatsapp_service
import structlog

logger = structlog.get_logger()

router = APIRouter()

@router.get("/webhook")
async def verify_webhook(request: Request):
    """WhatsApp verification endpoint."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("WhatsApp Webhook Verified")
        return int(challenge)
    raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/webhook")
async def handle_whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receives JSON body directly and frees connection to prevent Timeout."""
    body = await request.json()
    
    # Únicamente enrutamos al Servicio, liberando inmediatamente a Meta HTTP
    background_tasks.add_task(whatsapp_service.process_incoming_message, body)
    return {"status": "ok"}
