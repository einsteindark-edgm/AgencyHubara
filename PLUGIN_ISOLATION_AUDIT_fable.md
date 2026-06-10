# Plugin Isolation Audit — segunda opinión (fable)

> **Qué es.** Auditoría **independiente y code-first** del sistema de plugins,
> hecha desde cero contra el código vivo, SIN asumir como ciertos los hallazgos
> de [PLUGIN_ISOLATION_AUDIT.md](PLUGIN_ISOLATION_AUDIT.md) (2026-06-05) ni el
> estado declarado en [PLUGIN_CONTRACT.md](PLUGIN_CONTRACT.md) /
> [PLUGIN_ARCHITECTURE_TESTS.md](PLUGIN_ARCHITECTURE_TESTS.md). Cada claim de
> esos docs fue re-verificado; cada hallazgo nuevo tiene evidencia `file:line`.
>
> **Fecha:** 2026-06-09 · **Base:** HEAD `9c21fe7` (post-PR #49: `ads` extraído
> `a5bc586`, `eta` extraído `d3a6289`, CI arch-gate `48059a4`/`9c21fe7`).
> **Método:** lectura directa de loaders/manifests/dispatcher/tests + 2 barridos
> exhaustivos (backend/frontend) + **ejecución real de todos los gates** (no
> solo lectura de su código).
>
> **Documentos hermanos:**
> [AUDIT_DIFF_fable.md](AUDIT_DIFF_fable.md) — qué encontré que la auditoría
> anterior no vio, y qué de sus docs quedó stale.
> [PLUGIN_REFACTOR_PLAN_fable.md](PLUGIN_REFACTOR_PLAN_fable.md) — el plan de
> refactor completo + los candados para que esta clase de errores no vuelva.

---

## §0. Veredicto

