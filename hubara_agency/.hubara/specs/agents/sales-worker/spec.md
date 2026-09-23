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

#### Scenario: signo en otro color — la foto es referencia (Duo Zodiacal, 2026-09-23)

- GIVEN un producto multi-variante cuyo mapeo signo→color vive en `product.metadata["colores"]` (el color de la FOTO de cada signo; ej. Leo=naranja, Aries=rojo)
- WHEN el cliente pide un color que NO es el de la foto del signo elegido (ej. "Leo en rojo") y el LLM invoca `set_order_slot(diseno="Leo", color="rojo")`
- THEN se guardan los dos: la vela del Duo se hace en cualquier color de la paleta, en cualquier signo y sin costo extra (regla del operador, 2026-09-23)
- AND el envelope trae `custom_color` (`sign`, `photo_colors`, `color`) y el `summary` instruye a confirmar "la foto es de referencia; te la hacemos en rojo" — NUNCA hacerle elegir entre su signo y su color
- AND si el cliente da color sin signo, el envelope trae `signs_for_color` (qué foto muestra ese color) como referencia visual, sin empujar ese signo
- AND el color sigue siendo closed-list contra la paleta real (variantes, no tags stale); tolerante a género/número/acentos ("ROJAS" → "rojo")
- AND `get_product_by_handle` expone el mapeo como `variant_colors` en el detalle del producto

#### Scenario: Duo con signos distintos en plato y vela (2026-09-22)

- GIVEN una clienta que pide "el plato con el signo de Leo y la vela con el de Escorpio"
- THEN el agente confirma que SÍ se hace, al mismo precio; el signo de la vela va en `diseno` y el del plato en `notas`
- AND si no hay en stock, informa elaboración de 1 a 2 días (hasta 2 del mismo signo; 3 o más del mismo signo, el equipo confirma el tiempo) y que le envían foto al estar lista
- AND no confunde el color de la vela con el del plato (si no está claro, lo pregunta en una línea)

#### Scenario: tono pedido dentro de una familia de color (tolerancia de gama)

- GIVEN un producto cuyo catálogo ofrece una FAMILIA de color (tag `Color: Azul`, o alias `azul petróleo` en `variant_colors`)
- AND la tabla de familias del tenant (`config/color_families/families.yaml`, override por `COLOR_FAMILIES_PATH`, hot-reload por mtime) declara los tonos de cada familia en español (celeste, azul mar, marino… → azul; fucsia → rosado; lavanda → lila)
- WHEN el cliente pide un tono ("azul clarito", "azul mar", "celeste") y el LLM invoca `set_order_slot(color=<palabras del cliente>)`
- THEN el color se resuelve determinísticamente a la familia del catálogo y se persiste el color REAL (`Azul` / `azul petróleo`) — NO se rechaza
- AND el tono pedido queda en `notas` del draft para el operador ("Tono de color pedido por el cliente: 'azul clarito' (registrado como Azul)")
- AND el envelope trae `color_family` (`requested`, `captured`, `family`, `shade_requested`) y el `summary` instruye al agente a confirmar que SÍ manejamos ese color y a mostrar el tono real (`present_product_detail`) sin negarlo ni prometer el tono exacto
- AND una variación de género/número ("azules") se captura sin ceremonia de tono (`shade_requested: false`, sin nota)
- AND si la gama tiene VARIOS colores en el producto (ej. `Lila` y `Morado` para "lila o morado") el slot NO se adivina: `rejected` con `reason: color_family_ambiguous` y `candidates` para ofrecerlos
- AND si la familia pedida no existe en el producto ("vinotinto" con paleta Azul/Blanco) → `rejected` con `reason: color_family_not_offered`, `family: Rojo` y la paleta `available`
- AND una palabra sin familia conocida ("chartreuse") conserva el rechazo legacy; sin archivo de familias el matcheo vuelve a ser exacto (degrada abierto)
- AND los guiones de etapa (descubrimiento, variantes, FAQ del sales_script) MUST NOT responder "ese tono no lo manejo" cuando la familia existe

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

#### Scenario: Nota operativa del portavelas viaja al humano (solo si el pedido lo incluye)

