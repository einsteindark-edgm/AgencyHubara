# HU-002 — Enriquecimiento UI WhatsApp + Sync Meta Catalog

> **Objetivo**: convertir la conversación de venta de Hubara (hoy 100% texto plano) en una experiencia rica nativa de WhatsApp — imágenes, botones, listas, formularios, ubicación, audio transcrito, product cards — y sincronizar el catálogo Medusa a Meta Commerce Catalog para desbloquear Product Messages, Order Details, y Click-to-WhatsApp con DPA.
>
> **Alcance**: feature end-to-end. Sin Kapso ni BSP intermediario — todo se integra contra WhatsApp Cloud API y Graph API directo.

---

## 0. Contexto y estado actual

### Lo que hace hoy el agente de ventas (`src/plugins/chats/agent/sales/`)

- Recibe mensajes `text` y `media` vía webhook (`parsers.py`).
- Responde solo con `text` plano (`src/platform/whatsapp/client.py` → `send_message`).
- Tools internos: `search_products`, `get_product_by_handle`, `verify_order_for_checkout`, `manage_conversation_tag`, `escalate_to_human`.
- Catálogo: snapshot Medusa filesystem en `/var/lib/hubara/catalog`, consumido solo por el agente. **No vive en Meta**.
- Flujo de compra: cierre dentro de WhatsApp, pago contra entrega (> $45k COP) o link a checkout web.

### Limitaciones a resolver

1. **Descubrimiento de producto sin foto** → bajo engagement.
2. **Recolección de datos de envío en 4-6 mensajes** → cuello de fricción más alto del funnel.
3. **Cliente manda audio → se descarta** silenciosamente (parser devuelve `media` sin procesar).
4. **No hay confirmación visual de orden** → riesgo de error en cierre.
5. **CTWA ads no pueden correr campañas DPA** porque no existe Catálogo Meta.
6. **No hay reply-buttons** → cada respuesta requiere parseo NLP del texto libre del cliente.

### Estado objetivo

Al cierre de la HU:

- Cliente recibe productos con foto, ve catálogo en listas tappables, comparte ubicación con un tap, llena datos de envío en un Flow nativo de 30s, confirma orden con Order Details, y puede mandar audio sin que el agente se quede mudo.
- Catálogo Medusa replicado a Meta Catalog por tenant, sincronizado cada N min.
- Agente conserva sus reglas anti-alucinación (`closed-list`, citación literal, verificación live).
- Métricas instrumentadas: % de sesiones que usan cada componente, tasa de conversión por paso, abandono por paso.

### Métricas de éxito propuestas

| Métrica | Baseline (texto plano) | Objetivo post-HU |
|---|---|---|
| Conversión sesión→compra | X% (medir 2 semanas pre-rollout) | +30-50% |
| Mensajes promedio para cerrar | ~25-35 | ~10-15 |
| Abandono en paso "datos de envío" | Y% | -50% |
| Sesiones con audio inbound no procesado | Z% | 0% |

---

## PARTE A — Componentes UI a integrar en el flujo de ventas

Catorce componentes, organizados en tres tiers según dependencia con Meta Catalog. Cada uno especifica: cuándo dispararlo, qué ve el cliente, qué payload viaja, qué cambia en el agente, criterios de aceptación.

### A.0 Cambios cross-cutting (aplican a todos los componentes)

#### A.0.1 Extensiones a `src/platform/whatsapp/client.py`

Nuevas funciones públicas que envían cada tipo de mensaje contra Cloud API. Todas comparten contrato: reciben `phone_number_id` + `to` + payload tipado, devuelven `None` o lanzan en error de red.

| Función | Tipo Meta | Componentes que lo usan |
|---|---|---|
| `send_image(...)` | `image` | A.1 |
| `send_audio(...)` | `audio` | A.5 (outbound, opcional) |
| `send_document(...)` | `document` | (futuro, ficha técnica) |
| `send_interactive_buttons(...)` | `interactive.button` | A.2 |
| `send_interactive_list(...)` | `interactive.list` | A.3 |
| `send_location_request(...)` | `interactive.location_request_message` | A.4 |
| `send_reaction(...)` | `reaction` | A.6 |
| `send_contact(...)` | `contacts` | A.7 |
| `send_cta_url(...)` | `interactive.cta_url` | A.8 |
| `send_flow(...)` | `interactive.flow` | A.9 |
| `send_product(...)` | `interactive.product` | A.10 |
| `send_product_list(...)` | `interactive.product_list` | A.11 |
| `send_order_details(...)` | `interactive.order_details` | A.12 |

#### A.0.2 Extensiones a `src/plugins/chats/agent/sales/parsers.py`

Hoy `WhatsAppMessage` tiene solo `text` y `media: dict`. Hay que normalizar los tipos interactivos en un campo separado para que el ingestor y el agente los traten distinto.

Propuesta de extensión:

```
WhatsAppMessage:
  message_id, from_number, phone_number_id, timestamp,
  text: str | None,
  media: dict | None,           # imagen/video/doc inbound del cliente
  interactive: dict | None,     # button_reply, list_reply, nfm_reply (Flow), order
  location: dict | None,        # {lat, lng, name?, address?}
  audio: dict | None,           # {id, mime_type, voice: bool}  → pipeline de transcripción
  referral: dict | None,        # CTWA: {source_id, source_type, headline, body, ctwa_clid}
  context: dict | None,         # quoted message → mantener contexto si el cliente replica a un msg específico
```

Parser:
- `msg.type == "interactive"` → `msg.interactive.type` ∈ {`button_reply`, `list_reply`, `nfm_reply`, `order`}. Normalizar a `interactive` field.
- `msg.type == "location"` → `location` field.
- `msg.type == "audio"` → `audio` field, marcar `pending_transcription=True`.
- `msg.referral` (cualquier tipo) → `referral` field (CTWA attribution).
- `msg.context.id` → `context` field (cliente respondió a un msg previo).

#### A.0.3 Conversión de respuestas interactivas a "texto efectivo"

El LLM **no debe** ver `button_reply.id` ni `list_reply.id` como JSON. El ingestor traduce a "texto efectivo" antes de dárselo al agente:

