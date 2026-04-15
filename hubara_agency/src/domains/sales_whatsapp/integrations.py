import httpx
from src.core.config import WHATSAPP_ACCESS_TOKEN, WHATSAPP_API_URL

async def send_message(phone_number_id: str, to: str, text: str) -> None:
    """Envía un mensaje de texto plano a un usuario a través del API Oficial de WhatsApp Cloud."""
    if not WHATSAPP_ACCESS_TOKEN:
        print(f"⚠️ Fake Send -> [To: {to}]: {text}")
        return

    url = WHATSAPP_API_URL.format(phone_number_id=phone_number_id)
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text}
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data)
        if response.status_code == 200:
            print(f"📤 WhatsApp Reply OK a {to}")
        else:
            print(f"❌ Failed to reply: {response.text}")
