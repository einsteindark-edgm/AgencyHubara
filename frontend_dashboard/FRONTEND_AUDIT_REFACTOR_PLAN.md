# Auditoría Frontend + Plan de Refactor — `frontend_dashboard` (Tauri 2 + React 19)

> **Fecha:** 2026-06-10 · **Alcance:** todo `frontend_dashboard/` (src, src-tauri, config, e2e) + los puntos del backend que explican el tráfico de red (`chats/api/dashboard.py`, CORS en `main.py`).
> **Método:** lectura dirigida de los archivos núcleo + 3 barridos exhaustivos paralelos (patrones React, capa de datos TanStack, shell/config/tooling) + verificación contra el código instalado de las librerías (`@opentelemetry/*` en node_modules) para no reportar bugs fantasma.
> **Estado:** documento de trabajo. Cada fase del §4 es una orden de trabajo auto-contenida, mapeada a HUs en §5.
>
> **Ejecución 2026-06-10 (misma sesión, branch `claude/elated-tesla-04e90e`):**
> - ✅ **F0 completa** (8/8 tareas) — verificada con `tsc -b` + 134 unit + 20 arch + build.
> - ✅ **F2 parcial**: F2.1 ErrorBoundary (por sección + global), F2.2 fallback de Suspense (texto neutro; skeleton 3-col queda para F4), F2.3 StatusBar sin métricas fake (fase A — los indicadores reales llegan con F1), F2.4 resuelta con la opción recomendada (TitleBar fake ELIMINADA; decoraciones nativas).
> - ✅ **F3 parcial**: F3.1 CSP+devCsp, F3.2 identifier `com.hubara.dashboard` + metadata Cargo, F3.3 con **desviación deliberada**: el puerto Vite se MANTIENE en 5173 porque el stack Docker mapea host 5174→container 5173 (cambiarlo rompía el compose vivo); `devUrl: http://127.0.0.1:5173` (IPv4 explícito) basta para esquivar el squat IPv6 de Archon. F3.4 parcial (min-size+center sí; plugins window-state/single-instance pendientes — requieren deps Rust y build de verificación). **Pendiente humano: correr `npx tauri dev` una vez para validar CSP/ventana** (esta sesión no tiene toolchain Rust).
> - ⏳ Pendientes: **F1** (event bus — 2 HUs, backend primero), F2 fase B (métricas reales), F4, F5, F6, F3.5/F3.6.
>
> **Ejecución 2026-06-11 (rama `refactor/frontend-integral-2026-06`, rebased sobre main):**
> - ✅ **F1 completa** — backend: `src/platform/events` (bus in-process) + `/api/dashboard/events` (SSE multiplexado, sampler compartido con diff por mtime, heartbeat 25s; reemplaza `/stream`) + publish en mutaciones de orders/catalog. Frontend: `EventStreamProvider` (una conexión por app) + `useDashboardEvents`/`useInvalidateOnReconnect`; los 4 plugins migrados a push con fallback 5min; StatusBar con estado de conexión real. Único poll corto sobreviviente: function-form 1.2s de catalog para el run activo (allowlisteado en el gate).
> - ✅ **F5 completa** — OrdersInspector 1092→163 líneas + 9 módulos; `orders-vault-reconciliation` extraída de la Page; ConfirmPaymentAction con useReducer + unión discriminada (patrón de referencia); ReadyForShip con error derivado de la mutation.
> - ✅ **F4 ejecutada en su parte segura** — tokens de estados en @theme (valores EXACTOS de los literales → cero cambio visual) + migración de los hex/rgba exactos a `var(--color-*)`. **F4.3 (split de index.css per-plugin) DIFERIDA a propósito**: sin verificación visual desde el worktree, partir 2250 líneas de CSS a ciegas es la apuesta incorrecta — va con F4.4 (snapshots Playwright) en su HU.
> - ✅ **F6 parcial** — OTel opt-in en dev + sampler prod (F6.6); gate `test_realtime_policy.arch.test.ts` (anti-polling + single-stream + single-page; PROTECTED → label `architecture-change`); jsx-a11y en el lint (3 reglas de interacción en WARN por ~28 sitios legacy — `hu-fe-a11y-interacciones`); Panel/InsBlock accesibles. **Pendiente de F6**: e2e specs de orders/chats (F6.5 → `hu-fe-e2e-core`), gate de index-keys (mejor como regla eslint en la HU a11y).
> - 🧹 Fix colateral pre-existente de main: guard anti-voseo vs vocabulario del detector de la rúbrica de evals (exención documentada).
> - ⏳ Quedan como HUs: F2 fase B (platform/health + contadores reales), F3.5 CORS allowlist (con auth story), F3.6 updater/firma, F4.3+F4.4, F6.5, hu-fe-a11y-interacciones, y el lint rojo PRE-existente de main (react-refresh/set-state-in-effect en features de evals — no introducido por este refactor).

---

## 0. Resumen ejecutivo