| Inbound interactivo | Texto efectivo que ve el LLM |
|---|---|
| `button_reply.id = "ver_catalogo"` | "[el cliente tocó: Ver catálogo]" |
| `list_reply.id = "<handle>"` | "[el cliente seleccionó el producto: <title>]" — el ingestor resuelve title via `get_product_by_handle` local |
| `nfm_reply.response_json` (Flow) | "[datos de envío recibidos] ciudad=...; barrio=...; ..." |
| `location.{lat,lng}` | "[ubicación recibida] lat=4.71, lng=-74.07 (Bogotá, Chapinero)" |

Razón: el LLM razona con texto natural; los IDs sintéticos rompen su modelo mental de la conversación.

#### A.0.4 Decision tools nuevas

En lugar de hacer que el LLM decida qué tipo de mensaje renderizar, agregamos **decision tools** que el LLM invoca por intención. La capa de orquestación (workflow/use_case) interpreta y emite el componente correcto.

| Tool nueva | Reemplaza / complementa | Disparada cuando |
|---|---|---|
| `present_products` | `search_products` (post-respuesta) | LLM tiene N>=4 productos a mostrar |
| `present_product_detail` | `get_product_by_handle` (post-respuesta) | LLM va a mostrar UN producto con foto |
| `request_shipping_details` | (nueva) | LLM va a recolectar datos de envío |
| `request_location` | (nueva) | LLM necesita ubicación física |
| `present_order_confirmation` | `verify_order_for_checkout` (post-respuesta) | Orden verificada, listo para confirmar |
| `react_to_message` | (nueva) | Ack visual rápido sin texto |

Estas tools no producen texto para el LLM — la salida es un envelope que el workflow renderiza como el mensaje WA correspondiente. El LLM solo decide **qué** mostrar y **cuándo**.

#### A.0.5 Actualizaciones a `workspace/TOOLS.md` y `workspace/IDENTITY.md`

Agregar reglas:

1. "Cuando muestres ≥4 productos, llama `present_products` en lugar de listar en texto."
2. "Cuando muestres UN producto en detalle, llama `present_product_detail` (manda foto + card)."
3. "Cuando vayas a recolectar datos de envío, llama `request_shipping_details` UNA sola vez. NO pidas datos uno a uno en texto."
4. "Tras `verify_order_for_checkout` exitoso, llama `present_order_confirmation` para el cierre formal."
5. "Si el cliente manda un audio, lo recibirás transcrito. Procesalo como si fuera texto."
6. "Si el cliente toca un botón, el texto que recibís ya refleja su elección. NO le preguntes 'qué elegiste'."

---

### A.1 Imagen de producto (`image`)

- **Cuándo**: el agente decide mostrar UN producto con foto, vía `present_product_detail`. También al final de `present_products` (foto del primero + lista tappable abajo).
- **Cliente ve**: foto del producto (de `CatalogProductDTO.thumbnail` o `images[0].url`) con caption "Vela X · $23.000 COP".
- **Outbound payload** (resumen): `type: "image"`, `image.link: <https url>`, `image.caption: <texto>`.
- **Cambios agente**:
  - Decision tool `present_product_detail(handle)` → workflow lee snapshot, manda imagen + texto.
- **Cambios infra**:
  - `send_image()` en `client.py`.
- **Reglas de negocio Hubara**:
  - Caption ≤ 1024 chars Meta. Incluir título exacto + precio del envelope (citación literal).
  - Si `thumbnail` ausente, usar `images[0].url`. Si ambos ausentes, fallback a texto + log warning.
  - Validar HTTPS y dominio whitelist (Medusa CDN).
- **Limites Meta**: 5 MB JPG/PNG, link público HTTPS, sin auth.
- **Aceptación**:
  - Test: con producto `cruz-de-vida` el cliente recibe imagen + caption "Vela Cruz de Vida · $23.000 COP".
  - Si la URL devuelve 404, el agente fallback a texto y loguea `image_fallback`.
- **Esfuerzo**: 1 día.

---

### A.2 Reply Buttons (`interactive.button`)

- **Cuándo**:
  - Saludo inicial → `[Ver velas] [Asesoría] [Hablar con humano]`
  - Tras mostrar detalle de producto → `[Quiero esta] [Ver otras] [Más info]`
  - Confirmación de orden (fallback si no usamos A.12) → `[Confirmar] [Modificar] [Cancelar]`
  - Método de pago → `[Tarjeta] [Transferencia] [Contra entrega]` (último solo si total > $45k COP)
- **Cliente ve**: mensaje de texto con hasta 3 botones tappables debajo.
- **Outbound** (resumen): `type: "interactive"`, `interactive.type: "button"`, body + footer opcional + array de buttons con `id` + `title`.
- **Inbound** (cuando cliente toca): `interactive.button_reply.{id,title}`.
- **Cambios agente**:
  - El LLM **no escribe** botones manualmente. Hay templates de botones definidos por intención (ver A.0.4: el workflow emite los botones según el envelope de la decision tool).
- **Cambios infra**:
  - `send_interactive_buttons(body, buttons, footer=None)`.
  - Parser maneja `button_reply` → texto efectivo "[el cliente tocó: Ver catálogo]".
- **Reglas de negocio Hubara**:
  - Body ≤ 1024 chars, button title ≤ 20 chars, footer ≤ 60 chars.
  - IDs estables: `payment.cash_on_delivery`, `confirm_order`, `view_products`, etc. Documentar en `agents_admin` para QA.
  - Idioma español neutro (registro cálido), mismo tono que la voz Hubara.
- **Limites Meta**: 3 botones máx, no se pueden combinar con list message en mismo mensaje.
- **Aceptación**:
  - Cuando el cliente entra orgánico (sin `referral`), recibe saludo + 3 botones.
  - Tocar "Ver velas" dispara `present_products` automáticamente.
- **Esfuerzo**: 1-2 días.

---

### A.3 List Message (`interactive.list`)

- **Cuándo**:
  - `present_products` con >=4 ítems.
  - Selección de aroma cuando producto tiene >3 variantes en `tags`.
  - Selección de ciudad si no usamos location request (fallback).
