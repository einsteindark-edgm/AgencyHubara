# Plugin Isolation Audit — AgencyHubara

> **Propósito.** Auditoría **code-first** (no de los `.md`) del estado real del
> sistema de plugins, contra los dos requisitos multitenant del operador:
>
> - **REQ-1 — Aislamiento al AGREGAR:** un plugin nuevo para el tenant 1 debe
>   ser solo para ese tenant y **no tocar nada productivo**. Completamente
>   self-contained.
> - **REQ-2 — Seguridad al APAGAR:** quitar un plugin de `ENABLED_PLUGINS` **no
>   debe romper** nada — simplemente deja de verse en el frontend.
>
> **Fecha:** 2026-06-05 · **Método:** lectura de loaders, manifests, adapters,
> import-linter, dependency-cruiser, tests de invariantes + grep de imports
> cross-plugin en ambos stacks. Evidencia con `file:line`.
>
> **Relación:** insumo para el refactor que habilita
> [MULTI_TENANT_COMMERCE_ARCHITECTURE.md](MULTI_TENANT_COMMERCE_ARCHITECTURE.md)
> (cada tenant = distinto set de plugins + distinta config Medusa).

---

## §0. Veredicto

**El MECANISMO de plugins es sólido; las FRONTERAS de plugin están mal trazadas.**

| Requisito | Backend | Frontend | Veredicto |
|---|---|---|---|
| **REQ-1 (agregar = aislado)** | ✅ para plugins self-contained; ❌ para el patrón "split" (eta/ads/evals) | ❌ **2 fugas sistémicas**: entities + íconos viven en paths PROTECTED centrales | ⚠️ **PARCIAL** |
| **REQ-2 (apagar = no rompe)** | ✅ para self-contained; ❌ gating **asimétrico** en split plugins; ⚠️ coupling `orders→chats/eta` no declarado | ✅ shell degrada limpio (lazy + fallback) | ⚠️ **PARCIAL** |

Traducción: **hoy NO estás al nivel de abstracción que pediste.** El loader y el
shell hacen lo correcto (auto-discovery, gating, cero imports cross-plugin), pero
(a) tres "plugins" tienen su backend metido dentro de `chats`, así que su
prendido/apagado es inconsistente entre stacks; (b) agregar un plugin con una
entity o un ícono nuevo **edita archivos PROTECTED centrales**; y (c) hay
dependencias runtime reales (`orders→chats/eta`) que están **sin declarar y sin
enforcement**.

---

## §1. Lo que SÍ cumple (NO romper en el refactor)

Esto está bien y es la base sobre la que se construye el fix:

| Componente | Evidencia | Por qué cumple |
|---|---|---|
| **Loader FastAPI** | `hubara_agency/src/main.py:86-234` | Auto-discovery de manifests, `ENABLED_PLUGINS`-gated (`_enabled_plugins()`), fail-fast, **cero lista hardcodeada**. |
| **Meta-launcher workers** | `hubara_agency/src/run_workers.py:104-149` | Mismo patrón: descubre `agent.workers` de manifests, gateado por `ENABLED_PLUGINS`. |
| **render-compose** | `hubara_agency/scripts/render-compose.py:56-59` | Genera docker-compose escaneando manifests (`iterdir`), sin hardcode. |
| **agents_admin** | `hubara_agency/src/plugins/agents_admin/service.py:163-197` | Escanea TODOS los manifests genéricamente, **nunca nombra un plugin**, lee workspaces/manifests como DATA (no imports), con defensa de path-escape. **Exemplar del patrón correcto.** |
| **Import hygiene backend** | `.importlinter` + grep | R-DIP #9 (platform ↛ plugins) y #10 (siblings independientes) **se cumplen**. Cero imports cross-plugin reales (el único hit platform→plugin es un docstring en `orchestration/dispatcher.py:14`). |
| **Orquestación cross-worker** | `platform/orchestration/dispatcher.py` | Dispatcher **genérico** — no importa ningún plugin; rutea por strings del manifest (ADR-2026-05-20). |
| **Queues en manifests** | `platform/constants.py:1-17`, `plugin_manifest.py` | Post-PR11 las task queues viven en `agent.workers[].task_queue`; agregar worker no toca código compartido. |
| **Loader frontend** | `frontend_dashboard/scripts/plugins-sync.ts:104-195` | Codegen `ENABLED_PLUGINS`-gated → `plugin-registry.generated.ts` (gitignored). Requiere bloque `frontend:` (backend-only se excluye solo). |
| **Shell frontend** | `frontend_dashboard/src/pages/Dashboard.tsx:43-85` | **No hay `App.tsx`**; las secciones se derivan de `PLUGINS.flatMap(p=>p.sections)`. **Sin lista hardcodeada de plugins/secciones.** Toolbar genérico. |
| **Apagar limpio (FE)** | `Dashboard.tsx:64`, registry `lazy()` | Quitar de `ENABLED_PLUGINS` → el registry lo omite, su `Page` (lazy import) nunca se referencia, fallback a `sections[0]`. **REQ-2 cumplido en el shell.** |
| **Cross-plugin imports (FE)** | dep-cruiser `plugins-no-cross-plugin` | **Cero** imports `plugins/<a>` → `plugins/<b>`. Enforced en CI (`npm run test:arch`). |

---

## §2. Hallazgos por severidad

Cada uno mapeado a REQ-1 / REQ-2 + evidencia.

### 🔴 CRÍTICO

#### F1 — "Split plugins": `eta`, `ads`, `evals` tienen el backend dentro de `chats`
**Rompe REQ-1 y REQ-2.**

- `ads`, `eta`, `evals` son plugins en el **frontend** (`frontend_dashboard/src/plugins/{ads,eta,evals}/plugin.yaml`) pero su **backend vive en `chats`**:
  - `eta`: `src/plugins/eta/` es **solo `__init__.py` vacío**; el worker real es `chats.workers.eta` y la API es `chats.api.eta` (`chats/plugin.yaml:37-41,385-402`).
  - `ads`: **sin backend dir**; endpoints en `chats.api.ads` (`chats/plugin.yaml:32-36`).
  - `evals`: **sin backend dir**; worker `chats.workers.sales_eval` + `chats.api.evals` (`chats/plugin.yaml:42,443-447`).
- Está **reconocido en los comentarios del manifest** (`chats/plugin.yaml:32-35`): *"El plugin `ads` no tiene backend propio — sus endpoints viven aquí... Cross-plugin import sería violación de R-DIP, por eso vive en chats."*
- **Gating asimétrico** (la consecuencia que rompe el toggle):
  - El backend de eta/ads/evals se gatea por **`chats ∈ ENABLED_PLUGINS`** (están bajo el manifest de chats).
  - El frontend se gatea por **`eta`/`ads`/`evals` ∈ ENABLED_PLUGINS** (su propio manifest).
  - → **Prendés `eta` sin `chats`** = la UI aparece pero **el worker eta NO corre y `/api/chats/eta/*` no existe** → roto.
  - → **Apagás `eta` (frontend) con `chats` prendido** = el **worker eta sigue corriendo** y las transitions `orders→eta` siguen disparando. Apagar el plugin **no apaga su backend**.

Esto es exactamente lo contrario de tu requisito *"si apago un plugin… ya no se ve en el frontend y ya"*.

#### F2 — Las entities del frontend viven en el PROTECTED `src/entities/`, no por-plugin
**Rompe REQ-1.**