1. **El síntoma reportado ("Orders llama orders y traces cada rato") NO es el SSE de chats.** Es (a) polling deliberado con `refetchInterval` en orders (30s lista + 60s vault) y (b) el exporter de OpenTelemetry que postea spans a `/v1/traces` cada ~5s mientras haya actividad — y cada poll genera spans, así que siempre hay. El SSE de chats se cierra correctamente al salir de la sección. Evidencia completa en §1.
2. **La arquitectura macro está sana y hay que preservarla:** FSD + plugins con 12 gates automáticos, lazy-loading por plugin, una sola Page montada a la vez (los pollers de una sección NO corren en otra), key factories jerárquicos, Zod en todos los boundaries, optimistic updates con rollback en el kanban.
3. **El problema real de datos es la estrategia realtime fragmentada:** 5 mecanismos distintos conviviendo sin política (SSE snapshot-cada-2.5s para la lista de chats, poll 3s para el detalle, poll 30/60s en orders, poll 5/15s en catalog, poll 5s en eta). El propio código lo admite en comentarios ("Por ahora se refresca cada 30s", "hasta que el backend exponga SSE per-sesión"). El SSE del backend es además polling server-side disfrazado (`while True → snapshot completo → sleep(2.5)`).
4. **Tauri está en estado prototipo:** CSP `null`, identifier default `com.tauri.dev`, `devUrl` en el puerto 5173 (el que squattea Archon), TitleBar fake sin drag-region conviviendo con las decoraciones nativas, StatusBar con métricas inventadas hardcodeadas.
5. **Robustez y deuda de UI:** cero ErrorBoundary (un throw en cualquier plugin = pantalla blanca total), ~20 archivos de features muertas en chats, `OrdersInspector.tsx` de 1092 líneas, 100+ estilos inline con colores hardcodeados fuera del sistema de tokens, ningún `queryFn` cancela requests en vuelo (AbortSignal ignorado).

**Salud por área:**

| Área | Estado | Nota |
|---|---|---|
| Arquitectura FSD + plugins | 🟢 Sólida | Gates automáticos; sin violaciones detectadas |
| Capa de datos TanStack | 🟡 Buena base, política ausente | Keys/Zod/mutations bien; realtime fragmentado; AbortSignal ignorado |
| Manejo de estado React | 🟡 Aceptable | Server state bien separado; formularios multi-paso con useState sueltos; sin máquina de estados donde hace falta |
| Tauri | 🔴 Prototipo | CSP null, identifier default, drag-region ausente, puerto conflictivo |
| Robustez (errores/carga) | 🔴 Frágil | Sin ErrorBoundary; Suspense fallback null; banners de error opt-in por payload |
| Design system | 🟡 Erosionado | @theme existe pero 100+ inline styles lo bypasean; index.css 2343 líneas con clases per-plugin |
| Observabilidad | 🟡 Funciona pero ruidosa | Sin sampler, sin gate por env; exporta el 100% de spans siempre |
| Testing | 🟡 Unit/arch bien, e2e raquítico | 12 gates arquitectura ✓; solo 2 specs e2e para 7 plugins |

---

## 1. Diagnóstico del síntoma: "Orders recarga orders y traces cada rato"

### 1.1 Qué se ve en la pestaña Network estando en Orders, y por qué

| Request observado | Frecuencia | Causa exacta | Archivo |
|---|---|---|---|
| `GET /api/orders/orders` | cada 30s | `refetchInterval: 30_000` — polling deliberado; el comentario admite que debería ser invalidación por evento | `src/plugins/orders/frontend/entities/order/api.ts:144` |
| `GET /api/orders/vault-orders` | cada 60s | `refetchInterval: 60_000` | `src/plugins/orders/frontend/entities/order/api.ts:319` |
| `POST /v1/traces` (puerto 4318) | cada ~5s mientras haya spans | OTel `BatchSpanProcessor` con `scheduledDelayMillis` default = 5000ms. **Cada fetch del dashboard genera un span** (FetchInstrumentation instrumenta todo `fetch`), así que cada poll de orders produce además un POST de traces poco después | `src/app/observability/otel.ts:57-61` + default verificado en `@opentelemetry/sdk-trace-base` |
| `GET /api/orders/orders/{id}`, `/customer-score` | al cambiar selección | normales (staleTime 10s / 5min) | `order/api.ts:150-166, 392-407` |

Dos agravantes del ruido de traces:

- Si no hay collector corriendo en `localhost:4318`, los POST fallan en rojo en la Network tab (más percepción de "algo anda mal"). El default vive en `src/shared/config/env.ts` (`VITE_OTEL_EXPORTER_URL ?? "http://localhost:4318/v1/traces"`).
- En dev, `StrictMode` duplica mounts/effects, así que tras cada montaje se ven fetches dobles.

**Descartado explícitamente:** el feedback loop "exportar traces genera traces". El transport del exporter (`@opentelemetry/otlp-exporter-base` ≥0.218, verificado en node_modules) usa `fetch.__original` precisamente para que la instrumentación no vea sus propios POSTs. No hay loop — no perseguir ese fantasma en el futuro.

### 1.2 El SSE de chats NO afecta Orders (evidencia)

- `Dashboard.tsx` monta **solo la Page activa**: `const ActivePage = pageByKey.get(section)` y la renderiza dentro de `<Suspense key={section}>` ([Dashboard.tsx:93-117](src/pages/Dashboard.tsx)). No hay secciones ocultas montadas.
- `useSessionsStream()` se monta en `ChatsSection` (la Page del plugin), y su `useEffect` cierra el `EventSource` en el cleanup ([session/api.ts:65-80](src/plugins/chats/frontend/entities/session/api.ts)). Al pasar a Orders, la conexión `/api/dashboard/stream` muere.
- Los `refetchInterval` de TanStack solo corren mientras el hook tiene observers montados — al desmontar `ChatsSection`, el poll de 3s del detalle se detiene.

Conclusión: **no hay fuga cross-sección**. Esta propiedad depende de que el shell siga montando una sola Page; cualquier futuro "keep-alive de secciones" la rompería (ver guardrail en F6).

### 1.3 Pero dentro de Chats el costo sí es alto, y el "SSE" no es push real

