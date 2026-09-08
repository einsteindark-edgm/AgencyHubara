# Plugin: orders

> Behavior contract — bootstrap inicial 2026-05-25.
> Fuente: `hubara_agency/src/plugins/orders/api/__init__.py` + `frontend_dashboard/src/plugins/orders/`.

## Purpose

El plugin `orders` provee el **tablero kanban operacional** que la
operadora humana usa para gestionar ciclo de vida de pedidos (creados por
el `agents/sales-worker` o vía draft orders directos en Medusa).
Encapsula la lectura, transiciones de stage, agendamiento, confirmación
manual de pago y cancelación. La fuente de verdad de los datos es
**Medusa v2** (`/admin/orders` + `/admin/draft-orders`); el vault local
sólo retiene pedidos "huérfanos" (failed registrations + stubs) para
reconciliación manual.

## Requirements

### Requirement: Listar órdenes para el kanban

El sistema SHALL exponer `GET /api/orders/orders` que devuelva una lista
paginada de órdenes activas (no canceladas, no entregadas hace > 30
días) consumiendo Medusa v2.

#### Scenario: Medusa configurado y respondiendo

- GIVEN Medusa v2 configurado con credenciales válidas
- WHEN se invoca `GET /api/orders/orders?limit=100&offset=0&include_drafts=true`
- THEN se devuelve `{orders: [...], count, offset, limit, catalog_available: true, error_detail: null}`
- AND cada orden incluye `OrderSummaryDTO` con stage actual, totales, customer phone
- AND draft orders recién cerradas vía `register_order` (status='pending') aparecen mezcladas con orders confirmadas

#### Scenario: Medusa no configurado

- GIVEN env vars `MEDUSA_REGION_ID` o `MEDUSA_API_KEY` ausentes
- WHEN se invoca `GET /api/orders/orders`
- THEN se devuelve HTTP 200 con `{orders: [], catalog_available: false, error_detail: "<reason>"}`
- AND el frontend pinta empty state explícito (no error 500)

#### Scenario: Medusa down / 503

- GIVEN Medusa configurado pero respondiendo 5xx
- WHEN se invoca el endpoint
- THEN se devuelve HTTP 200 con `{orders: [], catalog_available: false, error_detail: "medusa_unreachable: ..."}`
- AND el operador puede seguir trabajando con vault-orders manualmente

### Requirement: Detalle de orden para inspector

El sistema SHALL exponer `GET /api/orders/orders/{order_id}` que devuelva
el `OrderDetailDTO` completo, incluyendo `data_completeness_missing[]`
para slots que Medusa todavía no soporta.

#### Scenario: ID válido de order

- GIVEN una order con `id="order_01HXX..."` existente en Medusa
- WHEN se invoca `GET /api/orders/orders/order_01HXX...`
- THEN se devuelve el OrderDetailDTO con summary, items_detail, addresses, totales, timeline, payment_method_label
- AND el campo `data_completeness_missing[]` lista slots como `due_date`, `agent_assignee`, `notes` si no están en Medusa

#### Scenario: ID válido de draft_order

- GIVEN una draft_order con `id="draft_01HXX..."` existente
- WHEN se invoca `GET /api/orders/orders/draft_01HXX...`
- THEN el endpoint hace fallback automático entre `/admin/orders/{id}` y `/admin/draft-orders/{id}`
- AND devuelve el mismo shape OrderDetailDTO

#### Scenario: ID inexistente

- GIVEN un order_id que no existe en Medusa (ni order ni draft)
- WHEN se invoca el endpoint
- THEN se devuelve HTTP 404 con `detail="Order {id} not found in Medusa."`

### Requirement: Visibilidad de pedidos huérfanos del vault

El sistema MUST exponer `GET /api/orders/vault-orders` que liste pedidos
que existen en el vault local pero NO en Medusa, para que la operadora
pueda reconciliarlos manualmente.

#### Scenario: Registration fallida en Medusa

