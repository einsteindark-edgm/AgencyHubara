# Meta Business Agent — Roadmap a producción

> **Estado:** En ejecución · **Fecha:** 2026-09-04 · **Base estratégica:** `META_BUSINESS_AGENT_PLAN.md` (2026-07-02, por qué y cómo convivir con MBA).
> **Este documento es el QUÉ HAY QUE CONSTRUIR**, en orden, con archivos, tests y criterio de terminado por desarrollo. Cuando contradiga al plan estratégico, gana este (está hecho con la doc de Meta releída el 2026-09-02..04 y con el código vivo).
> Rama de trabajo: `claude/meta-business-agent-integration-dd9274`.

---

## 0. Principios que gobiernan todo el roadmap

1. **MBA reemplaza UNA capa: el turn loop conversacional** (DeepSeek + harness). Temporal sigue siendo el cerebro de comercio y la fuente de verdad (orden idempotente, pago conciliado, ETA, remarketing fuera de ventana, sentinel, CAPI, inbox humano).
2. **MBA propone, Hubara decide.** Todo lo que MBA manda por connector (tags, escalación, slots, orden) es una propuesta que Hubara reconcilia con reglas deterministas antes de aplicar. Ver §D1.3.
3. **Nada se envía a Meta sin verse antes en la tab "Meta Business Agent"** del dashboard (desarrollo 1). La tab es la fuente única de "qué le mandamos".
4. **Dos toggles, siempre reversible:** Meta-side (`rollout.enabled` + `ai_audience` + allowlist) y Hubara-side (`ENABLED_PLUGINS` incluye `mba`). Apagar cualquiera de los dos devuelve el sistema a hoy.
5. **Todo mensaje que Hubara envíe toma el hilo.** Cada envío proactivo (remarketing, ETA, handoff) necesita una decisión explícita de `release` o no. Ver §D1.6.
6. **TDD + gates** del repo (`hubara-plugin-developer`): rojo → verde → refactor; arquitectura, import-linter, TCK y `test:arch` verdes antes de cada PR.

---

## 1. Qué está hecho (desarrollo 1, este PR)

| Pieza | Dónde | Estado |
|---|---|---|
| Normalizador puro workspace → config MBA (skills, business_info, FAQs, settings, connector tools, UI skills, mapa de 19 tools, exclusiones con motivo) | `hubara_agency/src/plugins/agents_admin/mba_config.py` | ✅ 40 tests |
| Lectura de workspace (bootstrap + `skills/*/SKILL.md` con front-matter, nunca `memory/`) | `hubara_agency/src/plugins/agents_admin/service.py` | ✅ |
| `GET /api/agents/{agent_id}/mba-config` | `hubara_agency/src/plugins/agents_admin/api/routes.py` | ✅ |
| Tab "Meta Business Agent" en Agents (todos los agentes) | `frontend_dashboard/src/plugins/agents_admin/frontend/{entities/mba-config,features/agents-mba-preview}` | ✅ 31 tests |
| Reparto del etiquetado: MBA propone INTERESADO/RECHAZO, Hubara deriva CONFIRMADO_* y el silencio | skill `etiquetas-de-cierre` + notas del tool `manage_conversation_tag` | ✅ |

**Hallazgos que condicionan lo que sigue** (todos verificados contra la doc de Meta):

- La doc de la Platform **no dice que MBA lea el catálogo del WABA**. El catálogo aparece solo como ejemplo de connector. → el catálogo entra por `search_products` hasta que F3 demuestre lo contrario.
- Las **UI skills** son declaraciones por componente (`instruction` = cuándo + TODOS los datos). Las dinámicas (carrusel, picker, fotos, confirmación) no tienen documentado cómo se poblan. → a verificar en F3.
- **Connector tools son síncronas y sin reintento/idempotencia documentados.** → todo endpoint de escritura es idempotente por diseño.
- **Cualquier mensaje de nuestra app toma el hilo**; devolverlo exige `release`. Dejar de escribir no devuelve nada.
- **Ráfagas / agrupación de mensajes: no documentado.** Solo observable por ecos en `standby`.
- El guion de ventas (núcleo + 5 etapas) suma **20.544 caracteres** y el límite por skill es 20.000. Decisión pendiente en §D0.1.

---

## 2. Roadmap

Notación: **D<fase>.<n>** · Objetivo · Alcance (archivos) · Tests / DoD · Depende de.