- GIVEN `register_order` devolvió `registered=true`
- AND algún ítem del pedido es un producto que trae portavela según el catálogo (`metadata.portavelas` explícito, o mención "portavela" en título/description — hoy el Dúo Zodiacal)
- WHEN la tool arma el `order_registered_decision.motivo` (el texto que la red de seguridad `ensure_payment_pending_closure` escribe en `metadata.motivo` al escalar)
- THEN el envelope SHALL traer `portavelas.included=true` + `order_registered.portavelas_included=true`
- AND el motivo SHALL incluir la nota "enviarle al cliente foto de los colores disponibles del portavelas para que escoja"
- AND el envelope instruye al LLM a incluir la misma nota en el `summary` de `escalate_to_human(PAYMENT_VERIFICATION_PENDING)`
- AND a avisarle al comprador en la despedida que le enviarán una foto con los colores disponibles del portavelas para que escoja (nunca "al finalizar el pago": quien paga contra entrega no entiende cuándo)

#### Scenario: Pedido sin portavelas — nadie habla del portavelas (run 943e6bff, 2026-09-07)

- GIVEN `register_order` devolvió `registered=true`
- AND ningún ítem del pedido trae portavela (o el catálogo no está disponible / el handle no resuelve — la tool es conservadora)
- THEN el envelope SHALL traer `portavelas.included=false` y el motivo MUST NOT mencionar el portavelas
- AND el envelope instruye al LLM a NO mencionar el portavelas ni sus colores, ni al cliente ni en el summary
- AND si igual el LLM escribe una oración sobre el portavelas en la despedida, el workflow (gate `portavelas-notice-guard-v1`) SHALL removerla antes de enviar y de persistir, dejando el resto del mensaje intacto (si no queda texto, envía la despedida mínima "Listo, tu pedido quedó registrado 🤍. Gracias por elegir a Hubara.")

### Requirement: Política de color del portavelas

El sales-worker MUST responder a la pregunta por el color del portavelas
(el "plato") que se escoge después, con una foto de los colores disponibles
(regla del operador 2026-09-23), y MUST NOT tratarlo como variante
del pedido (no se fija con `set_order_slot` ni se ofrece con picker). La
política aplica SOLO a los productos que traen portavela (hoy el Dúo
Zodiacal); el agente MUST NOT mencionar el portavelas por su cuenta cuando
el pedido no incluye uno.

#### Scenario: Cliente pregunta el color del portavelas

- GIVEN una conversación en cualquier etapa del funnel
- WHEN el cliente pregunta de qué color es el portavelas
- THEN el agente responde que el color del portavelas lo escoge después: le envían una foto con los colores disponibles
- AND MUST NOT decir "al finalizar el pago" (2026-09-22: la clienta pagaba contra entrega y preguntó dos veces de qué color quedaba)
- AND NO promete un color específico ni lo registra como slot del pedido

### Requirement: Datos de envío — quién recibe (2026-08-31)

El formulario de datos de envío (WhatsApp Flow `shipping_v2` y su fallback
de texto plano) MUST incluir el **nombre de quien recibe** (obligatorio) y
el **número de cédula de quien recibe** (opcional). `register_order` MUST
rechazar (`registered=false`, `error_detail=missing_receiver_name`) un
registro sin `shipping.receiver_name`; el nombre viaja a
`shipping_address.first_name/last_name` de la draft order Medusa y la
cédula (si está) a `metadata.receiver_national_id`.

#### Scenario: Registro sin nombre de quien recibe

- GIVEN el LLM invoca `register_order` sin `shipping.receiver_name`
- WHEN la tool corre
- THEN devuelve `registered=false` con `error_detail=missing_receiver_name` SIN llamar al port
- AND el summary instruye recolectar el dato (`set_order_slot(nombre_recibe=...)`) y reintentar

#### Scenario: Cédula opcional

- GIVEN el cliente no dio cédula
- WHEN se registra el pedido con los demás datos completos
- THEN el registro procede normalmente y ningún campo de cédula viaja a Medusa

### Requirement: Formas de pago informadas (2026-08-31)

El sales-worker MUST informar exactamente TRES formas de pago, con sus
condiciones: **contra entrega** (compras desde $45.000 COP en productos,
umbral INCLUSIVO — `config/shipping.py`; el valor del envío se calcula con la
transportadora), **pago anticipado** (Nequi o llave
3229041190) y **link de pago** (recargo adicional de 1,5% sobre la venta
con Nequi o Bancolombia, 2,69% con otros bancos). "Tarjeta" ya NO es una
opción directa (queda cubierta por el link de pago). Los ids de método en
`register_order` son `cash_on_delivery` / `transfer` / `payment_link`
(`card` queda solo como legacy de lectura).

