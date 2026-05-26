# Messaging — cross-plugin contracts

> Behavior contract — bootstrap inicial 2026-05-25.
> Capability cross-cutting que define cómo fluyen mensajes WhatsApp +
> eventos del EventLog entre plugins.

## Purpose

Define los **contratos de comunicación cross-plugin** del sistema Hubara:
mensajes inbound de WhatsApp, mensajes outbound (texto + UI intents),
eventos publicados al EventLog que distintos plugins consumen
(declarative orchestration, ADR-2026-05-20). Sin esta capability, los
plugins se llamarían entre sí violando R-DIP. Con esta capability, todo
flujo cross-plugin pasa por contratos JSON-serializables.

## Requirements

### Requirement: Inbound de WhatsApp se persiste antes de procesarse

El sistema MUST persistir el mensaje inbound en el session history
ANTES de despachar al workflow. Si la persistencia falla, NO se despacha
(at-least-once semantic con dedup downstream).

#### Scenario: Inbound de texto

- GIVEN un mensaje WhatsApp inbound `{from: "+57...", text: "hola", message_id: "wamid.XXX"}`
- WHEN el use case `IngestInboundMessage` lo procesa
- THEN persiste el turn (sender=`user`, content=text, ts) en `hubara_vault/wa_{phone}/session_history.json`
- AND luego invoca `start_or_signal_sales_workflow_activity` o `start_or_signal_remarketing_workflow_activity`
- AND el `message_id` se usa para deduplicación (si llega 2x el mismo wamid, ack pero no re-procesar)

#### Scenario: Persistencia falla

- GIVEN el filesystem read-only o disco lleno
- WHEN el use case intenta persistir
- THEN arroja excepción que `BackgroundTasks` captura y loguea
- AND el cliente WhatsApp ya recibió HTTP 200 (no reintentará — pérdida de mensaje, conocido)

### Requirement: Outbound message envelope

El sistema SHALL enviar mensajes outbound vía `send_whatsapp_message_activity`
que acepta `{phone, content, message_type, metadata?}` y devuelve
`{success, whatsapp_message_id, error_detail?}`.

#### Scenario: Texto plano

- GIVEN un workflow llamó al LLM y obtuvo respuesta `"Hola!"`
- WHEN se invoca `send_whatsapp_message_activity(phone, "Hola!", "text")`
- THEN se POST a WhatsApp Cloud `/messages` con body `{messaging_product: "whatsapp", to: phone, type: "text", text: {body: "Hola!"}}`
- AND el response devuelve `whatsapp_message_id` (wamid)
- AND el turn (sender=`assistant`, content="Hola!") se persiste al session_history

#### Scenario: Imagen, audio, document

- GIVEN content_type ∈ {image, audio, document}
- WHEN se invoca con `metadata.media_url`
- THEN se construye body multimedia válido + se manda
- AND si WhatsApp rechaza (media expired, format invalid), retry con backoff exponencial vía Temporal

#### Scenario: Rate limit de WhatsApp

- GIVEN WhatsApp devuelve HTTP 429 con `error.code=131056` (rate limit)
- WHEN la activity recibe el error
- THEN reintenta con backoff (Temporal retry policy) hasta `max_attempts=3`
- AND si los 3 fallan, escala a alert log y deja el mensaje en dead-letter para reenvío manual

### Requirement: UI intents post-LLM

El sistema MUST permitir que decision tools del LLM emitan **UI intents**
(rich UI: botones, listas, productos, reacciones) que se renderizan
**después** del texto del LLM en mensajes WhatsApp nativos separados.

#### Scenario: LLM invoca `present_products` tool

- GIVEN el LLM responde con texto `"Mira estos:"` + tool call `present_products(handles=["wax-a", "wax-b"])`
- WHEN el workflow procesa el turn
- THEN primero `send_whatsapp_message_activity(text="Mira estos:")` se ejecuta
- AND luego `flush_pending_ui_intents_activity` lee `metadata.json[pending_ui_intents]` y renderiza cada uno (catalog list message, botones, etc.) como mensaje WA separado
- AND cada UI intent quedan persistidos en session_history con tipo apropiado

#### Scenario: Múltiples UI intents en mismo turn

- GIVEN el LLM invoca 2 tools en un turn: `react_to_message("👍")` + `present_products(...)`
- WHEN se procesan
- THEN se ejecutan secuencialmente preservando orden de invocación
- AND si una falla, las siguientes igual se ejecutan (best-effort)

### Requirement: EventLog para orchestration cross-plugin

El sistema MUST publicar eventos al EventLog del workspace vía
`dispatch_event_activity` para que plugins suscriptores reaccionen
(declarative orchestration, ADR-2026-05-20). Esto reemplaza llamadas
directas plugin → plugin (que violarían R-DIP).

#### Scenario: Sales registra orden → orders plugin se entera