- GIVEN el sales agent invocó `register_order` y Medusa devolvió 5xx
- WHEN se invoca `GET /api/orders/vault-orders`
- THEN el response incluye un record con `kind="failed"`, `error_detail="medusa_api_error: HTTP 5xx"`, payload completo en `raw`
- AND el `failed_count` se incrementa

#### Scenario: Stub registration (Medusa no configurado al cierre)

- GIVEN el sales agent cerró un pedido con `StubOrderRegistration` (sin Medusa configurado)
- WHEN se invoca el endpoint
- THEN el record tiene `kind="stub"`, `order_id="HUB-..."`, customer info válida
- AND `stub_count` se incrementa
- AND el cliente vía WhatsApp recibió confirmación, pero el pedido NO existe en Medusa hasta migración manual

### Requirement: Agendar entrega y transicionar a "preparing"

El sistema SHALL permitir agendar fecha + hora de entrega vía `PATCH
/api/orders/orders/{id}/schedule`, lo cual transiciona la orden de stage
`new` → `preparing` atómicamente.

#### Scenario: Agendamiento válido

- GIVEN una orden en stage `new` con `order_id="order_01HXX..."`
- WHEN se invoca `PATCH /api/orders/orders/order_01HXX.../schedule` con `{delivery_iso: "2026-05-26", delivery_time: "09:00", note: "Antes 10am"}`
- THEN se devuelve `{success: true, current_stage: "preparing", order_id, audit_id}`
- AND el frontend invalida queries de list + detail
- AND la fecha agendada queda persistida en metadata custom de Medusa

#### Scenario: delivery_iso ausente

- GIVEN body sin `delivery_iso` o vacío
- WHEN se invoca el endpoint
- THEN se devuelve HTTP 422 con `detail="`delivery_iso` (YYYY-MM-DD) es requerido"`

#### Scenario: Medusa rechaza la transición

- GIVEN una orden en stage incompatible (ej: `cancelled`)
- WHEN se invoca schedule
- THEN se devuelve HTTP 200 con `{success: false, error_detail: "invalid_transition: ..."}`
- AND el frontend muestra el error_detail al operador

### Requirement: Transición libre de stage (drag-and-drop)

El sistema SHALL exponer `PATCH /api/orders/orders/{id}/stage` para
transiciones manuales validando el DAG permitido entre stages.

#### Scenario: Transición válida

- GIVEN una orden en stage `preparing`
- WHEN se invoca con body `{stage: "ready", note: "Empaquetado"}`
- THEN se devuelve `{success: true, current_stage: "ready", ...}`
- AND la transición queda audited

#### Scenario: Transición inválida sin force

- GIVEN una orden en stage `new`
- WHEN se invoca con body `{stage: "delivered"}` (saltea preparing/ready/shipping)
- THEN se devuelve HTTP 200 con `{success: false, error_detail: "invalid_transition: ..."}`

#### Scenario: Transición inválida con force=true (corrección humana)

- GIVEN una orden en stage `new`
- WHEN se invoca con `{stage: "delivered", force: true, note: "Corrección manual"}`
- THEN se devuelve `{success: true, current_stage: "delivered"}`
- AND el frontend pidió confirm dialog antes de mandar el request

#### Scenario: Stage inválido (typo)

- GIVEN body con `{stage: "shippings"}` (typo)
- WHEN se invoca
- THEN se devuelve HTTP 422 con la lista de stages válidos en el detail

#### Scenario: Transición sin notificación ETA (agente order-sentinel)

- GIVEN una transición inferida de la conversación humana (el cliente YA fue
  avisado por chat)
- WHEN se invoca con `{stage: "ready", by: "order-sentinel", notify_customer: false}`
- THEN la transición se aplica y el SSE del dashboard se publica igual
- AND la cascada ETA (`EmitOrderStageWorkflow` → mensaje WhatsApp al cliente)
  NO se dispara — evita duplicar lo que el humano ya dijo
- AND con `notify_customer` omitido o `true` el comportamiento actual queda
  intacto (ETA notifica). La supresión es POR TRANSICIÓN, nunca por tag
  HUMANO (L-6: toda venta exitosa termina en HUMANO)