#### Scenario: Instrucciones deterministas de pago anticipado

- GIVEN un pedido registrado con `payment_method=transfer`
- WHEN el flush procesa el intent `payment_instructions`
- THEN el mensaje incluye la llave/Nequi (default 3229041190, override `PAYMENT_NEQUI_NUMBER`) y, si `PAYMENT_TRANSFER_*` está completo en env, el bloque bancario verbatim
- AND con la llave desactivada (`PAYMENT_NEQUI_NUMBER=""`) y sin config bancaria completa NO se envía nada (fail-closed)

#### Scenario: Desglose de lo que paga el cliente (2026-09-07)

- GIVEN un pedido registrado con `payment_method` transfer o payment_link (montos validados por SEC-07: subtotal = Σ ítems, total = subtotal + envío)
- WHEN el flush renderiza el intent `payment_instructions`
- THEN el mensaje muestra `*Productos*`, `*Envío*` (o "sin costo" si es 0) y `*Total*` (`*Total sin recargo*` para link de pago), en ese orden, antes de la referencia del pedido
- AND un intent sin `subtotal_cop`/`shipping_cop` (encolado antes del deploy) sigue mostrando la línea única `*Valor*` — el sistema nunca inventa un reparto

#### Scenario: Aviso determinista del link de pago

- GIVEN un pedido registrado con `payment_method=payment_link`
- WHEN el flush procesa el intent
- THEN el cliente recibe el aviso de que el link llega por el chat, con el recargo (1,5% / 2,69%) y el valor sin recargo
- AND el link real lo genera el humano tras la escalación `PAYMENT_VERIFICATION_PENDING`

### Requirement: El valor del envío nunca es definitivo (2026-09-07)

Las tarifas de envío publicadas ($7.900 Bogotá y municipios cercanos,
$16.940 nivel nacional) son MÍNIMAS: el valor final lo recalcula la
transportadora antes de despachar según tamaño y peso. El sistema MUST NOT
darle al cliente un valor de envío como definitivo. Con **contra entrega**
el envío se paga al recibir, así que el resumen del pedido MUST NOT mostrar
valor de envío ni total; con **pago anticipado / link de pago** el envío se
cobra por adelantado con la tarifa mínima y el resumen MUST aclararlo como
tal. Los textos viven en `config/shipping.py` (`SHIPPING_RATES_MESSAGE`,
`ORDER_SUMMARY_SHIPPING_NOTE`); el LLM no los redacta.

#### Scenario: Pregunta por el valor del envío → mensaje estándar

- GIVEN el cliente pregunta cuánto vale / cuesta el envío
- WHEN el LLM invoca `send_shipping_rates` (sin parámetros)
- THEN el flush envía EXACTAMENTE `SHIPPING_RATES_MESSAGE` (tarifas mínimas Bogotá / nacional + "el valor definitivo se confirma al despachar"), ignorando cualquier param del intent
- AND la tool corta el turno (L-11): el mensaje ES la respuesta, sin burbuja adicional reformulando tarifas
- AND el historial del dashboard registra el texto real enviado

#### Scenario: Resumen del pedido contra entrega — envío "Por confirmar"

- GIVEN el LLM invoca `present_order_confirmation` con `payment_method=cash_on_delivery` y `shipping_cop` (tarifa mínima estimada, incluso 0)
- WHEN el flush renderiza el intent `order_confirmation`
- THEN el body muestra los ítems, `Subtotal productos: $X COP`, `Envío: Por confirmar*`, `📍 Dirección: …`, `💳 Medio de pago: Contra entrega` y la nota `📌 El valor final del envío se recalculará directamente con la transportadora antes de despachar y te lo confirmaremos para cerrar tu pedido.`
- AND NO aparece el valor del envío ni una línea de total
- AND `shipping_cop`/`total_cop` siguen viajando en el intent (analytics + consistencia con `register_order`) y el envelope al LLM no trae un total con envío

#### Scenario: Resumen del pedido pago anticipado / link — envío como tarifa mínima

