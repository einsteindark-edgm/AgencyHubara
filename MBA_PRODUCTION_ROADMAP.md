# Meta Business Agent — Roadmap a producción

> **Estado:** En ejecución · **Actualizado:** 2026-09-08 (D1.3: MBA propone la etiqueta, Hubara decide — reconciliación determinista en `chats` antes de aplicar `/tag`) · **Base estratégica:** `META_BUSINESS_AGENT_PLAN.md` (2026-07-02, por qué y cómo convivir con MBA).
> **Este documento es el QUÉ HAY QUE CONSTRUIR**, en orden, con archivos, tests y criterio de terminado por desarrollo. Cuando contradiga al plan estratégico, gana este (está hecho con la doc de Meta releída el 2026-09-02..04 y con el código vivo).
> PRs mergeados a `main`: #228 (preview), #230 (requests literales), #233 (plugin `mba` + guard de routers públicos + forge), #234 (roadmap). #235 (D1.2a: tools de lectura + hardening del router público). #242 (D1.2b: tools de escritura por cast + `session-actions@v1` en chats + castkit modo service). D1.3 (reconciliación de etiquetas) en PR abierto; siguiente D1.4. Cada D siguiente es un PR chico desde `main`.

---

## 0. Principios que gobiernan todo el roadmap

1. **MBA reemplaza UNA capa: el turn loop conversacional** (DeepSeek + harness). Temporal sigue siendo el cerebro de comercio y la fuente de verdad (orden idempotente, pago conciliado, ETA, remarketing fuera de ventana, sentinel, CAPI, inbox humano).
2. **MBA propone, Hubara decide.** Todo lo que MBA manda por connector (tags, escalación, slots, orden) es una propuesta que Hubara reconcilia con reglas deterministas antes de aplicar. Ver §D1.3.
3. **Nada se envía a Meta sin verse antes en la tab "Meta Business Agent"** del dashboard (desarrollo 1). La tab es la fuente única de "qué le mandamos".
4. **Dos toggles, siempre reversible:** Meta-side (`rollout.enabled` + `ai_audience` + allowlist) y Hubara-side (`ENABLED_PLUGINS` incluye `mba`). Apagar cualquiera de los dos devuelve el sistema a hoy.
5. **Todo mensaje que Hubara envíe toma el hilo.** Cada envío proactivo (remarketing, ETA, handoff) necesita una decisión explícita de `release` o no. Ver §D1.6.
6. **TDD + gates** del repo (`hubara-plugin-developer`): rojo → verde → refactor; arquitectura, import-linter, TCK y `test:arch` verdes antes de cada PR.

---

## 1. Qué está hecho (PRs #228, #230, #233 — en `main` y en prod)