### Fase 0 — Cierre del desarrollo 1

**D0.1 · Guion de ventas dentro del límite de 20k**
Objetivo: que ninguna skill supere `SKILL_CHAR_LIMIT` sin recorte silencioso.
Alcance: decidir entre (a) partir en `guion-nucleo` (sales_script) + `guion-etapas` (5 etapas), (b) mover más objeciones a FAQs (ya salen 7), (c) podar texto. Recomendación: (a), son dos skills con descripciones no conflictivas ("aplica siempre" / "aplica según el estado del pedido").
Tests: `test_agents_admin_mba_config.py` — `test_real_sales_workspace_normalizes_end_to_end` pasa a exigir `over_limit is False` en todas las skills (hoy documenta el `True`).
DoD: tab sin chips "excede el límite".

**D0.2 · Verificación viva en el dashboard**
Alcance: rebuild del contenedor API (`cd hubara_agency && docker compose -f docker-compose.local.yml up -d --build hubara-api`), abrir `:5174` → Agents → Sales → tab MBA. Comparar contra la captura del PR.
DoD: la tab carga con datos reales; ningún bloque en "No se pudo cargar".

### Fase 1 — Plugin `mba` (backend): la API que MBA invoca y el oído en standby

**D1.1 · Scaffold del plugin, apagado**
Alcance: `cd hubara_agency && uv run python -m src.sdk.cli create plugin mba --archetype api_only`. Manifest `frontend_dashboard/src/plugins/mba/plugin.yaml` con `depends_on: [chats, orders, catalog]` y `consumes:` declarados (P-22: nunca importar entities de otro plugin; datos vía casts server-side). Fuera de `ENABLED_PLUGINS` por defecto. `tests/conformance/test_mba_tck.py`.
Tests: TCK C0–C2 verde; `pytest -m architecture`, `lint-imports`, `build_system_graph()` con edges hacia afuera (lección L-17/L-18: un plugin sin dependencias funcionales declaradas ya mordió dos veces).
DoD: `ENABLED_PLUGINS=chats,orders,catalog,mba uv run python run_api.py` levanta; sin `mba` el sistema es idéntico a hoy.

**D1.2 · Endpoints connector `/api/mba/tools/*` (9 tools)**
Objetivo: exponer las tools que la tab lista como connector tools, con el contrato exacto que se registrará en Meta.
Alcance: `hubara_agency/src/plugins/mba/api/tools.py` con `PUBLIC_ROUTER=True` (patrón `chats/api/sales.py`). Auth por header `X-API-Key` contra `MBA_CONNECTOR_API_KEY` (SSM). Scoping por cliente: el macro `WHATSAPP_PHONE_NUMBER` llega como parámetro y se traduce a `session_key = wa_<phone>`. Rate limit básico. Cada tool delega a los use cases existentes vía cast:

| Tool | Método | Delegación | Escritura |
|---|---|---|---|
| `search_products`, `list_categories`, `get_product_by_handle` | GET | `catalog` snapshot (cast `catalog@v1`) | no |
| `verify_order_for_checkout` | POST | `chats` checkout use case | no |
| `check_order_status` | GET | `orders` by-session + `pay_status` real | no |
| `set_order_slot` | POST | `chats` order draft (vault) | sí, sobrescribe |
| `register_order` | POST | `chats` order registration (fingerprint + pre-check ya existentes) | sí, idempotente |
| `manage_conversation_tag` | POST | reconciliación §D1.3 | sí |
| `escalate_to_human` | POST | route=humano + tag HUMANO + mensaje de handoff | sí, toma el hilo |

Cada endpoint publica su **JSON schema de request** en `GET /api/mba/tools` para que la tab (D2.5) reemplace "definir en desarrollo 2" por el contrato real.
Tests: por tool, `tests/plugins/mba/test_tools_api.py` con TestClient: 401 sin API key; idempotencia (dos POST iguales → una orden); scoping (phone A no ve pedidos de B); schemas expuestos válidos.
Depende de: D1.1.