- GIVEN el LLM invoca `present_order_confirmation` con `payment_method` transfer o payment_link
- WHEN el flush renderiza el intent `order_confirmation`
- THEN el body muestra `Subtotal productos: $X COP`, `Envío (tarifa mínima): $Y COP` (o `Envío: sin costo` si es 0) y `Total: $Z COP`, seguidos de dirección y medio de pago
- AND NO aparece "Por confirmar" ni la nota 📌 (solo aplican a contra entrega)
- AND el envelope al LLM trae el total y le pide aclarar "tarifa mínima" si menciona el envío

### Requirement: El precio sale del catálogo, nunca del anuncio ni del cliente (2026-09-16)

Incidente run ebbc203d: el referral del anuncio traía "$45.000", el catálogo
tenía el set a $49.500, el bot cotizó el del anuncio, el formulario de envío
ocultó "Contra entrega" (umbral estricto) y el resumen salió a $49.500 sin
explicación. El sales-worker MUST tomar todo precio de producto del catálogo
(snapshot o live verificado); ningún monto que provenga del LLM, del anuncio
o del cliente MUST llegar a un componente, al resumen ni a Medusa.

#### Scenario: El anuncio no es fuente de precio

- GIVEN un primer inbound con referral CTWA cuyo headline/body trae montos ("💲 $45.000")
- WHEN el ingest arma el banner `[el cliente vino desde un anuncio…]`
- THEN los montos del creative van enmascarados (`[precio omitido: usa el del catálogo]`) y el resto del texto se conserva

#### Scenario: El formulario de envío calcula el total desde el catálogo

- GIVEN el LLM invoca `request_shipping_details(items=[{handle, quantity}])`
- WHEN la tool resuelve los handles en el catálogo
- THEN el total del Flow, el resumen del header y la disponibilidad de contra entrega (desde $45.000 en productos, inclusive) salen del catálogo
- AND un `order_total_cop` que aún mande el LLM se ignora; un handle inexistente devuelve `unknown_handle` sin mostrar nada al cliente

#### Scenario: La verificación fija los precios y detecta lo cotizado en el chat

- GIVEN el LLM invoca `verify_order_for_checkout`
- WHEN la verificación live responde
- THEN el envelope trae `unit_price_cop` por ítem y `subtotal_cop` (live si Medusa cambió, si no snapshot) y el ledger `metadata.checkout_verification` queda persistido
- AND si el bot escribió en el episodio un monto que no es precio de catálogo (ni múltiplo/suma de líneas, ni monto de política en su contexto), el envelope trae `quoted_price_mismatch=true` con los montos y la instrucción de aclararlo ANTES de presentar la confirmación

#### Scenario: Confirmación y registro rechazan cualquier otro precio

- GIVEN `present_order_confirmation` o `register_order` reciben un `unit_price_cop` distinto del catálogo (snapshot) y del live verificado
- WHEN la tool valida los ítems
- THEN responde `price_mismatch` con el precio esperado, no encola el resumen ni registra en Medusa
- AND con el catálogo caído (sin referencia) degrada a SEC-07 + gate humano de pago en vez de bloquear la venta

#### Scenario: Umbral de contra entrega inclusivo y único

- GIVEN un pedido con $45.000 exactos en productos
- WHEN el formulario de envío (Flow o texto plano) arma las formas de pago
- THEN "Contra entrega" aparece; el umbral vive en `config/shipping.py` y los prompts lo citan como "desde $45.000 en productos"

### Requirement: Tools de UI rica (decision tools)

El sales-worker MUST tener 11 decision tools que emiten UI intents
renderizados post-LLM como mensajes WhatsApp nativos:
`present_product_detail`, `present_products`, `present_product_gallery`,
`present_variant_picker`, `present_order_confirmation`,
`request_shipping_details`, `react_to_message`, `send_quick_replies`,
`send_contact_card`, `send_cta_url`, `send_shipping_rates`.

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
- WHEN el LLM responde con saludo + `send_quick_replies([{title: "Ver catálogo"}, {title: "Promos"}, {title: "Hablar con alguien"}])`
- THEN se envía un mensaje WA tipo `interactive.button` con 3 botones
- AND el cliente puede tappear → genera inbound con `button_reply.title`

### Requirement: Saludo garantizado en el primer contacto (2026-09-11)