- **Cliente ve**: card con título + botón "Ver opciones" → al tocar abre bottom sheet con sections de filas tappables (`title` + `description`).
- **Outbound** (resumen): `interactive.type: "list"`, body, button, `sections: [{title, rows: [{id, title, description}]}]`.
- **Inbound**: `interactive.list_reply.{id, title, description}`.
- **Cambios agente**:
  - `present_products` envelope incluye los items en formato compatible con sections. El workflow agrupa por categoría o aroma según `categories[]` / `tags[]`.
  - El LLM no genera la lista; la decision tool define la intención y los handles candidatos.
- **Cambios infra**:
  - `send_interactive_list(body, button_label, sections)`.
  - Parser maneja `list_reply` → texto efectivo "[seleccionó: Vela Lavanda]".
- **Reglas de negocio Hubara**:
  - Row title ≤ 24 chars, description ≤ 72 chars. Cortar `title` largos con `…`.
  - `id` del row = `handle` del producto (closed-list: solo handles del envelope).
  - Agrupar en sections por categoría real (`categories[]`), nunca inventar.
  - Si hay >10 productos en una sección, mostrar primero los más relevantes (ranking del search) y agregar fila final "Ver más" que dispare nuevo `search_products`.
- **Limites Meta**: 10 sections × 10 rows = 100 ítems máx. Pero la UX se degrada >30 ítems.
- **Aceptación**:
  - "qué velas tienen" → cliente recibe list con hasta 30 productos en sections por categoría.
  - Cliente tap en una fila → el agente continúa el flujo como si hubiera escrito el nombre.
- **Esfuerzo**: 2-3 días.

---

### A.4 Location Request (`interactive.location_request_message`)

- **Cuándo**: al iniciar recolección de datos de envío, ANTES del Flow (A.9). El Flow ya tiene ciudad+barrio+dirección como inputs pero la location request resuelve coordenadas precisas para calcular zona/envío.
- **Cliente ve**: mensaje "Compartí tu ubicación para calcular el envío" + botón nativo "Compartir ubicación".
- **Outbound**: `interactive.type: "location_request_message"`, body.
- **Inbound**: `type: "location"`, `location.{latitude, longitude, name?, address?}`.
- **Cambios agente**:
  - Decision tool `request_location(reason)`.
  - Tras recibir location, opcional: reverse-geocode (Google Maps API o Nominatim) para obtener barrio/ciudad y pre-rellenar el Flow.
- **Cambios infra**:
  - `send_location_request(body)`.
  - Parser maneja `location` → texto efectivo "[ubicación recibida] lat=X lng=Y".
- **Reglas de negocio Hubara**:
  - Solo Colombia: si lat/lng fuera del bounding box CO → `escalate_to_human(INTERNATIONAL)`.
  - Persistir lat/lng en `state.json` de la sesión, asociado a la orden.
- **Limites Meta**: el cliente puede negar compartir → fallback a Flow con campos texto.
- **Aceptación**:
  - Cliente comparte ubicación de Medellín → sesión actualiza ciudad="Medellín" en state.
  - Cliente comparte ubicación de Quito → agente escala a humano.
- **Esfuerzo**: 2 días + 1 día para reverse-geocode si lo incluimos.

---

### A.5 Audio Transcription (inbound)

- **Cuándo**: cliente envía audio (común en LATAM, especialmente en venta).
- **Hoy**: parser devuelve `media={"type": "audio", ...}` y el ingestor lo descarta.
- **Propuesta**:
  - Si `audio` field presente, el ingestor encola actividad `transcribe_audio_activity`.
  - La activity descarga el media de Meta (`GET /v23.0/{media_id}` → URL → GET binary), lo manda a Whisper API (o Deepgram) y devuelve texto.
  - El texto transcrito se inyecta en la conversación como si el cliente hubiera escrito ese texto (con marca interna `source=audio`).
- **Cambios agente**:
  - El LLM lee el texto transcrito normalmente. Opcional: el system prompt aclara que si transcripción está vacía o incomprensible (`[INAUDIBLE]`), el agente pide que el cliente lo escriba.
- **Cambios infra**:
  - Nueva activity `transcribe_audio_activity` (Temporal, retry policy ≤3, heartbeat si dura >5s).
  - Provider port: `AudioTranscriptionPort` con impl `WhisperAdapter` (OpenAI) o `DeepgramAdapter`.
  - Costo: ~$0.006/min audio (Whisper). Loguear duración + costo por sesión para attribution.
- **Reglas de negocio Hubara**:
  - Idioma español Colombia.
  - Si transcripción es voice note muy corta (<1s) o silencio → "no entendí, ¿podés escribirlo?".
  - Audios > 60s → escalar a humano (riesgo de queja larga).
- **Limites Meta**: 16 MB por audio, formatos: AAC, M4A, AMR, MP3, OGG, OPUS.
- **Aceptación**:
  - Cliente manda audio "quiero una vela de lavanda" → agente responde como si hubiera escrito eso.
  - Audio >60s → escalación + log.
- **Esfuerzo**: 3-5 días (provider + activity + integración con ingestor).

---

### A.6 Reactions del agente

- **Cuándo**: ack visual sin texto. Ej. cliente envía datos correctos del Flow → el agente reacciona 👍 al mensaje del Flow + manda confirmación de orden.
- **Cliente ve**: el emoji aparece en el mensaje del cliente.
- **Outbound** (resumen): `type: "reaction"`, `reaction.message_id: <id>`, `reaction.emoji: "🤍"`.
- **Cambios agente**:
  - Decision tool `react_to_message(message_id, emoji)`.
- **Cambios infra**:
  - `send_reaction(message_id, emoji)`.
- **Reglas de negocio Hubara**:
  - Solo emojis aprobados (🤍, ✨, 👍, 🎉). No usar emojis ambiguos (😅 puede leerse mal).
  - **Atención al billing**: cada reaction cuenta como mensaje Meta+Kapso. Usar con moderación.
- **Aceptación**:
  - Tras submit del Flow, cliente ve 🤍 en su último mensaje del Flow.
- **Esfuerzo**: 0.5 día.

