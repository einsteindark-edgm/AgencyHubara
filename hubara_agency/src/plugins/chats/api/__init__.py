"""HTTP API del plugin `chats` — routers FastAPI.

Cada submódulo expone un ``router`` (APIRouter) que ``src/main.py`` registra
con su prefijo y tags propios:

- ``sales``: webhook de WhatsApp inbound (POST /webhook). Ingresa el mensaje
  al use case ``IngestInboundMessage`` que dispara/signaliza el workflow.
- ``dashboard``: SSE de sesiones + endpoints lectura para la UI.
- ``handoff``: intervención humana (intervene / send / return-to-bot).

PR2 mantiene los 3 routers como módulos separados; ``main.py`` importa cada
uno explícito. PR3 los unificará bajo ``api.legacy_routers`` del manifest
para que el loader los descubra y registre automáticamente.
"""