En el PRIMER intercambio de una conversación (el historial que ve el LLM no
tiene ningún mensaje del agente), el cliente MUST recibir la Burbuja 1 del
guion de apertura (saludo según la hora de Bogotá + propuesta de valor) ANTES
de cualquier menú o componente visual, sin depender de dónde el LLM puso el
texto. Motivación: runs dc32f7fe y 3ce50ef3 (CTWA "amor y
amistad") — el LLM saludó como content junto a `search_products` (descartado
por el default-deny) y cerró el turno con `present_products` → menú sin saludo.
La decisión es pura (`first_contact_greeting.should_send_first_contact_greeting`)
y la hora vive en la activity `build_first_contact_greeting` (R-DET). Gated por
`workflow.patched("first-contact-greeting-v1")`.

#### Scenario: primer contacto que sale por present_products sin saludo

- GIVEN el historial no tiene mensajes del agente (primer contacto)
- AND el LLM saluda como content junto a `search_products` y termina el turno con `present_products(intro_text="Estas son nuestras piezas para amor y amistad:")`
- WHEN el workflow procesa el turno
- THEN envía la burbuja "¡Buenas noches! Bienvenido a *Hubara*, velas artesanales hechas a base de cera de palma, a mano en Colombia." (saludo según hora) ANTES del flush del menú
- AND la persiste al dashboard como mensaje del agente
- AND la envía UNA sola vez (el turno de ghosting no re-saluda)

#### Scenario: cliente con conversación previa no recibe re-saludo

- GIVEN el historial ya tiene mensajes del agente
- WHEN un turno termina con `present_products`
- THEN NO se inyecta ninguna burbuja de saludo (regla del guion: retomar el hilo)

#### Scenario: el saludo ya viajó en el canal legítimo

- GIVEN primer contacto
- AND el `intro_text` de la tool (o una burbuja de texto del turno) ya contiene "Buenas noches" / "Bienvenido" / "Hola"
- WHEN el workflow procesa el turno
- THEN NO duplica el saludo

#### Scenario: turno de texto solo

- GIVEN primer contacto
- WHEN el LLM responde solo texto (sin tools outbound), ej. "¡Buenas tardes! Bienvenido a *Hubara*... ¿Buscas algo para ti o es para regalo?"
- THEN el texto sale como siempre y no se inyecta nada (caso run a15bb71c)

### Requirement: Escalación a humano

El sales-worker MUST tener `escalate_to_human` tool que cierra la
conversación agéntica, setea `active_route="humano"` y devuelve el
control al operador via dashboard.

#### Scenario: LLM determina escalación

- GIVEN el LLM detectó una pregunta fuera de scope (legal, devolución compleja, queja)
- WHEN invoca `escalate_to_human(reason)`
- THEN se setea `metadata.json[tag]="HUMANO"`, `active_route="humano"`
- AND la tool devuelve `{escalation_decision, customer_message}`
- AND el workflow envía el `customer_message` como despedida y cierra
- AND inbounds subsecuentes NO arrancan workflow (espera intervención manual)

#### Scenario: El relevo no delata al bot (run 5ed9af2d)

El cliente NUNCA debe notar cuándo lo atiende el bot y cuándo una persona: el
relevo se nombra "un colega / un compañero del equipo", jamás con vocabulario
que oponga persona vs. sistema ("humano", "bot", "IA", "asistente virtual",
"automático").

- GIVEN el LLM decide escalar (p. ej. `BULK_ORDER`, ~100 unidades)
- WHEN invoca `escalate_to_human(reason_category, summary, customer_message)`
- THEN la escalación TERMINA el turno: el workflow NO pide otro `llm_chat` tras el tool result (antes ese `llm_chat` forzado producía un acuse al sistema — "Listo, la conversación quedó en manos del equipo humano." — y se enviaba al cliente)
- AND el único texto que recibe el cliente es el `customer_message` que devuelve la tool
- AND el `content` que el LLM emita junto a la tool call se descarta (default-deny)

#### Scenario: customer_message ausente o que rompe la persona

- GIVEN el LLM llama `escalate_to_human` sin `customer_message` (sesión en vuelo con el schema viejo) o con oraciones que `breaks_human_persona` / `looks_like_admin_leak` marcan
- WHEN la tool arma el envelope
- THEN se caen SOLO las oraciones marcadas (el aviso del portavelas sobrevive a un "un humano verificará tu pago")
- AND si falta el texto o lo que queda tiene menos de 4 palabras, `customer_message` es la despedida aprobada de la categoría (default: "Un colega del equipo te responde en este mismo chat 🤍")
- AND el cliente nunca queda sin respuesta ni recibe la oración marcada

