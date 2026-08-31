# Agent: sales-worker

> Behavior contract — bootstrap inicial 2026-05-25.
> Fuente: `hubara_agency/src/plugins/chats/workers/sales.py` +
> `hubara_agency/src/plugins/chats/agent/sales/`.

## Purpose

El **sales-worker** es el agente LLM (Claude/OpenAI) que conduce
conversaciones de venta vía WhatsApp para Hubara. Recibe inbounds del
cliente, los acumula en un workflow Temporal (`HubaraSalesSessionWorkflow`)
que decide cuándo responder, qué tools usar (catálogo, registro de
orden, escalación, UI rica) y cuándo terminar. Single-tenant por phone:
cada `wa_{phone}/` es un workspace aislado con su workflow + memoria.

## Requirements

### Requirement: Workspace por conversación

El sistema MUST aislar cada conversación en su propio workspace bajo
`hubara_vault/wa_{phone}/` con archivos canónicos: `IDENTITY.md`,
`SOUL.md`, `USER.md`, `TOOLS.md`, `AGENTS.md`, `memory/`, `skills/`,
`session_history.json`, `metadata.json`.

#### Scenario: Primera conversación con un phone

- GIVEN un phone `+57311XXX` sin workspace previo
- WHEN llega el primer inbound
- THEN `bootstrap_sales_session_activity` crea `wa_57311XXX/` con templates canónicos copiados de `hubara_vault/_templates/sales/`
- AND `metadata.json` se inicializa con `{active_route: "ventas", created_at: <ts>}` (default ROUTE_VENTAS de `platform/constants.py`)

#### Scenario: Conversación existente

- GIVEN `wa_57311XXX/` ya existe
- WHEN llega un nuevo inbound
- THEN `LoadOrStartSalesSession` lee `metadata.json` y decide:
  - si `active_route="ventas"` → arranca/signala workflow sales
  - si `active_route="humano"` → corta (no procesa, espera intervención)
  - si `active_route="remarketing"` → cancela remarketing scheduled + transfiere a sales y signala

### Requirement: Workflow turn-based con LLM

El `HubaraSalesSessionWorkflow` SHALL operar en **turn-based**: por cada
inbound nuevo (signal `add_user_message`), construye prompt con history +
tools disponibles, llama LLM (`llm_chat` activity), procesa response
(texto + tool calls), ejecuta tools secuencialmente, envía mensajes
outbound, y queda esperando el próximo signal.

#### Scenario: Turn simple con respuesta texto

- GIVEN un workflow activo y un signal `add_user_message("¿Tenés velas?")`
- WHEN el workflow procesa
- THEN llama `build_prompt` activity con history + tools registradas
- AND llama `llm_chat` activity → response `{text: "Sí, mirá:", tool_calls: []}`
- AND llama `send_whatsapp_message_activity("Sí, mirá:")`
- AND `record_turn` activity persiste sender=assistant
- AND queda esperando próximo signal

#### Scenario: Turn con tool calls

- GIVEN inbound `"mostrame velas de soya"`
- WHEN el LLM responde con tool call `search_products(query="soya")`
- THEN `execute_tool` activity ejecuta la tool y devuelve resultados
- AND el LLM se re-invoca con tool results en el contexto
- AND devuelve respuesta final con `present_products(handles=[...])` (decision tool)
- AND `flush_pending_ui_intents_activity` renderiza el catalog list message en WA

#### Scenario: Idle timeout (ghosting)

- GIVEN un workflow esperando signal sin recibir uno en > `idle_timeout_seconds`
- WHEN el timeout dispara
- THEN se invoca `decide_ghosting_action(workspace, ghosting_count)`
- AND si action="wait_longer" → workflow continúa esperando con timeout extendido
- AND si action="schedule_remarketing" → se invoca `schedule_remarketing_workflow_activity` y el sales workflow cierra
- AND si action="close_silently" → workflow cierra sin más outreach

#### Scenario: Cliente responde durante el turno de ghosting (corrientazo)

