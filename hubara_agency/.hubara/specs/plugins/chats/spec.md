# Plugin: chats

> Behavior contract — bootstrap inicial 2026-05-25.
> Fuente: `hubara_agency/src/plugins/chats/` (workers + api + agent + shared).

## Purpose

El plugin `chats` es el **dominio agéntico de WhatsApp**: ingresa
mensajes inbound vía webhook, despacha al worker correcto (sales o
remarketing) según conversation state, y expone los endpoints HTTP/SSE
que el dashboard usa para visualizar conversaciones y permitir
intervención humana (handoff). Internamente compone DOS workers
Temporal exclusivos (uno por sub-dominio) cada uno con su propia task
queue. Detalles del comportamiento del agente se definen en
[`agents/sales-worker/spec.md`](../../agents/sales-worker/spec.md) y
`agents/remarketing-worker/spec.md` (pendiente).

## Requirements

### Requirement: Webhook de WhatsApp inbound

El sistema MUST exponer `POST /api/webhook` que reciba mensajes inbound
de WhatsApp Cloud API y los despache a `IngestInboundMessage` use case
en background (sin bloquear la response al cliente WhatsApp).

#### Scenario: Mensaje de texto válido

- GIVEN un body WhatsApp Cloud bien formado con un mensaje de texto
- WHEN `POST /api/webhook` recibe el body
- THEN parsea con `parse_whatsapp_inbound`
- AND devuelve `{status: "ok"}` con HTTP 200 en < 200ms
- AND `IngestInboundMessage.execute(parsed)` se invoca en background
- AND eventualmente arranca/signalá el workflow apropiado (sales o remarketing)

#### Scenario: Body malformado

- GIVEN un body que no cumple el schema esperado de WhatsApp Cloud
- WHEN el parser arroja `ValueError`
- THEN se devuelve HTTP 400 con `detail="malformed payload: ..."`
- AND se loguea `Malformed WhatsApp webhook body` con `error=str(exc)`

#### Scenario: Status update (no es mensaje)

- GIVEN un webhook que es status update (delivered/read), no mensaje nuevo
- WHEN se procesa
- THEN el parser devuelve `None`
- AND el endpoint responde `{status: "ok"}` sin dispatch a use case

#### Scenario: Verification handshake

- GIVEN WhatsApp Cloud envía `GET /api/webhook?hub.mode=subscribe&hub.verify_token=X&hub.challenge=Y`
- WHEN el token matchea `WHATSAPP_VERIFY_TOKEN`
- THEN se devuelve `int(challenge)` con HTTP 200
- AND se loguea `WhatsApp Webhook Verified`

#### Scenario: Verification con token inválido

- GIVEN un GET con `hub.verify_token` que NO matchea
- WHEN se procesa
- THEN se devuelve HTTP 403 con `detail="Forbidden"`

### Requirement: Worker exclusivo por sub-dominio

El sistema MUST arrancar dos workers Temporal separados:
`HubaraSalesSessionWorkflow` y `RemarketingSessionWorkflow`, cada uno
en su task queue exclusiva derivada de `get_task_queue("chats", "sales")`
y `get_task_queue("chats", "remarketing")`.

#### Scenario: Arranque worker sales

- GIVEN env de Temporal configurado con mTLS
- WHEN `python -m src.plugins.chats.workers.sales` corre
- THEN se conecta al cluster, registra `HubaraSalesSessionWorkflow` + 17 activities
- AND escucha en task queue `queue-chats-sales`
- AND emite log `😎 Sales Agent En Vivo. Escuchando la cola exclusiva: 'queue-chats-sales'`

#### Scenario: Arranque worker remarketing

- GIVEN env Temporal OK
- WHEN `python -m src.plugins.chats.workers.remarketing` corre
- THEN registra `RemarketingSessionWorkflow` + activities específicas
- AND escucha en `queue-chats-remarketing`

#### Scenario: Worker NO arranca por config rota

- GIVEN `TEMPORAL_ADDRESS` o `TEMPORAL_NAMESPACE` ausente
- WHEN se intenta arrancar el worker
- THEN `get_temporal_client()` arroja `ConfigurationError` y el worker muere con exit code != 0
- AND el log indica la env var faltante

### Requirement: Registro de tools por worker (composition root)

Cada worker SHALL ser el composition root que registra sus tools de
dominio via `register_tool_extension(name, factory)`. Las tools NO se
registran cross-worker.

#### Scenario: Sales worker registra todas sus tools

- GIVEN el módulo `workers/sales.py` cargado
- WHEN se ejecutan los `register_tool_extension(...)` de top-level
- THEN al menos las siguientes tools quedan registradas: `sales.transfer_to_sales_agent`, `sales.manage_conversation_tag`, `sales.search_products`, `sales.get_product_by_handle`, `sales.escalate_to_human`, `sales.verify_order_for_checkout`, `sales.register_order`, `sales.present_*` + `sales.send_*` + `sales.react_*` + `sales.request_*` (10 decision tools de UI total)
- AND el sales worker puede invocar cualquiera via `execute_tool` activity