- GIVEN el sales worker invocó `register_order` exitosamente
- WHEN la activity termina
- THEN `dispatch_event_activity` publica un evento `OrderRegistered` con shape `{plugin_origin: "chats.sales", order_id, customer_phone, total, currency, items}` al EventLog
- AND el evento queda persistido en `hubara_vault/_events/eventlog.jsonl`
- AND cualquier plugin con un transition declarado en `plugin.yaml[events.consumers]` lo procesa

#### Scenario: Customer abandona → remarketing se programa

- GIVEN el sales workflow detectó ghosting (`decide_ghosting_action` returnó `schedule_remarketing`)
- WHEN se invoca `schedule_remarketing_workflow_activity`
- THEN se programa un workflow remarketing con start_at = `now + idle_timeout_seconds`
- AND no se llama directamente al plugin remarketing — se usa Temporal scheduler

### Requirement: Active route + conversation classification

El sistema SHALL mantener `metadata.json[active_route]` ∈
`{"ventas", "remarketing", "humano"}` (definido en
`platform/constants.py`) que governa cuál worker procesa el próximo
inbound. Además SHALL clasificar el estado conversacional vía
`classify_conversation_state` (use case puro en
`plugins/chats/agent/sales/use_cases/classify_conversation_state.py`)
en uno de `{"no_reply", "nuevo", "activo", "calificado", "cotizado",
"ganado", "perdido"}` para el dashboard.

#### Scenario: Primer mensaje de un nuevo número

- GIVEN un phone sin `metadata.json` previo
- WHEN llega inbound
- THEN `LoadOrStartSalesSession` crea `metadata.json` con `active_route="ventas"` (default ROUTE_VENTAS)
- AND `classify_state(metadata, total_msgs=1, last_inbound_ms=now)` devuelve `"nuevo"`
- AND arranca workflow sales

#### Scenario: Cliente abandonado (ghosting)

- GIVEN un workflow sales activo y `idle_timeout` (5min default) excedido sin signal
- WHEN el workflow llama a `decide_ghosting_action`
- THEN si el cliente acumula 2+ ghostings → action `schedule_remarketing`, sales workflow cierra
- AND si es el primer ghosting → action `wait_longer` (extiende timeout)
- AND si el cliente respondió pero el LLM no resolvió → action `close_silently`

#### Scenario: Cliente vuelve después de remarketing

- GIVEN `active_route="remarketing"` y un workflow remarketing scheduled
- WHEN llega un inbound del cliente
- THEN `LoadOrStartSalesSession` detecta el caso y signala el sales workflow (re-arranca si no hay uno activo)
- AND `active_route` se restaura a `"ventas"`
- AND el remarketing workflow scheduled se cancela (no envía más outreach)

#### Scenario: Estado "ganado" (compra real)

- GIVEN una sesión con `registered_order.success=true` en metadata (tras `register_order` exitoso)
- WHEN se invoca `classify_state(metadata, ...)`
- THEN devuelve `"ganado"` con prioridad sobre cualquier tag
- AND el dashboard pinta la sesión en columna "Ganados"

### Requirement: Tag de conversación visible para operador

El sistema SHALL permitir al LLM marcar la conversación con un tag
(`HUMANO`, `RESCATE`, `INFO`, etc.) vía `manage_conversation_tag` tool,
que la UI muestra como badge en el sidebar de sesiones.

#### Scenario: LLM escala a humano

- GIVEN el LLM determinó que necesita humano (e.g., pregunta legal compleja)
- WHEN invoca `escalate_to_human` tool
- THEN se setea `metadata.json[tag]="HUMANO"` y `active_route="humano"`
- AND el sales workflow envía el mensaje de despedida del LLM y cierra
- AND inbounds subsecuentes NO arrancan workflow (`LoadOrStartSalesSession` corta cuando `active_route==humano`)

#### Scenario: Operador devuelve control al bot

- GIVEN `active_route="humano"` y el operador hizo `return-to-bot`
- WHEN se procesa el endpoint
- THEN `active_route="ventas"` se restaura y tag se limpia
- AND el próximo inbound arranca workflow normal

## Out of scope

- Detalle de prompts / system messages del LLM — viven en `agents/<worker>/spec.md`
- Tool implementations específicas — `agents/<worker>/spec.md`
- Endpoints HTTP del dashboard — `plugins/chats/spec.md`
- Webhook signing / security — `auth/spec.md` (TBD)

## Dependencias

- **`platform/whatsapp/`** — driving adapter HTTP outbound
- **`platform/session_history/`** — persistencia de turns
- **`platform/orchestration/`** — `dispatch_event_activity`
- **`platform/temporal/dispatcher`** — `start_or_signal_*_workflow_activity`
- **`exoclaw_temporal/activities`** — `build_prompt`, `llm_chat`, `record_turn`