- GIVEN el idle timeout disparó y el turno de auto-etiquetado del ghosting está corriendo (`_force_shutdown` programado)
- WHEN llega un mensaje real del cliente antes de que ese turno toque outbound
- THEN el turno se interrumpe y se recompone con el mensaje del cliente (corrientazo)
- AND el shutdown programado por ghosting se CANCELA (el cliente volvió — la premisa del ghosting quedó invalidada)
- AND `flush_pending_ui_intents_activity` corre normalmente tras el turno recompuesto (los UI intents del turno SÍ llegan a WhatsApp)
- AND la sesión sigue viva esperando la próxima respuesta

#### Scenario: Idle timeout dinámico por Flow pendiente

- GIVEN un WhatsApp Flow fue enviado (e.g., `RequestShippingDetailsTool`) y el cliente no respondió
- WHEN se llega al timeout
- THEN `read_idle_timeout_seconds_activity` detecta el Flow pendiente y devuelve timeout extendido (default 5min → 30min)
- AND el workflow waitea más antes de decidir ghosting

### Requirement: Tools de catálogo

El sales-worker MUST tener acceso a tools que consulten el snapshot de
catálogo (mantenido por `catalog_sync`, plugin `catalog`) — NO consulta
Medusa live durante la conversación (latency + cuota).

#### Scenario: search_products

- GIVEN el snapshot del catálogo cargado en memoria
- WHEN el LLM invoca `search_products(query="soya", limit=5)`
- THEN se devuelve lista de productos matching (handle, title, price, variant_summary, thumbnail_url)
- AND la búsqueda es fuzzy + lemmatizada (matchea "velas de soya", "vela de cera de soja", etc.)

#### Scenario: filtro por categoría (determinista, typo-tolerante)

- GIVEN el snapshot con categorías `velas-religiosas` ("Velas Religiosas") y `velas-aromaticas` ("Velas Aromáticas")
- WHEN el cliente pide una categoría y el LLM invoca `search_products(q="", category="velas religosas")`
- THEN el resolver determinista (`platform/catalog/categories.py`) resuelve a `velas-religiosas` tolerando typo, plural y nombre parcial
- AND se devuelven SOLO los productos que pertenecen a esa categoría (pertenencia real, NO substring contra description)
- AND el envelope trae `category.matched` con el NOMBRE real de la categoría
- AND si la query es ambigua (ej. "velas") `matched` es null y `candidates` trae las categorías empatadas
- AND si no resuelve, `available` trae la lista CERRADA de categorías existentes — el agente NUNCA niega una categoría sin mirarla

#### Scenario: list_categories

- GIVEN el snapshot del catálogo cargado
- WHEN el LLM invoca `list_categories()`
- THEN se devuelve la lista cerrada de categorías con su nombre real y `product_count`
- AND el orden es estable (alfabético por nombre) entre turnos

#### Scenario: color pedido en otro signo (variant_colors, Duo Zodiacal)

- GIVEN un producto multi-variante cuyo mapeo signo→color vive en `product.metadata["colores"]` (cada signo viene en UN color fijo; ej. Leo=naranja, Aries=rojo)
- WHEN el cliente pide un color que NO es el del signo elegido (ej. "Leo en rojo") y el LLM invoca `set_order_slot(diseno="Leo", color="rojo")`
- THEN la combinación se rechaza determinísticamente (`rejected` con `reason: color_sign_mismatch`) — el valor recién llegado NO se escribe al draft
- AND el envelope trae `sign_colors` (el color real del signo pedido) y `same_color_signs` (los signos que SÍ tienen el color pedido)
- AND el agente ofrece el MISMO color en el otro signo aclarando explícitamente que el signo es distinto — NUNCA niega el color ni registra la combinación inexistente
- AND si el cliente da color sin signo, el envelope trae `signs_for_color` para ofrecer el signo dueño del color de una
- AND el matching de color es tolerante a género/número/acentos ("ROJAS" → "rojo") y la paleta citable sale de las variantes reales, no de tags stale
- AND `get_product_by_handle` expone el mapeo como `variant_colors` en el detalle del producto

#### Scenario: get_product_by_handle

- GIVEN un handle válido `wax-soja-vainilla`
- WHEN el LLM invoca `get_product_by_handle("wax-soja-vainilla")`
- THEN se devuelve product detail completo (variants, prices, images, description, stock)
- AND si el handle no existe, devuelve `{error: "product_not_found"}`