#### Scenario: Remarketing worker SOLO registra `transfer_to_sales_agent`

- GIVEN `workers/remarketing.py` cargado
- WHEN inicializa
- THEN sólo registra `sales.transfer_to_sales_agent` (única tool que necesita — para devolver el control a Sales si el cliente responde)
- AND NO registra tools de UI ni de catálogo (no las necesita)

#### Scenario: NameError lazy en lambda (footgun documentado)

- GIVEN un `register_tool_extension(name, lambda workspace: NotImportedTool(...))` donde `NotImportedTool` NO está en los `from ... import ...` del top del archivo
- WHEN el worker arranca, NO falla
- WHEN una activity invoca `apply_tool_extensions` para esa tool en runtime
- THEN arroja `NameError: NotImportedTool is not defined` y tumba la conversación
- AND el incident debe diagnosticar revisando imports del worker, no asumiendo bug en HEAD (puede ser deploy stale)

### Requirement: Dashboard SSE de sesiones

El sistema SHALL exponer endpoints bajo `/api/dashboard/*` que la UI
consume vía TanStack Query + SSE para ver conversaciones activas,
historial, métricas en tiempo real.

#### Scenario: List sesiones activas

- GIVEN N sesiones activas en `hubara_vault/wa_*/`
- WHEN se invoca `GET /api/dashboard/sessions`
- THEN se devuelve lista con phone, last_message_at, conversation_state, tag
- AND ordenadas por `last_message_at` desc

#### Scenario: SSE stream

- GIVEN el dashboard abre `EventSource('/api/dashboard/events')`
- WHEN un mensaje inbound o outbound se procesa
- THEN el server emite un SSE event con shape `{session_key, event_type, payload}`
- AND el frontend actualiza el sidebar sin polling

### Requirement: Handoff humano (intervención)

El sistema SHALL exponer endpoints bajo `/api/dashboard/handoff/*` que
permitan al operador humano intervenir manualmente una conversación
agéntica (`intervene`), responder en nombre del bot (`send`), y devolver
control al bot (`return-to-bot`).

#### Scenario: Intervenir conversación activa

- GIVEN una conversación con `active_route=auto` y workflow sales corriendo
- WHEN el operador hace `POST /api/dashboard/handoff/{session_key}/intervene`
- THEN `active_route=human` se setea en `metadata.json`
- AND el workflow sales NO procesa nuevos inbounds (`LoadOrStartSalesSession` corta si `active_route!=auto`)
- AND la UI marca la sesión como "intervenida"

#### Scenario: Enviar mensaje como humano

- GIVEN conversación intervenida
- WHEN `POST /api/dashboard/handoff/{session_key}/send` con `{text: "Hola, soy un humano"}`
- THEN se envía el mensaje vía `send_whatsapp_message_activity`
- AND queda persistido en session history como sender=`human`

#### Scenario: Devolver control al bot

- GIVEN conversación intervenida con N mensajes humanos
- WHEN `POST /api/dashboard/handoff/{session_key}/return-to-bot`
- THEN `active_route=auto` se restaura
- AND el próximo inbound del cliente arranca/signala workflow normal

### Requirement: Ads campaigns endpoint

El sistema SHALL exponer endpoints bajo `/api/ads/*` para consumir Meta
Ads campaigns asociadas a la conversación (origen del lead, ad_id,
campaign_id) — útil para personalización del agente.

#### Scenario: List campaigns

- GIVEN credenciales Meta Ads configuradas
- WHEN se invoca `GET /api/ads/campaigns`
- THEN se devuelve lista de campaigns activas con id, name, adset_id, ad_creative

## Out of scope

- Workflow logic del sales/remarketing — ver specs específicas en `agents/`
- Tool implementations específicas — documentadas en `agents/sales-worker/spec.md`
- Catalog snapshot — ver `plugins/catalog/spec.md` (pendiente)
- Order registration logic — ver `plugins/orders/spec.md`
- Eventos del EventLog — ver [`messaging/spec.md`](../../messaging/spec.md)

## Dependencias cross-plugin

- **`plugins/orders`** — el sales worker invoca `register_order` que escribe en Medusa via `OrderCommandPort`
- **`plugins/catalog`** — sales tools (`search_products`, `present_*`) consultan snapshot mantenido por `catalog_sync`
- **`platform/whatsapp`** — `send_whatsapp_message_activity`, `send_typing_indicator_activity`
- **`platform/session_history`** — persistence de turns
- **`platform/orchestration`** — `dispatch_event_activity` para eventos cross-plugin

## Notas de migración

- Pre-PR11 vivía como `src/sales_whatsapp/` y `src/remarketing_whatsapp/`. Esos paths existen como shells legacy (re-exports) hasta limpieza definitiva.
- Manifest del plugin: `frontend_dashboard/src/plugins/chats/plugin.yaml`.