- **Ningún plugin tiene `frontend/entities/`** (`find src/plugins -type d -name entities` → vacío). Toda la data vive en el top-level `src/entities/{ads-campaign, agent, catalog-sync, chat, eval-candidate, handoff, message, order, session, tracked-order}` (51 import sites `@/entities/<x>`).
- `src/entities/<id>/*` es un **path PROTECTED** (spinal, frontend CLAUDE.md). → **Agregar un plugin con una entity de dominio nueva crea archivos bajo un path protegido central** — NO es self-contained.
- Contradice el modelo mental de la doc (*"cada `src/plugins/<id>/frontend/` tiene su propio mini-FSD: pages/features/entities"*). En el código vivo, **el tercio `entities/` de ese mini-FSD no existe para ningún plugin.**

### 🟠 ALTO

#### F3 — `orders → chats/eta`: dependencia runtime no declarada + `depends_on` sin enforcement
**Rompe REQ-2 (degradación silenciosa).**

- `orders/plugin.yaml` declara **5 transitions** que apuntan a `target_plugin: chats, target_worker: eta` (`:56-113`), pero `depends_on: []` (`:6`).
- **`depends_on` no lo lee NINGÚN loader.** Solo lo consume `system_map` para dibujar (`system_map/domain/builder.py:124,459` + `orphan_detector.py:34`). Los loaders (`main.py`, `run_workers.py`, `plugins-sync.ts`) **lo ignoran**. Y **todos los plugins declaran `depends_on: []`** → las dependencias reales son invisibles Y sin validación.
- → **Prendés `orders` sin `chats`** = al pasar un pedido a `preparing`, el dispatcher hace `start_workflow_with_replace` de `HubaraEtaSessionWorkflow` en `queue-eta-agent` **sin worker que la levante** → la workflow queda pending; los signals (ready/shipping/…) dan NOT_FOUND (absorbido). Las notificaciones ETA **desaparecen sin error visible**.

#### F4 — Los íconos resuelven del PROTECTED `Icon.tsx` central
**Rompe REQ-1.**

- `Toolbar.tsx:60-71 resolveIcon()` hace `Icon[name]` contra el registry central `src/shared/ui/Icon.tsx` (PROTECTED, append-only), con fallback a `Icon.bot` + `console.warn` si falta.
- Hoy todos los íconos declarados ya existen → nadie está forzado a editar. Pero **un plugin nuevo con un glifo nuevo debe appendear a `Icon.tsx`** (PROTECTED) o renderizar el `bot` silencioso. Es el anti-pattern #12 del frontend CLAUDE.md; el aislamiento depende de que cada plugin futuro **reuse** un ícono existente.

### 🟡 MEDIO

#### F5 — `chats` es un mega-plugin no granularmente toggleable
4 workers (`sales`, `remarketing`, `eta`, `sales_eval`) + 6 routers (`sales`, `dashboard`, `handoff`, `ads`, `eta`, `evals`) bajo un solo manifest (`chats/plugin.yaml:28-478`). No podés darle a un tenant "sales sin remarketing" ni "chats sin eta". Deuda estructural que limita la granularidad multitenant.

#### F6 — El tool de CREACIÓN de órdenes vive en `chats/sales`, no en `orders`
`register_order` (lo que el agente usa para crear la orden) está en `chats/agent/sales/tools/order_registration.py`, registrado por el sales worker. El plugin `orders` solo tiene kanban/reconcile/query. → "orders" como capability está partido: creación en chats, gestión en orders. Defendible (es un tool del agente) pero es coupling.

#### F7 — `agents_admin` (y la enumeración de invariantes) ignoran `ENABLED_PLUGINS`
`discover_agents()` escanea TODOS los manifests sin filtrar por enabled (`agents_admin/service.py:176`). → un tenant con `chats` apagado **igual ve** los agentes sales/remarketing/eta en la sección Agents. Leak multitenant (menor hoy, real en prod multi-tenant).