---

### A.7 Contact Card (handoff)

- **Cuándo**: cuando `escalate_to_human` se ejecuta, opcionalmente mandar vCard del asesor humano para que el cliente lo agregue a contactos.
- **Cliente ve**: card con nombre + número del asesor + botón "Agregar a contactos".
- **Outbound**: `type: "contacts"`, array de vCards.
- **Cambios agente**:
  - Opt-in: `escalate_to_human` con flag `share_contact_card=True`.
- **Cambios infra**:
  - `send_contact(contacts: list[ContactCard])`.
- **Reglas de negocio Hubara**:
  - Validar consent: solo si el cliente lo pide explícitamente.
  - El número del asesor lo provee `agents_admin` plugin (no hardcoded).
- **Aceptación**:
  - Cliente pide "dame el número del asesor" → agente escala + manda contact card.
- **Esfuerzo**: 1 día.

---

### A.8 CTA URL Button (`interactive.cta_url`)

- **Cuándo**:
  - Cliente explícitamente pide "mándame el catálogo de Instagram".
  - Post-venta: mandar tracking URL al cliente.
  - Cases muy específicos donde no podemos cerrar en chat.
- **Cliente ve**: mensaje con un botón con la URL visible.
- **Outbound**: `interactive.type: "cta_url"`, body + button_text + url.
- **Reglas de negocio Hubara**:
  - **Anti-patrón por defecto**: el agente NO debe sacar al cliente a la web (regla de TOOLS.md). Solo usar si el cliente lo pide o si es URL de tracking post-venta.
  - URLs whitelist: `hubara.com.co`, `instagram.com/hubara.com.co`, dominio de la carrier de envío.
- **Aceptación**:
  - Cliente pide "ver Instagram" → recibe botón con URL IG.
- **Esfuerzo**: 0.5 día.

---

### A.9 WhatsApp Flow — Datos de envío

> **El componente de mayor ROI**. Reduce 4-6 mensajes en uno solo.

- **Cuándo**: tras cliente confirmar producto + cantidad, antes de `verify_order_for_checkout`.
- **Cliente ve**: botón "Completar datos de envío" → toca → se abre pantalla nativa dentro de WA con formulario:
  - Dropdown **ciudad** (ciudades Colombia con cobertura, lista hardcoded inicial).
  - Input **barrio** (texto libre).
  - Input **dirección** (texto libre, validación min 5 chars).
  - Input **número de contacto** (validación regex CO `+57 3XX XXXXXXX`).
  - Radio **método de pago**: Tarjeta / Transferencia / Contra entrega (este último solo si total > $45k COP — lógica en data endpoint).
  - Botón "Confirmar".
- **Flow JSON**: vivirá en `src/platform/whatsapp/flows/shipping_details.flow.json`. Versionado.
- **Data endpoint**: nueva ruta HTTP `POST /api/whatsapp/flows/shipping/data` que Meta llama desde el Flow para:
  - Validar ciudad disponible.
  - Filtrar método "Contra entrega" si total ≤ $45k.
  - Validar formato de teléfono.
- **Outbound al iniciar**: `interactive.type: "flow"`, `flow_id`, `flow_token` (único por sesión), `flow_action: "navigate"`, screen inicial.
- **Inbound al completar**: `interactive.nfm_reply.response_json` con todos los campos.
- **Cambios agente**:
  - Decision tool `request_shipping_details(order_total_cop)` → workflow emite Flow.
  - Parser maneja `nfm_reply` → texto efectivo "[datos de envío recibidos] ciudad=X; barrio=Y; ...".
  - El LLM, al recibir el texto efectivo, **NO** vuelve a pedir esos datos. Continúa directo a `verify_order_for_checkout`.
- **Cambios infra**:
  - `send_flow(flow_id, flow_token, screen, data)`.
  - Endpoint HTTP firmado (Meta firma con `X-Hub-Signature-256`) en `src/sales_whatsapp/http/flow_data_endpoint.py`.
  - Encryption: Meta Flows requieren cifrado RSA del response. Generar par de claves, registrar pública en Meta Business Manager, almacenar privada en secrets manager.
  - Flow registrado vía Graph API: `POST /v23.0/{waba_id}/flows` con el JSON, luego publicado.
- **Reglas de negocio Hubara**:
  - Ciudades válidas: lista mantenida en `agents_admin` plugin, no hardcoded en Flow.
  - Idioma español neutro.
  - Si cliente niega el Flow (lo cierra sin completar), fallback al modo legacy (pedir datos en texto).
- **Limites Meta**: Flows requieren approval inicial (1-3 días). Cada versión publicada debe pasar review básico. Tienen versioning estricto.
- **Aceptación**:
  - Cliente confirma 2 velas → recibe Flow → llena → agente recibe datos → continúa a `verify_order_for_checkout`.
  - Total = $30k → opción "Contra entrega" no aparece en el Flow.
  - Cliente cierra Flow sin completar → agente fallback a pedir en texto.
- **Esfuerzo**: 1-2 semanas (Flow JSON design + data endpoint + encryption + Meta approval + integración).

---

### A.10 Single Product Message (`interactive.product`)

> **Requiere Meta Catalog (ver Parte B).**

- **Cuándo**: `present_product_detail` cuando el producto ya está en Meta Catalog.
- **Cliente ve**: card nativo con foto grande + título + precio + botón "Ver" → abre detalle dentro de WA + "Agregar al carrito".
- **Outbound**: `interactive.type: "product"`, `interactive.action.catalog_id`, `interactive.action.product_retailer_id`.
- **Inbound al "Add to Cart"**: `type: "order"` con items.
- **Cambios agente**:
  - Misma decision tool `present_product_detail(handle)` — el workflow detecta si el producto existe en Meta Catalog. Si sí, manda product message. Si no, fallback a imagen + texto (A.1).
- **Reglas de negocio Hubara**:
  - `product_retailer_id` = `CatalogProductDTO.id` (alineado con Parte B mapping).
- **Aceptación**:
  - Producto sincronizado a Meta → cliente recibe product card nativo.
  - Producto no sincronizado → cliente recibe imagen + texto (fallback transparente).