#### Scenario: Snapshot stale

- GIVEN el snapshot tiene > 60min sin actualizarse
- WHEN se invocan tools de catálogo
- THEN aún devuelven datos (stale pero válidos) — `catalog_sync` correrá pronto
- AND si el snapshot está corrupto o no cargado, las tools devuelven `{error: "catalog_unavailable"}` y el LLM debe decirle al cliente "estoy verificando..."

### Requirement: Tool de cierre — register_order

El sales-worker MUST poder cerrar la venta vía `register_order` tool que
crea una draft order en Medusa (o stub local si Medusa no configurado).

#### Scenario: Registration exitosa contra Medusa

- GIVEN env `MEDUSA_REGION_ID` + `MEDUSA_SALES_CHANNEL_ID` configuradas
- WHEN el LLM invoca `register_order` con payload completo (customer, items, shipping)
- THEN `MedusaOrderRegistration` adapter hace `POST /admin/draft-orders`
- AND devuelve `{success: true, order_id: "draft_01HXX...", total, currency}`
- AND `dispatch_event_activity` publica `OrderRegistered` al EventLog
- AND el LLM continúa con `present_order_confirmation` para mostrar resumen al cliente

#### Scenario: Stub fallback (Medusa no configurado)

- GIVEN Medusa no configurado
- WHEN se invoca `register_order`
- THEN `StubOrderRegistration` adapter persiste el payload en `metadata.json[failed_order_registrations[]]` con `order_id="HUB-{uuid}"`
- AND devuelve `{success: true, order_id: "HUB-...", warning: "stub_mode"}`
- AND el cliente recibe confirmación normalmente (no se entera del stub)
- AND el `vault-orders` endpoint expone el stub para reconciliación manual

#### Scenario: Medusa rechaza el payload

- GIVEN Medusa configurado pero rechaza el `POST /admin/draft-orders` (5xx o validation error)
- WHEN la tool corre
- THEN se persiste el payload en `metadata.json[failed_order_registrations[]]` con `kind="failed"`, `error_detail`
- AND la tool devuelve `{success: false, error_detail: "..."}`
- AND el LLM debe decirle al cliente "tuvimos un problema, te confirmamos en un momento"

#### Scenario: Nota operativa del portavelas viaja al humano

- GIVEN `register_order` devolvió `registered=true`
- WHEN la tool arma el `order_registered_decision.motivo` (el texto que la red de seguridad `ensure_payment_pending_closure` escribe en `metadata.motivo` al escalar)
- THEN el motivo SHALL incluir la nota "definir con el cliente el color del portavelas (según disponibilidad)"
- AND el envelope instruye al LLM a incluir la misma nota en el `summary` de `escalate_to_human(PAYMENT_VERIFICATION_PENDING)`
- AND a avisarle al comprador en la despedida que al finalizar el pago del pedido se escogen los colores del portavelas

### Requirement: Política de color del portavelas

El sales-worker MUST responder a la pregunta por el color del portavelas
que el color es según disponibilidad, y MUST NOT tratarlo como variante
del pedido (no se fija con `set_order_slot` ni se ofrece con picker).

#### Scenario: Cliente pregunta el color del portavelas

- GIVEN una conversación en cualquier etapa del funnel
- WHEN el cliente pregunta de qué color es el portavelas
- THEN el agente responde que el color del portavelas es según disponibilidad
- AND que al finalizar el pago del pedido se escogen los colores
- AND NO promete un color específico ni lo registra como slot del pedido

### Requirement: Tools de UI rica (decision tools)

El sales-worker MUST tener 10 decision tools que emiten UI intents
renderizados post-LLM como mensajes WhatsApp nativos:
`present_product_detail`, `present_products`, `present_product_gallery`,
`present_variant_picker`, `present_order_confirmation`,
`request_shipping_details`, `react_to_message`, `send_quick_replies`,
`send_contact_card`, `send_cta_url`.

#### Scenario: present_products renderiza catalog list message

- GIVEN el LLM invocó `present_products(handles=["a", "b", "c"])`
- WHEN el workflow flushea UI intents
- THEN se envía un mensaje WA tipo `interactive.list` con header, body, footer, sections
- AND cada section item incluye title, description, image_url del producto
- AND el cliente puede tappear → genera inbound con `list_reply.id=<handle>`

