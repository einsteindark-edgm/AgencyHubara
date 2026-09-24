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
- AND `EmitOrderStageWorkflow` corre en modo silencioso: encola el evento
  CAPI de la etapa pero NO despacha el `OrderStageChangedEvent` → ningún
  WhatsApp al cliente (evita duplicar lo que el humano ya dijo)
- AND con `notify_customer` omitido o `true` el comportamiento actual queda
  intacto (ETA notifica). La supresión es POR TRANSICIÓN, nunca por tag
  HUMANO (L-6: toda venta exitosa termina en HUMANO)

#### Scenario: Pedido entregado sin pasar por las etapas intermedias

- GIVEN una orden en `preparing` que el operador olvidó mover y ya se entregó
- WHEN desde el kanban la suelta en `delivered` y confirma el diálogo de
  salto (aviso al cliente apagado por defecto)
- THEN se invoca con `{stage: "delivered", force: true, notify_customer: false,
  note: "Salto manual: se omitió Lista, En camino"}`
- AND el stage history registra UNA entrada con la nota (no se inventan
  entradas para las etapas omitidas)
- AND sale el evento CAPI `OrderDelivered`; NO salen los de las etapas
  omitidas ni ningún WhatsApp al cliente

#### Scenario: Notificación con Meta Business Agent al frente (D1.9)

- GIVEN `MBA_STANDBY_ENABLED=1`, el cliente en `MBA_CUSTOMER_ALLOWLIST` y MBA
  controlando el hilo (`control_owner=mba`, o último inbound por `standby`)
- WHEN la cascada ETA reclama la notificación de un stage
  (`claim_eta_notification_activity`)
- THEN Hubara NO envía texto ni template (un envío por Cloud API le quitaría
  el hilo a MBA): el ETA le cuenta la novedad al plugin `mba`
  (`POST /api/mba/sessions/{session_key}/agent-events`, identidad de servicio)
  con el texto exacto de `render_stage_notification` (con la guía de envío en
  `shipping`: el workflow pasa `tracking_url` al claim) y el tipo del catálogo
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
- AND si el API no respondió a tiempo (504, también transporte roto tras
  conectar o 2xx ilegible) o falló a mitad (500) la activity falla y Temporal
  la reintenta (`mba` reserva el evento como `pending` bajo lock ANTES de
  llamar a Meta, así el retry o una activity vencida en paralelo ven
  `already_emitted`); agotados los 3 intentos el workflow lo loguea como
  no-fatal y el stage queda sin reservar (el próximo evento del pedido lo
  vuelve a intentar); un `ambiguous` (Meta no respondió a `mba`) reserva el
  stage marcado `flagged=true, flag=mba_ambiguous` en el timeline
- AND la reserva del stage en `eta_tracking` es un update bajo el flock del
  store que toca solo ese bloque (lo que `mba`, el ingest o un handover
  escribieron durante el hop sobrevive)
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

#### Scenario: "En camino" con valor del envío (modal del kanban, 2026-09-22)

- GIVEN el operador suelta un pedido en la columna `shipping` del kanban
- WHEN el modal "Marcar en camino" muestra el valor del pedido prellenado
  (total vivo de la orden, solo lectura), le pide (opcionalmente) el valor
  del envío, recalcula el total en vivo, y confirma con
  `{stage: "shipping", shipping_cost: 12000}` (COP entero, con o sin
  `tracking_url`)
- THEN la transición se aplica y el stage history registra la nota
  `Valor del envío: $ 12.000` (concatenada a la nota / guía si venían)
- AND `EmitOrderStageWorkflow` recibe el `shipping_cost`, el
  `OrderStageChangedEvent` lo lleva y el manifest lo mapea
  (`$.shipping_cost`) al signal `notify_stage_change` del ETA
- AND el mensaje de WhatsApp detalla el cobro separado, una línea por
  concepto en la misma burbuja: `Tu pedido #9 (…) ya va en camino 🚚.` /
  `Valor del pedido: $ 50.000` / `Valor del envío: $ 12.000` /
  `Total: $ 62.000`. El valor del pedido lo lee el ETA del pedido vivo (no
  viaja desde el modal). Contra entrega: `Recuerda que al recibirlo pagas
  $ 62.000 al repartidor…`; pagado: `El valor del pedido ya está pagado.`
  (ya NO "no tienes que pagar nada"). Sin total del pedido conocido (Medusa
  caído) sale solo la línea del envío. Fuera de la ventana 24h viaja en el
  slot `status_label` del template `order_status_utility_v2` (`en camino.
  Valor del pedido: $ 50.000, valor del envío: $ 12.000, total: $ 62.000.
  Sigue tu envío aquí: …`)
- AND sin `shipping_cost` (ausente, `null` o `0`) el mensaje es byte-a-byte
  el de siempre
- AND `shipping_cost` no entero, bool, negativo o > 10.000.000 → HTTP 422
  mencionando `shipping_cost` y la transición NO se aplica

#### Scenario: El envío del modal es el REAL y reemplaza al estimado (2026-09-23)

- GIVEN un pedido registrado con total $ 57.900 = pedido $ 50.000 + envío
  estimado $ 7.900 (tarifa mínima: el envío solo se conoce al despachar)
- WHEN el operador lo suelta en `shipping` (o toca "Despachar" en el panel
  del celular)