#### Scenario: Notificación con Meta Business Agent al frente (D1.9)

- GIVEN `MBA_STANDBY_ENABLED=1`, el cliente en `MBA_CUSTOMER_ALLOWLIST` y MBA
  controlando el hilo (`control_owner=mba`, o último inbound por `standby`)
- WHEN la cascada ETA reclama la notificación de un stage
  (`claim_eta_notification_activity`)
- THEN Hubara NO envía texto ni template (un envío por Cloud API le quitaría
  el hilo a MBA): el ETA le cuenta la novedad al plugin `mba`
  (`POST /api/mba/sessions/{session_key}/agent-events`, identidad de servicio)
  con el texto exacto de `render_stage_notification` y el tipo del catálogo
  (`preparing` → `payment_received` si `pay_status=paid`, si no
  `order_preparing`; `ready`/`shipping`/`delivered`/`cancelled` →
  `order_ready`/`order_shipped`/`order_delivered`/`order_cancelled`)
- AND `mba` emite `POST /{phone_number_id}/agent_event` (`X-API-Version: 2.0.0`)
  y registra `agent_events[]` en la sesión; el stage queda en
  `notified_stages` con `[agent_event <tipo> → Meta Business Agent: accepted <id>]`
  en el timeline (dedupe ante reentregas)
- AND si `mba` responde `already_emitted` o `ambiguous` (Meta pudo aceptarlo)
  tampoco se envía; si responde `hubara_controls`, `rejected`, `unavailable`,
  `not_configured` o `entity_id_missing`, o el API no está (502/503/403/404),
  el ETA notifica como siempre (el cliente no se queda sin aviso)
- AND si el API no respondió a tiempo (504) o falló a mitad (500) la activity
  falla y Temporal la reintenta (el dedupe de `mba` hace seguro el reintento)
- AND con la flag apagada (default) el predicado es falso y nada cambia

#### Scenario: Atribución del actor en el stage history

- GIVEN body con `{stage: "ready", by: "order-sentinel"}`
- WHEN se aplica la transición
- THEN el stage history registra `by: "order-sentinel"` (omitido → `"human"`)

#### Scenario: "En camino" con link de guía (modal del kanban)

- GIVEN el operador suelta un pedido en la columna `shipping` del kanban
- WHEN el modal "Marcar en camino" le pide (opcionalmente) el link de la guía
  y confirma con `{stage: "shipping", tracking_url: "https://…?guia=123"}`
- THEN la transición se aplica y el stage history registra la nota
  `Guía de envío: https://…?guia=123` (concatenada a la nota si venía una)
- AND `EmitOrderStageWorkflow` recibe el `tracking_url`, el
  `OrderStageChangedEvent` lo lleva y el manifest lo mapea (`$.tracking_url`)
  al signal `notify_stage_change` del ETA
- AND el mensaje de WhatsApp que anuncia "ya va en camino" termina con la URL
  cruda en su propia burbuja (`Puedes seguir tu envío aquí: https://…`) para
  que WhatsApp la muestre como link tappable; fuera de la ventana 24h el link
  viaja en el slot `status_label` del template `order_status_utility_v2`
- AND sin `tracking_url` (o vacío) el mensaje es byte-a-byte el de siempre;
  "Cancelar" en el modal no mueve el pedido ni manda request

#### Scenario: Link de guía inválido

- GIVEN body `{stage: "shipping", tracking_url: "www.x.com/guia"}` (sin esquema
  http/https, o con espacios, o > 500 chars, o esquema `javascript:`/`ftp:`)
- WHEN se invoca
- THEN se devuelve HTTP 422 mencionando `tracking_url` y la transición NO se
  aplica (un "en camino" con link roto no se puede re-notificar por template)

### Requirement: Confirmación manual de pago

El sistema SHALL exponer `PATCH /api/orders/orders/{id}/confirm-payment`
que marca `hubara_payment_confirmed=true` en metadata. **Hoy NO toca el
`payment_status` real de Medusa** (sin gateway integrado) — cuando se
integre, este endpoint capturará el pago.