#### F8 — `chats` alcanza la entity `order` (coupling cross-dominio)
`chats/frontend/features/chats-conversation/ui/ConfirmPaymentAction.tsx:3-7` importa `useConfirmOrderPayment, useScheduleOrder` de `@/entities/order` (entity conceptualmente de `orders`). dep-cruiser NO lo flagea (entities son capa compartida). Apagar el plugin `orders` **no** rompe chats (la entity es independiente del plugin), pero es una dependencia cross-dominio real.

#### F9 — `pluginProps` hardcodeado en el shell
`Dashboard.tsx:106-119` pasa un bag fijo de selection state (`selectedChatId/OrderId/TrackedId/JobId/AgentId`). Un plugin nuevo que necesite una key de selección cross-section nueva edita `Dashboard.tsx`. Bajo impacto (un plugin self-contained no lo necesita); reconocido en `:101-105`.

### 🟢 BAJO (gaps de enforcement — guards que faltan)

- **F10** — dep-cruiser **no tiene regla `plugins → @/features/*`** (latente, count 0). Nada impide que el próximo plugin se acople a un `feature` compartido.
- **F11** — **ningún test asserta que los íconos de los manifests existan en `Icon.tsx`** → un plugin con ícono no registrado renderiza `bot` silencioso (el test de registry solo chequea ids, no íconos).
- **F12** — **ningún enforcement de `depends_on`** al boot (ver F3).
- **F13** — **ningún test de consistencia de frontera cross-stack**: nada verifica que un plugin frontend tenga backend, ni que el set de plugins sea coherente entre frontend y backend (F1 pasó desapercibido).
- **F14** — los invariantes (`tests/plugins/test_premortem_invariants.py`) guardan el MECANISMO (queue-uniqueness, k8s parity, compose drift) pero **no la ISOLATION ni la on/off safety**.

---

## §3. Scorecard por plugin (ambos stacks)

| Plugin | Backend | Frontend | Self-contained | Toggle limpio | Notas |
|---|---|---|---|---|---|
| **catalog** | agent+worker+api propios | section propia | ✅ | ✅ | Referencia de plugin sano (full-stack). Entity `catalog-sync` en central (F2). |
| **system_map** | api+domain propios, lee manifests | sin `frontend/` (excluido del registry, correcto) | ✅ | ✅ | Backend/api only. |
| **agents_admin** | service escanea manifests | section propia | ✅ | ⚠️ | No filtra `ENABLED_PLUGINS` (F7). |
| **orders** | api+reconcile+agent propios | section propia | ⚠️ | ⚠️ | Coupling no declarado `→chats/eta` (F3); creación en chats (F6). |
| **chats** | sales+remarketing **+ eta+sales_eval+ads/eta/evals-api** | section propia | ⚠️ | ⚠️ | Mega-plugin (F5); absorbe backends ajenos (F1). |
| **eta** | **`__init__.py` vacío** (backend en chats) | section propia | ❌ | ❌ | Split plugin (F1). |
| **ads** | **sin backend** (en chats) | section propia | ❌ | ❌ | Split plugin (F1). |
| **evals** | **sin backend** (en chats) | section propia | ❌ | ❌ | Split plugin (F1). |

Todos los plugins frontend: **cero imports cross-plugin** (✅). Fuga sistémica común: cada uno **posee una entity en `src/entities/` (PROTECTED)** y **referencia íconos en `Icon.tsx` (PROTECTED)** — F2/F4.

---

## §4. Plan de refactor priorizado

Orden por payoff/riesgo. Cada item es independiente y shippa verde.

### Prioridad 1 — Resolver los split plugins (F1) [decisión de diseño requerida]

Dos caminos; elegí según si eta/ads/evals deben ser **toggleables independientemente** por tenant:

- **Opción A — Colapsar a secciones de `chats`** (si nunca son independientes de chats): eta/ads/evals dejan de ser plugins; sus `sections` frontend las **contribuye el manifest de `chats`** (que ya soporta `frontend.contributes.sections`). Una sola frontera `chats` coherente en ambos stacks. **Bajo esfuerzo, honesto con la realidad backend.** Pierde toggle independiente.
- **Opción B — Extraer backends a plugins propios** (si querés togglearlos por tenant): mover la session/workspace/routing machinery de chats a un **platform capability compartido** (`platform/conversation/`), y dejar `sales`/`remarketing`/`eta` como plugins-agente delgados encima. ads/evals → plugins api propios consumiendo platform ports. **Alto esfuerzo** (toca el runtime conversacional), pero es el que da granularidad multitenant real.

> Recomendación: **A para `ads`/`evals`** (son facetas internas de WhatsApp/chats, rara vez independientes) + **B para `eta`** si los tenants de post-venta lo justifican (eta ya solo lee el order query port de platform → es el candidato más limpio a extraer).

### Prioridad 2 — Dispatcher tolerante a target no-habilitado (F3) [cierra REQ-2]

Hacer que `dispatch_event_activity` **skipee toda transition cuyo `target_plugin`/`target_worker` no esté en `ENABLED_PLUGINS`** (en vez de disparar al vacío). Entonces `orders` funciona standalone (sin ETA, degradación limpia) sin necesitar `depends_on`. Cheap, alto payoff.

### Prioridad 3 — Enforcement de `depends_on` (F12) [cierra REQ-1/REQ-2]

Los loaders (`main.py`, `run_workers.py`, `plugins-sync.ts`) validan al boot que el `depends_on` de cada plugin habilitado también esté habilitado → **fail-fast** con mensaje claro. Y declarar las deps reales (o eliminarlas vía P2). Convierte `depends_on` de decorativo a contrato.

### Prioridad 4 — Entities por-plugin (F2) [cierra REQ-1, el grande del frontend]

Mover las entities de dominio a `src/plugins/<id>/frontend/entities/` (el mini-FSD que la doc promete). Las **shared cross-dominio** (`order`, usada por chats y orders) se quedan en `src/entities/` con ownership explícito, o se introduce el concepto "shared entity". Agregar regla dep-cruiser: plugin → su-propia-entity ✅, plugin → shared-entity ✅, plugin → entity-de-otro-plugin ❌ (cierra F8 también). FSD refactor acotado.

### Prioridad 5 — Íconos contribuibles por plugin (F4)

El codegen `plugins-sync.ts` agrega los íconos que cada plugin trae en su `frontend/` al registry generado → `Icon.tsx` queda como base compartida pero **el plugin shippa su glifo sin editar el archivo protegido**. + test que asserta que cada ícono de manifest resuelve (F11).

### Prioridad 6 — Guards de isolation (F10, F11, F13, F14)

Agregar tests/reglas que **prevengan la regresión** de toda esta clase:
- dep-cruiser: `plugins → @/features` forbidden (F10).
- test: cada ícono de manifest existe en el registry (F11).
- test cross-stack: cada plugin frontend con `api`/`agent` declarado tiene backend real; el set de plugins es coherente entre stacks (F13).
- test: apagar un plugin no deja referencias colgadas (on/off safety) (F14).
- `agents_admin` + enumeraciones de prod filtran `ENABLED_PLUGINS` (F7).

---

## §5. Cómo encaja con la arquitectura multitenant

Este refactor es **prerequisito** del modelo de [MULTI_TENANT_COMMERCE_ARCHITECTURE.md](MULTI_TENANT_COMMERCE_ARCHITECTURE.md): "cada tenant = distinto set de plugins" solo es seguro cuando **prender/apagar un plugin es simétrico entre stacks (F1), no rompe por coupling no declarado (F3), y agregar uno no toca paths PROTECTED (F2/F4)**. Las prioridades P1–P3 cierran REQ-2 (apagar limpio); P2–P5 cierran REQ-1 (agregar aislado). P6 evita que la regresión vuelva.

---

**Fin del informe.** Evidencia toda en `file:line`; el código vivo gana sobre cualquier `.md`.