- THEN el modal muestra `Valor del pedido: $ 50.000` (total − envío vigente,
  SIN el estimado), el estimado `$ 7.900` como referencia que NO se suma, y
  la casilla "Valor del envío real"; con $ 12.000 el total es $ 62.000
- AND la transición persiste `metadata.hubara_shipping_cost_cop = 12000`
  (Medusa v2 no permite editar el monto de un método de envío existente en
  una orden real — el valor real vive en metadata, como la etapa)
- AND desde ahí todo Hubara lee el envío real: el query adapter (único
  writer de OrderFacts) devuelve `shipping_cop = 12000`,
  `shipping_confirmed = true` y `total_cop = 62000` (total Medusa − envío
  estimado + envío real) en lista, detalle, OrderFacts (Ads, Campañas…), el
  cobro de "Confirmar pago" y el mensaje del ETA (que desglosa con el valor
  del pedido SIN envío, `order_value_cop`, para no sumarlo dos veces)
- AND sin valor real (vacío / 0) se mantiene el estimado

#### Scenario: "En preparación" y "Listo" no mencionan el precio (2026-09-22)

- GIVEN un pedido contra entrega que pasa a `preparing` o a `ready`
- WHEN el ETA renderiza el aviso
- THEN en `preparing` el mensaje es `¡Hola {nombre}! Soy tu asistente de
  seguimiento de Hubara. Tu pedido #… (…) acaba de entrar en preparación. Te
  aviso en cada paso 🙌` — sin "Recuerda que es contra entrega: pagarás $ X…"
- AND en `ready` el mensaje es `¡Buenas noticias {nombre}! Tu pedido #… ya
  está empacado y listo para salir. Te escribo apenas vaya en camino.` — sin
  "Ten listos $ X para pagar…". `ready` SIGUE avisando (con la foto del
  pedido si el operador la subió; el aviso de estado si no)
- AND el monto se recuerda recién en "en camino"

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

### Requirement: La línea con cupo por unidad lleva la marca del cupo

Una línea con descuento de un cupón con cupo por unidad (central de cupones)
MUST llevar `metadata.coupon_quota_id` además de `coupon_code`,
`list_unit_price_cop` y `discount_unit_cop`. Las vendidas de cada cupo se
DERIVAN de esas líneas en `/admin/orders` y `/admin/draft-orders`, sin
contador aparte: un pedido cancelado (en Medusa o `hubara_stage=cancelled`),
borrado o marcado de prueba (`hubara_test_order`) deja de contar solo. El
reintento de reconciliación MUST conservar el cupo de cada unidad.

#### Scenario: Cancelar devuelve la unidad

- GIVEN un pedido con 1 unidad del cupo "Cubo Love · Rosado · Café"
- WHEN el pedido se cancela (Medusa o Hubara) o se marca de prueba
- THEN en la siguiente lectura esa unidad vuelve a quedar disponible para el cupón

#### Scenario: Reintento de un registro con cupo

- GIVEN un registro con cupo falló y quedó en `failed_order_registrations`
- WHEN la reconciliación lo reintenta
- THEN cada unidad con descuento viaja con el mismo `quota_id` y la línea en Medusa lleva `coupon_quota_id`

#### Scenario: La línea dice qué color y aroma se despachan

- GIVEN el cliente eligió Cubo Love Rosado · Café (producto de variante "Unico")
- WHEN se registra el pedido (bot, "Crear pedido" o reintento de reconciliación)
- THEN la línea del draft lleva `metadata.color` y `metadata.aroma`, el detalle del pedido los expone (`color`, `aroma`) y el fingerprint los distingue (solo cuando existen: sin ellos el hash no cambia)

#### Scenario: El reintento no se cuenta a sí mismo (L-28)

- GIVEN el intento original SÍ creó el draft en Medusa aunque el adapter reportó falla (timeout) y ese draft se llevó la última unidad
- WHEN la reconciliación relee el cupo bajo el candado
- THEN ese draft (misma `metadata.session_key` + `metadata.order_fingerprint`) no cuenta como vendido para él, el port lo reusa por fingerprint y el registro queda `resolved` — no `abandoned` ni duplicado

#### Scenario: Sin poder releer el cupo

- GIVEN el candado está ocupado, o Medusa o el vault no responden al releer el cupo
- WHEN la reconciliación reintenta
- THEN NO registra, suma un intento (`quota_unavailable: …`) y el registro sigue `pending`; el barrido continúa con los demás pedidos aunque uno falle

#### Scenario: La reconciliación no pisa la sesión

- GIVEN un reintento en curso (candado + Medusa) y, mientras, otro writer cambia la sesión (un humano la toma, un tag, otro registro)
- THEN el reintento guarda SOLO su record sobre una lectura fresca bajo el lock de la sesión, y con el candado del cupo tomado relee el estado: dos reintentos del mismo pedido (barrido + "Reintentar") terminan `resolved`, nunca `abandoned` con el pedido creado

#### Scenario: Abandonado porque se acabaron las unidades

- GIVEN la reconciliación abandonó un pedido con `abandon_reason: quota_changed`
- THEN `/api/orders/vault-orders` expone `abandon_reason` y "Reintentar" responde ese motivo (no "max_attempts alcanzado"): el operador re-confirma el total nuevo con el cliente antes de registrarlo a mano

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