**El refactor parcial (PR #49) arregló el plano del CÓDIGO. El plano del
DEPLOY — el que corre en producción — quedó fuera del radar: ahí el toggle de
plugins directamente NO existe.**

La auditoría anterior midió REQ-1/REQ-2 contra los *loaders*. Esta segunda
opinión agrega la capa que faltaba — qué pasa en los artefactos que de verdad
corren (compose / k8s / imagen Docker) — y el resultado cambia el cuadro:

| Capa | ¿Filtra `ENABLED_PLUGINS`? | Evidencia | Veredicto |
|---|---|---|---|
| API loader (FastAPI) | ✅ sí | `hubara_agency/src/main.py:78-100` | OK (dev y prod si el env llega) |
| Meta-launcher workers (**solo dev**) | ✅ sí | `hubara_agency/src/run_workers.py:77-118` | OK, pero **prod no usa este path** |
| Dispatcher (P-SKIP) | ✅ sí | `src/platform/orchestration/dispatcher.py:140,179-187` | Implementado y testeado |
| Frontend codegen | ✅ sí (build-time) | `frontend_dashboard/scripts/plugins-sync.ts:179-195` | OK, pero es **build-time** (§2 N-5) |
| **render-compose (artefacto local/prod)** | ❌ **no** | `hubara_agency/scripts/render-compose.py:51-72` | Renderiza TODOS los workers siempre |
| **Containers de workers** | ❌ **no** (corren `python -m <module>` directo) | `render-compose.py:95`; compose/k8s: **0 matches** de `ENABLED_PLUGINS` | El worker arranca aunque su plugin esté "apagado" |
| **k8s prod** | ❌ **no** (hand-maintained, sin env) | `hubara_agency/k8s/aws-produccion/worker-*.yaml` (6) | Sin gating |

**Consecuencia directa:** en el stack desplegado nadie setea `ENABLED_PLUGINS`
(grep en `docker-compose.base.yml`, `docker-compose.local.yml` y `k8s/` = 0
matches) → `enabled_plugins()` devuelve `None` en todos los procesos → **el
dispatcher-skip (P-7) está implementado pero INERTE en los deployments
reales**, y apagar un plugin hoy = editar artefactos de infra a mano.

| Requisito | Backend código | Deploy real | Frontend | Veredicto global |
|---|---|---|---|---|
| **REQ-1 (agregar = aislado)** | ⚠️ casi (rutas de agente tocan `platform/constants.py` PROTECTED — §2 N-2) | ⚠️ k8s a mano por worker | ❌ entities + íconos centrales (F2/F4 siguen) + `pluginProps` | ⚠️ **PARCIAL** |
| **REQ-2 (apagar = no rompe)** | ✅ loaders + dispatcher | ❌ **no existe el toggle** (N-1) | ⚠️ shell degrada bien, pero apagar un provider rompe UI ajena en runtime (N-4) | ⚠️ **PARCIAL** |

---

## §1. Estado real de los candados (ejecutados hoy, no leídos)

Corrí los gates exactamente como CI (`.github/workflows/architecture-gates.yml`):

| Gate | Comando | Resultado 2026-06-09 |
|---|---|---|
| Import-linter (R-DIP) | `uv run lint-imports` | ✅ **4 contracts kept, 0 broken** |
| Arch tests backend | `uv run pytest tests/architecture -q` (+ dummies `MEDUSA_*`, `OTEL_SDK_DISABLED` como CI `architecture-gates.yml:82-88`) | ✅ **54 passed, 1 xfailed** (el xfail es P-9) |
| Premortem invariants | `uv run pytest tests/plugins/test_premortem_invariants.py -q` | ✅ **7 passed** |
| Codegen registry | `npm run plugins:sync` | ✅ 6 plugins (ads, agents_admin, catalog, chats, eta, orders); `system_map` correctamente excluido |
| Arch tests frontend | `npm run test:arch` | ✅ **10 files / 16 tests passed** |

> Gotcha local re-confirmado: sin los dummies Medusa, `test_spinal.py::
> test_every_src_module_imports_cleanly` falla en local (1 failed) — es el env,
> no el código. CI los setea (`architecture-gates.yml:86-88`).

### Mapa P-# → estado REAL en el código (vs lo que los docs declaran)

| Regla | Doc dice | Código real (verificado) |
|---|---|---|
| P-1 self-contained | 🟢 | 🟢 `tests/architecture/test_plugin_contract.py:38` |
| P-2 backend parity | 🔴 (header §1) / 🟢 (resumen) | 🟢 `test_plugin_contract.py:76` — pasa |
| P-3 no cross-import | 🟢 | 🟢 `test_plugin_contract.py:48` |
| P-4 platform↛plugins | 🟢 | 🟢 `test_plugin_contract.py:66` |
| P-6 enabled⊇depends_on | 🔴 | 🔴 confirmado — no existe `validate_enabled` en ningún loader |
| P-7 dispatcher-skip | 🟢 | 🟢 `dispatcher.py:179-187` + tests; **pero inerte en deploy (N-1)** |
| P-9 frontend own-API | 🟡 xfail | 🟡 xfail `strict=False` (`test_plugin_contract.py:123-131`); **hoy solo matchea por COMENTARIOS** (N-3) |
| P-10 cruiser rules | 🟢 | 🟡 **a medias**: `plugins-no-features` existe (`.dependency-cruiser.cjs:126`); `plugins-own-entities-only` **NO existe** (12 reglas enumeradas — no está) |
| P-11 entities por-plugin | 🔴 | 🔴 confirmado — 11 entities centrales (`src/entities/`: ads-campaign, agent, catalog-sync, chat, eval-candidate, eval-trend, handoff, message, order, session, tracked-order); **ningún** plugin tiene `frontend/entities/` |
| P-12 íconos resuelven | 🟢 | 🟢 `src/test/architecture/test_plugin_icons.arch.test.ts` |
| P-13 parity cross-stack | 🟡 | 🔴 no existe como test |
| P-14 cast (forma) | 🟢 forma / 🔴 uso | 🟢/🔴 confirmado — `test_plugin_contract.py:89`; cero bloques `consumes:` en manifests |
| P-15..P-18 | 🔴 propuestos | 🔴 confirmado — no existen |
| P-19 transition→runtime | 🔴 propuesto | 🟡 **PARCIALMENTE EXISTE y los docs no lo registran**: `tests/architecture/test_manifest_orchestration_consistency.py` valida (a) `workflow_classes` ⇔ `@workflow.defn(name=)` por AST, (b) `on_event ∈ emits`, (c) target `(plugin,worker)` + `target_workflow` resuelven contra el índice de manifests, (d) eventos importables. Falta solo el smoke funcional. |

Además del set P-#, el frontend tiene candados que ningún doc inventarió:
`test_plugin_registry.arch.test.ts` (registry ⊆ manifests válidos),
`test_fetch_isolation` (fetch solo en `shared/api/client.ts`),
`test_env_and_urls`, `test_zod_at_boundary`, `test_tokens_and_css`,
`test_naming_and_extensions`, `test_typescript_compiles`, y el meta-gate
(`test_meta.arch.test.ts`).

---

## §2. Hallazgos NUEVOS (no están en la auditoría 2026-06-05 ni en el contrato)

### 🔴 N-1 — El toggle de plugins NO existe en la capa de deploy (la real)

**Rompe REQ-2 donde más importa.**

- `render-compose.py:51-72` itera TODOS los manifests **sin leer
  `ENABLED_PLUGINS`** → `docker-compose.local.yml` siempre contiene los 6
  worker-services.
- Cada service corre el módulo del worker directo: `command: ["python", "-m",
  module]` (`render-compose.py:95`) — **no pasa por `run_workers.py`**, que es
  el único lugar (aparte del API) que filtra. El `main()` del worker no chequea
  nada: conecta a Temporal y pollea su queue incondicionalmente.
- Ni `docker-compose.base.yml`, ni el compose generado, ni los 6
  `k8s/aws-produccion/worker-*.yaml` setean `ENABLED_PLUGINS` (grep = 0).
- → En el stack real: `enabled_plugins() = None` en TODOS los procesos →
  **P-7 nunca skipea** y "apagar un plugin" = cirugía manual de infra. El
  toggle simétrico hoy solo existe para quien corre `uv run python -m
  src.run_workers` en su laptop.

### 🔴 N-2 — `eta` NO es togglable independiente de `chats` (y las rutas de agente viven en un spinal PROTECTED de platform)

**Rompe REQ-2 (toggle eta) y REQ-1 (agregar agente nuevo).** Es más grave que
el "residuo PM-2" que documenta el contrato:

- El **ingest de WhatsApp es un router del plugin `chats`**
  (`chats/plugin.yaml:29` — `src.plugins.chats.api.sales` con prefix `/api`).
  Con `ENABLED_PLUGINS=orders,eta` (sin `chats`): las notificaciones ETA salen,
  pero el webhook **no existe** → toda respuesta del cliente al agente ETA se
  pierde. La conversación queda muda. **Dependencia funcional dura
  eta→chats(ingest) sin declarar** — no la cubre ni P-7 (es inbound, no
  transition) ni `depends_on` (vacío).
- El ruteo de inbounds a ETA está en `chats`:
  `load_or_start_sales_session.py:178-179` (`if active_route == ROUTE_ETA:
  workflow_id = f"eta-{session_id}"`), duplicando el template del manifest de
  orders (`orders/plugin.yaml:59`) — esto sí es PM-2, sigue vivo y sin guard.
- **Lo nuevo:** `ROUTE_ETA` no vive en chats — vive en
  `src/platform/constants.py:34`, que es **spinal file PROTECTED**
  (`hubara_agency/CLAUDE.md`). Un futuro plugin-agente con ruta propia debe
  (a) editar un PROTECTED central y (b) editar el use case de chats. Es el
  espejo backend exacto del anti-pattern de íconos (F4/AP-4): falta el
  contribution point (`agent.owns_route` + route registry).

### 🟠 N-3 — P-9 detecta por COMENTARIOS y es ciego al canal real (lavado vía entities centrales)

**Blind spot de detección — el candado clave del contrato no mide lo que cree
medir.**

- El test P-9 grepea texto `/api/<otro>/` bajo `plugins/`. Hoy, los ÚNICOS
  matches de agents_admin son **docstrings JSDoc**:
  `agents_admin/frontend/features/agents-quality/ui/AgentsQuality.tsx:9` y
  `eval-trend-chart/ui/EvalTrendChart.tsx:89`. Borrá los comentarios y P-9 se
  pone verde **con el coupling intacto**.
- El canal real es el lavado: *plugin → entity central → API de otro plugin*,
  que P-9 no ve porque el string vive fuera de `plugins/`:
  - `chats` → `@/entities/order` (`ConfirmPaymentAction.tsx:6`) → `order/api.ts:105,159` → **`/api/orders/*`**
  - `agents_admin` → `@/entities/eval-trend` → `api.ts:11` → **`/api/chats/evals/history`**
  - `agents_admin` → `@/entities/eval-candidate` → `api.ts:12` → **`/api/chats/evals/candidates`** (incluye **POST/DELETE** — escritura cross-plugin)
- Y dep-cruiser no compensa: no existe regla de ownership de entities
  (`plugins-own-entities-only` nunca se agregó).
- → Hoy la única "detección" de consumo cross-API son comentarios. El plan
  reemplaza esto por ownership de entity + ownership de prefijo (P-22/P-23 en
  [PLUGIN_REFACTOR_PLAN_fable.md](PLUGIN_REFACTOR_PLAN_fable.md)).

### 🟠 N-4 — Apagar un provider rompe UI ajena en runtime (la concreción de F2/F8)

Mapa completo entity-central → endpoint → consumidores (verificado archivo por
archivo; conteo de imports `@/entities/<x>` bajo `plugins/`):

| Entity | Endpoint que llama | Importada desde | Riesgo al apagar el provider |
|---|---|---|---|
| `order` | `/api/orders/*` (`api.ts:105,159`) | orders (7) + **chats (1)** | apagás `orders` → el canvas de pago de chats da 404 |
| `eval-trend` / `eval-candidate` | `/api/chats/evals/*` (`api.ts:11` / `:12`) | **agents_admin** (1+1) | apagás `chats` → "Calidad LLM" muerta (lecturas Y escrituras) |
| `session` / `handoff` | `/api/dashboard/*` (`session/api.ts:26,31,66`; `handoff/api.ts:28,54,76`) | chats (13+2) | propio de chats — OK |
| `tracked-order` | `/api/eta/tracked-orders` (`api.ts:20`) | eta (6) | propio — OK |
| `ads-campaign` | `/api/ads/*` (`api.ts:223,245,272`) | ads (9) | propio — OK |
| `agent` | `/api/agents` | agents_admin (3) | propio — OK |
| `catalog-sync` | `/api/catalog/*` | catalog (4) | propio — OK |
| `chat` / `message` | adaptadores (sin HTTP propio) | chats (11+4) | — |

Solo 2 cadenas son cross-plugin, pero son exactamente las que el shell NO
protege: el registry degrada bien al apagar un plugin (su sección desaparece),
pero **nada degrada los fetch de un plugin vivo contra un backend apagado**.

### 🟠 N-5 — El gating frontend es BUILD-time; el backend es RUN-time

`plugins-sync.ts` corre en `predev`/`prebuild` (`package.json:7-9`), lee
`process.env.ENABLED_PLUGINS` (`plugins-sync.ts:179` — no es `VITE_*`, no
llega al bundle) y genera `plugin-registry.generated.ts` (gitignored,
`.gitignore:29`). → Por-tenant plugin sets en el modelo multitenant (S3 +
CloudFront según `infra/INFRASTRUCTURE.md`) implican **un build de frontend por
tenant** o un refactor a registry runtime. Nadie lo decidió todavía; hay que
agendarlo ANTES del refactor multitenant.

### 🟡 N-6 — El manifest único vive en el árbol del FRONTEND y el backend depende de él en runtime

No hay `plugin.yaml` bajo `hubara_agency/` (verificado por glob). El backend
los lee cruzando el monorepo: `plugin_manifest.py:36-37`, `main.py:74-75`,
`run_workers.py:48-49`, `agents_admin/service.py:44`,
`tests/.../test_manifest_orchestration_consistency.py:35`. La imagen Docker lo
hace explícito: `Dockerfile:27` → `COPY frontend_dashboard/src/plugins/
./frontend_dashboard/src/plugins/`. Es single-source-of-truth (bien) pero la
ubicación es un contrato implícito: renombrar/mover el árbol FE rompe el
backend en runtime, y nada lo documenta como frontera dura.

### 🟡 N-7 — `ENABLED_PLUGINS`: semántica fail-open + 4 implementaciones duplicadas

- Parseo duplicado en `main.py:78`, `run_workers.py:77`,
  `plugin_manifest.py:40` (la "canónica") y `plugins-sync.ts:179`. Drift
  posible; ya hay 3 copias Python idénticas que nadie obliga a mantener
  sincronizadas.
- Unset/vacío → **todo encendido** en las 4. Razonable hoy; para multitenant
  es fail-open: un deployment de tenant que olvida el env levanta TODOS los
  agentes (con costo LLM y side effects reales). Decisión de política
  pendiente (ver plan F0-D3).

### 🟡 N-8 — El meta-gate backend tampoco coincide con su CLAUDE.md (PM-11 era solo FE)

`tests/architecture/conftest.py:126-136` protege únicamente
`hubara_agency/tests/architecture/`, `.importlinter`, `.archon/workflows/`,
`.claude/skills/exoclaw-`. Pero `hubara_agency/CLAUDE.md` declara PROTECTED
también `src/platform/{contracts,registries,tool_extensions,constants}.py` y
`tests/plugins/test_premortem_invariants.py` — **ninguno está en el meta-gate**.
Hoy podés editar `platform/constants.py` o los invariantes premortem sin
`ARCH_CHANGE_APPROVED` y CI pasa. El mismo mismatch que PM-11 documentó para el
frontend, replicado en backend y sin registrar.

### 🟢 N-9 — Silent-skips divergentes entre loaders

`main.py:112-118`: manifest con `id` ≠ nombre de dir → **warning + skip**
(plugin desaparece silenciosamente), contradiciendo la filosofía fail-fast del
propio loader (que sí muere por import error). `plugins-sync.ts` para el mismo
caso falla duro. Mismo error, dos severidades.

### 🟢 N-10 — Rot de documentación/razones (instancias vivas de PM-12)

- `orders/api/__init__.py:432`: comment dice "HubaraEtaSessionWorkflow del
  plugin **chats**" — falso post-extracción.
- xfail P-9 (`test_plugin_contract.py:123-131`): "…`eta` sigue split… Verde
  cuando … + **eta extraído**" — eta YA está extraído; la mitad de la razón es
  historia.
- `PLUGIN_ARCHITECTURE_TESTS.md` §1: header de P-2 dice 🔴, su propio resumen
  §4 dice 🟢 (el código real: 🟢).

### 🟢 N-11 — Regex del gate de workflows no admite dígitos en ids

`test_manifest_orchestration_consistency.py:76-79`:
`^src\.plugins\.(?P<plugin>[a-z_]+)\.workers\.(?P<name>[a-z_]+)$` — el naming
oficial permite `[a-z0-9_]` (`hubara_agency/CLAUDE.md` §naming). Un plugin
`ads2` con workflow declarado fallaría el gate con un mensaje engañoso ("no
@workflow.defn found").

### 🟢 N-12 — Huecos menores de guard (inventario)

- Ningún test detecta un **dir backend huérfano** (`src/plugins/foo/` con
  código pero sin manifest → código muerto invisible).
- `wiring_intents.env_vars_required` del manifest no se valida contra el env
  real de compose/k8s (PM-10 "contenido k8s puede driftear" sigue 100% abierto).
- No hay P-13 (parity de ids cross-stack como test).
- `chatKeys.all = ["chat-inbox"]` (raíz de query key con nombre de UI, no de
  entity) — cosmético, sin colisiones reales (verificado: las 11 raíces son
  únicas).

---

## §3. Re-verificación de los hallazgos previos (F# / PM#) — estado HOY

| Hallazgo previo | Estado 2026-06-09 | Evidencia |
|---|---|---|
| F1 split plugins (eta/ads/evals) | ✅ **CERRADO en código** para ads (`plugins/ads/{api,frontend}`) y eta (`plugins/eta/{agent/eta,workers,api,frontend}`); evals disuelto (UI en agents_admin; worker `sales_eval` en chats por decisión). ⚠️ Residuo funcional: N-2 | manifests + dirs verificados |
| F2 entities centrales | ❌ **ABIERTO** — 11 entities en `src/entities/`, cero `frontend/entities/` por plugin | `ls src/entities/` |
| F3 orders→eta sin declarar | ✅ cerrado por diseño (transitions soft + P-7) **pero inerte en deploy** (N-1) | `dispatcher.py:179`; deploy sin env |
| F4 íconos centrales | ⚠️ PARCIAL — candado P-12 puesto (`test_plugin_icons.arch.test.ts`); el mecanismo de contribución NO existe (glifo nuevo = editar `Icon.tsx` PROTECTED) | Toolbar `resolveIcon` + Icon.tsx |
| F5 chats mega-plugin | ⚠️ REDUCIDO — quedó sales + remarketing + sales_eval (3 workers, `chats/plugin.yaml:43-421`) + 4 routers (sales/dashboard/handoff/evals `:28-36`) | manifest |
| F6 register_order en chats | ❌ ABIERTO (decisión pendiente; tool del agente) | — |
| F7 agents_admin sin filtro | ❌ ABIERTO — `service.py:176` itera todos; `agentic` sigue ignorado (PM-3) | `agents_admin/service.py:176,189` |
| F8 chats→entity order | ❌ ABIERTO — ahora con endpoint y runtime-impact mapeados (N-3/N-4) | `ConfirmPaymentAction.tsx:6` |
| F9 pluginProps bag | ❌ ABIERTO — 12 props / 5 slots de selección en `Dashboard.tsx:106-119` | verificado |
| F10 cruiser plugins→features | ✅ CERRADO — `plugins-no-features` (`.dependency-cruiser.cjs:126`) | corre en test:arch |
| F11 íconos sin test | ✅ CERRADO — P-12 | test verde hoy |
| F12 depends_on sin enforcement | ❌ ABIERTO — P-6 inexistente; todos los `depends_on: []` | loaders |
| F13 consistencia cross-stack | ⚠️ PARCIAL — P-2 verde + registry test; falta P-13 explícito y guard de dir-huérfano (N-12) | — |
| F14 invariantes solo de mecanismo | ⚠️ PARCIAL — se sumaron P-1/2/3/4/14(forma) + orchestration-consistency; faltan los de isolation runtime (P-6, P-15..P-19) | `tests/architecture/` |
| PM-2 template duplicado | ❌ ABIERTO sin guard (P-18 no existe) | `load_or_start_sales_session.py:179` ↔ `orders/plugin.yaml:59` |
| PM-3 `agentic` decorativo | ❌ ABIERTO (P-17 no existe) | `service.py` no lo lee |
| PM-4 agents_admin asimétrico | ❌ ABIERTO (= F7) | ídem |
| PM-6 workspace sin test | ❌ ABIERTO (P-15 no existe); 3 paths `dashboard.workspace` manuales en manifests | chats/eta manifests |
| PM-10 k8s contenido drift | ❌ ABIERTO — solo paridad de EXISTENCIA (`test_premortem_invariants.py:88-133`) | — |
| PM-11 PROTECTED ≠ meta-gate | ❌ ABIERTO y **peor**: también en backend (N-8) | `conftest.py:126`, `helpers.ts:82` |
| PM-12 xfail rot | ❌ ABIERTO (N-10) | `test_plugin_contract.py:123` |
| PM-13 wiring ≠ comportamiento | ⚠️ PARCIAL — `test_manifest_orchestration_consistency.py` cubre lo estático; smoke funcional sigue faltando | — |

---

## §4. Scorecard por plugin (2026-06-09)

| Plugin | Backend propio | Frontend | Entity propia | Toggle real (deploy) | Notas |
|---|---|---|---|---|---|
| **catalog** | ✅ | ✅ | ❌ (`catalog-sync` central) | ❌ (N-1) | sano salvo entity |
| **system_map** | ✅ (api/domain) | — (excluido, correcto) | — | ❌ (N-1, sin worker igual) | sano |
| **agents_admin** | ✅ service | ✅ | ❌ (`agent`, `eval-*` centrales) | ❌ | F7/PM-3; consume `/api/chats/evals` (N-3) |
| **orders** | ✅ | ✅ | ❌ (`order` central) | ❌ | transitions→eta soft OK |
| **chats** | ✅ (3 workers, 4 routers) | ✅ | ❌ (chat/session/message/handoff centrales) | ❌ | dueño de facto del edge WhatsApp (N-2); consume entity `order` |
| **eta** | ✅ extraído | ✅ | ❌ (`tracked-order` central) | ❌ y **funcionalmente atado a chats para inbounds** (N-2) | residuo PM-2 |
| **ads** | ✅ extraído | ✅ | ❌ (`ads-campaign` central) | ❌ | el más limpio post-extracción |

Cero imports cross-plugin en ambos stacks (verificado por AST backend + dep-cruiser frontend). La fuga sistémica común sigue siendo la de siempre: **entities + íconos en paths centrales**, más la nueva fila: **ningún plugin es togglable en el deployment real** (N-1).

---

## §5. Implicancia multitenant

El modelo "cada tenant = distinto set de plugins" requiere, en orden de dureza:

1. **N-1** resuelto (sin toggle en deploy no hay tenants con sets distintos —
   hay UN deployment con todo).
2. **N-5** decidido (build FE por tenant vs registry runtime).
3. **N-2** resuelto o aceptado (chats como "core obligatorio" de cualquier
   tenant conversacional — si se acepta, hay que DECLARARLO, no dejarlo
   implícito).
4. F2/F4/N-3/N-4 (entities/íconos/casts) para que agregar el plugin del tenant
   N no toque PROTECTED ni rompa al tenant N-1.
5. F7 (agents_admin filtrado) para que un tenant no VEA los agentes de otro.

El detalle de ejecución está en
[PLUGIN_REFACTOR_PLAN_fable.md](PLUGIN_REFACTOR_PLAN_fable.md).

---

**Fin.** Todo `file:line` de este informe fue verificado de primera mano sobre
HEAD `9c21fe7`; los gates se EJECUTARON, no solo se leyeron. El código vivo
gana sobre este doc también.