#### Scenario: La despedida sale aunque el batch traiga un picker

- GIVEN un batch `[present_variant_picker, escalate_to_human]`
- THEN la supresión "el picker ya es el mensaje" NO aplica: el `customer_message` se envía igual

#### Scenario: La escalación rechazada NO corta el turno

- GIVEN la guarda de Sales rechaza la escalación (`escalated: false`, sin `escalation_decision`)
- WHEN el tool-loop procesa el result
- THEN el turno continúa y el LLM decide el siguiente paso con el error en contexto

#### Scenario: Los guiones propios no dictan la palabra prohibida

- GIVEN cualquier línea DICTADA al agente (`*"…"*` en el workspace, `customer_message='…'` o "despide … con: '…'" en strings de Python)
- THEN `breaks_human_persona(línea)` es False (guard `test_scripted_customer_lines_keep_persona.py`)

### Requirement: El cierre comercial termina el turno (run b06636a6)

`manage_conversation_tag` MUST declarar en su envelope (`tag_closure`) si el
tag aplicado es AUTOSUFICIENTE (`ends_turn`). Un tag autosuficiente
(`INTERESADO`, `RECHAZO`, `COMPRA_EXITOSA`, o un `CONFIRMADO_SIN_DATOS`
degradado a `INTERESADO`) MUST terminar el turno sin otro `llm_chat` cuando ya
no queda nada que pedirle al modelo. Los tags combo (`CONFIRMADO_SIN_DATOS`,
`CONFIRMADO_PAGO_PENDIENTE`) MUST NOT terminarlo: exigen `escalate_to_human` y
ahí el modelo todavía tiene trabajo (el resumen para el colega).

El texto para el cliente MUST viajar en el param `customer_message` de la tool
y validarse DENTRO de la tool (activity), nunca en el workflow.

#### Scenario: Cierre por ghosting (turno admin)

- GIVEN el cliente dejó de responder y el sistema inyectó el trigger de ghosting
- WHEN el LLM llama `manage_conversation_tag("INTERESADO" | "RECHAZO", motivo)`
- THEN el turno termina ahí: el workflow NO pide otro `llm_chat` (antes ese `llm_chat` forzado producía el acuse "Etiqueta registrada.", ~55K prompt tokens por cierre)
- AND ningún texto llega al cliente

#### Scenario: El cliente se despide (turno de cliente)

- GIVEN el cliente dice "no gracias" o "lo voy a pensar"
- WHEN el LLM llama `manage_conversation_tag(tag, motivo, customer_message)`
- THEN el único texto que recibe el cliente es ese `customer_message`, una sola vez
- AND el `content` que el LLM emita junto a la tool call se descarta (default-deny)
- AND el workflow NO pide otro `llm_chat`

#### Scenario: customer_message con oraciones inseguras

- GIVEN el `customer_message` trae oraciones que `breaks_human_persona` / `looks_like_admin_leak` marcan
- WHEN la tool arma el envelope
- THEN se caen SOLO esas oraciones
- AND si no sobrevive ninguna, el `customer_message` viaja VACÍO (≠ ausente) y el turno termina en silencio: al modelo que confundió al destinatario no se le reabre el canal

#### Scenario: Tag de cierre sin customer_message en turno de cliente

- GIVEN el LLM llama la tool sin `customer_message` (sesión en vuelo con el schema viejo, o prefiere responder aparte)
- THEN el turno NO se corta: el cliente sigue esperando respuesta y el siguiente `llm_chat` es legítimo
- AND el tool result describe un hecho y nombra al destinatario ("Tu próximo mensaje lo lee el cliente"), sin forma de parte interno

#### Scenario: Etiqueta degradada

- GIVEN el LLM manda `CONFIRMADO_SIN_DATOS` con `customer_message`, pero no hay confirmación de compra y la tool degrada a `INTERESADO`
- THEN el envelope NO declara `customer_message`: el modelo lo redactó bajo una premisa que la tool rechazó ("un colega te escribe por los datos de envío") y nadie va a escalar
- AND en turno de cliente el turno NO se corta: el modelo responde con el aviso de degradación a la vista
- AND en turno admin se corta igual, sin texto

