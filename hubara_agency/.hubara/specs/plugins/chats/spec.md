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

### Requirement: Web cart hot lead (carrito web → cierre por WhatsApp)

Cuando el texto inbound contiene un token `ref:cart_<id>` (link wa.me
prellenado que genera la página web con el carrito Medusa), el ingest MUST
detectarlo determinísticamente (regex — nunca el LLM), persistir
`metadata.web_cart`, clasificar `origin.channel = "web_cart"` (salvo que el
inbound traiga `ctwa_clid`, que gana para no perder atribución CAPI) e
hidratar el carrito vía la Store API de Medusa **best-effort**: la
hidratación MUST correr inline con timeout corto y CUALQUIER fallo (sin
config, timeout, 404, mapping roto) MUST degradar en silencio — el turno se
señala igual y el bot vende con lo que dice el mensaje. Los precios que
vengan en el texto del cliente MUST NOT usarse jamás (el catálogo es la
única fuente de precios).

#### Scenario: Cart hidratado siembra el draft y salta etapas

- GIVEN un inbound con `ref:cart_<id>` y la Store API devuelve el cart
- WHEN el ingest hidrata
- THEN los items que matchean el snapshot siembran `order_draft.slots`
  (producto/cantidad/eje de variante; ciudad/dirección/teléfono si el
  checkout web los capturó; multi-item va a `notas`)
- AND `resolve_funnel_stage` proyecta la etapa avanzada sin código nuevo
- AND el plugin_context del MISMO turno lleva la nota `[LEAD CALIENTE
  DESDE LA WEB, ...]` + el breadcrumb del draft ya sembrado
- AND se emite el evento analytics `web_cart_captured` (status=hydrated),
  una sola vez por cart_id (re-envío del mismo link = no-op; un cart_id
  nuevo gana y re-hidrata — los slots ya sembrados por el cart anterior se
  CONSERVAN en el draft, no se pisan: la nota nueva lleva los items nuevos
  y el LLM reconcilia con el cliente)
- AND la nota es episodio-scoped: se apaga al cerrar el episodio en que se
  capturó el carrito (RECHAZO/TIMEOUT/re-engagement) — jamás resucita como
  "lead caliente" en un episodio posterior

#### Scenario: Hidratación falla — el bot vende igual

- GIVEN un inbound con `ref:cart_<id>` y la Store API caída / sin
  `MEDUSA_PUBLISHABLE_API_KEY` / cart inexistente
- WHEN el ingest degrada (`web_cart.status = "degraded"` + reason interno)
- THEN el turno se señala normal y la nota de lead caliente instruye
  validar los productos del mensaje contra el catálogo con las tools
- AND el motivo interno de degradación NUNCA entra al prompt

#### Scenario: Producto del cart no está en el catálogo (ataque o desync)

- GIVEN un cart hidratado con un item cuyo producto no matchea el snapshot
- WHEN se mapea
- THEN el item NO se siembra al draft; la nota instruye decirlo con
  honestidad y ofrecer los más similares (`present_products`)
