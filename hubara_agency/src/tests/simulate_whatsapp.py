import httpx
import asyncio
import structlog

logger = structlog.get_logger()

async def test():
    # Este es el formato EXACTO de JSON que los servidores de Meta envían a tu webhook.
    body = {
      "object": "whatsapp_business_account",
      "entry": [{
          "id": "12345",
          "changes": [{
              "value": {
                  "messaging_product": "whatsapp",
                  "metadata": {"display_phone_number": "12345", "phone_number_id": "12345"},
                  "contacts": [{"profile": {"name": "Test User"}, "wa_id": "5215555555"}],
                  "messages": [{
                      "from": "5218888888",
                      "id": "wamid.123",
                      "timestamp": "1600000000",
                      "text": {"body": "¡Hola! Estoy probando el Hola Mundo de Hubara."},
                      "type": "text"
                  }]
              },
              "field": "messages"
          }]
      }]
    }

    async with httpx.AsyncClient() as client:
        logger.info("TEST: Disparando Webhook hacia el Servidor (Uvicorn)...")
        try:
            response = await client.post("http://localhost:8000/api/webhook", json=body)
            logger.info("Respuesta del API (Inmediata)", status_code=response.status_code)
            logger.info("Ahora, mira la terminal del Worker de Ventas. Debería procesar con Temporal e imprimir los logs.")
        except httpx.ConnectError:
            logger.error("Error: No puedo conectar. ¿Está prendido el servidor Uvicorn? (uv run uvicorn src.main:app)")

if __name__ == "__main__":
    asyncio.run(test())