#### Scenario: Picker en el mismo batch

- GIVEN un batch `[present_variant_picker, manage_conversation_tag(..., customer_message)]`
- THEN gana el corte L-11: el picker ES el mensaje del turno (igual que antes de este requirement)
- AND la despedida no se envía, no se persiste al dashboard y el LLM no la recuerda
- AND (contraste) con `escalate_to_human` es al revés — la despedida sobrevive al picker — porque la escalación es definitiva

#### Scenario: Una tool del batch falló

- GIVEN cualquier tool del batch devolvió `error` (p. ej. dos tags y el último rebota por precondición)
- THEN el turno NO se corta aunque otro tag del batch haya declarado su cierre: el modelo tiene que leer el error

#### Scenario: CONFIRMADO_SIN_DATOS en el cierre por ghosting

- GIVEN el LLM marca `CONFIRMADO_SIN_DATOS` (con confirmación de compra registrada)
- THEN el turno continúa para que llame `escalate_to_human("ORDER_PENDING_SHIPPING_DETAILS", summary)`
- AND si en vez de escalar acusa recibo, la red `ensure_closing_escalation` escala por él; el acuse no sale

### Requirement: Lo que no salió, el LLM no lo recuerda (run b06636a6 → 5ed9af2d)

El historial que el LLM lee en la sesión siguiente MUST NOT contener como
mensaje `assistant` un texto final que nunca llegó al cliente por ser de un
turno admin o por oler a parte interno. El resto del turno (mensaje del
cliente, tool calls, tool results) MUST conservarse.

#### Scenario: Acuse de un turno admin

- GIVEN un cierre por ghosting en el que el LLM escribe texto (acuse, resumen, o la despedida de una escalación)
- THEN ese texto no se envía Y no se persiste con `record_turn`
- AND queda en la traza del turno (`llm_text`, `suppressed_reason: admin_turn`)

#### Scenario: Texto bloqueado por el tripwire en un turno de cliente

- GIVEN el texto final huele a parte interno (`looks_like_admin_leak`)
- THEN no se envía Y no se persiste como `assistant`

#### Scenario: La abstención explícita sí se recuerda

- GIVEN un turno de cliente o de handoff (NO admin) en el que el LLM responde el sentinel `NO_MESSAGE`
- THEN no se envía, pero SÍ queda en el historial: es el canal correcto de abstención y verlo usado es el few-shot bueno
- AND en un turno admin se recorta como cualquier otro texto (ahí la respuesta correcta era la tool call, no el sentinel)

#### Scenario: Turno admin sin ninguna tool call

- GIVEN un cierre por ghosting en el que el LLM escribe prosa y NO llama ninguna tool
- THEN `record_turn` se agenda igual pero con la lista VACÍA: el turno "nunca pasó"
- AND el trigger `[SISTEMA]` de ghosting NO queda en el historial sin cerrar (la sesión siguiente lo leería pegado al mensaje nuevo del cliente y podría aplicarle esa orden vieja)

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

### Requirement: Confirmación de compra antes del cierre

El sistema SHALL registrar de forma determinista la confirmación de compra del
cliente (`episode.order_draft.confirmed_at_ms`): un mensaje afirmativo ("sí",
"dale", "lo quiero", "dame 2") o el botón Confirmar, con un producto ya elegido
en el draft del episodio activo. Sin esa confirmación (ni orden registrada):
`request_shipping_details` SHALL rechazar la llamada con `purchase_not_confirmed`
y el siguiente paso explícito; `manage_conversation_tag(CONFIRMADO_SIN_DATOS)`
SHALL degradar a `INTERESADO` sin cerrar el episodio ni escalar; y
`escalate_to_human(ORDER_PENDING_SHIPPING_DETAILS)` SHALL rechazarse. La
confirmación es episodio-scoped (incidente runs 01a0a0eb / 01a0a0f1, 2026-09-14).

#### Scenario: El cliente eligió pero nunca dijo que sí

- GIVEN un draft con producto/aroma/color y sin `confirmed_at_ms`
- WHEN el LLM llama `request_shipping_details`
- THEN la tool devuelve `queued=false, error=purchase_not_confirmed` y no encola nada
- AND si el ghosting etiqueta `CONFIRMADO_SIN_DATOS`, queda `INTERESADO` y la sesión sigue en ruta ventas