- AND se emite `web_cart_product_mismatch` (categoría system) — un miss
  con cart real es casi siempre snapshot stale (clase PR #215), el
  operador debe re-sincronizar

#### Scenario: Conversación en manos de un humano

- GIVEN una sesión con `active_route = humano`
- WHEN llega un inbound con `ref:cart_<id>`
- THEN el ingest NO siembra drafts ni notas (el humano conserva el control)

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
- THEN al menos las siguientes tools quedan registradas: `sales.transfer_to_sales_agent`, `sales.manage_conversation_tag`, `sales.search_products`, `sales.get_product_by_handle`, `sales.list_categories`, `sales.escalate_to_human`, `sales.verify_order_for_checkout`, `sales.register_order`, `sales.present_*` + `sales.send_*` + `sales.react_*` + `sales.request_*` (10 decision tools de UI total)
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

### Requirement: Acciones de pedido desde el chat (cast a orders)

El sistema SHALL exponer, bajo `/api/chats/order-actions/*` (cast al
contrato `order@v1` de orders), las acciones de pedido del composer
intervenido: agendar entrega (`PATCH {id}/schedule`), confirmar pago
(`PATCH {id}/confirm-payment`) y leer el detalle (`GET {id}`).

El operador SHALL poder asignar la fecha de entrega SIN confirmar el pago
("Asignar fecha"), y confirmar el pago SHALL NOT modificar una fecha de
entrega ya asignada (`summary.due_iso != null`).

#### Scenario: Asignar fecha sin confirmar pago

- GIVEN conversación intervenida con `pending_payment_order_id`
- WHEN el operador agenda vía "Asignar fecha" (`PATCH /api/chats/order-actions/{id}/schedule`)
- THEN el pedido queda agendado (draft→Order, `new→preparing`) y el cliente recibe la notificación ETA
- AND el pago NO se confirma — `pending_payment_order_id` sigue expuesto y ambos botones siguen montados

#### Scenario: Confirmar pago con fecha ya asignada no re-agenda

- GIVEN un pedido con `summary.due_iso` asignado (por "Asignar fecha" o por el tablero de orders)
- WHEN el operador confirma el pago desde el chat
- THEN se invoca SOLO `confirm-payment` — ningún `schedule` que pise la fecha
- AND el popover muestra la fecha agendada en lugar de pedir una nueva

#### Scenario: Read-side no disponible degrada con aviso

- GIVEN el detalle del pedido no se puede leer (Medusa caído / orden stub inexistente)
- WHEN el operador abre el popover de confirmar pago
- THEN el flujo de 2 pasos (agendar + confirmar) queda disponible como fallback
- AND el popover avisa explícitamente que no se pudo verificar si ya hay fecha (la protección está apagada)

#### Scenario: Agendar sobre stage avanzado se rechaza

- GIVEN un pedido en stage `ready`/`shipping`/`delivered`/`cancelled`
- WHEN llega un `schedule` (desde el chat o el tablero)
- THEN el comando devuelve `success=false` con `error_detail` `invalid_state`
- AND NO se modifica `hubara_scheduled_delivery_iso` ni se emite cascada ETA

### Requirement: Visibilidad de envíos no-textuales en el histórico

Todo envío no-textual exitoso del bot (catálogo, foto de producto, galería,
formulario/flow de datos de envío, botones quick-reply, confirmación de
pedido, reacción, tarjeta de contacto, CTA URL) SHALL quedar registrado en
el session history JSONL que consume el dashboard, de modo que el operador
pueda seguir la conversación sin huecos. Un envío fallido MUST NOT dejar
marker (el histórico no puede afirmar que el cliente recibió algo que no
recibió). El registro es best-effort: un fallo de I/O al persistir el
marker MUST NOT bloquear ni duplicar el envío al cliente.

#### Scenario: Marker de componente UI enviado

- GIVEN un intent `quick_replies` pendiente en `metadata.json[pending_ui_intents]`
- WHEN `flush_pending_ui_intents_activity` lo envía con éxito
- THEN se appendea al JSONL `{role: assistant, kind: ui_component, component_kind: quick_replies, content: <nota human-readable con body y botones>, timestamp}`
- AND `GET /api/dashboard/sessions/{id}` lo proyecta como `ui_type: ui_component_sent`
- AND el frontend lo pinta como nota de sistema visible en el panel central

#### Scenario: Variant picker persiste el texto real

- GIVEN un intent `variant_picker` (que envía TEXTO renderizado al cliente y el workflow suprime el final_content del LLM)
- WHEN el flush lo envía con éxito
- THEN se persiste el texto renderizado como assistant message normal (sin `kind`)
- AND el operador ve exactamente lo que vio el cliente

#### Scenario: Envío fallido no deja marker

- GIVEN un intent cuyo `send_*` devuelve `ok=false`
- WHEN el flush lo procesa
- THEN NO se appendea ningún evento al session history

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
