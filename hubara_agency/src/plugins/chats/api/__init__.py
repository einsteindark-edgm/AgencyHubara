"""HTTP API del plugin ``chats`` — tres routers FastAPI.

Cada submódulo expone un ``router`` (APIRouter) que ``src.main`` registra con
su prefijo y tags propios desde ``manifest.api.legacy_routers``:

- ``sales``: webhook de WhatsApp inbound (POST /webhook). Ingresa el mensaje
  al use case ``IngestInboundMessage``. Prefix ``/api``.
- ``dashboard``: SSE de sesiones + endpoints lectura para la UI. Prefix
  ``/api/dashboard``.
- ``handoff``: intervención humana (intervene / send / return-to-bot). Prefix
  ``/api/dashboard``.

**Por qué 3 routers y no 1**: los prefijos heterogéneos (uno bajo ``/api``,
dos bajo ``/api/dashboard``) y los tags OpenAPI distintos son contratos del
frontend; unirlos rompería los hooks de TanStack Query. Si se unifican en el
futuro, ``legacy_routers`` desaparece del manifest y este ``__init__`` puede
exponer un ``router`` agregado.

**Sobre ``api.python_module`` del manifest**: este módulo NO expone ``router``
intencionalmente. El loader (``src.main``) lo importa pero el ``getattr`` da
``None`` y cae al modo ``legacy_routers``. La presencia de ``python_module``
en el manifest queda como ancla simbólica para introspección/herramientas
futuras (ej. un generador de docs que liste módulos por plugin).
"""