#### Scenario: Confirmación primera vez

- GIVEN una orden con metadata sin `hubara_payment_confirmed`
- WHEN se invoca con body `{by: "operador-1"}`
- THEN se devuelve `{success: true, ...}` y `hubara_payment_confirmed=true` queda en metadata
- AND el campo `by` queda registrado para auditoría

#### Scenario: Idempotencia

- GIVEN una orden con `hubara_payment_confirmed=true` ya seteado
- WHEN se re-invoca confirm-payment
- THEN se devuelve `{success: true, ...}` sin side effects extra

#### Scenario: La conversación vuelve al bot al confirmar el pago

- GIVEN una orden con `session_key` cuyo chat está en `active_route=humano`
  (el bot escaló con `PAYMENT_VERIFICATION_PENDING` o el humano intervino)
- WHEN se invoca confirm-payment (desde el tablero de orders o desde el
  botón "Confirmar pago" del chat — mismo comando)
- THEN el `metadata.json` del chat queda con `tag=COMPRA_EXITOSA` y
  `active_route=ventas`, con una entrada en `status_history`
  (`source=orders_confirm_payment`)
- AND el próximo inbound del cliente lo atiende el bot de ventas; la
  conversación sale de la bandeja "Asignadas al humano" y aparece como Cliente
- AND si el chat ya estaba en `ventas`/`remarketing`, la ruta NO se toca

### Requirement: Cancelación de orden

El sistema SHALL exponer `POST /api/orders/orders/{id}/cancel` que
transiciona a `cancelled` con `force=true` y persiste razón.

#### Scenario: Cancelación con razón

- GIVEN una orden en stage `preparing`
- WHEN se invoca con `{reason: "Cliente cambió de opinión"}`
- THEN se devuelve `{success: true, current_stage: "cancelled"}`
- AND `hubara_cancelled_reason="Cliente cambió de opinión"` queda en metadata
- AND la razón aparece en el inspector + timeline

#### Scenario: Idempotencia

- GIVEN una orden ya en stage `cancelled`
- WHEN se re-invoca cancel
- THEN se devuelve `{success: true, ...}` sin side effects extra

### Requirement: Health check del port

El sistema SHALL exponer `GET /api/orders/orders-health` que devuelva
qué `OrderQueryPort` está inyectado + si Medusa responde a una probe
mínima sin consumir cuota.

#### Scenario: Medusa OK

- GIVEN Medusa configurado y respondiendo
- WHEN se invoca `GET /api/orders/orders-health`
- THEN se devuelve `{port: "MedusaOrderQueryAdapter", catalog_available: true, error_detail: null, sample_count: N}`

#### Scenario: Medusa down

- GIVEN Medusa configurado pero no responde
- WHEN se invoca el endpoint
- THEN se devuelve `{port: "MedusaOrderQueryAdapter", catalog_available: false, error_detail: "<reason>", sample_count: 0}`

## Out of scope (NO go en este spec)

- Listado/historial completo de órdenes archivadas (>30 días entregadas) — fuera del kanban
- Editar items de una orden (Medusa lo controla)
- Pagos automáticos con gateway (sin integrar todavía)
- Notificaciones push al operador (manual refresh por ahora)

## Dependencias

- **`platform/orders/`** — define `OrderQueryPort`, `OrderCommandPort`, DTOs (`OrderSummaryDTO`, `OrderDetailDTO`, `CancelOrderCommand`, etc.)
- **`platform/catalog/`** — para enriquecer items con product info
- **Medusa v2** — fuente de verdad upstream
- **`vault_scanner.py`** — escanea `hubara_vault/wa_*/metadata.json` para vault-orders

## Mensajes del frontend

El frontend (`frontend_dashboard/src/plugins/orders/`) consume estos
endpoints vía TanStack Query. Invalida list+detail después de cada PATCH
exitoso. Si `catalog_available=false`, pinta empty state con tarjeta
explicativa.