- **Esfuerzo**: 2 días (depende de Parte B).

---

### A.11 Multi-Product Message (`interactive.product_list`)

> **Requiere Meta Catalog.**

- **Cuándo**: `present_products` con 4-30 ítems, cuando todos están en Meta Catalog.
- **Cliente ve**: mensaje "Nuestras velas" → botón "Ver catálogo" → abre mini-store dentro de WA con sections + items con foto/precio/CTA "Agregar".
- **Outbound**: `interactive.type: "product_list"`, header, body, footer, action con `catalog_id` y sections de `product_items`.
- **Inbound**: `type: "order"` con array de items seleccionados.
- **Cambios agente**:
  - Misma decision tool `present_products`. Si todos los items están en Catalog, emite `product_list`. Si no, fallback a `list` (A.3).
- **Reglas de negocio Hubara**:
  - Header text ≤ 60 chars. Body ≤ 1024.
  - Sections por categoría real (mismo criterio que A.3).
- **Limites Meta**: 10 sections × 30 product items totales.
- **Aceptación**:
  - "muéstrame el catálogo" con todos los productos sincronizados → multi-product nativo.
- **Esfuerzo**: 2 días (depende de Parte B).

---

### A.12 Order Details Message (`interactive.order_details`)

> **Requiere Meta Catalog + payment gateway integrado en Meta Business.**

- **Cuándo**: tras `verify_order_for_checkout` exitoso, en lugar de pedir confirmación con reply buttons.
- **Cliente ve**: card formal de orden:
  - Lista de items (foto, cantidad, precio unitario, subtotal)
  - Subtotal, envío, impuestos, total
  - Dirección de envío
  - Botón "Pagar" → abre payment sheet nativo (Razorpay / PayU / Wompi para Colombia)
- **Outbound**: `interactive.type: "order_details"` con `payment_settings`, `order` (items, totales, currency), `expiration` opcional.
- **Inbound**:
  - Tras pago: `interactive.type: "order_status"` con `status: "captured"` y `reference_id`.
  - Si falla: `status: "failed"`.
- **Cambios agente**:
  - Decision tool `present_order_confirmation(items, shipping, payment_provider)`.
  - Cuando llega `order_status: captured`:
    - Tag automático `COMPRA_EXITOSA`
    - Trigger workflow `order_fulfillment` (genera orden en Medusa + dispara packing)
- **Cambios infra**:
  - `send_order_details(payload)`.
  - Integración con gateway: Wompi tiene Meta Payments integration para Colombia.
  - Webhook handler para `order_status`.
- **Reglas de negocio Hubara**:
  - Reference_id formato: `HUB-{tenant}-{session_id}-{epoch}`.
  - Tax COP: 19% IVA si aplica (revisar — velas artesanales tienen régimen especial).
  - Si gateway rechaza, fallback automático a link de pago externo.
- **Limites Meta**:
  - Requiere business verification completa.
  - Gateway de pago aprobado por Meta para Colombia.
- **Aceptación**:
  - Cliente completa pago dentro de WA → tag `COMPRA_EXITOSA` automático → orden en Medusa creada con `reference_id`.
- **Esfuerzo**: 1-2 semanas (gateway approval + integración).

---

### A.13 Catalog Browsing Button (futuro)

> Opcional, posterior a Parte B.

- Botón "Ver catálogo completo" que abre la mini-store de WhatsApp con todo el catálogo Meta del cliente.
- Útil para clientes nuevos que llegan sin intent específico.
- Esfuerzo: 1 día tras Parte B.

---

### A.14 Typing Indicator (ya existe)

Confirmar que `send_typing_indicator` se sigue llamando antes de cada respuesta del agente. No requiere cambios.

---

## PARTE B — Sincronización Medusa → Meta Catalog

### B.1 Por qué

Hoy el catálogo Medusa vive en `/var/lib/hubara/catalog` (snapshot JSON) y solo lo lee el agente. Para desbloquear A.10, A.11, A.12 y campañas CTWA con DPA, cada producto debe existir también en el **Catalog de Meta del cliente** (tenant). Una vez sincronizado:

- WhatsApp puede mandar Product Messages y Order Details nativos.
- Meta Ads Manager puede correr Advantage+ Shopping campaigns con destino WhatsApp.
- El cliente puede agregar productos al carrito de WhatsApp.
- Aparece el botón "View Catalog" en el perfil del Business.

### B.2 Arquitectura

Reutilizamos el pipeline existente del plugin `catalog`, agregando un **sink adicional** al workflow de sync:

```
              ┌─────────────────────────────────────────────────┐
              │ src/plugins/catalog/agent/workflows/sync.py     │
              │ (Temporal Schedule cada N min)                  │
              └──────┬──────────────────────────────────┬───────┘
                     │                                   │
                     ▼                                   ▼
        ┌──────────────────────────┐         ┌─────────────────────────────┐
        │ PullCatalogUseCase       │         │ PushMetaCatalogUseCase ★NEW │
        │ (Medusa → DTO)           │         │ (DTO → Meta Catalog Batch)  │
        │  ✓ ya existe             │         │  ★ a construir              │
        └──────────┬───────────────┘         └──────────┬──────────────────┘
                   │                                     │
                   ▼                                     ▼
        ┌──────────────────────────┐         ┌─────────────────────────────┐
        │ WriteSnapshotUseCase     │         │ Meta Graph API               │
        │ /var/lib/hubara/catalog  │         │ POST /{catalog_id}/items_batch│
        │  ✓ ya existe             │         │ POST /{catalog_id}/products  │
        └──────────────────────────┘         └─────────────────────────────┘
```

Cada sink es independiente: si Meta API falla, el snapshot interno se actualiza igual (resilencia). Cada uno tiene retry policy y heartbeat propios.

### B.3 Mapping CatalogProductDTO → MetaCatalogItem

Mapeo de campos. Lo gris es lo que se omite o transforma.