**D1.3 · Semántica de las tools de estado (MBA propone, Hubara decide)**
Alcance: `hubara_agency/src/plugins/mba/service/reconcile.py` (puro):
- `manage_conversation_tag(tag ∈ {INTERESADO, RECHAZO}, motivo)`: si existe orden registrada en la sesión → aplica CONFIRMADO_PAGO_PENDIENTE y descarta la propuesta; si hay slots de pedido sin orden → CONFIRMADO_SIN_DATOS; si no → aplica la propuesta. Siempre idempotente por (sesión, tag).
- `escalate_to_human(reason_category, summary)`: valida categoría contra la tabla de TOOLS.md; marca route+tag (invariante `human_handoff_tag_invariant`); envía el mensaje de handoff (esto toma el hilo: deseado).
- `set_order_slot`: valida closed-lists como hoy (aromas/colores/diseños del snapshot).
Tests: tabla de casos en `test_reconcile.py` (propuesta × estado → tag aplicado).
Depende de: D1.2.

**D1.4 · Webhook `standby` (el oído cuando MBA controla)**
Objetivo: no perder el historial cuando MBA responde.
Alcance: parser de `standby.messages` (inbound del cliente), `standby.message_echoes` (lo que MBA mandó, body exacto) y `standby.statuses` (entregado/leído/pricing) → persistir al vault (historial LLM + copias + costo por `pricing`), dedupe por `wamid`, **no despachar a Temporal**. ADR-pendiente §3.1 decide dónde vive el branch: (a) `chats/api/sales.py` (hoy ignora `field` desconocido) o (b) pre-router en `platform`. Recomendación: (b) con fallback a (a) si el ADR se demora.
Tests: fixtures con los tres payloads de la doc; dedupe; costo acumulado desde `pricing`; un `standby` nunca arranca workflow.
Depende de: D1.1.

**D1.5 · `messaging_handovers` → `control_owner` por sesión**
Alcance: parser de `control_taken` (`previous_owner_app_id`, `new_owner_app_id`, `metadata`) → persistir `control_owner ∈ {mba, hubara}` en `metadata.json` de la sesión. Exponer en `GET /api/mba/sessions/{session_key}/control`.
Tests: transición MBA→Hubara al enviar un mensaje; Hubara→MBA tras `release`.
Depende de: D1.4.

**D1.6 · Thread control + política de `release`**
Alcance: cliente `POST https://api.facebook.com/business/whatsapp/phone_numbers/{phone_number_id}/thread_control` (`action: release|take`, `X-API-Version: 2.0.0`) en `hubara_agency/src/plugins/mba/adapters/thread_control.py`. Política declarada en una tabla (puro, testeable):

| Envío proactivo de Hubara | ¿Release después? |
|---|---|
| Remarketing (INTERESADO) y el cliente responde | Sí si la etapa es previa a pedido; no si hay orden registrada |
| ETA / aviso de despacho | Sí (o mejor: `agent_event`, §D1.9, que no toma el hilo) |
| Handoff a humano resuelto por el operador | Sí, al cerrar el caso desde el inbox |
| Comprobante verificado | No hasta emitir `agent_event` payment_received; luego sí |

Tests: tabla de política; el cliente HTTP con `respx`.
Depende de: D1.5.

**D1.7 · Watchdog por señales del connector**
Objetivo: reemplazar el trigger de ghosting al LLM cuando `control_owner = mba`.
Alcance: el watchdog (`ServiceWindowWatchdogWorkflow` / Window Strategist) lee señales persistidas por D1.2 (búsquedas, slots, orden registrada) y etiqueta INTERESADO / CONFIRMADO_SIN_DATOS por silencio; **no envía toques dentro de ventana** cuando MBA controla (followup de MBA queda apagado por decisión, así no hay doble toque). Fuera de ventana, templates como hoy → aplica §D1.6.
Tests: workflow-level con time-skipping (patrón `test_sales_workflow_debounce.py`).
Depende de: D1.3, D1.5.

**D1.8 · CAPI por ciclo de vida de la orden**
Objetivo: la atribución no puede depender de closing tags del LLM (hoy `sales_session.py` los dispara).
Alcance: `LeadSubmitted` al registrar orden; `Purchase` (COP) al conciliar `pay_status=paid`. Es necesario con o sin MBA.
Tests: eventos emitidos desde el ciclo de vida, no desde el turno; sin duplicados.
Depende de: nada (hacer temprano).

**D1.9 · `agent_event` hacia MBA**
Alcance: `POST /{entity_id}/agent_event` cuando Hubara concilia pago (`payment_received`) o marca despacho (`order_shipped`), para que MBA se lo cuente al cliente sin que Hubara tome el hilo.
Tests: cliente HTTP con `respx`; disparo desde los mismos puntos de D1.8.
Depende de: D1.6.