#### Scenario: El cliente dijo que sí

- GIVEN un draft con producto y el inbound "sí, déjalo en azul"
- WHEN el ingest procesa el mensaje
- THEN `order_draft.confirmed_at_ms` queda registrado y `request_shipping_details` procede

### Requirement: Aplazamiento del cliente

Cuando el ÚLTIMO inbound es un aplazamiento ("voy en camino", "luego",
"mañana", "ahora no"), el sistema SHALL: inyectar al `plugin_context` la nota
`[SISTEMA — EL CLIENTE APLAZÓ]` (respuesta breve, sin tools de cierre);
rechazar `request_shipping_details` con `customer_deferred`; y, si el turno lo
maneja remarketing, prefijar el resumen del handoff con `[EL CLIENTE APLAZÓ]`
para que ventas no avance el cierre.

#### Scenario: "Voy apenas en camino a casa" tras el gancho de remarketing

- WHEN remarketing transfiere a ventas
- THEN el handoff empieza con `[EL CLIENTE APLAZÓ]` y prohíbe pedir datos de envío
- AND ventas responde texto y no manda el formulario

### Requirement: Variantes siempre en formato picker

Si el texto final del turno enumera 4 o más aromas o colores del catálogo y el
turno no emitió `present_variant_picker`, el workflow SHALL encolar el picker
(mismo intent que la tool) y suprimir el texto plano (run 9bd495be).

#### Scenario: Lista de 11 aromas en texto

- WHEN el LLM responde "Tenemos 11 aromas disponibles: Caballero de la noche, …" sin picker
- THEN el cliente recibe el picker curado con los 11 aromas y no la lista plana

### Requirement: El formulario de envío extiende el ghosting

Tras enviar el Flow nativo de datos de envío, el flag
`shipping_flow_awaiting_reply_since_ms` SHALL sobrevivir a la persistencia del
flush, de modo que `read_idle_timeout_seconds` devuelva el timeout extendido
(hasta 10 min) y no los 5 min por defecto (run 01a0a0f1).

### Requirement: Traza por turno para la evaluación (2026-09-15)

Después de enviar, persistir y flushear cada turno, el workflow SHALL escribir
una traza del turno en `<vault>/<sesión>/evals/turn_traces.jsonl` (gate
`turn-trace-v1`) con: disparador (cliente, ghosting o handoff), tools con su
resultado (ok o rechazo y motivo), narración descartada por el default-deny,
texto enviado, texto suprimido y motivo, guardas que actuaron, etapa de
entrada y salida, draft, confirmación, señal del cliente y cambios de estado.
La traza NUNCA SHALL bloquear ni alterar lo que recibe el cliente; un error al
escribirla se registra y el turno sigue. La traza vive fuera del JSONL del
dashboard para no alterar el corte por episodio (`msgs_count_at_start/close`).
La traza SHALL atribuirse al episodio abierto cuando ARRANCÓ el turno (el
último con `started_at_ms ≤ inicio del turno`), no al último de la lista.

#### Scenario: Tool rechazada por su guarda

- GIVEN el cliente aplazó ("voy en camino a casa")
- WHEN el LLM llama `request_shipping_details` y la tool responde `customer_deferred`
- THEN la traza del turno registra la tool con `ok=false` y `error=customer_deferred`
- AND el scorecard marca `CON-05` (la guarda tuvo que actuar) sin marcar `CON-01`

#### Scenario: Texto suprimido por la guarda de variantes

- WHEN el texto final enumera 11 aromas y la guarda lo reemplaza por el picker
- THEN la traza registra `sent_texts=[]`, `suppressed_reason=variant_enumeration_guard` y el texto del LLM

#### Scenario: Turno de ghosting

- WHEN el turno lo dispara el ghosting
- THEN la traza registra `trigger=ghost` y el texto que el LLM produjo como suprimido

#### Scenario: El cliente contesta mientras el turno de cierre todavía envía

- GIVEN el turno cerró `ep_007` y, antes de persistir su traza, el ingest abrió `ep_008` con el mensaje siguiente
- WHEN se persiste la traza del turno
- THEN la traza es de `ep_007` (el episodio abierto al arrancar el turno) y encadena su numeración
- AND `ep_008` no recibe un turno fantasma

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