- El endpoint SSE del backend es un loop: `while True: data = await list_dashboard_sessions(); yield; sleep(2.5)` ([dashboard.py:68-78](../hubara_agency/src/plugins/chats/api/dashboard.py)). **Recalcula y empuja el snapshot completo de sesiones cada 2.5s por cliente conectado**, haya cambios o no. No es event-driven; es polling movido al servidor.
- Además, con una sesión abierta, `useSession(id)` pollea el detalle cada 3s (`SESSION_DETAIL_REFETCH_MS`, [session/api.ts:23,53](src/plugins/chats/frontend/entities/session/api.ts)) — ~20 requests/minuto.
- Lo único que amortigua el costo en re-renders es que el stream usa `setQueryData` (structural sharing): si el snapshot no cambió, no re-renderiza. Bien hecho — pero el ancho de banda y el CPU del backend se gastan igual.

### 1.4 Inventario completo de pollers (la tabla centro de esta auditoría)

| Plugin | Hook | Intervalo | Endpoint | Condición | Se monta en |
|---|---|---|---|---|---|
| orders | `useOrders` | **30s** | GET /api/orders/orders | siempre (sección activa) | OrdersSection |
| orders | `useVaultOrders` | **60s** | GET /api/orders/vault-orders | siempre | OrdersSection |
| chats | `useSession` | **3s** | GET /api/dashboard/sessions/{id} | sesión seleccionada | ChatsConversation (vía adapters de `entities/chat`) |
| chats | `useSessionsStream` | push c/**2.5s** (server) | GET /api/dashboard/stream (SSE) | siempre | ChatsSection |
| catalog | `useSyncHistory` | **5s** | GET /api/catalog/syncs | siempre | SyncHistory |
| catalog | `useSnapshotInfo` | **15s** | GET /api/catalog/snapshot | siempre | SyncRunner |
| catalog | `useSyncStatus` | **1.2s** | GET /api/catalog/sync/{id} | solo mientras `status === "running"` (function-form — este patrón está BIEN) | SyncInspector |
| eta | `useTrackedOrders` | **5s** | GET /api/eta/tracked-orders | siempre | EtaList, EtaCards |
| ads | `useAdsCampaigns` (+detail) | — | GET /api/ads/campaigns* | staleTime 30s, sin poll | AdsSection |
| agents_admin | `useAgents` etc. | — | GET /api/agents* | staleTime 5min, sin poll | AgentsSection |

Ads y agents_admin demuestran que el equipo ya sabe hacerlo sin polling; orders/catalog/eta quedaron en el patrón viejo.

---

## 2. Hallazgos

Convención: **C** = crítico (corregir ya), **M** = medio (entra al plan), **B** = bajo (oportunista). Cada ítem trae archivo:línea.

### 2.1 Críticos

- **C-1 · Estrategia realtime fragmentada, polling como default.** 5 mecanismos sin política única (tabla §1.4). Costo: tráfico constante (el síntoma reportado), backend recalculando snapshots, UX con latencia de hasta 30s para ver una orden nueva. Los comentarios del propio código lo reconocen como stopgap (`order/api.ts:142-144`, `session/api.ts:44-46`). → **F1**
- **C-2 · Ningún `queryFn` pasa el `AbortSignal` de TanStack.** `apiClient` lo soporta (`shared/api/client.ts:27`) pero las ~22 queries lo ignoran. Al cambiar de sección, los requests en vuelo siguen vivos y pueden resolver tarde (race conditions con cache, trabajo de red desperdiciado). Fix mecánico: `queryFn: async ({ signal }) => apiClient.get(url, { signal })`. → **F0**
- **C-3 · Cero ErrorBoundary.** Ni global ni por plugin (`app/providers/index.tsx` solo tiene QueryProvider; el comentario lo lista como "futuro"). Un throw en render de cualquier plugin (p.ej. un Zod parse error propagado) = pantalla blanca de toda la app. → **F2**
- **C-4 · Tauri sin hardening:** `"csp": null` (sin Content-Security-Policy — la recomendación #1 de seguridad de Tauri), `identifier: "com.tauri.dev"` (default; rompe firma/updater y colisiona con cualquier otra app dev), `devUrl: http://localhost:5173` + `vite.config.ts` `port: 5173 strictPort` — **el puerto donde corre el Vite de Archon** (gotcha #12 del CLAUDE.md raíz: el split IPv4/IPv6 hace que `localhost:5173` pueda cargar la app equivocada dentro de la ventana Tauri). [tauri.conf.json](src-tauri/tauri.conf.json), [vite.config.ts](vite.config.ts). → **F3**
- **C-5 · StatusBar y TitleBar muestran datos inventados al operador.** "Conectado · WhatsApp Cloud API", "247 conversaciones · 38 sin asignar", "Latencia 184 ms", "Agente: remarketing", "3 agentes activos", teléfono hardcodeado — todo estático ([StatusBar.tsx:11-34](src/shared/ui/chrome/StatusBar.tsx), [TitleBar.tsx:14-21](src/shared/ui/chrome/TitleBar.tsx)). Un operador decide con métricas falsas. → **F2**
- **C-6 · ~20 archivos de features muertas en chats.** `features/session-chat`, `features/session-list`, `features/session-metadata` y `features/memory-modal` no los importa nadie (verificado por grep de imports; `shared/lib/format.ts:62` solo los menciona en un comentario). Incluyen los peores hallazgos puntuales del barrido React (keys por índice en `ChatMessageList`, `dangerouslySetInnerHTML` sin sanitizer en `ChatBubble`, `Date.now()` fallback en `useCombinedHistory`) — deuda fantasma que confunde auditorías y refactors. → **F0**

### 2.2 Medios

- **M-1 · Lógica de reloj dentro del mapper de cache.** `toLegacyOrder` llama `computeOverdue`/`humanizeDue` que usan `new Date()` ([order/api.ts:46-96](src/plugins/orders/frontend/entities/order/api.ts)): el dato cacheado encierra "hoy/mañana/overdue" calculado al momento del fetch — se vence respecto al reloj y cambia identidad en cada refetch. Lo derivado-de-ahora se computa en render con util pura `(dueIso, now)`. → **F0/F5**
- **M-2 · `new Date()`/`Date.now()` en render** en OrdersFilters (×3), OrdersBoard:306, ReadyForShip (×2), ConfirmPaymentAction:169-171. → **F0**
- **M-3 · Patrón catch-to-empty duplicado** (orders C3 + catalog): el queryFn captura errores y devuelve shape vacío con flag (`catalog_available: false`); la UI debe acordarse de mirar el flag o muestra "vacío" cuando el backend está caído. No está centralizado; cada consumidor nuevo es un riesgo. → **F1** (política de errores junto con la de datos)
- **M-4 · Componentes gigantes / UI de negocio en la capa Page.** [OrdersInspector.tsx](src/plugins/orders/frontend/features/orders-inspector/ui/OrdersInspector.tsx) = 1092 líneas; `OrdersSection.tsx` define `VaultOrdersBanner` + `VaultOrderRow` inline (339 líneas) — la reconciliación de vault es una feature, no parte de la Page. → **F5**
- **M-5 · 100+ estilos inline con colores hardcodeados** (`#ff7269`, `rgba(255,180,74,…)`, etc.) concentrados en orders (~60), ads (~25), ConfirmPaymentAction (~15), bypaseando el `@theme` block. Además `index.css` (2343 líneas) contiene bloques per-plugin (`.ord-*`, `.chat-*`, `.ag-*`) en scope global. → **F4**
- **M-6 · Keys por índice en listas vivas.** `ChatsNotes.tsx:58-86` (`key={i}` en notas que se prependean) — bug real de reconciliación; el resto está en features muertas (C-6). La animación de ondas (`ChatsMessageList` wave, lista estática) puede quedarse con índice + comentario. → **F0**
- **M-7 · `window.confirm` en flujo de reconciliación** (`OrdersSection.tsx`, VaultOrderRow). En webviews de Tauri los diálogos JS nativos no son confiables cross-platform (en macOS WKWebView pueden no mostrarse) → en desktop el "Marcar resuelto" podría no confirmar nunca. Reemplazar por modal propio o `@tauri-apps/plugin-dialog`. → **F0**
- **M-8 · Suspense `fallback={null}`** en el shell ([Dashboard.tsx:112](src/pages/Dashboard.tsx)): flash en blanco en cada cambio de sección mientras carga el chunk lazy. → **F2**
- **M-9 · Formularios multi-paso como useState sueltos.** `ConfirmPaymentAction` (5 useState) y `ReadyForShip` (5 useState) codifican un flujo con estados contradictorios posibles (el "revuelto de variables"): pending/error/done/fechas/notas como flags independientes. Modelar con `useReducer` + uniones discriminadas (`{phase:"idle"}|{phase:"submitting"}|{phase:"error",detail}…`). → **F5**
- **M-10 · OTel sin sampler ni gate por entorno.** Exporta el 100% de los spans, siempre, incluso en dev sin collector. Falta: `VITE_OTEL_ENABLED` (off por default en dev), sampler por ratio en prod, y silenciar el exporter cuando el endpoint no responde. → **F6**
- **M-11 · Sin lint de a11y ni de keys.** `eslint.config.js` no incluye `jsx-a11y` (los divs clickeables de `Panel.tsx:20,45` sin `role`/`aria-expanded` pasaron limpio) ni `react/no-array-index-key`. → **F6**
- **M-12 · e2e raquítico:** 2 specs (smoke + agents) para 7 plugins; orders y chats — las secciones operacionalmente críticas — sin cobertura e2e. → **F6**
- **M-13 · CORS backend `allow_origins=["*"]` sin auth** ([main.py:56-61](../hubara_agency/src/main.py)). Hoy es lo que permite que el build Tauri (origin `tauri://localhost`) funcione, pero significa que cualquier página web abierta en la máquina del operador puede llamar la API. Restringir a lista cuando exista auth story (coordinar con plan infra Cognito). → **F3** (nota backend)

### 2.3 Bajos

- **B-1** Metadata placeholder en `Cargo.toml` ("A Tauri App", authors "you") y `productName`/icons default.
- **B-2** Ventana Tauri sin `minWidth/minHeight`, sin plugin `window-state` (no recuerda tamaño/posición), sin single-instance.
- **B-3** Seeds de selección del shell duplicadas: `Dashboard.tsx:77-81` siembra `{orders:"#1247", agents_admin:"sales"}` pero los plugins ya pasan su fallback a `useSelection("orders", "#1247")` — la del shell es redundante y acopla shell→plugin (el comentario mismo lo justifica con culpa).
- **B-4** Comentarios stale: `Dashboard.tsx:22-26` describe el "envelope de props vía pluginProps" que ya no existe (post-F7 es PluginHost); `sse.ts:9` apunta a un patrón "refetch en onMessage" que ya no se usa.
- **B-5** Dockerfile corre `npm run dev` (sin target de build prod) — correcto para el stack local HMR, pero documentarlo para que nadie lo use de imagen prod.
- **B-6** `console.warn` como única señal de SSE caído (`session/api.ts:69,76`) — el operador no se entera de que perdió realtime; falta indicador de conexión (encaja con StatusBar real, F2).

### 2.4 Lo que está bien — y es contrato a preservar

1. **Una sola Page montada** + lazy chunks por plugin (registry generado, gitignored). Es lo que evita que los pollers se acumulen cross-sección.
2. **FSD + aislamiento de plugins con 12 gates** (`src/test/architecture/`: dep-cruiser, entity ownership P-11/P-22, icons P-12, zod-at-boundary, fetch-isolation, env/URLs centralizados…). Los barridos no encontraron violaciones de import.
3. **TanStack bien usado en lo estructural:** key factories jerárquicos `as const` por entity, Zod parse en cada boundary (sin `any`), `useTransitionOrderStage` con optimistic update + rollback + `onSettled` reconcile ([order/api.ts:214-254](src/plugins/orders/frontend/entities/order/api.ts)), SSE→`setQueryData` (sin refetch redundante), `refetchOnWindowFocus: false` coherente con push.
4. **El patrón function-form de catalog** (`refetchInterval: (q) => q.state.data?.status === "running" ? 1_200 : false`) es exactamente cómo se hace polling condicional — usarlo de plantilla.
5. **Capabilities Tauri mínimas** (`core:default`, sin shell/fs/http) — least privilege correcto.
6. **PluginHost** como contrato shell↔plugin genérico (mata el bandejón de props), `env.ts` fail-fast, `apiClient` minimalista tipado.
7. Sin timers sueltos, sin listeners sin cleanup, sin localStorage ad-hoc (verificado por grep).

---

## 3. Política objetivo de estado y datos (la "constitución" que falta)

Para que el refactor no sea whack-a-mole, estas 6 reglas se documentan en `frontend_dashboard/CLAUDE.md` al cerrar F1 y los reviewers las exigen:

1. **Server state vive SOLO en TanStack Query.** Nunca copiado a `useState`/`useEffect`. (Hoy se cumple — mantener.)
2. **Realtime por push, polling solo como fallback degradado.** Un (1) EventSource por app; eventos tipados por dominio invalidan keys específicas. `refetchInterval` queda permitido únicamente: (a) function-form acotado a un run activo (patrón catalog), (b) fallback lento ≥5min como red de seguridad si el stream cae.
3. **UI state local y colocado** (`useState` en el componente dueño). Si un flujo tiene >3 flags interdependientes o estados imposibles, se modela con `useReducer` + unión discriminada.
4. **Estado cross-sección SOLO vía PluginHost** (`useSelection(pluginId, fallback)` — el fallback lo declara el plugin, no el shell).
5. **Errores se lanzan, no se disfrazan.** El queryFn no fabrica shapes vacíos; los componentes leen `isError`/`error` y un ErrorBoundary por plugin contiene los crashes. Donde la degradación parcial es deliberada (Medusa caído ≠ app rota), el estado degradado viene **del backend** en el payload (como `catalog_available`), no de un catch del cliente.
6. **Derivados de reloj se computan en render** con utils puras `(isoDate, now)`, nunca dentro de mappers/queryFn (la cache debe ser estable respecto al tiempo).

---

## 4. Plan de refactor por fases

> Orden diseñado para riesgo creciente y dependencias: F0 limpia el terreno, F1 ataca la causa raíz del síntoma, F2-F3 robustez/desktop, F4-F5 deuda de UI, F6 candados para que no regrese.
> **Verificación base de TODA fase** (desde repo root): `cd frontend_dashboard && npx tsc -b && npm test && npm run test:arch && npm run build`. Visual: stack Docker `docker ps` → http://localhost:5174 (NUNCA levantar vite suelto en 5173 — Archon).

### F0 — Quick wins de higiene (sin cambio de comportamiento visible) · ~1 día · riesgo bajo

| # | Tarea | Archivos | Criterio de aceptación |
|---|---|---|---|
| F0.1 | Pasar `AbortSignal` en TODOS los queryFn: `queryFn: async ({ signal }) => apiClient.get(url, { signal })` | `src/plugins/{orders,catalog,chats,eta,ads,agents_admin}/frontend/entities/*/api.ts` (~22 queries) | grep `queryFn: async ()` sin `signal` = 0 en entities |
| F0.2 | Borrar features muertas de chats: `features/session-chat`, `features/session-list`, `features/session-metadata`, `features/memory-modal` + sus tests + los re-exports/adapters de `entities/chat` que solo ellas consumían (`useChatMemory`, `useChatRoutingLog` — verificar con grep antes de borrar cada uno) | `src/plugins/chats/frontend/features/{session-chat,session-list,session-metadata,memory-modal}/` | `tsc -b` + vitest verdes; `npx depcruise` sin orphans nuevos; ChatsSection intacta visualmente en :5174 |
| F0.3 | Key estable en notas: `key={i}` → id real o `key={\`${n.ts}-${n.author}\`}` | `src/plugins/chats/frontend/features/chats-conversation/ui/ChatsNotes.tsx:58-86` | sin `key={i}` en listas mutables del plugin chats |
| F0.4 | Sacar `new Date()` del render: módulo-level o `useMemo` para hoy/mañana | `orders/features/orders-filters/ui/OrdersFilters.tsx:29-34`, `orders-board/ui/OrdersBoard.tsx:306`, `orders-inspector/ui/ReadyForShip.tsx:24-27`, `chats/.../ConfirmPaymentAction.tsx:169-171` | grep `new Date(` en cuerpos de componentes de esos files = 0 |
| F0.5 | Pureza del mapper orders: `toLegacyOrder` deja de computar `overdue/due/humanized`; nueva util pura `computeDueView(dueIso, now)` en `entities/order/model.ts` llamada en render | `orders/entities/order/api.ts:46-96` + consumidores (OrdersBoard, OrdersInspector) | el objeto cacheado no contiene strings relativos ("hoy/mañana"); tests de la util pura con `now` inyectado |
| F0.6 | `window.confirm` → modal propio (reusar patrón DangerPanel de OrdersInspector) | `orders/frontend/OrdersSection.tsx` (VaultOrderRow.onResolve) | cero `window.confirm/alert/prompt` en src/ |
| F0.7 | Quitar seeds de selección del shell (los plugins ya declaran fallback en `useSelection`) — verificar agents_admin/catalog pasan el suyo | `src/pages/Dashboard.tsx:77-81` | `useState<Record<…>>({})` vacío en Dashboard; cada sección abre con su default correcto en :5174 |
| F0.8 | Corregir comentarios stale (pluginProps envelope, "refetch en onMessage") | `Dashboard.tsx:22-26`, `shared/api/sse.ts:5-9` | — |

### F1 — Unificación realtime: de polling a push (LA fase que mata el síntoma) · 3-5 días · riesgo medio · **toca backend**

**Diseño objetivo:**

```
backend (platform):  EventBus in-process → GET /api/platform/events (SSE multiplexado)
                     eventos: {domain:"orders"|"chats"|"catalog"|"eta", type, id?, ts}
                     emitidos por los plugins al mutar (register_order, transición de tag,
                     progreso de sync…). Heartbeat cada 25s. Soporte Last-Event-ID best-effort.
frontend (shared):   UN EventSource app-level (app/providers/EventStreamProvider) sobre
                     subscribeSse existente + reconexión con backoff + estado de conexión expuesto.
                     API para plugins: useDashboardEvents(domain, handler) desde @/shared/api.
plugins:             orders → onEvent(order.*) → invalidate orderKeys.list()/detail(id)
                     chats  → onEvent(session.updated) → invalidate sessionKeys.detail(id);
                              lista sigue por setQueryData con payload del evento o invalidate
                     catalog/eta → ídem; el poll 1.2s function-form de runs activos SE QUEDA
```

| # | Tarea | Criterio de aceptación |
|---|---|---|
| F1.1 | **HU backend**: EventBus en `src/platform/` + endpoint SSE multiplexado + emisión de eventos desde orders (alta/cambio de orden — ya existe la decisión/activity post `register_order`), chats (cambio de sesión/tag/mensaje), catalog (progreso), eta (update). Respetar R-DIP: el bus es contrato de platform; los plugins publican, ninguno importa a otro. El loop snapshot-cada-2.5s de `dashboard.py` se reemplaza por push-on-change (o, fase intermedia, diff server-side: solo emite si el snapshot cambió) | Temporal/pytest verdes; conectarse con `curl -N` y ver eventos al mutar una orden |
| F1.2 | Frontend: `EventStreamProvider` (un solo EventSource, reconexión, expone `connectionState`) + `useDashboardEvents(domain, handler)` en shared/api. El handler registry es genérico — shared NO conoce dominios (regla FSD intacta) | Provider montado en `app/providers/index.tsx`; e2e: evento simulado → invalidación observada |
| F1.3 | orders: `refetchInterval` 30s/60s → `false` + fallback `5 * 60_000`; suscripción a `orders.*` que invalida `orderKeys.list()` (+`detail(id)` si trae id) | En Orders, Network de 60s muestra ≤1 request de orders sin actividad; una orden nueva del agente Sales aparece <2s |
| F1.4 | chats: eliminar poll 3s del detalle → suscripción a `session.updated(id)`; mantener `setQueryData` para la lista; indicador de conexión (consume `connectionState`, pinta el dot del StatusBar real de F2) | Con un chat abierto 60s sin actividad: 0 requests de detalle; mensaje entrante visible <2s |
| F1.5 | catalog/eta: history/snapshot/tracked-orders → eventos + fallback lento; conservar function-form 1.2s SOLO para sync activo | ídem patrón |
| F1.6 | Política de errores (M-3): retirar catch-to-empty del cliente; `catalog_available`/`error_detail` los responde el backend en 200 degradado (orders ya lo hace así server-side — unificar catalog); los errores reales se lanzan y los renderiza el ErrorBoundary/`isError` | grep "catch-to-empty" (ApiError→shape vacío) = 0 en entities |
| F1.7 | Documentar la política §3 en `frontend_dashboard/CLAUDE.md` + criterio para reviewers | — |

**Métrica de éxito de F1 (medible en :5174):** operador en Orders, 60s sin actividad → de ~3 requests de API + ~6-12 POST de traces a **1 heartbeat SSE + 0-1 requests**. Operador en Chats con sesión abierta → de ~44 requests/min (20 detalle + 24 snapshots) a ~0 + eventos puntuales.

### F2 — Robustez del shell · 1-2 días · riesgo bajo

| # | Tarea | Criterio |
|---|---|---|
| F2.1 | `PluginErrorBoundary` (por plugin, envuelve `<ActivePage/>` con UI de retry + plugin id) + boundary global en AppProviders | Throw forzado en un Page de prueba: el shell y el toolbar siguen vivos, la sección muestra fallback con "Reintentar" |
| F2.2 | Suspense fallback: skeleton del layout 3 columnas en vez de `null` | Cambio de sección sin flash blanco (verificable en :5174 con throttling) |
| F2.3 | StatusBar honesta: quitar TODOS los números fake; fase A = solo estado de conexión SSE real (de F1.2) + sección activa; fase B (HU aparte) = contadores reales vía endpoint platform/health | Cero literales fake ("247", "184 ms", teléfono) en `shared/ui/chrome/` |
| F2.4 | TitleBar: decidir modelo de ventana — **recomendado**: decoraciones nativas y NO renderizar la TitleBar fake en desktop (los semáforos no funcionan); alternativa: `decorations:false` + `data-tauri-drag-region` + window controls reales vía `@tauri-apps/api/window` | En build desktop: una sola barra de título, ventana arrastrable, botones cierran/minimizan de verdad |

### F3 — Tauri hardening · 1 día · riesgo bajo (no toca lógica de la app)

| # | Tarea | Criterio |
|---|---|---|
| F3.1 | CSP estricta en `tauri.conf.json`: `default-src 'self'; connect-src` con API + OTel + SSE (de env), `style-src 'self' 'unsafe-inline'` (Tailwind inline vars), `img-src 'self' data:` | App desktop funcional con CSP activa (fetch, SSE y traces pasan) |
| F3.2 | `identifier` → `com.hubara.dashboard` (o el dominio real), metadata de `Cargo.toml` y `productName`/version honestos | `tauri build` empaqueta con el bundle id correcto |
| F3.3 | Resolver el puerto: dev del proyecto → **5175** (libre; 5174 lo usa el container) en `vite.config.ts` + `devUrl` con **`http://127.0.0.1:5175`** (IPv4 explícito, inmune al squat IPv6 de Archon) | `npx tauri dev` abre el dashboard correcto SIEMPRE, con Archon corriendo |
| F3.4 | Ventana: `minWidth/minHeight` razonables (p.ej. 1100×700), `center: true`; agregar `tauri-plugin-window-state` (recordar geometría) y `tauri-plugin-single-instance` | Reabrir la app restaura tamaño/posición; segunda instancia enfoca la primera |
| F3.5 | Nota backend (M-13): plan para restringir CORS a lista de origins (`tauri://localhost`, `http://tauri.localhost`, `http://localhost:5174`, `http://127.0.0.1:5175`) cuando entre auth (Cognito, plan infra). Hoy `*` es consciente y queda documentado | Comentario en `main.py` + ítem en backlog infra |
| F3.6 | (Si se decide distribuir el .app) plugin updater + firma — **fuera de alcance hasta decisión de distribución** | — |

### F4 — Sistema de diseño: tokens y CSS per-plugin · 2-3 días · riesgo medio (visual)

| # | Tarea | Criterio |
|---|---|---|
| F4.1 | Censo de colores hardcodeados (script: grep de `#hex`/`rgba(` en .tsx) → mapa a tokens `@theme` nuevos (`--color-warn-soft`, `--color-error`, `--color-success`, etc.) | Tabla censo→token committeada en el PR |
| F4.2 | Migrar inline styles a clases/tokens, por plugin y en este orden: orders (60+) → ads (25+) → chats (15+) | `test_tokens_and_css.arch` endurecido (ver F6.3) pasa |
| F4.3 | Partir `index.css`: bloques `.ord-*`, `.chat-*`, `.ag-*` → `src/plugins/<id>/frontend/styles.css` importado por la Page del plugin; `index.css` queda con @theme + shell + shared | `index.css` < 1000 líneas; cada plugin carga su CSS con su chunk lazy |
| F4.4 | Snapshot visual antes/después por sección (Playwright screenshots) para no romper estética en la migración | Diffs visuales revisados y aprobados |

### F5 — Descomposición de componentes y estado de formularios · 2-3 días · riesgo medio

| # | Tarea | Criterio |
|---|---|---|
| F5.1 | Partir `OrdersInspector.tsx` (1092 líneas) en sub-archivos del mismo feature: `TimelinePanel`, `ItemsPanel`, `CustomerPanel` (score+summary), `DangerPanel`, etc. — sin mover de capa, solo modularizar | Ningún archivo del feature >300 líneas; misma UI en :5174 |
| F5.2 | Extraer `VaultOrdersBanner`/`VaultOrderRow` de OrdersSection → `features/orders-vault-reconciliation/` | OrdersSection <120 líneas, solo composición |
| F5.3 | `ConfirmPaymentAction` y `ReadyForShip`: estado del flujo a `useReducer` con unión discriminada (`idle → editing → submitting → done|error`) — referencia de patrón para futuros formularios | Estados imposibles irrepresentables (sin combinación pending+done); tests del reducer puros |
| F5.4 | `dangerouslySetInnerHTML` del bubble vivo (si quedó alguno post-F0.2): migrar a render de tokens (split por `**`/`\n` a elementos React) o DOMPurify | grep `dangerouslySetInnerHTML` = 0 (o justificado + sanitizado) |

### F6 — Guardrails para que no regrese · 1 día · riesgo bajo

| # | Tarea | Criterio |
|---|---|---|
| F6.1 | ESLint: agregar `eslint-plugin-jsx-a11y` (recommended) + `react/no-array-index-key` + regla custom/grep-gate prohibiendo `refetchInterval` numérico fuera de una allowlist comentada (la política §3.2) | `npm run lint` falla ante un poll nuevo no justificado |
| F6.2 | Fix de los hallazgos a11y existentes: `Panel.tsx:20,45` headers → `<button>` con `aria-expanded`; presence dot de Avatar con `aria-label` | jsx-a11y verde |
| F6.3 | Endurecer `test_tokens_and_css.arch.test.ts`: prohibir `#hex`/`rgba(` literales en props `style` de .tsx (allowlist para casos justificados) | Gate rojo si reaparecen colores hardcodeados |
| F6.4 | Arch test nuevo: "el shell monta una sola Page" (proteger la propiedad §1.2 — p.ej. asserting que Dashboard no importa Pages eager ni renderiza más de un ActivePage) | Gate en `src/test/architecture/` |
| F6.5 | e2e mínimos para orders (kanban carga, mover card optimista, banner Medusa caído con route mock) y chats (lista por SSE mock, enviar mensaje humano) | `npx playwright test` verde en CI |
| F6.6 | OTel: `VITE_OTEL_ENABLED` (default off en dev), sampler ratio configurable en prod, y no instanciar exporter si está off | En dev sin flag: 0 POSTs a /v1/traces; en :5174 con flag: trazas en SigNoz |

---

## 5. Mapa a HUs del pipeline

Cada fila es candidata a `archon workflow run hu-hubara-pipeline <issue>`. F0 puede ir como una sola HU; F1 son dos (backend primero).

| HU sugerida | Fase | Plugins afectados | Esfuerzo | Depende de |
|---|---|---|---|---|
| `hu-fe-hygiene-quickwins` (signal+dead code+dates+confirm+seeds) | F0 | chats, orders (frontend only) | S-M | — |
| `hu-platform-event-bus-sse` (backend: bus + endpoint + emisores) | F1.1 | platform, orders, chats, catalog, eta (backend) | M-L | — |
| `hu-fe-event-stream` (provider + useDashboardEvents + migrar pollers) | F1.2-F1.7 | shared/app + 4 plugins (frontend) | M-L | hu-platform-event-bus-sse |
| `hu-fe-shell-robustez` (ErrorBoundary + skeleton + StatusBar/TitleBar honestas) | F2 | shared/ui, app, pages | S-M | ideal post F1 (usa connectionState) |
| `hu-tauri-hardening` (CSP, identifier, puerto, window, plugins) | F3 | src-tauri, vite.config | S | — (independiente) |
| `hu-fe-design-tokens` (censo + migración inline→tokens + split CSS) | F4 | todos los plugins frontend | M-L | F0 |
| `hu-fe-orders-decomposicion` (inspector split + vault feature + reducers) | F5 | orders, chats | M | F0 (borra muerto antes) |
| `hu-fe-guardrails` (lint a11y/keys/polling + arch gates + e2e + OTel gate) | F6 | tooling, e2e | S-M | F1 (la regla de polling referencia la política) |

**Total estimado:** ~2-3 semanas de pipeline. El par F1 (backend+frontend) es el de mayor impacto percibido por el usuario — es el que elimina el síntoma reportado.

---

## 6. Apéndices

### A. Checklist Tauri best practices — estado actual

| Práctica | Estado | Ref |
|---|---|---|
| CSP definida | ❌ `csp: null` | tauri.conf.json → F3.1 |
| Identifier propio | ❌ `com.tauri.dev` | → F3.2 |
| Capabilities mínimas | ✅ `core:default`, sin fs/shell/http | capabilities/default.json |
| Sin IPC innecesario | ✅ lib.rs sin commands; app = webview pura | src-tauri/src/lib.rs |
| devUrl confiable | ❌ `localhost:5173` (conflicto Archon + resolución IPv6) | → F3.3 |
| Drag region / window chrome | ❌ TitleBar fake sin drag-region, doble chrome | → F2.4 |
| Window state/min-size/single-instance | ❌ | → F3.4 |
| Diálogos nativos JS | ⚠️ `window.confirm` no confiable en WKWebView | → F0.6 |
| Updater/firma | ➖ N/A hasta decisión de distribución | → F3.6 |
| CORS del backend para origin tauri | ⚠️ funciona porque es `*` (consciente, sin auth aún) | → F3.5 |

### B. Dónde está cada número citado

- `refetchInterval` 30s/60s: `src/plugins/orders/frontend/entities/order/api.ts:144,319` · 3s detalle: `chats/.../entities/session/api.ts:23,53` · 5s/15s/1.2s: `catalog/.../entities/catalog-sync/api.ts:52,67,87` · 5s eta: `eta/.../entities/tracked-order/api.ts:28`.
- SSE server loop 2.5s: `hubara_agency/src/plugins/chats/api/dashboard.py:68-78`.
- OTel flush 5s: default `OTEL_BSP_SCHEDULE_DELAY ?? 5000` en `@opentelemetry/sdk-trace-base` (BatchSpanProcessorBase); exporter URL default: `src/shared/config/env.ts`.
- Anti-loop del exporter verificado: `@opentelemetry/otlp-exporter-base/build/esm/transport/fetch-transport.js` (usa `fetch.__original`).
- Single-page mount: `src/pages/Dashboard.tsx:93-117`. CORS `*`: `hubara_agency/src/main.py:56-61`.
- Features muertas (0 importadores): `src/plugins/chats/frontend/features/{session-chat,session-list,session-metadata,memory-modal}` (session-list solo aparece en un comentario de `shared/lib/format.ts:62`).

### C. Ranking de archivos a refactorizar (peor primero)

1. `orders/features/orders-inspector/ui/OrdersInspector.tsx` — 1092 líneas, ~40 inline styles, key compuesta con índice (F5.1, F4.2)
2. `orders/frontend/OrdersSection.tsx` — 339 líneas, features inline, window.confirm (F5.2, F0.6)
3. `chats/.../ConfirmPaymentAction.tsx` — 5 useState de flujo + fechas en render + 15 inline styles (F5.3, F0.4)
4. `orders/entities/order/api.ts` — mapper impuro + catch-to-empty + polling (F0.5, F1.3, F1.6)
5. `chats/entities/session/api.ts` — poll 3s a reemplazar por eventos (F1.4)
6. `shared/ui/chrome/{StatusBar,TitleBar}.tsx` — datos fake (F2.3, F2.4)
7. `src/index.css` — 2343 líneas, clases per-plugin en global (F4.3)
8. `orders/features/orders-inspector/ui/ReadyForShip.tsx` — 5 useState + fechas en render (F5.3, F0.4)
9. `src-tauri/tauri.conf.json` — CSP/identifier/devUrl (F3)
10. `eslint.config.js` — sin a11y ni guardrails de keys/polling (F6.1)
