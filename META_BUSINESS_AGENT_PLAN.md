# Meta Business Agent — Plan de Integración para AgencyHubara

> **Estado:** Propuesto · **Fecha:** 2026-07-02 · **Autor:** pipeline agente (research + arquitectura)
> **Alcance:** integrar el nuevo producto **Meta Business Agent (MBA)** como un **plugin toggleable** (`plugins/mba/`) que convive con el agente Sales/Remarketing actual sin tocarlo, con un switch de "prender / probar / apagar / volver" impecable.
> **De este documento depende el desarrollo.** Es implementation-grade: un implementer (humano o el pipeline Archon) debería poder construir desde acá. Todo lo marcado **⚠️ VALIDAR** requiere confirmación contra el sandbox de Meta antes de comprometerse.

---

## Índice

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Qué es Meta Business Agent (concepto + contexto)](#2-qué-es-meta-business-agent-concepto--contexto)
3. [Elegibilidad y prerequisitos](#3-elegibilidad-y-prerequisitos)
4. [Índice completo de documentación Meta](#4-índice-completo-de-documentación-meta)
5. [El modelo de APIs de MBA (referencia detallada)](#5-el-modelo-de-apis-de-mba-referencia-detallada)
6. [Nuestra arquitectura: el poder del plugin system](#6-nuestra-arquitectura-el-poder-del-plugin-system)
7. [Seams de convivencia](#7-seams-de-convivencia-cómo-se-para-nuestra-app-cuando-mba-responde)
8. [El modelo de dos toggles](#8-el-modelo-de-dos-toggles)
9. [Diseño del plugin `mba`](#9-diseño-del-plugin-mba)
10. [El script de activación/desactivación](#10-el-script-de-activacióndesactivación-el-botón)
11. [Mapeo tool-por-tool (connector tools ↔ nuestras use-cases)](#11-mapeo-tool-por-tool-connector-tools--nuestras-use-cases)
12. [Manejo del Remarketing agent](#12-manejo-del-remarketing-agent)
13. [Reubicación del CAPI (el riesgo silencioso)](#13-reubicación-del-capi-el-riesgo-silencioso)
14. [Riesgos y verdades duras](#14-riesgos-y-verdades-duras)
15. [Plan de rollout por fases](#15-plan-de-rollout-por-fases)
16. [Decisiones abiertas / ADRs pendientes](#16-decisiones-abiertas--adrs-pendientes)
17. [Verificación y testing](#17-verificación-y-testing)
18. [Próximos pasos](#18-próximos-pasos)
19. [Glosario](#19-glosario)

---

## 1. Resumen ejecutivo

Meta lanzó **Meta Business Agent (MBA)**: un agente de IA **hospedado por Meta** que se vuelve el **respondedor primario** de una conversación de WhatsApp. Tu app queda en modo **`standby`** — recibe los mensajes del cliente, copias de lo que manda el agente, y recibos de entrega/lectura; y toma el control mandando un mensaje o lo devuelve con la Thread Control API.

Esto toca **el core de Hubara** (nosotros SOMOS el respondedor de IA en WhatsApp: Sales + Remarketing sobre Temporal/DEHA, DeepSeek V4 Pro, SOUL.md, órdenes Medusa, CTWA/CAPI). Es a la vez amenaza (Meta commoditiza la capa conversacional) y **oportunidad** (MBA no tiene orquestación de comercio durable, motor de unit-economics CTWA, remarketing fuera de ventana, ni voseo colombiano fino — ese es nuestro moat).

**Postura estratégica: NO competir de frente — cabalgarlo.** MBA = *front-of-house* conversacional (chat sincrónico, FAQ, catálogo). Hubara sube de capa = **cerebro de comercio + control-plane**: connector tools durables, agent-events proactivos, remarketing fuera de ventana, economía CTWA, inbox humano, y onboarding/operación gestionada de flotas MBA.

**Forma de implementación:** un plugin **SIBLING** `plugins/mba/` (arquetipo `api_only` o `full_stack`), aislado por contrato (INV-1). El toggle de nuestro plugin system da el "prender / probar / apagar / volver" que necesitamos. El switch operativo diario vive del lado de **Meta** (instantáneo, API), mientras el flag `ENABLED_PLUGINS` se prende una vez y solo se toca en un decommission-deploy.

---

## 2. Qué es Meta Business Agent (concepto + contexto)

### 2.1. El modelo primario / standby

Cuando activás MBA en un número de WhatsApp:

- **MBA es el respondedor primario** — Meta contesta directamente al cliente desde tu conocimiento de negocio (business info, FAQs, websites, files) y **toma acciones a través de tus sistemas** (connector tools: consultar una orden, agendar, etc.), y hace handoff a tu app / a un humano cuando hace falta.
- **Tu app queda en `standby`** — sigue recibiendo:
  - los mensajes del consumidor,
  - **copias de los mensajes que manda el agente**,
  - recibos de entrega/lectura.
- **Qué campo de webhook dispara depende de quién tiene el control:**
  - campo **`messages`** cuando **tu app** tiene el control,
  - campo **`standby`** cuando **MBA** tiene el control.
- Un webhook **`messaging_handovers`** dispara **cuando el control cambia**.
- **Tu app toma el control simplemente mandando un mensaje** a la conversación.
- **Para devolver el control:** Thread Control (Cloud API) con `action: pass`.

### 2.2. Superficies de API (Onboard / Configure / Operate)

- **Onboard:** Eligibility · Onboarding · Settings · Allowlist
- **Configure:** Skills · Business info · FAQs · Websites · Files · Connectors · Connector tools
- **Operate:** Thread Control (Cloud API) · Agent Event · Agent test · Agent eval

### 2.3. Autenticación

- **System user token** (integradores directos) **o** **BISU token** (BSPs / Tech Providers).
- Permisos requeridos: `whatsapp_business_messaging` **y** `whatsapp_business_management`.
- Capabilities que autorizan las APIs de MBA (cualquiera de): `omniagent_api_access`, `bizai_wa_enterprise_api_3p_access`, o el permiso `whatsapp_business_messaging`.
- Todas las llamadas usan `Authorization: Bearer <token>` + header opcional `X-API-Version` (`2.0.0` para la mayoría; Thread Control usa `1.0.0`).

### 2.4. Setup base (una vez)

1. Configurar en **WhatsApp Manager**.
2. Crear **system user** (rol Admin) en Meta Business Suite.
3. Asignar **App + WABA** al system user (con permiso "View and manage phone numbers").
4. Generar **token**.
5. Suscribir la app a la WABA: `POST /{WABA_ID}/subscribed_apps` (verificar con `GET`).
6. Suscribir a los campos de webhook: **`messages`**, **`standby`**, **`messaging_handovers`**.

### 2.5. Contexto para Hubara

- **Canal:** WhatsApp únicamente (los schemas de skills/connectors mencionan otros canales — email, instagram, messenger, sms, tiktok, webchat, line — pero el producto vivo acá es WhatsApp).
- **Catálogo:** un product catalog de Meta (Commerce Manager) alimenta info de productos al agente.
- **Pricing:** los mensajes de MBA se cobran **distinto** a otros tipos de mensaje de WhatsApp (tarifa de "non-template messages"). **⚠️ VALIDAR** el número real antes de escalar; alimentarlo al motor de unit-economics CTWA (ver [memoria Ads Analytics Engine] y [cost_unit_lesson]).
- **Modelo LLM:** MBA corre **el modelo de Meta, no DeepSeek V4 Pro.** Perdemos el control fino del prompt/razonamiento/voseo. Se mitiga con Skills + gate de eval; **no** se reemplaza poseer el modelo. Ver §14.

---

## 3. Elegibilidad y prerequisitos

Un número es elegible si su WABA cumple **todo**:

| Criterio | Detalle | ¿Nos aplica? |
|---|---|---|
| **Fuera de EU/EEA** | Números registrados en EU/EEA **no** son elegibles hoy | ✅ Colombia OK |
| **Vertical soportada** | Todas excepto Finanzas, Gobierno, Salud, Alcohol, Gambling, OTC drugs, matrimonio. Requiere categoría de negocio válida | ✅ Comercio OK |
| **Cloud API** | Debe usar WhatsApp Business Platform (Cloud API), **no** la app WhatsApp Business | ✅ Ya la usamos |
| **En buen estado** | No restringido/baneado | ✅ |
| **Trust & verification** | Cumple requisitos de verificación de negocio | ✅ |
| **Sin producto de mensajería en conflicto** | **Un solo producto de mensajería por número** | ⚠️ **Probar en número FRESCO** |

**Para construir:** WABA + número + una Meta app con `whatsapp_business_messaging`. BSPs/Tech Providers deben aceptar los Tech Provider Terms of Service o las llamadas se rechazan.

**Cobertura de chat:** usuarios en 182 países pueden chatear con el agente (EU/EEA excluidos de elegibilidad).

---

## 4. Índice completo de documentación Meta

> **Base:** `https://developers.facebook.com/documentation/meta-business-agent/`
> Todos verificados y leídos el 2026-07-02. Los que dicen "**⚠️ leer al implementar**" no fueron cubiertos en profundidad en esta investigación (páginas de knowledge secundarias) y deben leerse antes de tocar esa superficie.

### 4.1. Conceptual
- **Get started** — https://developers.facebook.com/documentation/meta-business-agent/get-started
- **Overview** — https://developers.facebook.com/documentation/meta-business-agent/overview

### 4.2. Onboard
- **Eligibility** — https://developers.facebook.com/documentation/meta-business-agent/reference/onboard/agent-eligibility
- **Onboarding** — https://developers.facebook.com/documentation/meta-business-agent/reference/onboard/agent-onboarding
- **Settings** — https://developers.facebook.com/documentation/meta-business-agent/reference/onboard/agent-settings
- **Allowlist** — https://developers.facebook.com/documentation/meta-business-agent/reference/onboard/agent-allowlist

### 4.3. Configure
- **Skills** — https://developers.facebook.com/documentation/meta-business-agent/reference/configure/agent-skills
- **Business info** — https://developers.facebook.com/documentation/meta-business-agent/reference/configure/agent-knowledge-business-info
- **FAQs** ⚠️ leer al implementar — https://developers.facebook.com/documentation/meta-business-agent/reference/configure/agent-knowledge-faqs
- **Websites** ⚠️ leer al implementar — https://developers.facebook.com/documentation/meta-business-agent/reference/configure/agent-knowledge-websites
- **Files** ⚠️ leer al implementar — https://developers.facebook.com/documentation/meta-business-agent/reference/configure/agent-knowledge-files
- **Connectors** — https://developers.facebook.com/documentation/meta-business-agent/reference/configure/connectors
- **Connector tools** — https://developers.facebook.com/documentation/meta-business-agent/reference/configure/connector-tools

### 4.4. Operate
- **Thread Control (Cloud API)** — https://developers.facebook.com/documentation/meta-business-agent/reference/operate/thread-control-cloud-api
- **Agent Event** — https://developers.facebook.com/documentation/meta-business-agent/reference/operate/agent-event
- **Agent test** — https://developers.facebook.com/documentation/meta-business-agent/reference/operate/agent-test
- **Agent eval** — https://developers.facebook.com/documentation/meta-business-agent/reference/operate/agent-eval

### 4.5. Referencias externas
- WhatsApp Manager — https://business.facebook.com/wa/manage/home/
- Meta Business Suite (settings) — https://business.facebook.com/settings/
- Facebook Developer Portal (apps) — https://developers.facebook.com/apps/
- Graph API Explorer — https://developers.facebook.com/tools/explorer/
- Get started for Tech Providers — https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/get-started-for-tech-providers
- BISU tokens — https://developers.facebook.com/documentation/business-messaging/whatsapp/access-tokens/#business-integration-system-user-access-tokens
- Subscribed Apps API — https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/whatsapp-business-account/subscribed-apps-api#post-version-waba-id-subscribed-apps
- WhatsApp pricing (non-template messages) — sección de pricing de la plataforma WhatsApp (referenciada por el Overview; confirmar tarifa vigente).

---

## 5. El modelo de APIs de MBA (referencia detallada)

> Base URL genérica de configuración: `https://api.facebook.com/{entity_id}/...`
> `entity_id` = **WhatsApp Business Phone Number ID** (o Facebook Page ID en canales no-WhatsApp).
> Auth: `Authorization: Bearer <token>` + header `X-API-Version`.

### 5.1. Eligibility (read-only)
```
GET https://api.facebook.com/{phone_number_id}/agent_eligibility
→ 200 { "is_eligible": true|false }         # BizAIOmniChannelEligibilityResponse
```
Sin efectos secundarios. Devuelve solo el booleano (no desglosa qué criterio falló).

### 5.2. Onboarding
```
POST https://api.facebook.com/{entity_id}/agent_onboarding?channel=whatsapp
→ 201 { "agent_id": "<id de la agent settings entity>" }   # BizAIOmniChannelOnboardingResponse
```
"Crea las entidades necesarias y agenda jobs async de preparación de datos." Sin body. **Idempotencia:** guardá `agent_id`; si ya existe, no re-onboardees (⚠️ VALIDAR el comportamiento de doble-POST).

### 5.3. Settings
```
GET  https://api.facebook.com/{entity_id}/agent_config/settings      # BizAIOmniChannelSettingsResponse
PUT  https://api.facebook.com/{entity_id}/agent_config/settings      # BizAIOmniChannelSettingsRequest (reemplazo completo)
```
Campos:
- `rollout` → `{ enabled: bool }` — **el on/off del agente.** `false` = "the AI stops responding to all threads". Re-habilitar = responde a **threads nuevos únicamente**.
- `handoff` → `{ enabled: bool, message: string }` — handoff a **agente humano** (no "a la app"); `message` se muestra al usuario cuando ocurre.
- `followup` → `{ followup_interval_in_seconds }` — valores permitidos: `0, 300, 900, 1800, 3600, 7200, 28800, 86400`. `0` = deshabilita followup (re-engagement de usuarios inactivos, **dentro de ventana**).
- `ai_audience` → enum `EVERYONE` | `ALLOWLISTED_ONLY` — WhatsApp-only. `EVERYONE` = todos (default). `ALLOWLISTED_ONLY` = solo números en la allowlist.

### 5.4. Allowlist
```
POST   https://api.facebook.com/{entity_id}/agent_config/allowlist   { "consumer_phone_number": "+57..." }  → 201
GET    https://api.facebook.com/{entity_id}/agent_config/allowlist   → 200 [ { id, consumer_phone_number } ]
DELETE https://api.facebook.com/{entity_id}/agent_config/allowlist/{entry_id}  → 204
```
`consumer_phone_number` en E.164. **Con `ai_audience=ALLOWLISTED_ONLY`, la allowlist ES el control de la cohorte** — la palanca de rollout gradual. (Límite de tamaño no documentado.)

### 5.5. Skills
```
Base: https://api.facebook.com/{entity_id}/agent_config/skills   (query: agent_id)
POST / · GET / · GET /{skill_id} · PUT /{skill_id} · DELETE /{skill_id}
```
Schema (`BizAIOmniChannelSkillsRequest`):
- `title` — máx 64 chars, `lowercase-numeros-guiones`, no empieza/termina en guión (ej. `greeting-skill`, `product-return-policy`).
- `description` — máx 1024 chars. **El agente lo usa para decidir qué skill aplica** a la conversación actual.
- `skill` — el cuerpo de instrucciones, máx **20000 chars**.
- `channel` (en response, requerido): `whatsapp`.

**Regla:** no crear múltiples skills que reclamen prioridad para la misma situación (el agente no puede resolver prioridades en conflicto) — consolidar en un skill con secuencia explícita de pasos.
**Para Hubara:** SOUL.md + REGLA #1 anti-voseo → cuerpo del `skill`. Gate con eval (§17).

### 5.6. Knowledge — Business info
```
GET / · PUT / · DELETE /   en  https://api.facebook.com/{entity_id}/agent_config/business_info
```
Schema (`BizAIOmniChannelKnowledgeBusinessInfoRequest`, todos opcionales):
`payment_method`, `return_policy`, `purchase_info`, `delivery_and_shipping`, `business_description`, `contact_info { email, hours_of_operation, address }`.
(No hay campo `business_name`.) FAQs / Files / Websites tienen endpoints hermanos — ⚠️ leer al implementar.

### 5.7. Connectors
```
Base: https://api.facebook.com/{entity_id}/agent_connectors
POST / · GET / · GET /{id} · PUT /{id} · DELETE /{id}
GET  /{id}/logs        (errores del 3rd-party; rango ≤ 7 días; success_rate, avg/p95/p99 latency)
POST /{id}/upsertApiKey · /{id}/upsertOAuth · /{id}/upsertCertificate
```
Schema (`BizAIOmniChannelConnectorRequest`):
- `name` (req), `description` (req — "el agente lo usa para entender las capacidades del connector"),
- `base_url` (req) — base de **nuestra API** (el plugin `mba`),
- `auth_type` (req) — soportados: **`OAUTH2_CLIENT_CREDENTIALS`**, **`API_KEY`**, **`NONE`** (el schema lista OAUTH2/BASIC/CUSTOM pero no están soportados),
- `auth_config`, `user_auth_injection_config { location: body|headers|path|query, field_name, prefix }`, `requires_certificate` (mTLS).
- `connection_status.status` ∈ `PENDING_OAUTH | ACTIVE | EXPIRED | ERROR`.

Auth options: **API_KEY** (arrays `headers`/`query_params`/`body_params` con `field_name`,`value`,`prefix`) · **OAUTH2_CLIENT_CREDENTIALS** (`token_url`, `scopes_to_request`, `client_id`, `client_secret`) · **mTLS** (`upsertCertificate` con `client_certificate`/`client_key`/`ca_certificate`; la private key nunca se expone).

### 5.8. Connector tools
```
Base: https://api.facebook.com/{entity_id}/agent_connectors/{connector_id}/tools
GET / · POST / · GET /{tool_id} · PUT /{tool_id} · DELETE /{tool_id}
POST /{tool_id}/run    # ejecuta la acción; devuelve la respuesta cruda del upstream
```
Schema (`BizAIOmniChannelConnectorToolRequest`):
- `name` — clave estable visible al agente (ej. `check_order_status`, `create_return_request`).
- `description` — **maneja cuándo el agente invoca la tool. Descripciones vagas/ausentes → el agente invoca mal o no invoca.**
- `request_definition`:
  - `method` ∈ GET|POST|PUT|DELETE|PATCH
  - `path` — template con `{placeholder}`
  - `path_parameters`, `query_parameters`, `headers` — cada uno un `ParameterNode { type: string|integer|number|boolean, description, required, binding }`
  - `body` — `BodyDefinition { content_type: application/json, params: {BodyNode}, required: [keys] }`; `BodyNode` agrega `object`/`array` con `properties`/`items` (usar `object` con `properties` explícitas, nunca `object` sin definir).
- `user_auth_required` (bool), `user_auth_action_config` (login/refresh: `user_action_tool_type: auth|refresh`, `user_auth_token_path`, `refresh_token_path`, `expires_at_path`, `expires_at_type: absolute|relative_seconds`).
- **Bindings** (`ParameterBinding`): campos que Meta llena. `kind: default` (literal en `value`) o `kind: macro` (`macro ∈ WHATSAPP_PHONE_NUMBER | WHATSAPP_IDENTITY_HASH | WHATSAPP_CURRENT_STATUS_ID`). **Sin binding = el agente lo llena desde la conversación.**

**Run response** (`BizAIOmniChannelConnectorToolRunResponse`): `output` (JSON-encoded string), `status: success|error`.
**Error** (`StandardError`): `title`, `detail`, `type`, `status`.

### 5.9. Agent Event (app → agente)
```
POST https://api.facebook.com/{entity_id}/agent_event      # enqueue async, devuelve status + agent_event_id
GET  https://api.facebook.com/{agent_event_id}             # poll status
```
**Dirección: la app MANDA eventos AL agente** (no es un webhook que recibís). Schema (`BizAIOmniChannelAgentEventRequest`):
- `to` (req) — consumidor E.164
- `event.type` (req, ≤256) — partner-defined (ej. `payment_received`, `document_verified`, `order_shipped`)
- `event.description` (req, ≤1024) — human-readable
- `event.payload` (req, ≤4096) — JSON string opaco pasado al agente as-is

Status enum: `request_received | processing | sent | failed | skipped | success`.
**Para Hubara:** así empujamos ETA/confirmación-de-pago/orden-despachada **dentro** de la conversación de MBA. Mapea a nuestro agente ETA (`eta`).

### 5.10. Thread Control (Cloud API)
```
POST https://api.facebook.com/business/whatsapp/phone_numbers/{phone_number_id}/thread_control
X-API-Version: 1.0.0
{ "messaging_product": "whatsapp", "action": "pass"|"release", "to": "<consumidor E.164 o WA ID>" }
→ 200 { "messaging_product": "whatsapp" }
```
- `pass` → entrega el control a MBA (resume respondiendo al consumidor).
- `release` → suelta el control, la conversación queda idle sin respondedor automático.
- **Precondición: debés tener el control actualmente.** **Tomar** el control = mandar un mensaje (no hay acción `take` explícita).
- Capability requerida: `first_party_apps_accessing_whatsapp_business_manager_apis`.

### 5.11. Agent test
```
POST https://api.facebook.com/{entity_id}/agent_test
{ "user_msg": "...", "conversation_id"?: "..." }     # multi-turn encadenando conversation_id
→ 200 { message_id, agent_response, conversation_id, timestamp?, handoff_reason?,
        no_response_reason?, quick_replies?[], product_variant_ids?[] }
```
Corre el pipeline completo del agente **sin** número de consumidor. No expone tools invocadas ni chain-of-thought (las señales más cercanas son `product_variant_ids` y `handoff_reason`).

### 5.12. Agent eval
```
Base: https://api.facebook.com/{entity_id}/agent-eval
GET  /cases                          # BizAIEvalCaseResponse: { id, scenario, categories, max_turns, success_criteria }
POST /run?eval_case_ids=<pfbid,...>  # → { job_id, status: QUEUED }
GET  /run?job_id=<id>                # QUEUED|RUNNING|COMPLETED|FAILED + progress + result
GET  /details                        # per-conversation: score, per_turn_labels, reasons, transcript
GET  /summary                        # avg_conversation_score, avg_turn_score, top_failure_categories
```
Corre un **simulador de usuario** contra escenarios + los puntúa con un **judge LLM** (score **1–5**). `avg_conversation_score` / `avg_turn_score`. Building blocks para regresión: eval cases reusables + re-run + comparar scores.
**Para Hubara:** el gate de calidad determinista que encaja con nuestra cultura TDD/gates (ver §17).

---

## 6. Nuestra arquitectura: el poder del plugin system

> Fuentes: `ARCHITECTURE_FINAL_fable.md`, `PLUGIN_PROTOCOL_fable.md`, `PLUGIN_CONTRACT.md`, código vivo en `hubara_agency/`. Refs `file:line` verificadas contra HEAD de `main`.

### 6.1. El toggle es una env var, aplicada simétricamente

**`ENABLED_PLUGINS` (CSV) es la ÚNICA fuente de verdad de la presencia de un plugin (INV-2).** El manifest solo *describe*; el env *decide qué corre*. Vacío/ausente = `None` = **todos** (fail-open, dev-friendly).

Semántica canónica: `src/platform/plugin_manifest.py:46` → `enabled_plugins()`. Se aplica en **5 superficies**:

| Superficie | `file:line` | Con el plugin OFF |
|---|---|---|
| API (routers HTTP) | `src/main.py:80-125` (`_discover_plugin_manifests`, filtro `:103`) → `_bootstrap_routers:168` | no importa su módulo api → **no registra router** |
| Workers (containers) | `scripts/render-compose.py:65` (`_resolved_enabled`), `:93` (`_discover_worker_services`) | **no se renderiza** el service → no arranca |
| Worker self-gate (runtime) | `src/platform/plugin_runtime.py:32` (`ensure_plugin_enabled`) | container huérfano → `SystemExit(1)` |
| Route registry | `src/platform/routing.py:66-116` (`_build_registry`, salta no-enabled `:69`) | su ruta **no resuelve** → fallback |
| Frontend | `frontend_dashboard/scripts/plugins-sync.ts` | sección/sidebar/íconos desaparecen |

Guard de coherencia al boot: `src/platform/plugin_loader.py:33` (`validate_enabled`) — habilitar un plugin cuyo `depends_on` no está habilitado → **falla al boot** con mensaje accionable (llamado en `main.py:184`).

### 6.2. Las dos invariantes que nos dan "prender/apagar/volver"

`PLUGIN_CONTRACT.md:40-61`:
- **INV-1 (aislamiento aditivo):** agregar un plugin = crear archivos **únicamente** bajo `plugins/<id>/` (ambos stacks) + su `plugin.yaml`. **Cero ediciones fuera** (ni `main.py`, ni `constants.py`, ni `Dashboard.tsx`, ni barrels). Gate P-NOEDIT (`:150`).
- **INV-2 (toggle simétrico):** presencia gobernada SOLO por `ENABLED_PLUGINS`. Apagar remueve TODA la superficie sin romper nada. Corolario: todo el código de X vive bajo `plugins/X/`, nunca dentro de otro plugin.

**Consecuencia clave:** como `mba` no puede tocar `chats`, apagar `mba` deja Sales/Remarketing **idéntico a antes** — "volver exactamente a donde estábamos" es garantía de contrato, no esperanza.

### 6.3. Exponer HTTP APIs como plugin (routers)

`src/main.py:128` `_register_router_from_module` + `:168` `_bootstrap_routers`:
- El plugin declara en su manifest `api.python_module: src.plugins.<id>.api` (+ `prefix`, `tags`), o `api.legacy_routers: [{module, prefix, tags}]` para múltiples sub-routers (gana sobre `python_module`, `main.py:195-221`).
- El módulo DEBE exponer `router: APIRouter` (protocolo `ApiModule`, `plugin_protocol.py:39`). Sin `router` → boot muere (`main.py:139-146`).
- **Auth default: fail-closed** — toda ruta de plugin nace con `Depends(require_auth)` (JWT Cognito, `main.py:147-165`).
- **Router público:** `PUBLIC_ROUTER = True` a nivel módulo → sin `require_auth` (`:152-157`). Patrón vivo: `chats/api/sales.py:31-37` (webhook de Meta, valida su propio HMAC).
- **Un plugin puede mezclar routers públicos y privados** vía `legacy_routers` — `chats` ya lo hace (`sales.py` público + `dashboard.py`/`handoff.py` privados).

### 6.4. Route registry + `owns_route`

`src/platform/routing.py`: un worker declara `owns_route: <ruta>` + `route_workflow_id_template` bajo `agent.workers[]`. El registry (`_build_registry:66`) escanea manifests habilitados; `resolve_route_workflow_id(route, session_id):119` devuelve el workflow o `None`. Rutas core `{ventas, remarketing, humano}` (`_CORE_ROUTES:44`) **no son registrables**.
**Gotcha (ver §7):** el registry asume que el dueño de la ruta es un **workflow Temporal RUNNING** al que se le signalea (`load_or_start_sales_session.py:190-193`). **MBA no es un workflow.** Por eso el `owns_route` clásico **no aplica** al MBA — el switch va por otro lado (§7).

### 6.5. Los 5 arquetipos

`src/sdk/testkit/archetypes.py:53-134` (P-29 — cada plugin declara `archetype:` y el TCK audita su forma de por vida):

| archetype | frontend/api/workers | ejemplo | relevante al MBA |
|---|---|---|---|
| `api_only` | ✗ / req / ✗ | system_map | si MBA es solo tools HTTP |
| `full_stack` | req / req / opcional | orders, ads | tools HTTP + panel de control |
| `agentic` | opc / opc / req | chats | worker conversacional Temporal (NO es el caso MBA) |
| `notifier` | opc / opc / req (`forbid_owns_route`) | eta | push puro |
| `sync` | opc / opc / req | catalog | pipeline source→sink |

**MBA → `api_only` o `full_stack`** (NO `agentic`: el turno conversacional lo maneja Meta, no un worker nuestro). Scaffold certificado C2:
```
cd hubara_agency && uv run python -m src.sdk.cli create plugin mba --archetype api_only
```

### 6.6. `chats` es un plugin

Sales/Remarketing/ETA-legacy viven como el plugin `chats`: backend `src/plugins/chats/`, manifest `frontend_dashboard/src/plugins/chats/plugin.yaml` (⚠️ **el manifest vive en el árbol FRONTEND**, leído por `src/platform/plugin_manifest.py`). **MBA será un SIBLING** (`plugins/mba/`), nunca dentro de `chats`.

Estructura de un plugin (canónica, `PLUGIN_CONTRACT.md:65-90`):
```
plugins/<id>/
├── plugin.yaml            (contrato — en frontend_dashboard/src/plugins/<id>/)
├── api/                   __init__.py expone router (+ legacy_routers)
├── agent/<worker>/        (solo arquetipo agentic)
├── workers/<worker>.py    (solo si hay workers)
└── shared/contracts/      eventos que emite
```

---

## 7. Seams de convivencia (cómo se para nuestra app cuando MBA responde)

> El explorer confirmó dos puntos de inserción. **El patrón ya existe** — es idéntico al handoff humano que ya opera (ver [memoria human_handoff_tag_invariant]).

- **[SEAM-1] Ingreso del webhook** — `src/plugins/chats/api/sales.py:53-127` (`handle_whatsapp_webhook`). Hoy solo parsea `messages` (`:116,125-126`) y `statuses` (`:105-113`); **un `field` desconocido cae en `parsed is None` y se ignora en silencio (`:121-123`).** Los campos `standby` / `messaging_handovers` caen ahí hoy.
- **[SEAM-2] Resolutor de ruta** — `load_or_start_sales_session.py:160-165`: `if active_route == ROUTE_HUMANO: return` (persiste el mensaje, no despacha al workflow). **Es el molde exacto de un standby.**

### 7.1. El switch lo maneja Meta, nos lo avisa por el campo del webhook

**Diseño recomendado (evita inventar un `ROUTE_META_AGENT` con dueño Temporal):**

- campo **`messages`** → nosotros tenemos control → despachamos a Sales/Temporal (comportamiento de HOY, intacto).
- campo **`standby`** → MBA tiene control → **persistimos/observamos, no despachamos.**

Cuando MBA está apagado (`rollout.enabled=false` o allowlist vacía), **Meta nunca manda `standby`** → todo entra como `messages` → flujo actual. **La rama de standby queda dormida cuando MBA está off** → reversibilidad gratis.

**El único agregado que toca `chats`:** un branch nuevo en el webhook (`sales.py`) que detecta el campo `standby`/`messaging_handovers`, persiste para observabilidad y retorna. `chats` es el dueño legítimo del ingress de WhatsApp, así que este branch le corresponde — pero **roza INV-1** (edita `chats` por una feature de `mba`). Ver la decisión de ADR en §16.

### 7.2. Invariante a replicar

Si en algún punto se materializa un `active_route`/tag para MBA (opción alternativa del ADR): replicar el par `active_route`+`tag` y el guard de `ingest_inbound_message.py:198` (que no rota episodios ni pisa el tag cuando otro respondedor tiene el caso), y el skip del watchdog (§12) — **sino el chat queda huérfano** (lección ya vivida con el handoff humano).

---

## 8. El modelo de dos toggles

Son **dos switches a dos velocidades**. Confundirlos es el error de diseño a evitar.

| Switch | Qué controla | Velocidad | Quién lo mueve |
|---|---|---|---|
| **Meta-side** (`rollout.enabled` + `ai_audience` + allowlist) | Si MBA responde y a quién | **Instantáneo** (API call) | El botón del dashboard (§10) |
| **`ENABLED_PLUGINS` incluye `mba`** | Si existe nuestra maquinaria (tools HTTP + panel) | **Deploy** (restart de containers) | Se prende UNA vez; solo se toca en decommission |

**Por qué el flag de plugins NO es un botón instantáneo:**
1. `ENABLED_PLUGINS` se lee en el **boot** (routers, render-compose, self-gate, registry, plugins-sync). Cambiarlo = reescribir SSM + re-render compose + **reiniciar**. Eso es un deploy.
2. **Trampa lógica:** el endpoint que apaga el plugin no puede vivir dentro del plugin — se borraría a sí mismo (su router deja de montarse). Un botón que flipee `ENABLED_PLUGINS` es auto-referencial.

**Resolución:** el plugin `mba` cargado **pero con Meta en `rollout.enabled=false` es INERTE** (INV-1 garantiza `chats` intacto; Meta nunca manda `standby`). Entonces:
- **Loop diario "probar / apagar / volver":** el botón toca **solo Meta**. El flag de plugins queda prendido (inofensivo).
- **Decommission (fin del experimento):** un botón/acción separada que además saca `mba` de `ENABLED_PLUGINS` vía SSM + re-render + restart (= workflow *Backend deploy*). Para "volver exactamente" ni hace falta — con `rollout.enabled=false` ya estás ahí.

---

## 9. Diseño del plugin `mba`

```
hubara_agency/src/plugins/mba/
├── api/
│   ├── __init__.py            # expone `router` (o usa legacy_routers en el manifest)
│   ├── tools.py               # PUBLIC_ROUTER=True → connector-tools que llama Meta (firma-gated)
│   ├── control.py             # (JWT, privado) el "botón": PUT/GET /api/mba/state
│   └── handover.py            # (opcional) parser de standby/messaging_handovers para observabilidad
├── use_cases/
│   ├── toggle_mba.py          # activate() / deactivate()  (idempotente, discover→plan→apply)
│   ├── run_connector_tool.py  # traduce el tool-call de Meta → use-case interno (orden/catálogo/ETA)
│   └── push_agent_event.py    # empuja ETA/pago/despacho a MBA vía /agent_event
├── adapters/
│   └── meta_agent_client.py   # cliente HTTP de las APIs admin de MBA (Bearer system-user de SSM)
└── (frontend) frontend_dashboard/src/plugins/mba/
    ├── plugin.yaml            # manifest (archetype: api_only|full_stack; api.legacy_routers)
    ├── entities/mba/          # tipos + Zod + query hooks
    └── features/
        ├── mba-toggle/        # switch + badge de estado (useMbaState / useToggleMba)
        └── mba-allowlist/     # alta/baja de la cohorte de prueba
```

**Dos superficies de router (vía `legacy_routers`):**
1. **Pública** (`tools.py`, `PUBLIC_ROUTER=True`): los endpoints que Meta llama como connector tools. **Gate de seguridad obligatorio** (firma/token compartido con Meta o mTLS) — a diferencia del webhook de `chats` que valida HMAC de Meta, estas tools consultan orden/catálogo/ETA y sin gate son superficie de abuso.
2. **Privada** (`control.py`, JWT): el botón del dashboard.

**Secretos (SSM `/hubara/<tenant>/mba/…`):** system-user token, `agent_id` cacheado. Reusa `WHATSAPP_BUSINESS_ACCOUNT_ID` (`config.py:72`) y el phone_number_id existente.

**Reglas duras que aplican:**
- **P-28:** plugins importan `src.sdk`, nunca `src.platform` directo. (Nota: `chats` es legacy pre-SDK y viola esto; no lo empeoremos en `mba`.)
- **P-NOEDIT/INV-1:** `mba` no toca `main.py`, `routing.py`, `constants.py`. La única excepción candidata (el branch de standby en `chats`) es materia de ADR (§16).
- **L-6 (worker lambda missing import):** si registra tool extensions, `ruff check --select F821`.
- **Regla de oro del manifest:** ningún campo del manifest sin su check (Capa 3, `test_plugin_conformance.py`).

---

## 10. El script de activación/desactivación (el "botón")

Un endpoint privado que orquesta las llamadas a Meta de forma **idempotente**, con la filosofía **discover → plan → apply** del provisioning toolkit (PR #94, [memoria whatsapp_provisioning_toolkit]).

### 10.1. Contrato del endpoint (`api/control.py`, JWT)
```
GET  /api/mba/state
     → { enabled: bool, is_eligible: bool, ai_audience, allowlist: [E.164], agent_id?, connection_status }
        (lee el estado REAL de Meta: GET agent_eligibility + GET settings + GET allowlist)

PUT  /api/mba/state
     body: { enabled: bool, allowlist?: [E.164] }
     → aplica el delta y devuelve el nuevo state
```
Como Meta es la fuente de verdad, si un `apply` falla a mitad, `GET /api/mba/state` muestra exactamente en qué quedó → re-corrés. Sin estado parcial mentiroso.

### 10.2. `activate()` (idempotente)
1. `GET agent_eligibility` → si `is_eligible=false`, cortar con la razón.
2. `POST agent_onboarding` **si no hay `agent_id`** (sino skip). Cachear `agent_id` en SSM.
3. Upsert idempotente de config: `business_info`, `skills` (voseo), `connectors` + `tools` (solo lo que falte).
4. `POST /{WABA}/subscribed_apps` con `messages,standby,messaging_handovers` (si falta).
5. `POST allowlist` — delta contra lo que ya está (agrega la cohorte).
6. `PUT settings` → `rollout.enabled=true`, `ai_audience=ALLOWLISTED_ONLY`.

### 10.3. `deactivate()` (el off reversible)
1. `PUT settings` → `rollout.enabled=false` ("AI stops responding to all threads") → nuestra app vuelve a responder al instante.
2. (opcional) `POST thread_control action=release` para hilos que MBA tenía en vuelo (no quedan colgados).

### 10.4. Frontend (FSD)
- Sección "Meta Business Agent" (contribution point del shell).
- `features/mba-toggle/`: switch + badge de estado (`useMbaState` GET, `useToggleMba` PUT).
- `features/mba-allowlist/`: alta/baja de números (con `ALLOWLISTED_ONLY`, la allowlist ES la cohorte — probás con tu propio número primero).
- (opcional) botón "correr eval" contra `agent-eval`.

### 10.5. Decommission (deploy, separado del botón diario)
Acción "Decomisionar MBA" que dispara un job de deploy: actualiza el SSM param `ENABLED_PLUGINS` → `render-compose` → `docker compose up --remove-orphans` (en prod = workflow *Backend deploy*, [memoria prod_credential_replacement_authorized]). Más lento, reinicia servicios; solo para cerrar el experimento del todo.

---

## 11. Mapeo tool-por-tool (connector tools ↔ nuestras use-cases)

> La tabla que guía el `api/tools.py`. Cada connector tool de Meta apunta a un endpoint HTTP nuestro que reusa un use-case existente. Refs de las casts internas actuales: `chats/api/order_actions.py` (`/api/chats/order-actions/*`), `eta` (`/api/eta`), orders (`/api/orders`).

| Connector tool (Meta) | Endpoint nuestro (`plugins/mba/api/tools.py`) | Reusa | Bindings | Notas |
|---|---|---|---|---|
| `check_order_status` | `GET /api/mba/tools/order-status?phone={WHATSAPP_PHONE_NUMBER}` | use-case de consulta de orden (ETA/orders) | `phone`=macro `WHATSAPP_PHONE_NUMBER` | devuelve status + ETA + tracking |
| `browse_catalog` | `GET /api/mba/tools/catalog?query=...` | snapshot de catálogo (plugin catalog) | — | ⚠️ sembrar snapshot ([memoria catalog_snapshot_seed]) |
| `create_order` | `POST /api/mba/tools/order` | **envolver `RegisterOrderTool` en activity durable idempotente** | `phone`=macro | idempotencia real (fingerprint+pre-check), no solo marca en metadata ([memoria temporal_atomicity]) |
| `get_shipping_eta` | `GET /api/mba/tools/eta?phone={WHATSAPP_PHONE_NUMBER}` | agente ETA | `phone`=macro | payment_confirmed = `pay_status=='paid'`, no `pay_type` ([memoria eta_paytype_vs_paystatus]) |
| `escalate_to_human` | `POST /api/mba/tools/handoff` | `_route_to_human` / thread control | `phone`=macro | reconciliar con `handoff` nativo de MBA (§16) |

**Regla:** las tool-calls de MBA son HTTP best-effort; **nosotros envolvemos el side-effect en una activity Temporal idempotente** — MBA maneja la charla, Hubara garantiza la durabilidad del comercio. Ese es el moat.

---

## 12. Manejo del Remarketing agent

MBA trae `followup` nativo (re-engagement **dentro** de ventana, intervalos `300..86400s`). Solapa con nuestro watchdog. División limpia:

| Re-engagement | Dueño | Por qué |
|---|---|---|
| **Dentro de ventana 24h** (nudge sincrónico) | **MBA `followup`** | primitiva nativa; dejamos de competir en threads que MBA controla |
| **Fuera de ventana (24h+, utility templates)** | **Hubara Remarketing** | MBA no hace templates business-initiated fuera de ventana |
| **Economía/atribución del re-engagement** | **Hubara** | CTWA + CAPI |

**Cambios concretos:**
- Extender el skip de elegibilidad del watchdog (`check_watchdog_eligibility_activity`, hoy skipea `active_route==humano`, ref `handoff.py:145-148`) para **también skipear cuando MBA tiene el control** — sino mandamos un nudge por encima de la conversación de Meta y la Cloud API lo rechaza (no tenemos el thread control).
- Remarketing worker vive en `chats/workers/remarketing.py:47-53` (task queue `queue-remarketing-agent`); disparado event-driven vía `plugin.yaml` transitions (`SalesSessionCompletionEvent(tag=INTERESADO)`, `ServiceWindowOpenedEvent`).

**⚠️ VALIDAR en sandbox:** para mandar un template fuera de ventana **hay que tomar el thread control** (mandar un mensaje). Después del template: ¿`pass` control de vuelta a MBA para que maneje la respuesta sincrónica, o lo manejamos nosotros? Recomendación: `pass` a MBA (mantenemos scheduling+economía, MBA mantiene la charla). El comportamiento de "template mientras standby" hay que probarlo antes de comprometerlo.

---

## 13. Reubicación del CAPI (el riesgo silencioso)

Hoy el evento CAPI se dispara desde los **closing-tags de NUESTRO LLM**:
- `src/platform/whatsapp/capi.py:71` `LEAD_EVENT_NAME="LeadSubmitted"` (**NO "Lead"** — Meta lo rechaza, cazado por smoke real, [memoria capi_integration_plan]), `:80` `ALLOWED={LeadSubmitted, Purchase}`.
- `sales_session.py:58-63` `_map_closing_tag_to_capi_event`; `:499-510` dispatch bajo `workflow.patched("capi-event-emit-v1")`.
- `capi_activity.py:282-283` `send_capi_event_activity`.

**El riesgo:** si MBA maneja la conversación, **nuestro LLM ya no cierra episodios** → los closing-tags no se emiten → **la atribución CAPI se rompe en silencio** (justo el gotcha #1 "verificá comportamiento, no schema" + la lección "LeadSubmitted lo cazó el smoke, no los unit tests").

**El fix:** el trigger del CAPI debe **migrar** de closing-tag del LLM → a un trigger de **ciclo de vida de orden / Agent-Event**:
- cuando MBA invoca `create_order` (connector tool) o cuando la orden llega a `paid`/despachada, el plugin `mba` (o el plugin orders) dispara `send_capi_event_activity` con el mismo `make_event_id_for_lead` (`capi.py:243`) para idempotencia.
- **Esto es lo más delicado del plan.** Requiere un guard de comportamiento (un test que verifica que el CAPI dispara desde el nuevo trigger, no solo que el schema lo permite).

---

## 14. Riesgos y verdades duras

1. **Swap de modelo:** MBA corre el LLM de Meta, no DeepSeek V4 Pro. Perdemos control fino de tono/voseo/reasoning/thinking. Skills mitigan; **validar con eval ANTES de confiar producción**, no después.
2. **Migración del moat conversacional:** si MBA es "good enough", el valor de nuestro Sales agent migra a la capa de comercio/economía. Apostar deliberadamente a subir de capa.
3. **Race conditions de control:** un número con dos respondedores → la disciplina standby+thread-control debe ser impecable (espejar el invariante del handoff humano que ya nos mordió con chats huérfanos).
4. **CAPI silencioso** (§13).
5. **Pricing distinto:** modelarlo en el motor de unit-economics antes de escalar.
6. **"Un solo producto de mensajería por número":** probar en número FRESCO, no sobre uno productivo.
7. **Determinismo Temporal (R-DET):** toda rama nueva relacionada al workflow de Sales para el modo MBA necesita su propio `workflow.patched()` para no romper replay de workflows en vuelo.
8. **Seguridad de las connector tools públicas:** el webhook de `chats` es público *porque valida HMAC*; nuestras tools públicas que consultan orden/catálogo necesitan su propio gate (firma/token compartido/mTLS).
9. **`ENABLED_PLUGINS` no es runtime** (§8) — no prometer un botón instantáneo para el flag de plugins.

---

## 15. Plan de rollout por fases

Cada fase reversible.

- **Fase 0 — Sandbox & elegibilidad (sin tráfico prod).** Número FRESCO → `GET agent_eligibility` → `agent_onboarding` → cablear connectors/tools a un **backend Hubara de staging** → `agent_test` + `agent_eval`. **Aterriza todas las incógnitas: pricing real, timing del handover, calidad de tono vs DeepSeek, comportamiento de template-en-standby.**
- **Fase 1 — Champion/challenger vía allowlist (un número prod).** `ai_audience=ALLOWLISTED_ONLY` + allowlist de una cohorte chica → MBA los atiende, el resto sigue en Hubara. Introducir el branch de standby (§7). **Medir:** calidad (eval), costo/conversación, conversión, cumplimiento de voseo.
- **Fase 2 — División del trabajo.** MBA = front-of-house sincrónico. Hubara = comercio (connector tools) + notificaciones (agent events) + remarketing fuera de ventana + economía CTWA + inbox humano. Watchdog aprende el estado MBA.
- **Fase 3 — Flotas MBA gestionadas.** Onboarding/config/eval como producto de agencia (provisioning CLI + Paperclip).

---

## 16. Decisiones abiertas / ADRs pendientes

1. **ADR — Dónde vive el branch de `standby`.** (a) En el webhook de `chats` (`sales.py`, mínimo pero toca `chats` → roza INV-1); (b) un pre-router de webhook a nivel `platform` que decide standby antes de entrar a `chats` (INV-1 puro, infra nueva). **Recomendación:** empezar con (a) documentado como excepción consciente en el ADR, migrar a (b) si aparece un 2º consumidor de handover. Este es el único punto donde "volver exactamente" necesita diseño explícito.
2. **Decisión — Template fuera de ventana mientras MBA controla** (§12). ⚠️ VALIDAR en sandbox: `pass` a MBA post-template vs manejar la respuesta nosotros.
3. **Decisión — Reconciliar handoff humano.** MBA tiene `handoff.enabled` nativo (a humano) + nuestra bandeja rica (tag==HUMANO). ¿MBA señaliza → thread control lo enruta a nuestro inbox? Definir el round-trip.
4. **Decisión — Botón único vs. dos.** ¿El botón del dashboard incluye desde el día uno el decommission-deploy del flag de plugins (un solo control, más infra), o mantenemos el toggle Meta-side instantáneo separado del decommission (recomendado)?
5. **Decisión — Auth de las connector tools públicas** (§14.8): firma compartida vs mTLS vs token en header inyectado por `user_auth_injection_config`.

---

## 17. Verificación y testing

**TDD obligatorio (harness hubara-dev):** rojo → verde → refactor; el test asierta comportamiento observable.

- **Use-case `toggle_mba`:** rojo primero con `meta_agent_client` mockeado — verificar que `activate()` emite la secuencia de llamadas idempotente y que re-correr no duplica.
- **Connector tools:** cada endpoint de `tools.py` con test de contrato (input de Meta → use-case correcto → `output` JSON-encoded + `status`).
- **CAPI reubicado (§13):** **guard de comportamiento** — el CAPI dispara desde el nuevo trigger (orden/agent-event), no desde closing-tags. NO solo schema.
- **Standby branch (§7):** test del webhook que verifica que un payload con field `standby` NO despacha a Temporal y que un `messages` sí (comportamiento actual intacto).
- **Gates deterministas:** `/hubara-gates` (P-6/P-21 conformance, arquitectura, certificación TCK, CLI). Frontend: `npm run test:arch` (dependency-cruiser, FSD).
- **Eval de Meta como gate de calidad:** `agent-eval` con `eval_case`s de tono/voseo + `success_criteria`; umbral de `avg_conversation_score` antes de subir `rollout.enabled=true` en prod.
- **Smoke real:** como en CAPI, un smoke E2E contra el sandbox caza lo que los unit tests no (nombres de evento, elegibilidad, template-en-standby).

---

## 18. Próximos pasos

1. **Scaffold `plugins/mba` apagado:** `cd hubara_agency && uv run python -m src.sdk.cli create plugin mba --archetype api_only` (nace certificado, `ENABLED_PLUGINS` sin `mba`).
2. **`meta_agent_client` + `toggle_mba` (TDD):** rojo con cliente mockeado → `activate()`/`deactivate()` idempotentes → `PUT/GET /api/mba/state`.
3. **Mapeo tool-por-tool (§11):** implementar `api/tools.py` con el gate de firma, reusando use-cases existentes.
4. **Mini-ADR del branch de standby (§16.1)** antes de tocar `chats`.
5. **Fase 0 en sandbox:** elegibilidad + onboarding + connectors/tools contra staging + `agent_test`/`agent_eval`.
6. **Frontend:** sección + `mba-toggle` + `mba-allowlist`.

---

## 19. Glosario

- **MBA** — Meta Business Agent (agente hospedado por Meta = respondedor primario de WhatsApp).
- **standby** — estado de nuestra app cuando MBA tiene el control; recibe mensajes/copias/recibos pero no responde. También es el nombre del campo de webhook.
- **Thread Control** — API de handover (`pass`/`release`); tomar control = mandar mensaje.
- **connector / connector tool** — el puente HTTP MBA→nuestro backend; connector = servicio+auth, tool = una acción ejecutable.
- **agent_event** — API app→agente para empujar señales (pago/despacho/ETA) a la conversación de MBA.
- **allowlist** — con `ai_audience=ALLOWLISTED_ONLY`, la cohorte de consumidores que MBA atiende (palanca de rollout).
- **`ENABLED_PLUGINS`** — env var CSV, fuente única de presencia de plugins (INV-2). Deploy-level.
- **INV-1 / INV-2** — aislamiento aditivo / toggle simétrico (`PLUGIN_CONTRACT.md`).
- **SEAM-1 / SEAM-2** — webhook ingress / route resolver: los puntos donde un inbound puede desviarse a standby.
- **CTWA / CAPI** — Click-to-WhatsApp / Conversions API (atribución ad→conversación→orden→revenue; nuestro moat).

---

*Documento vivo. Cuando una decisión de §16 se resuelve, actualizar la sección correspondiente y anotar la lección (Síntoma→Causa→Fix→Regla→Guard) si aplica. Fuentes primarias: la documentación Meta de §4 + el código vivo de `hubara_agency/` (gana sobre docs grandes históricos).*
