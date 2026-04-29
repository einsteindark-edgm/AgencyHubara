from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from src.core.config import WHATSAPP_VERIFY_TOKEN
from src.domains.sales_whatsapp import service as whatsapp_service
from src.domains.sales_whatsapp.parsers import parse_whatsapp_inbound
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

    try:
        parsed = parse_whatsapp_inbound(body)
    except ValueError as exc:
        logger.warning("Malformed WhatsApp webhook body", error=str(exc))
        raise HTTPException(status_code=400, detail=f"malformed payload: {exc}")

    if parsed is None:
        # Status update or other non-message event: ack 200 without dispatch.
        return {"status": "ok"}

    background_tasks.add_task(whatsapp_service.process_incoming_message, parsed)
    return {"status": "ok"}