| Campo `CatalogProductDTO` | Campo Meta Catalog Item | Notas |
|---|---|---|
| `id` | `retailer_id` | clave única en Meta, debe ser estable |
| `title` | `name` | required, ≤ 200 chars |
| `description` | `description` | required, ≤ 9999 chars. Si null → usar title |
| `thumbnail` o `images[0].url` | `image_url` | required, HTTPS público. Ver B.5 |
| `images[1..N].url` | `additional_image_urls` | array, hasta 10 |
| `variants[0].prices[0].amount` + `currency_code` | `price` (formato "23000 COP") | required, debe ser string con currency |
| `variants[0].sku` | `gtin` o custom field | si formato GTIN-13 válido, va a `gtin` |
| `status == "published"` | `availability: "in stock"` | si `status != "published"` → `"out of stock"` |
| `categories[0]` | `google_product_category` | mapeo manual a taxonomía Google (ver B.10) |
| `categories[]` | `custom_label_0..4` | hasta 5 etiquetas custom |
| `tags[]` | `custom_data.tags` | string JSON-encoded |
| `handle` | `url` | construir `https://hubara.com.co/products/{handle}` |
| `metadata` | (varios custom fields) | TBD según necesidad |
| Hubara: `brand="Hubara"` | `brand` | constante por tenant |
| Hubara: `condition="new"` | `condition` | constante |

**Campos Meta requeridos sin equivalente directo**:
- `brand`: usamos constante "Hubara" (o el nombre del tenant en multi-tenant).
- `condition`: hardcoded "new".
- `currency`: parseado del `currency_code` (mayúsculas: "COP").

### B.4 API de Meta: Catalog Batch API

Endpoint principal:

```
POST https://graph.facebook.com/v23.0/{catalog_id}/items_batch
  ?access_token={tenant_system_user_token}

Body:
  {
    "requests": [
      { "method": "CREATE", "retailer_id": "prod_01", "data": {...} },
      { "method": "UPDATE", "retailer_id": "prod_02", "data": {...} },
      { "method": "DELETE", "retailer_id": "prod_03" },
      ...
    ]
  }
```

- Batch hasta **4.000 items por request**.
- Respuesta asíncrona: devuelve `handles` que se consultan con `GET /{catalog_id}/check_batch_request_status?handle=...` para confirmar éxito por ítem.
- Errores por ítem no abortan el batch — se reportan individualmente en el status check.

Alternativa para CRUD individual: `POST /{catalog_id}/products` (más lento, sin batch).

### B.5 Multi-tenant: catalog_id, token, image hosting

#### B.5.1 Por tenant guardamos

En `agents_admin` plugin (o nuevo `meta_tenant_config`):

| Campo | Tipo | Fuente |
|---|---|---|
| `tenant_id` | str | identificador interno |
| `meta_business_id` | str | Business Portfolio del cliente |
| `meta_waba_id` | str | WABA del cliente |
| `meta_phone_number_id` | str | número WA |
| `meta_catalog_id` | str | Catalog del cliente en Commerce Manager |
| `meta_system_user_token` | secret | token permanente del System User con scope `catalog_management` |
| `meta_page_id` | str | Facebook Page (para CTWA ads) |
| `image_cdn_base_url` | str | CDN público para fotos producto |

Para el cliente Hubara actual: configuración estática inicial. Para futuro SaaS multi-tenant: estos campos vienen del onboarding wizard (HU separada).

#### B.5.2 Image hosting

Meta requiere URLs HTTPS públicas, accesibles sin auth, estables. Los `thumbnail` de Medusa probablemente apuntan a un bucket interno o CDN ya público.

**Validar antes de empezar**:
- ¿Las URLs de `CatalogImageDTO.url` son HTTPS y accesibles sin auth?
- ¿Tienen TTL infinito o pueden expirar?
- ¿Tamaño correcto? Meta acepta hasta 8 MB, recomendado >500×500 px.

**Si no cumplen**, alternativas:
1. Re-hostear en un CDN nuestro (Cloudflare R2, S3+CloudFront). Workflow adicional `mirror_image_to_cdn`.
2. Subir a Meta Media API y referenciar (pero Meta media expira a 30 días → mala idea para catálogo).

**Decisión propuesta para HU**: asumir que Medusa CDN es accesible. Test con una imagen antes de batch completo. Si falla, fase 2 es CDN mirror.

### B.6 Initial load (primer sync por tenant)

Workflow nuevo: `initial_meta_catalog_sync(tenant_id)`.

1. Pull completo de Medusa (reutiliza `PullCatalogUseCase`).
2. Map a `MetaCatalogItem` por producto.
3. Validar imágenes (HEAD request a cada URL, descartar las 404).
4. Batch en chunks de 1000 ítems (margen de seguridad sobre el límite 4000).
5. Por cada batch:
   - `POST /items_batch` con `method: CREATE`.
   - Esperar `handle`.
   - Poll `check_batch_request_status` hasta `success` o `error`.
6. Persistir mapping `medusa_id ↔ meta_retailer_id` en estado (idempotencia para incremental).
7. Reporte final: `{created, failed, total, duration_s}`.

Disparo: manual desde script `scripts/trigger_initial_meta_sync.py --tenant=hubara`. No automático.

### B.7 Sync incremental

El pipeline existente corre periódico (Schedule de Temporal). Agregamos al final del workflow `sync` una activity nueva `push_to_meta_catalog`:

1. Lee `products_json` del use case `PullCatalogUseCase` (ya está en memoria del workflow).
2. Compara con snapshot anterior en disco para detectar:
   - **Nuevos** (en pull pero no en snapshot): `method: CREATE`.
   - **Actualizados** (en ambos pero hash distinto): `method: UPDATE`.
   - **Removidos** (en snapshot pero no en pull): `method: DELETE`.
3. Batch los cambios. Si no hay cambios, skip.
4. Persistir mapping y stats.

**Hash de detección**: SHA256 de `(title, description, price, availability, image_url, tags)` por producto. Solo se manda update si cambió.

### B.8 Eliminaciones / soft-delete

- Si un producto sale de Medusa (deshabilitado o eliminado), se manda `method: DELETE` a Meta.
- Si vuelve a aparecer, `method: CREATE` con mismo `retailer_id` (reusable).
- **Riesgo**: si un producto desaparece temporalmente de Medusa (bug, sync parcial), borraríamos del catálogo Meta y perderíamos historial / reviews / ad performance.
- **Mitigación**: regla de "no borrar si el pull devuelve < 50% del último count". Loguear warning y abortar el delete batch. Requiere intervención manual.