### Fase 2 — Cliente de configuración: llevar la tab a Meta

**D2.1 · `MbaAdminClient`**
Alcance: `hubara_agency/src/plugins/mba/adapters/meta_admin.py` (httpx) contra `https://api.facebook.com`, header `X-API-Version: 2.0.0`, token de system user (SSM `META_MBA_TOKEN`, permisos `whatsapp_business_messaging` + `whatsapp_business_management`). Operaciones: `agent_eligibility` (GET), `agent_onboarding` (POST), `agent_config/settings` (GET/PUT), `agent_config/skills` (CRUD), `agent_config/business_info` (PUT), `agent_config/faq` (POST/list/delete), `agent_connectors` + `/tools` (CRUD), `agent-ui-skills` (CRUD), `agent_config/allowlist` (POST/GET/DELETE), `agent_test` (POST).
Tests: `respx` por operación; reintento con backoff ante 5xx intermitentes (360dialog: "cualquier endpoint puede devolver 4xx/500").
Depende de: D1.1.

**D2.2 · Sync desde la tab (diff + apply)**
Alcance: `POST /api/mba/sync/{agent_id}` que (1) construye la config con `build_mba_config`, (2) GET del estado remoto, (3) diff por título/campo, (4) aplica upserts. **Nunca toca `rollout.enabled`.** Guarda `mba_sync_state.json` en vault (qué se envió, cuándo, hash). Botón en la tab con confirmación inline de dos pasos (política: cero diálogos nativos).
Tests: diff puro (`test_sync_diff.py`); apply idempotente (correr dos veces = un cambio).
Depende de: D2.1, D0.1 (no se sincroniza con skills fuera de límite).

**D2.3 · Allowlist, audiencia y rollout desde la tab**
Alcance: sección "Rollout" en la tab: allowlist (agregar/quitar teléfonos), `ai_audience`, `rollout.enabled`. Guardas: pasar a `EVERYONE` requiere confirmación y billing presente; `rollout.enabled=true` requiere sync exitoso previo y `connector` verificado.
Tests: la UI no permite EVERYONE sin confirmación; el backend rechaza rollout sin sync.
Depende de: D2.2.

**D2.4 · `agent_test` desde la tab**
Alcance: consola simple (mensaje → respuesta, `conversation_id` para seguir) para probar skills y knowledge sin billing ni hilos reales.
Depende de: D2.1.

**D2.5 · La tab lee el contrato real de las tools**
Alcance: `mba_config.py` deja de proponer `/tools/<name>` desde TOOLS.md y consume `GET /api/mba/tools` (schemas reales) cuando el plugin `mba` está habilitado; si no, mantiene la propuesta actual marcada "propuesto".
Tests: con `mba` habilitado, `body_parameters` de `register_order` = schema real.
Depende de: D1.2.

### Fase 3 — Provisioning y sandbox (F0 de la doc)

**D3.1 · Provisioning**
Alcance: en `infra/whatsapp-provisioning/` (CLI estilo Terraform ya existente): suscribir la app a `messages`, `standby`, `messaging_handovers`; aceptar Términos de MBA en WhatsApp Manager (manual, documentar); billing en Billing Hub (manual; **no requerido con `ALLOWLISTED_ONLY`**); secretos a SSM (`META_MBA_TOKEN`, `MBA_CONNECTOR_API_KEY`, `FLOW_ID` v2 publicado); `agent_onboarding` del número.
DoD: `GET agent_eligibility` devuelve `is_eligible: true` para el número de Sales.

**D3.2 · Checklist de experimentos (no es código)**
Cada uno con hipótesis y criterio, ejecutados con allowlist de dos teléfonos nuestros:

| # | Pregunta | Cómo | Criterio |
|---|---|---|---|
| 1 | ¿MBA lee el catálogo del WABA? | "¿qué velas tienen?" sin connector activo | Cita productos reales sin inventar |
| 2 | ¿Puebla un carrusel desde el connector? | UI skill `present-products` + `search_products` | Llega carrusel con títulos/precios del snapshot |
| 3 | ¿Cómo agrupa ráfagas? | 3 mensajes en 5 s | Contar ecos: 1 o 3 respuestas; latencia inbound→echo |
| 4 | ¿Una reacción de Hubara toma el hilo? | `react_to_message` en standby | `messaging_handovers` cambia o no |
| 5 | Tono, voseo, invención de precios | 10 conversaciones guionadas + `agent_eval` | 0 voseos, 0 precios inventados |
| 6 | Handoff nativo + connector `escalate_to_human` | "quiero hablar con alguien" | Chat aparece en inbox con tag HUMANO y MBA calla |
| 7 | Orden completa | flujo hasta `register_order` | Orden en Medusa, tag CONFIRMADO_PAGO_PENDIENTE, comprobante al humano |
| 8 | Release | tras comprobante verificado + `agent_event` | MBA vuelve a responder |