| Pieza | Dónde | Estado |
|---|---|---|
| Sección "Meta Business Agent" del dashboard (plugin `mba`, fuera de Agents): lista de agentes MBA, canvas con tabs (Configuración construida; Insights / Agent test / Agent eval deshabilitadas hasta que existan) e inspector con el estado en Meta | `frontend_dashboard/src/plugins/mba/frontend/{entities/mba-agent,entities/mba-config,features/mba-agents-list,features/mba-agent-canvas,features/mba-config-preview,features/mba-inspector}` | ✅ 13 tests |
| **Plugin `mba` con la fuente de verdad autorada**: `hubara_agency/src/plugins/mba/agents/sales/agent.yaml` (settings, business_info, FAQs, connector con 9 tools y sus parámetros tipados, 9 UI skills, allowlist, lo que no viaja) + `skills/*.md` (9 skills escritas PARA MBA: sin referencias a archivos, memoria inyectada, `load_skill`, tools de presentación ni etiquetas CONFIRMADO_*; solo nombran las 9 connector tools y sus parámetros, con guard en `test_real_sales_agent_is_clean_and_only_names_tools_mba_has`). El normalizador heurístico de `agents_admin` se eliminó: ya no hay dos configuraciones que diverjan | `hubara_agency/src/plugins/mba/{agents,domain/config.py,service.py}` | ✅ 9 tests backend |
| `GET /api/mba/agents`, `GET /api/mba/agents/{id}/config` (protegidos) y `/api/mba/tools/{tool}` (`PUBLIC_ROUTER`, API key `HUBARA_MBA_API_KEY` en `X-API-Key`, fail-closed 503 sin la variable, 401 sin key, 404 tool desconocida, 405 método equivocado, **501 hasta D1.2**) | `hubara_agency/src/plugins/mba/api/{__init__,connector}.py` | ✅ |
| Reparto del etiquetado: MBA propone INTERESADO/RECHAZO, Hubara deriva CONFIRMADO_* y el silencio | skill `etiquetas-de-cierre` + descripción del tool `manage_conversation_tag` en `agent.yaml` | ✅ |
| **Requests exactos a Meta** (`requests[]` en el DTO): las 41 llamadas HTTP de sales numeradas en orden de envío (1 business_info + 10 FAQs + 9 skills + 1 connector + 9 tools + 9 UI skills + 1 settings + 1 allowlist), cada una con método, URL completa, headers (`X-API-Version: 2.0.0`) y body JSON literal según el schema oficial; la sección muestra la secuencia arriba y el request desplegable dentro de cada ítem | `domain/config.py::_build_requests` + `MbaConfigPreview.tsx::RequestView` | ✅ |
| Guard de arquitectura: allowlist explícita de módulos `PUBLIC_ROUTER=True` (`chats.api.sales`, `mba.api.connector`) + ADR | `tests/architecture/test_public_routers.py`, `docs/adr/2026-09-04-public-router-allowlist.md` | ✅ (#233, label `architecture-change`) |
| **forge:** `mba` en `ENABLED_PLUGINS_DEFAULT`; overlay `mba_sales` propio (`agent.yaml` + skills, `required` propio); el connector se siembra como `<slug>-commerce`; scope crítico del scanner; F7c en `NEXT_STEPS.md` | `forge/{forge.py,manifest.yaml,templates/NEXT_STEPS.md.tpl}` | ✅ 36 tests |
| **Prod (2026-09-07):** `mba` en `ENABLED_PLUGINS` de `/opt/hubara/box.env` (por SSM, backup `box.env.bak-pre-mba`); `HUBARA_MBA_API_KEY` en SSM `/hubara/hubara/`; deploy re-rendido. `/api/mba/*` responde 401 (montado, auth), `/api/mba/tools/*` 401 (key presente), la sección aparece en el dashboard | SSM + `Backend deploy` 34134881752 | ✅ |
| **D1.2a · Tools de lectura del connector con lógica real** (canal 1, ports del SDK): `search_products`, `list_categories`, `get_product_by_handle` (CatalogPort del snapshot; envelopes cerrados con COP primero, aromas/colores/diseños/variantes), `check_order_status` (vault de la sesión + OrderQueryPort en vivo con timeout, degrada y lo dice), `verify_order_for_checkout` (`get_checkout_verification_port()` nuevo en el SDK). Hardening del router público: rate limit por (IP, tool) antes de la auth (429), tope de body 64 KiB (413), JSON inválido (400), validación de query/body contra el MISMO `request_definition` que se registra en Meta (422 con la lista de problemas), `customer_phone` E.164 → `session_key = wa_<dígitos>` (scoping por cliente) | `src/plugins/mba/{domain/tool_calls.py,domain/guard.py,tools/,api/connector.py}` + `src/sdk/connectorkit` | ✅ 34 tests |
| **D1.2b · Tools de escritura del connector por cast** (canal 3): `chats` publica `session-actions@v1` (`src/plugins/chats/api/session_actions.py`: `/draft` → `SetOrderSlotTool`; `/order` → precio server-side desde el snapshot (`use_cases/order_pricing.py`) + tarifa mínima por ciudad (`config/shipping.py::shipping_rate_for_city`, #241) + `RegisterOrderTool` + cierre `CONFIRMADO_PAGO_PENDIENTE` + escalación `PAYMENT_VERIFICATION_PENDING` bajo el lock del store + `EpisodeClosedEvent` + flush de las instrucciones de pago (`flush_pending_ui_intents` ahora es función plana; la activity delega), idempotente por contenido; `/tag` → `ManageConversationTagTool` + evento; `/escalate` → route=humano + tag HUMANO, sin pisar a un humano que ya tiene el hilo). `castkit.forward` gana `auth="service"` (`HUBARA_SERVICE_TOKEN`; Meta no trae bearer). `mba` declara `consumes` y el cast `api/chats_cast.py`; `tools/session.py` valida las listas cerradas (tags INTERESADO/RECHAZO, 15 categorías = las del `request_definition`), mapea `metodo_pago`, aplica #241 a la respuesta y convierte fallos del cast en errores explícitos (`chats_unavailable`/`chats_timeout`/`rejected`), nunca 5xx hacia Meta. | ✅ 2026-09-07 — 15 tests del contrato en chats (vault real) + 9 de precio/tarifa + 12 de las tools mba + cast + integración con `require_auth` ON y service token (`tests/test_casts_auth_integration.py`). |
| **D1.3 · MBA propone, Hubara decide** (reconciliación de etiquetas): `POST /session-actions/{s}/tag` acepta SOLO `INTERESADO`/`RECHAZO` y los pasa por `use_cases/tag_reconcile.reconcile_tag_proposal` (pura) antes de etiquetar: orden registrada en el último episodio → propuesta descartada (el tag visible no cambia); datos de la etapa de envío sin orden → `CONFIRMADO_SIN_DATOS` + escalación `ORDER_PENDING_SHIPPING_DETAILS` (invariante handoff, mismo par que la red de seguridad del workflow Sales); si no → la propuesta tal cual. Idempotente por (sesión, tag). La respuesta dice `tag` aplicado, `proposed_tag`, `applied`, `reconciled`, `reason`, `escalated`; `mba/tools/session.py` lo traduce a un mensaje para el agente (no insistir, no responder más si un colega tomó el hilo) | `hubara_agency/src/plugins/chats/agent/sales/use_cases/tag_reconcile.py`, `chats/api/session_actions.py`, `mba/tools/session.py`, skill `etiquetas-de-cierre`, `agent.yaml` (notes del tool); tests `tests/plugins/chats/test_tag_reconcile.py` (tabla propuesta × estado), `test_session_actions_api.py`, `tests/plugins/mba/test_tools_session.py` | ✅ (PR D1.3) |

**Hallazgos que condicionan lo que sigue** (todos verificados contra la doc de Meta):

- La doc de la Platform **no dice que MBA lea el catálogo del WABA**. El catálogo aparece solo como ejemplo de connector. → el catálogo entra por `search_products` hasta que F3 demuestre lo contrario.
- Las **UI skills** son declaraciones por componente (`instruction` = cuándo + TODOS los datos). Las dinámicas (carrusel, picker, fotos, confirmación) no tienen documentado cómo se poblan. → a verificar en F3.
- **Connector tools son síncronas y sin reintento/idempotencia documentados.** → todo endpoint de escritura es idempotente por diseño.
- **Cualquier mensaje de nuestra app toma el hilo**; devolverlo exige `release`. Dejar de escribir no devuelve nada.
- **Ráfagas / agrupación de mensajes: no documentado.** Solo observable por ecos en `standby`.
- El guion de ventas (núcleo + 5 etapas) sumaba **20.544 caracteres** y el límite por skill es 20.000. Resuelto: los skills autorados para MBA están partidos (`guion-de-ventas` 10.2k, `guion-de-cierre` 6.1k) y el loader reporta `problems[]` si alguno se pasa.
- **El workspace de Hubara NO es la fuente de MBA.** Derivarlo (Fase 0) arrastraba referencias que MBA no tiene (archivos IDENTITY/SOUL, `load_skill`, contexto inyectado, tools de presentación, tags CONFIRMADO_*). La fuente de verdad es el agente autorado en `hubara_agency/src/plugins/mba/agents/sales/`, con guard que falla si un skill nombra algo fuera de las 9 connector tools y sus parámetros.

---

## 2. Roadmap

Notación: **D<fase>.<n>** · Objetivo · Alcance (archivos) · Tests / DoD · Depende de.

### Fase 0 — Cierre del desarrollo 1 · ✅ hecha en #228/#230 (SUPERSEDIDA por el agente autorado de #233; se conserva como historia)

**D0.1 · Guion de ventas dentro del límite de 20k** — ✅ partido en `guion-sales-script` (núcleo, "aplica siempre") + `guion-etapas` (5 etapas en orden canónico; la descripción le dice a MBA cómo elegir etapa porque no recibe el "estado del pedido" que Hubara inyecta por turno). `test_real_sales_workspace_normalizes_end_to_end` exige `over_limit is False` en todas las skills.

**D0.2 · Verificación viva** — ✅ hecha contra API y Vite locales en puertos propios (el daemon de Docker estaba apagado). Al mergear: rebuild del contenedor API (`cd hubara_agency && docker compose -f docker-compose.local.yml up -d --build hubara-api`) y ver `:5174` → Agents → Sales → tab MBA.

**D0.4 · Request literal por ítem** — ✅ la tab ya no describe: muestra lo que se envía. Bodies verificados contra la referencia oficial: skills = un `POST` por skill `{title, description, skill}`; business_info = un `PUT` solo con los campos con fuente; FAQ = un `POST` por pregunta `{question, answer}`; settings = un `PUT` con `never_say_phrases` como lista de strings (reemplaza la lista completa), `rollout.enabled=false`, `followup.enabled=false`; connector = `auth_config.api_key.headers[{field_name: X-API-Key, value: <HUBARA_MBA_API_KEY>}]`; tools = `request_definition` con `customer_phone` ligado a la macro `WHATSAPP_PHONE_NUMBER` (query en GET, body en POST) y el resto de parámetros sin binding (los extrae el agente); UI skills = `{title, component_type, status, instruction}`; allowlist = `{consumer_phone_number}` (único valor que no sale del workspace, placeholder `+57XXXXXXXXXX`). Los tipos de los parámetros de las tools quedan como `string` hasta D1.2 (schemas reales).

**D0.3 · Cobertura total del workspace** — ✅ regla "lo que no está en la tab no existe": USER.md → `contexto-del-negocio`; secciones de TOOLS.md sin tabla (principios, memoria del pedido, anti-alucinación, UI rica, estilo) + párrafos de instrucción del skill de catálogo → `uso-de-tools`. Lo único que sigue fuera, con motivo visible: memoria dinámica, contexto por turno, trigger de ghosting, `react_to_message`.

### Fase 1 — Plugin `mba` (backend): la API que MBA invoca y el oído en standby

**D1.1 · Scaffold del plugin** — ✅ hecho con `create plugin mba --archetype full_stack` (el arquetipo cambió a `full_stack` porque el plugin también aporta la sección del dashboard). TCK C2 ✓, `pytest -m architecture` + conformance ✓, `lint-imports` ✓. Bug del CLI corregido de paso: `scaffold.py` calculaba la raíz del repo con `parents[5]` y escribía FUERA del checkout (en worktrees, en `.claude/worktrees/`); ahora `default_repo_root()` = `parents[4]` con test. Habilitado en `docker-compose.local.yml` (render-compose), en `infra/terraform/compute/{tenants.auto.tfvars,variables.tf}` y **en la caja viva** (`ENABLED_PLUGINS` sale de `/opt/hubara/box.env`, que escribe cloud-init una sola vez: se parcheó por SSM el 2026-09-07; cualquier caja NUEVA lo trae desde el tfvars). `depends_on: [chats, orders, catalog]` declarado (L-18). Pendiente de D1.2: `consumes:` (hoy el plugin no consume nada porque las tools aún no delegan). Limpieza pendiente: borrar las ramas `feat/mba-preview-exact-requests` y `feat/mba-plugin` (los PRs #231/#232 se mergearon ahí, no a `main`; #233 trajo todo).

**D1.2 · Endpoints connector `/api/mba/tools/*` (9 tools)**
Objetivo: exponer las tools que la sección lista como connector tools, con el contrato exacto que se registrará en Meta.
Estado: **D1.2 completa** (D1.2a lectura + hardening; D1.2b escritura por cast, ver §1). Las 9 tools responden con lógica real: 5 por canal 1 (ports del SDK) y 4 por canal 3 (cast `mba→chats` al contrato `session-actions@v1`, service token). El pipeline del router público es rate limit → API key → tool/método → tope de body → JSON → validación contra el `request_definition` autorado → lógica (canal 1 o cast).
**D1.2b ✅ · Tools de escritura por cast al contrato `session-actions@v1` de `chats`** (hecho 2026-09-07):
- `chats` publica `POST /api/chats/session-actions/{session_key}/{draft,order,tag,escalate}` (`src.plugins.chats.api.session_actions`, `require_auth` vía el loader; `session_key` solo `wa_<dígitos>`). Todo resuelve el episodio ACTIVO (§D1.10).
- `/order` recibe ítems SIN precio (Meta no los manda): precio por variante desde el snapshot (`variant_label` → variante por título/options, COP primero; sin match → primera variante con `variant_resolved=false`), tarifa MÍNIMA de envío por ciudad (Bogotá 7.900 / nacional 16.940, la misma del resumen #241), `RegisterOrderTool` (SEC-07 recompute, huella + pre-check Medusa, `portavelas` #238, intent de instrucciones de pago #236) y lo que en el workflow hacen las redes de seguridad: cierre `CONFIRMADO_PAGO_PENDIENTE` + escalación `PAYMENT_VERIFICATION_PENDING` (idempotentes, bajo `store.update`), `EpisodeClosedEvent` por el dispatcher (cancela watchdog) y flush de las instrucciones de pago por Cloud API. Idempotente por contenido: el mismo pedido dos veces devuelve la misma orden, sin re-registrar ni reenviar las instrucciones.
- `castkit.forward(..., auth="service")`: manda `Bearer HUBARA_SERVICE_TOKEN` (paso 1 de `require_auth`) e ignora la identidad entrante; sin token real no inventa header. 3 patas: `tests/test_castkit.py` + `docs/_sdk/10-castkit.md`.
- `mba`: `consumes: [{provider: chats, contract: session-actions@v1, into: session-ref, cast: api/chats_cast}]`; `tools/session.py` valida listas cerradas (tags solo INTERESADO/RECHAZO; 15 `reason_category` = los del `request_definition`, test lo verifica), mapea `metodo_pago` (`contra_entrega`/`anticipado`/`link_de_pago` y variantes) y aplica #241 a la respuesta de `register_order`: `subtotal_cop` siempre, `shipping_cop`/`total_cop` solo con anticipado o link (`shipping_is_minimum_rate`), con contra entrega `shipping_note`. `portavelas_included` y `already_registered` viajan para el guion de cierre.
- **Pendiente y medible en F0:** (a) el flush de las instrucciones de pago envía por Cloud API mientras MBA tiene el hilo — según §0.5 "todo mensaje que Hubara envíe toma el hilo"; verificar en F0 #4/#9 si el envío se entrega y qué pasa con el control (D1.6 decide la política de `release`). Si Meta lo rechaza, la alternativa es devolver el texto en la respuesta para que MBA lo relaye (riesgo: datos bancarios por el LLM). (b) CAPI `Purchase`/`LeadSubmitted` no se envía desde `/order`/`/tag` (el workflow lo hace por closing tag): va en D1.8. (c) `/escalate` no termina workflows Sales/Remarketing en vuelo (con MBA al frente no los hay; el watchdog se auto-skipea con route=humano). (d) La reconciliación de `/tag` (INTERESADO/RECHAZO × estado real) quedó hecha en D1.3. (e) Timeout de Meta para connector tools: no documentado; el cast espera hasta 90 s (`CHATS_CAST_TIMEOUT_S`) porque `/order` habla con Medusa y envía por WhatsApp — medir en F0 el corte real de Meta (si corta antes, MBA ve fallo con la escritura aplicada: `/draft`/`/escalate`/`/order` son idempotentes, `/tag` re-aplica). Revisión independiente aplicada: con un humano en el hilo `/tag` responde 409 y `/order` cierra el episodio sin pisar el tag HUMANO (invariante handoff); la idempotencia de `/order` se acota al episodio (mismo pedido en episodio nuevo = venta nueva) y `/order` se serializa por sesión (dos requests iguales en paralelo → una orden, un envío de instrucciones).
Tests D1.2b (verdes): `tests/plugins/chats/test_session_actions_api.py` (por endpoint, vault temporal, idempotencia, scoping, literal `humano` = constante de platform), `tests/plugins/chats/test_order_pricing.py`, `tests/plugins/chats/sales/test_flush_outside_activity.py`, `tests/plugins/mba/test_tools_session.py`, `test_chats_cast.py`, `test_api.py` (cast doble + errores del cast en 200), `tests/test_casts_auth_integration.py::test_mba_write_tool_reaches_chats_with_the_service_token` (auth ON, loopback real, vault escrito; sin token → `rejected 401`, nunca 500).

Tabla de delegación (vive en código):

| Tool | Método | Delegación | Escritura |
|---|---|---|---|
| `search_products`, `list_categories`, `get_product_by_handle` | GET | ✅ `get_catalog_client()` (snapshot) | no |
| `check_order_status` | GET | ✅ vault de la sesión + `get_order_query_port()` (`pay_status` real, timeout 8 s) | no |
| `verify_order_for_checkout` | POST | ✅ `get_checkout_verification_port()` (Medusa live) | no |
| `set_order_slot` | POST | ✅ cast → `chats` `session-actions@v1` `/draft` | sí, sobrescribe |
| `register_order` | POST | ✅ cast → `chats` `/order` (precio server-side + huella + pre-check + cierre + escalación + instrucciones de pago) | sí, idempotente |
| `manage_conversation_tag` | POST | ✅ cast → `chats` `/tag` (propuesta reconciliada con el estado real, §D1.3: orden registrada gana; datos de envío sin orden → CONFIRMADO_SIN_DATOS + escalación) | sí, idempotente por (sesión, tag) |
| `escalate_to_human` | POST | ✅ cast → `chats` `/escalate` (route=humano + tag HUMANO, no pisa a un humano) | sí, toma el hilo |

El schema de request de cada tool es el `request_definition` autorado en `agent.yaml` (lo que Meta ve); el endpoint valida contra ESE schema (`domain/tool_calls.py::contracts_from_config`), así no hay dos contratos.
Depende de: D1.1.

**D1.3 ✅ · Semántica de las tools de estado (MBA propone, Hubara decide)** (hecho 2026-09-08)
Dónde vive (desviación respecto del plan original `mba/service/reconcile.py`): la reconciliación es pura pero necesita el estado real de la sesión (episodios, draft, orden), que es de `chats` y que `mba` no puede leer (P-3/P-28). Por eso vive en `chats` (`agent/sales/use_cases/tag_reconcile.py`) y la aplica el provider del contrato (`/session-actions/{s}/tag`), no el consumidor. `mba` sigue sin conocer el vault: solo traduce la respuesta al agente.
- `manage_conversation_tag(tag ∈ {INTERESADO, RECHAZO}, motivo)`: el body del contrato ya no acepta otros tags (422). Reglas, en orden: (1) el último episodio tiene `order_id` (o `registered_order.success` en sesiones legacy sin episodios) → la propuesta se DESCARTA y el tag visible no cambia (`CONFIRMADO_PAGO_PENDIENTE` / `COMPRA_EXITOSA` / lo que haya): un INTERESADO acá dispararía remarketing a un cliente que ya compró y un RECHAZO borraría una venta; un episodio nuevo sin orden vuelve a aceptar propuestas (venta nueva). (2) El episodio ACTIVO tiene en el draft algún slot de la etapa de envío (`ciudad`, `barrio`, `direccion`, `telefono`, `nombre_recibe`, `cedula`, `metodo_pago`) sin orden → `CONFIRMADO_SIN_DATOS` (cierra el episodio + `EpisodeClosedEvent`) + escalación `ORDER_PENDING_SHIPPING_DETAILS` (route=humano + tag HUMANO, invariante handoff): exactamente lo que garantiza `ensure_closing_escalation` en el workflow Sales; vale para las dos propuestas (en duda, un colega mira el caso antes de dar la venta por perdida). Solo producto/color/cantidad NO es confirmación: INTERESADO se aplica tal cual. Un episodio ya CERRADO (RECHAZO, CONFIRMADO_SIN_DATOS resuelto por el colega y devuelto al bot con RETOMA_VENTA, TIMEOUT) tiene su `closing_tag` como verdad: un draft viejo no reescala ni reetiqueta como "faltan datos" (hallazgo H1 de la revisión). (3) Si no, la propuesta se aplica (sobre un episodio cerrado, como la tool del agente Sales: cambia el tag visible sin reabrir ni re-cerrar). Idempotente por (sesión, tag): si el tag visible ya es el que se aplicaría, no se reescribe ni se re-emite evento. **Atomicidad (H2/H3 de la revisión):** chequeo de ruta humana (409), reconciliación, tag, cierre formal y escalación ocurren sobre el mismo dict en UN `store.update` (flock) y bajo el lock de sesión del proceso que ya serializa `/order`: un `manage_conversation_tag` emitido en el mismo turno que `register_order` nunca saca la sesión de la bandeja humana ni deja `CONFIRMADO_SIN_DATOS` sin escalación (`test_concurrent_tag_and_order_never_break_the_handoff_invariant`). `/tag` ya no pasa por `ManageConversationTagTool` (su escritura sin lock); el mutador `_apply_tag` hace lo mismo que la tool + la red de seguridad. El `closing_motivo` conserva la propuesta y el motivo de MBA (`propuesta INTERESADO reconciliada → CONFIRMADO_SIN_DATOS: …`) para que el colega lo lea.
- `escalate_to_human(reason_category, summary)`: ya validaba la categoría contra la lista del `request_definition` (D1.2b) y marca route+tag juntos. **No envía mensaje de handoff**: el skill hace que MBA mande la línea previa al cliente, y un texto automático de Hubara la duplicaría. Tomar el hilo se resuelve en D1.6 con `thread_control(action=take)` (limpio, sin mensaje al cliente). Hasta D1.6, entre la escalación y la primera respuesta del colega desde la bandeja, MBA sigue siendo dueño del hilo: si el cliente vuelve a escribir, Meta puede invocar a MBA aunque el skill le diga que no responda — medir en F0 #4.
- `set_order_slot`: valida closed-lists como hoy (aromas/colores/diseños del snapshot, D1.2b).
- Respuesta de `/tag` y del tool: `tag` (aplicado), `proposed_tag`, `applied`, `reconciled`, `reason ∈ {proposal_accepted, order_registered, shipping_data_without_order, already_applied}`, `episode_closed`, `escalated`; `mba/tools/session.py` agrega el mensaje al agente (no insistir; si un colega tomó el hilo, no responder más). Skill `etiquetas-de-cierre` y `notes` del tool en `agent.yaml` lo dicen en prosa.
Tests: `tests/plugins/chats/test_tag_reconcile.py` (tabla de 17 casos propuesta × estado → tag aplicado, incluidos episodios cerrados con draft viejo; pureza; la categoría de escalación es la del mapa `_CLOSING_TAGS_REQUIRING_ESCALATION` del workflow Sales), `test_session_actions_api.py` (descartada con orden; `CONFIRMADO_SIN_DATOS` + escalación con el vault real; solo producto → INTERESADO; idempotencia; solo dos propuestas), `tests/plugins/mba/test_tools_session.py` (mensajes al agente por cada `reason`).
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

**D1.10 · Episodios con MBA al frente**
Objetivo: conservar el modelo de episodios (`episode_lifecycle.py`: un tramo por intención, cierra por tag o por 14 días, el siguiente inbound abre uno nuevo con tag/draft/atribución frescos) cuando quien conversa es MBA.
Alcance, en dos mitades como hoy:
- *Determinista (Hubara, intacta):* `ensure_active_episode` se dispara con el primer inbound de `standby` tras un episodio cerrado (D1.4 aporta `inbound_message_id` y referral); `close_episode` con el tag reconciliado (D1.3 ✅) o por timeout; `attach_order_to_active_episode` desde `register_order` (D1.2). **Todo connector tool resuelve el episodio activo**: tras un cierre, `set_order_slot` escribe en un draft vacío y `check_order_status` no devuelve el pedido viejo. MBA no puede actuar sobre datos del episodio anterior aunque los recuerde.
- *Conversacional (MBA, caja negra):* hoy tampoco borramos el historial del LLM: es una ventana de memoria + una nota de frontera en `plugin_context` (`_build_episode_boundary_note`). Con MBA no hay API para resetear el contexto del hilo. Palancas, de más a menos fuerte: (1) `agent_event` `episode_closed` al cerrar (data: resultado, order_id) con instrucción "si el cliente vuelve, es una conversación nueva: saluda, no retomes el pedido anterior" — **verificar en F0 que un agent_event puede ser silencioso** (la doc lo describe como disparador de acciones proactivas); (2) la regla en `persona-y-tono` / `contexto-del-negocio` (ya viaja: "se saluda una sola vez por conversación", "datos de envío nunca desde pedidos anteriores"); (3) si MBA arrastra contexto de forma inaceptable, `take` + mensaje de cierre desde Hubara y `release` al primer inbound nuevo (costoso; último recurso).
Tests: episodio nuevo desde standby; connector tools scoped al activo (dos episodios, dos drafts); agent_event emitido en `close_episode` con el mismo `respx` de D1.9.
Depende de: D1.2, D1.3, D1.4, D1.9.

### Fase 2 — Cliente de configuración: llevar la tab a Meta

**D2.1 · `MbaAdminClient`**
Alcance: `hubara_agency/src/plugins/mba/adapters/meta_admin.py` (httpx) contra `https://api.facebook.com`, header `X-API-Version: 2.0.0`, token de system user (SSM `META_MBA_TOKEN`, permisos `whatsapp_business_messaging` + `whatsapp_business_management`). Operaciones: `agent_eligibility` (GET), `agent_onboarding` (POST), `agent_config/settings` (GET/PUT), `agent_config/skills` (CRUD), `agent_config/business_info` (PUT), `agent_config/faq` (POST/list/delete), `agent_connectors` + `/tools` (CRUD), `agent-ui-skills` (CRUD), `agent_config/allowlist` (POST/GET/DELETE), `agent_test` (POST).
Tests: `respx` por operación; reintento con backoff ante 5xx intermitentes (360dialog: "cualquier endpoint puede devolver 4xx/500").
Depende de: D1.1.

**D2.2 · Sync desde la tab (diff + apply)**
Alcance: `POST /api/mba/sync/{agent_id}` que (1) construye la config con `load_agent` (los `requests[]` ya listos), (2) GET del estado remoto, (3) diff por título/campo, (4) aplica upserts. **Nunca toca `rollout.enabled`.** Guarda `mba_sync_state.json` en vault (qué se envió, cuándo, hash). Botón en la tab con confirmación inline de dos pasos (política: cero diálogos nativos).
Tests: diff puro (`test_sync_diff.py`); apply idempotente (correr dos veces = un cambio).
Depende de: D2.1, D0.1 (no se sincroniza con skills fuera de límite).

**D2.3 · Allowlist, audiencia y rollout desde la tab**
Alcance: sección "Rollout" en la tab: allowlist (agregar/quitar teléfonos), `ai_audience`, `rollout.enabled`. Guardas: pasar a `EVERYONE` requiere confirmación y billing presente; `rollout.enabled=true` requiere sync exitoso previo y `connector` verificado.
Tests: la UI no permite EVERYONE sin confirmación; el backend rechaza rollout sin sync.
Depende de: D2.2.

**D2.4 · `agent_test` desde la tab**
Alcance: consola simple (mensaje → respuesta, `conversation_id` para seguir) para probar skills y knowledge sin billing ni hilos reales.
Depende de: D2.1.

**D2.5 · La tab lee el contrato real de las tools** — ✅ resuelto por diseño en #233: los schemas viven tipados en `agent.yaml` y la sección los muestra tal cual viajan a Meta. Queda para D1.2 que el endpoint valide contra ese mismo schema.

### Fase 3 — Provisioning y sandbox (F0 de la doc)

**D3.1 · Provisioning**
Alcance: en `infra/whatsapp-provisioning/` (CLI estilo Terraform ya existente): suscribir la app a `messages`, `standby`, `messaging_handovers`; aceptar Términos de MBA en WhatsApp Manager (manual, documentar); billing en Billing Hub (manual; **no requerido con `ALLOWLISTED_ONLY`**); secretos a SSM (`META_MBA_TOKEN`; `HUBARA_MBA_API_KEY` ya existe desde 2026-09-07, leerla con `aws ssm get-parameter --with-decryption` y usar el MISMO valor en `auth_config.api_key` del connector); `agent_onboarding` del número.
**Placeholders del agente a resolver antes del primer sync** (viven en `agents/sales/agent.yaml`): `entity_id` (phone_number_id tras onboardear), `<FLOW_ID>` (Flow v2 publicado en el WABA del número), `<TELEFONO_ASESOR>`, `<WEB_HUBARA>`, `<INSTAGRAM_HUBARA>`, y los teléfonos reales de `allowlist` (hoy `+57XXXXXXXXXX`). La sección del dashboard muestra los 41 requests con esos valores ya sustituidos.
DoD: `GET agent_eligibility` devuelve `is_eligible: true` para el número de Sales y la sección no lista `problems` ni placeholders.

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
| 9 | Contexto entre episodios | cerrar episodio (pedido registrado) y escribir 2 días después | ¿MBA saluda como conversación nueva o retoma el pedido viejo? ¿`agent_event` de cierre puede ser silencioso? ¿crece el costo por turno con el historial del hilo? |

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
- **Docs:** `CODEMAP.md` y `hubara_agency/CLAUDE.md` ya listan el plugin (#233); actualizar al cerrar Fase 1 con los endpoints reales.

---

## 3. Decisiones abiertas (ADRs a escribir)

1. **Dónde vive el branch de `standby`:** en el webhook de `chats` (mínimo, toca chats) o pre-router en `platform` (INV-1 puro, infra nueva). Afecta D1.4.
2. **Dueño del hilo en el route registry:** hoy el dueño es un workflow Temporal RUNNING; MBA no lo es. Propuesta: `control_owner` en metadata de sesión (D1.5) y el registry lo respeta sin inventar `ROUTE_META_AGENT`.
3. **Reacciones y contacto:** `react_to_message` sin destino hasta el experimento 4; `send_contact_card` como `cta_url` wa.me.

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
D0 ✅ ─┐
D1.8 ─┤ (independiente, hacer temprano)
      ├─ D1.1 ✅ → D1.2a ✅ → D1.2b ✅ → D1.3 ✅ ─┐
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