### B.9 Rate limits y backoff

Meta Graph API:
- ~200 calls/h por business app en endpoints de catálogo en niveles bajos.
- Pueden escalar a 10k/h con uso continuo y volumen.
- Headers de response: `X-Business-Use-Case-Usage` con porcentaje usado.

**Estrategia**:
- Backoff exponencial en 429 / 4 / 17 (los códigos de rate limit de Meta).
- Heartbeat de Temporal activity cada 10s durante el batch.
- Max 3 retries automáticos. Si falla, dejar para próximo sync.

### B.10 Filtros de negocio (collection rule de Hubara)

Recordar: `PullCatalogUseCase` actualmente filtra `if mp.collection is not None: skipped`.

Esa misma regla aplica al sink Meta: **solo productos sin collection** se sincronizan a Meta. Las collections internas (test, duplicados sucios) NO deben aparecer en el catálogo público.

Documentar en código + en este spec: cualquier cambio futuro de regla debe aplicarse a ambos sinks consistentemente (un solo punto de filtrado en `PullCatalogUseCase`).

### B.11 Idempotencia

- `retailer_id` = `CatalogProductDTO.id` (Medusa id), inmutable.
- Repetir el batch con los mismos retailer_ids es seguro: Meta hace upsert.
- Cada activity de push lleva `idempotency_key` propio para Temporal.

### B.12 Error handling

| Error | Acción |
|---|---|
| 400 — invalid image URL | Marcar producto como `image_pending`, fallback sin imagen o postergar |
| 400 — invalid currency | Loguear como bug de mapeo, no reintentar (data error) |
| 429 — rate limit | Backoff exponencial |
| 500 — Meta side | Retry hasta 3 veces, luego diferir al próximo sync |
| Token expirado | Alert crítico, requiere rotación manual de System User token |
| Catalog not found | Alert crítico, configuración corrupta |

Todos los errores se persisten en una tabla `meta_catalog_sync_errors` para auditoría y para QA del estado del catálogo.

### B.13 Observabilidad

Métricas a emitir (logger estructurado + Prometheus si aplica):

- `meta_catalog_sync.products_pulled`
- `meta_catalog_sync.products_pushed_created`
- `meta_catalog_sync.products_pushed_updated`
- `meta_catalog_sync.products_pushed_deleted`
- `meta_catalog_sync.products_skipped_image`
- `meta_catalog_sync.products_skipped_collection`
- `meta_catalog_sync.batch_duration_seconds`
- `meta_catalog_sync.errors_total{type}`
- `meta_catalog_sync.last_success_timestamp`

Dashboard mínimo en `system_explorer` plugin para ver:
- Estado por tenant (last sync, count, errores)
- Productos en Medusa vs en Meta (debe ser igual modulo collection filter)
- Productos con discrepancia (imagen rota, precio out-of-sync)

### B.14 Verificación post-sync

Workflow opcional `verify_meta_catalog_consistency(tenant_id)`:
1. Pull catálogo Meta vía `GET /{catalog_id}/products`.
2. Pull snapshot Medusa.
3. Comparar field-by-field.
4. Reportar discrepancias.

Se ejecuta una vez al día. Si discrepancias >5%, alerta.

### B.15 Cambios en código (resumen por archivo)

**Nuevos archivos**:
- `src/plugins/catalog/agent/use_cases/push_meta_catalog.py` — el use case puro
- `src/plugins/catalog/agent/activities/push_meta.py` — la Temporal activity
- `src/platform/meta_catalog/__init__.py`
- `src/platform/meta_catalog/client.py` — HTTP client a Graph API
- `src/platform/meta_catalog/mapper.py` — `CatalogProductDTO → MetaCatalogItem`
- `src/platform/meta_catalog/dtos.py` — `MetaCatalogItem` dataclass
- `src/platform/meta_catalog/port.py` — `MetaCatalogPort` ABC
- `src/plugins/catalog/agent/contracts.py` — agregar `PushMetaResult`
- `scripts/trigger_initial_meta_sync.py`

**Archivos modificados**:
- `src/plugins/catalog/agent/workflows/sync.py` — agregar step `push_to_meta_catalog` al final
- `src/plugins/catalog/workers/sync.py` — registrar nuevas activities
- `src/plugins/agents_admin/...` — agregar campos de tenant config (`meta_catalog_id`, `meta_system_user_token`)
- `src/platform/config.py` — env vars opcionales para fallback single-tenant

### B.16 Plan de rollout por fase

| Fase | Acción | Validación |
|---|---|---|
| B-fase-1 | Configurar tenant: crear Catalog en Commerce Manager del cliente Hubara, generar System User token, registrar en config | `GET /{catalog_id}` responde 200 |
| B-fase-2 | Push de 1 producto manualmente vía script | Producto visible en Commerce Manager UI con imagen |
| B-fase-3 | Push de batch de 50 productos (dev) | Todos visibles, sin errores de imagen |
| B-fase-4 | Initial load completo (~120 productos Hubara) | Count match Medusa - collection filter |
| B-fase-5 | Habilitar incremental sync (Schedule cada 10 min) | Hash detection funciona, no se mandan updates innecesarios |
| B-fase-6 | Activar A.10 (Product Message) en un % de sesiones (feature flag) | Conversión no baja |
| B-fase-7 | Activar A.11 (Multi-Product) | Conversión sube |
| B-fase-8 | Activar A.12 (Order Details + gateway) tras integración Wompi | Pagos cierran dentro de WA |
| B-fase-9 | Lanzar primera campaña CTWA con DPA | Atribución funciona |

### B.17 Criterios de aceptación Parte B