#### Scenario: present_variant_picker para >=4 variants

- GIVEN un producto con ≥4 variantes (aromas, colores)
- WHEN el LLM invoca `present_variant_picker(product_handle, variants)`
- THEN se renderiza una lista WA tappable con un emoji curado por variant (de `variant_emoji.py`)
- AND el emoji NO lo elige el LLM (closed-list interno, evita repetición fea de 🌿🌿🌿)

#### Scenario: send_quick_replies en saludo inicial

- GIVEN primera conversación, mensaje "hola"
- WHEN el LLM responde con saludo + `send_quick_replies([{title: "Ver catálogo"}, {title: "Promos"}, {title: "Asesor humano"}])`
- THEN se envía un mensaje WA tipo `interactive.button` con 3 botones
- AND el cliente puede tappear → genera inbound con `button_reply.title`

### Requirement: Escalación a humano

El sales-worker MUST tener `escalate_to_human` tool que cierra la
conversación agéntica, setea `active_route="humano"` y devuelve el
control al operador via dashboard.

#### Scenario: LLM determina escalación

- GIVEN el LLM detectó una pregunta fuera de scope (legal, devolución compleja, queja)
- WHEN invoca `escalate_to_human(reason)`
- THEN se setea `metadata.json[tag]="HUMANO"`, `active_route="humano"`
- AND la tool devuelve `{escalation_decision: true, reason}`
- AND el workflow envía mensaje de despedida del LLM ("Te conecto con un asesor humano...") y cierra
- AND inbounds subsecuentes NO arrancan workflow (espera intervención manual)

### Requirement: Transcripción de audio inbound

El sales-worker MUST transcribir audios inbound vía
`transcribe_audio_activity` (Groq o OpenAI Whisper) antes de procesar el
turn, para que el LLM reciba texto en vez de bytes.

#### Scenario: Audio en español

- GIVEN un inbound con `type="audio"` y `media_url` válida
- WHEN el workflow procesa
- THEN `transcribe_audio_activity` descarga el audio, lo manda a Groq/OpenAI, devuelve transcript
- AND el turn se persiste con `content=transcript` y `metadata={original_type: "audio", duration_sec, ...}`
- AND el LLM ve el texto como si fuera un mensaje texto normal

#### Scenario: Transcripción falla

- GIVEN el provider de transcripción devuelve error o timeout
- WHEN la activity falla N=3 intentos
- THEN el workflow envía mensaje fallback `"No pude entender el audio, ¿podés escribirlo?"` y queda esperando
- AND el incident se loguea con `audio_media_id` para debug

### Requirement: Memoria persistente del agente

El sistema SHOULD permitir al sales-worker leer/escribir a su workspace
memory vía `workspace/memory/*.md` files para retener context entre
conversaciones del mismo phone (notas del operador, preferencias del
cliente, intentos previos de venta).

#### Scenario: Cliente vuelve después de meses

- GIVEN un workspace con `memory/customer_notes.md` que dice "prefiere velas grandes"
- WHEN llega un nuevo inbound de este phone
- THEN el sistema prompt incluye contenido relevante de memory/
- AND el LLM puede usarlo para personalizar (mostrar primero velas grandes)

## Out of scope

- Detalle del prompt engineering / SOUL.md / USER.md — viven en `hubara_vault/_templates/sales/`
- Workflow remarketing — `agents/remarketing-worker/spec.md` (pendiente)
- EventLog mechanics — `messaging/spec.md`
- Tool DTOs específicas — código vivo en `agent/sales/tools/*`
- Métricas / observability — `observability/spec.md` (pendiente)

## Dependencias

- **`HubaraSalesSessionWorkflow`** — workflow Temporal turn-based
- **`platform/catalog`** — snapshot consumido por tools
- **`platform/orders`** — `OrderRegistrationPort` para `register_order`
- **`platform/whatsapp`** — outbound activities
- **`exoclaw_temporal`** — `build_prompt`, `llm_chat`, `record_turn`
- **`platform/tool_extensions`** — `register_tool_extension` + `apply_tool_extensions`
- **`platform/orchestration`** — `dispatch_event_activity`