DoD: tabla completada en `hubara_agency/.hubara/specs/plugins/mba/f0-results.md` con evidencia (ecos, wamids).

### Fase 4 — Champion/challenger y producción

**D4.1 · Métricas por cohorte** — pedidos pagados / conversación, costo por conversación (tokens $2/1M + mensajes), tiempo de humano, tasa de handoff. Persistir por `control_owner`.
**D4.2 · Comparativo en el dashboard** — extensión de "Calidad LLM" o sección nueva: Hubara vs MBA con las métricas de D4.1.
**D4.3 · Kill switch y runbook** — un comando que hace `rollout.enabled=false` + quita `mba` de `ENABLED_PLUGINS` + `release` de hilos activos; runbook en `.hubara/project-context.md`.
**D4.4 · Unit economics** — modelar el precio de MBA en el motor CTWA antes de `EVERYONE`.
**D4.5 · Cohorte real** — allowlist de leads de un anuncio; criterio de pasar a `EVERYONE` definido antes de empezar.

### Transversal

- **Seguridad:** API key rotable; los endpoints públicos son superficie nueva (auditar con `security-review` antes de exponer); nunca datos personales en query strings.
- **Observabilidad:** logs de cada connector call con `session_key`, latencia y resultado; conteo de ecos/statuses; alerta si `standby` llega y el plugin está apagado.
- **Specs y ADRs:** `hubara_agency/.hubara/specs/plugins/mba/spec.md` (Requirements + Scenarios) y los ADRs de §3.
- **Docs:** actualizar `CODEMAP.md` y `hubara_agency/CLAUDE.md` (plugin nuevo) al cerrar Fase 1.

---

## 3. Decisiones abiertas (ADRs a escribir)

1. **Dónde vive el branch de `standby`:** en el webhook de `chats` (mínimo, toca chats) o pre-router en `platform` (INV-1 puro, infra nueva). Afecta D1.4.
2. **Dueño del hilo en el route registry:** hoy el dueño es un workflow Temporal RUNNING; MBA no lo es. Propuesta: `control_owner` en metadata de sesión (D1.5) y el registry lo respeta sin inventar `ROUTE_META_AGENT`.
3. **Guion > 20k:** ver D0.1.
4. **Reacciones y contacto:** `react_to_message` sin destino hasta el experimento 4; `send_contact_card` como `cta_url` wa.me.

---

## 4. Riesgos que ya conocemos

- Plataforma nueva e inestable (5xx intermitentes): reintentos con backoff en D2.1; nada crítico depende de una llamada única.
- Olvidar `release` deja a MBA mudo para ese cliente para siempre: D1.6 con tests de política y alerta si un hilo lleva más de N horas en `hubara` sin actividad del humano.
- Doble toque (MBA followup + Window Strategist): followup de MBA apagado por defecto (ya en la config), y D1.7 apaga los toques en ventana cuando MBA controla.
- Pérdida de historial cuando MBA responde: D1.4 es prerequisito de cualquier allowlist con clientes reales.
- Precio: hoy $2 por 1M tokens; la línea de crédito es obligatoria para `EVERYONE`. D4.4 antes de abrir.

---

## 5. Orden de ejecución sugerido

```
D0.1 ─┐
D0.2 ─┤
D1.8 ─┤ (independiente, hacer temprano)
      ├─ D1.1 → D1.2 → D1.3 ─┐
      │        └─ D1.4 → D1.5 → D1.6 → D1.9
      │                       └─ D1.7
      ├─ D2.1 → D2.2 → D2.3
      │        └─ D2.4
      │  D1.2 → D2.5
      └─ D3.1 → D3.2 (requiere D1.2, D1.4, D2.2)
                └─ D4.1 → D4.2 → D4.5
                   D4.3, D4.4 en paralelo
```

Cada D es un PR chico con su TDD. Ningún PR prende MBA para clientes reales: eso ocurre solo en D4.5, con la allowlist y los criterios escritos antes.