1. Initial sync de Hubara replica todos los productos publicados sin collection a Meta Catalog.
2. Cada producto en Meta tiene: nombre, descripción, precio en COP, imagen visible, URL correcta a hubara.com.co.
3. Incremental sync detecta cambios y solo manda updates necesarios (<1% de productos por ciclo en ausencia de cambios reales).
4. Eliminar un producto en Medusa → desaparece de Meta en el siguiente sync.
5. Sync robusto: tolera rate limit, tolera 1-2 imágenes rotas (skip + log).
6. Token expirado o catalog_id inválido → alerta operativa, no falla silencioso.
7. Mapping documentado y testeado con `tests/platform/meta_catalog/test_mapper.py`.
8. El producto sincronizado puede mandarse como Product Message en WA y aparece correctamente en la card.

---

## PARTE C — Roadmap, fases, dependencias

| Fase | Items | Esfuerzo | Pre-req | Resultado |
|---|---|---|---|---|
| **1. Quick wins UI** | A.1 imágenes, A.2 buttons, A.6 reactions, A.8 CTA URL | 5 días | nada | Saludo con buttons + producto con foto |
| **2. Catálogo tappable** | A.3 list message, A.7 contact card | 3 días | Fase 1 | Catálogo navegable en lista |
| **3. Audio + ubicación** | A.5 audio transcription, A.4 location request | 5 días | Fase 1 | Cliente puede mandar audio y compartir ubicación |
| **4. Flow datos envío** | A.9 WhatsApp Flow | 8 días | Fase 1, Meta Flow approval | Recolección de datos en formulario nativo |
| **5. Meta Catalog sync** | Parte B completa (fases B-1 a B-5) | 8 días | Catalog Meta creado | Catálogo replicado y sincronizado |
| **6. Product messages** | A.10, A.11 | 4 días | Fase 5 | UX premium con cards nativos |
| **7. Order Details + pago WA** | A.12 + integración Wompi | 10 días | Fase 5, gateway approval Meta | Cierre + pago 100% dentro de WA |

**Total estimado**: ~6-8 semanas de un dev senior dedicado, con paralelización posible entre fases 4 (Flow) y 5 (Catalog).

**MVP**: Fases 1+2+3+4 → 3-4 semanas → entrega 80% del valor.

**Premium**: + Fases 5+6+7 → 2-3 semanas más → 100% del valor + ads dinámicos.

---

## ANEXOS

### Anexo 1 — Límites Meta resumidos (refresh enero 2026)

| Tipo | Límite |
|---|---|
| Text message body | 4096 chars |
| Image (link) | 5 MB JPG/PNG, HTTPS sin auth |
| Audio outbound | 16 MB |
| Video outbound | 16 MB |
| Document outbound | 100 MB |
| Reply buttons | 3 botones × 20 chars title |
| List sections | 10 sections × 10 rows |
| List row title | 24 chars |
| List row description | 72 chars |
| Multi-product items | 10 sections × 30 product_items total |
| Catalog batch | 4000 items per `items_batch` request |
| Catalog rate limit | ~200/h inicial, escalable |
| Flow JSON tamaño | < 10 MB |
| Reaction emoji | 1 emoji por reaction msg |

### Anexo 2 — Reglas Hubara a preservar (no negociables)

1. **Closed-list strict**: el agente nunca menciona producto/aroma/color que no esté en el último `tool_result`.
2. **Citación literal de precios**: nunca redondear, nunca inventar, nunca decir "te confirmo en un rato".
3. **Cierre dentro de WhatsApp**: no sacar al cliente a la web salvo que lo pida explícitamente (con A.12, esta regla se cumple full).
4. **Verificación live antes de cerrar**: `verify_order_for_checkout` se sigue ejecutando antes de cualquier confirmación, incluso con Order Details.
5. **Escalación a humano**: las 13 categorías de `escalate_to_human` se respetan tal como están.
6. **Tag obligatorio al cierre**: `manage_conversation_tag` sigue siendo OBLIGATORIO al cerrar conversación.
7. **Solo Colombia**: location/Flow filtran fuera de CO → escalación.

### Anexo 3 — Riesgos

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Meta cambia Flow JSON schema | media | medio | versionado de Flow, monitor de approval status |
| URLs de imagen Medusa expiran | baja | alto | CDN mirror si pasa |
| Token Meta expira sin alert | media | alto | monitor + alert en `last_success_timestamp` |
| Whisper costo escala mal | media | medio | cap por sesión, max-duration filter |
| Cliente niega Flow / location | alta | bajo | fallback transparente a modo texto |
| Catalog desync con Medusa | media | medio | workflow de verify_consistency diario |
| Order Details + gateway tarda en aprobar Meta | alta | medio | fase 7 va de último, no bloquea MVP |

### Anexo 4 — Tests y QA

Por componente:
- **Unit tests** del mapper (B.3), del parser (A.0.2), de las decision tools (A.0.4).
- **Contract tests** del cliente Meta vs sandbox.
- **Integration tests** con webhook sintético (`tests/simulate_whatsapp.py` ya existe — extender).
- **E2E** con conversación simulada cubriendo todos los componentes.
- **QA manual** en sandbox Meta antes de production rollout (cada fase).
- **Feature flags** por componente, rollout gradual por % de sesiones.

### Anexo 5 — Dependencias externas a coordinar

1. **Meta Business Manager**: crear Catalog del cliente Hubara (manual, 1 día).
2. **System User Token**: generar con scope `catalog_management` + `whatsapp_business_messaging`.
3. **Flow approval**: submit del Flow JSON a review de Meta (1-3 días).
4. **Payment gateway**: contrato con Wompi o PayU para Colombia + integración con Meta Payments (semanas).
5. **OpenAI / Deepgram**: API key para Whisper (Whisper recomendado por costo y soporte español).
6. **Reverse geocoding** (opcional para A.4): Google Maps API key o Nominatim self-hosted.

### Anexo 6 — Out of scope (futuras HUs)

- Multi-idioma (solo español por ahora).
- Onboarding wizard para nuevos tenants (HU separada).
- Reportería avanzada de campañas CTWA con DPA.
- Inbox visual del agente humano (handoff) — existe parcialmente.
- Push notifications cross-channel (SMS/email) post-venta.
- Programas de fidelización dentro del chat.

---

**Fin del documento. Próximo paso**: revisar internamente, priorizar fases, definir feature flags, asignar dev, kickoff.
