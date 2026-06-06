# Plugin Contract — AgencyHubara (el desacople perfecto)

> **Qué es este documento.** La **ley** para construir plugins en AgencyHubara.
> Es prescriptivo, no histórico. Está escrito para volverse un **agente
> enforcement**: cada regla es chequeable, cada paso es seguible, cada
> anti-pattern es detectable, y cada uno apunta a su test de arquitectura
> (ver [PLUGIN_ARCHITECTURE_TESTS.md](PLUGIN_ARCHITECTURE_TESTS.md)).
>
> **Por qué existe.** La auditoría code-first
> ([PLUGIN_ISOLATION_AUDIT.md](PLUGIN_ISOLATION_AUDIT.md)) encontró que el
> *mecanismo* de plugins es sólido pero las *fronteras* están mal trazadas:
> backends viviendo dentro de otros plugins, entities e íconos en paths
> centrales PROTECTED, dependencias runtime sin declarar. Este contrato define
> el estado objetivo (el "desacople perfecto") y reemplaza las partes
> aspiracionales/desactualizadas de [PLUGIN_ARCHITECTURE.md](PLUGIN_ARCHITECTURE.md)
> (que sigue siendo válido para D1–D11; este doc corrige R1–R3 y los amplía).
>
> **Estado.** PARCIALMENTE IMPLEMENTADO (PR #49). Hecho: candados backend+frontend
> (P-1/2/3/4/12/14 + dispatcher-skip P-7), extracciones `ads` + `eta`, decisión
> `evals` per-agente. Pendiente: P-6 (enforce `depends_on` al boot), P-9/P-11
> (entities por-plugin + cast), y los guards derivados del pre-mortem (§9: P-15..P-19).
> Las lecciones REALES de las extracciones están en [§9 Pre-mortem](#9-pre-mortem--modos-de-fallo-de-una-extracción).

---

## §1. Los dos invariantes (todo se deriva de acá)

Un plugin es una **vertical capability slice** — una funcionalidad completa
(frontend + API + opcional agente/worker + jobs) que el sistema posee de punta a
punta. Dos invariantes la gobiernan. Si una decisión los viola, **la decisión
está mal — no el invariante.**

### INV-1 — Aislamiento aditivo (REQ-1: "agregar no toca producción")

> **Agregar un plugin = crear archivos ÚNICAMENTE bajo `plugins/<id>/` (en
> AMBOS stacks) + su `plugin.yaml`. CERO ediciones a cualquier archivo fuera de
> `plugins/<id>/`.** Sin excepciones: ni entities, ni íconos, ni `Dashboard.tsx`,
> ni barrels, ni providers, ni `constants.py`, ni `index.css`, ni `main.py`.

Corolario: si para agregar tu plugin necesitás tocar un archivo central, **el
mecanismo central está mal** (le falta un punto de contribución) — se arregla el
mecanismo, no se toca el archivo.

### INV-2 — Toggle simétrico (REQ-2: "apagar no rompe, solo desaparece")

> **La presencia de un plugin la gobierna SOLO `ENABLED_PLUGINS`, de forma
> simétrica en todos los stacks (api + workers + frontend). Apagarlo remueve TODA
> su superficie y no rompe nada. Prenderlo enciende TODA su superficie. No existe
> estado parcial ni asimétrico.**

Corolario (mata el anti-pattern #1): **TODO el código que implementa el
comportamiento del plugin X vive bajo `plugins/X/`** — el dir cuyo nombre == el
`id` del manifest == el token de `ENABLED_PLUGINS`. El backend de X **nunca** vive
dentro de Y.

---

## §2. Anatomía de un plugin (qué vive dónde)

```
plugins/<id>/                         ← TODO el plugin, ambos stacks, self-contained
│
├── plugin.yaml                       ← el contrato (frontend.* + api.* + agent.* + depends_on + contributes)
│
├── (backend) hubara_agency/src/plugins/<id>/
│   ├── api/            __init__.py expone `router` (FastAPI)  — prefix /api/<id>
│   ├── agent/<worker>/ ★ UN subdir POR worker agéntico; su nombre == `agent.workers[].name`.
│   │                     Dentro: workflows/ + activities/ + tools/ + use_cases/ + workspace/.
│   │                     CONVENCIÓN LOAD-BEARING (no cosmética): el gate
│   │                     `test_workflow_classes_exist_in_code` busca el workflow en
│   │                     `src.plugins.<id>.agent.<worker>.workflows.*`. Un plugin de UN
│   │                     solo agente IGUAL anida (`eta/agent/eta/`), no aplana a `agent/`.
│   ├── workers/<worker>.py  cada worker expone `async def main()` + `get_task_queue("<id>","<worker>")`
│   ├── shared/contracts/   eventos que EMITE (frontera para el dispatcher)
│   └── (todo importa de platform/ o de sí mismo — NUNCA de otro plugin)
│
└── (frontend) frontend_dashboard/src/plugins/<id>/frontend/
    ├── index.ts        export default { Page }   (sin esto, plugins-sync lo skipea)
    ├── pages/          composición page-level del plugin
    ├── features/       UX/lógica del plugin
    ├── entities/       ★ las entities de dominio del plugin VIVEN ACÁ (no en src/entities/)
    └── (importa de @/shared, sus propias entities, y entities shared curadas — NUNCA de otro plugin)
```

**Cambios respecto del estado actual (lo que el desacople perfecto exige):**

1. **Entities por-plugin + casting.** Las entities de dominio (`order`, `chat`,
   `tracked-order`, `eval-trend`, …) se mudan de `src/entities/<x>/` (PROTECTED,
   central) a `plugins/<id>/frontend/entities/<x>/`. **NO hay `src/shared/entities/`
   compartido.** Cuando un plugin necesita datos de otro, define su entity local +
   un **cast declarado** del contrato del provider a esa entity local (canal 3).
   `src/entities/` central queda **vacío**. Hoy NINGÚN plugin tiene `frontend/entities/`.
2. **Íconos contribuidos por el plugin.** El plugin trae sus glifos en su propio
   `frontend/` y el codegen los agrega al registry generado. `Icon.tsx` queda
   como base compartida pero agregar un plugin con ícono nuevo **no lo edita**.
3. **El backend del plugin vive bajo su propio dir.** Nada de `chats.api.eta` o
   `chats.workers.sales_eval` sirviendo a otro plugin.

---

## §3. Los 4 (y solo 4) canales de comunicación cross-plugin

Un plugin **nunca** conoce a otro directamente. Todo acoplamiento pasa por uno de
estos cuatro canales — cualquier otro es una violación.

| # | Canal | Stack | Regla |
|---|---|---|---|
| 1 | **Platform ports** | backend | Los plugins dependen HACIA ABAJO de abstracciones en `platform/`; nunca de lado hacia otro plugin. (Ej: `orders` y `eta` consumen `platform/orders` query port.) |
| 2 | **Eventos declarativos** | backend | Flujo runtime cross-plugin vía `emits`/`transitions` del manifest + dispatcher genérico. El dispatcher **SKIPEA todo target cuyo plugin no esté habilitado** (consumidor ausente = no-op limpio). Todo target cross-plugin se declara en `depends_on`. |
| 3 | **Entities propias + casting declarado** | ambos | NINGUNA entity es compartida. Cada plugin **posee** sus entities. Cuando el consumidor C necesita datos del provider P, C define su PROPIA entity local + declara un **cast** (P → entity-local-de-C) en el bloque `consumes:` de su manifest. El cast es el ÚNICO punto de acoplamiento y es **swappable**: cambiás el provider (otro `orders` con otra entity) → ajustás solo el cast, C queda intacto. |
| 4 | **Contribution points** | ambos | sidebar / sections / widgets / íconos / agentes declarados en el manifest, agregados por los loaders/codegen. El plugin contribuye; el shell agrega. |

**Prohibiciones duras (los "nunca"):**

- ❌ El código backend/agent/api/worker del plugin X **nunca** vive bajo `plugins/Y/`.
- ❌ Imports directos cross-plugin: `from src.plugins.Y` dentro de X (Python); `@plugins/Y` dentro de X (TS).
- ❌ `from src.plugins.*` en cualquier parte de `src.platform.*`.
- ❌ Editar cualquier archivo fuera de `plugins/<id>/` para agregar el plugin X.
- ❌ Dependencia cross-plugin sin declarar — todo acoplamiento (event target, API consumida, shared entity) va en `depends_on` y se valida al boot.
- ❌ Llamadas HTTP del frontend de X a `/api/<otro>/*` — el frontend de X habla a su propio `/api/<id>/*` + endpoints de platform/shared.
- ❌ Adoptar la entity de otro plugin como propia (importarla, o un `src/shared/entities` compartido). Cross-plugin data SIEMPRE vía **cast declarado** (canal 3) — entity local + mapper estipulado en config.

---

## §4. Las reglas duras (invariantes chequeables)

Cada regla tiene su test (P-#) en [PLUGIN_ARCHITECTURE_TESTS.md](PLUGIN_ARCHITECTURE_TESTS.md).

| Regla | Enunciado | Previene | Test |
|---|---|---|---|
| **P-SELF** | Todos los módulos referenciados por el manifest de X (`api.python_module`, `api.legacy_routers[].module`, `agent.python_module`, `agent.workers[].module`) empiezan con `src.plugins.<id>.`. | Manifest declarando módulos de otro plugin | P-1 |
| **P-OWN** | Todo el comportamiento de X vive bajo `plugins/X/` en ambos stacks. Ningún plugin frontend "frontend-only" depende del backend de otro plugin sin declararlo. | Split plugins (eta/ads) | P-2, P-9 |
| **P-NOXIMPORT** | Ningún módulo bajo `src/plugins/X/` importa `src.plugins.Y` (Y≠X). Única frontera: `shared/contracts/` propio. | Cross-plugin import backend | P-3 |
| **P-PLATFORM** | Ningún módulo bajo `src/platform/` importa `src.plugins.*`. | Platform→plugin (R-DIP #9) genérico | P-4 |
| **P-DEPS** | `depends_on` = solo deps DURAS (providers de un `consumes`/cast — el plugin no funciona sin ellas). Las transitions cross-plugin son SOFT: las declara la transition + el dispatcher las skipea si el target está apagado (P-SKIP); **NO** van en `depends_on`. | Mezclar soft/hard; coupling duro oculto | P-7, P-14 |
| **P-ENABLED** | Al boot, para el `ENABLED_PLUGINS` activo, todo `depends_on` de un plugin habilitado también está habilitado (fail-fast). | Habilitar X sin su dep → roto silencioso | P-6 |
| **P-SKIP** | El dispatcher skipea toda transition cuyo `target_plugin`/`target_worker` no esté habilitado (no dispara al vacío). | orders sin chats/eta → ETA al vacío | P-7 |
| **P-FECROSS** | Ningún archivo bajo `src/plugins/X/frontend/` importa `@plugins/Y`, `@/features/*`, `@/pages/*`, `@/app/*`, ni la entity poseída por otro plugin. | Cross-plugin/cross-layer frontend | P-10 |
| **P-ENTITY** | Las entities de dominio viven en `plugins/<id>/frontend/entities/`; `src/entities/` central queda vacío (sin shared entities). | Entities en PROTECTED central (F2) | P-11 |
| **P-CAST** | Todo dato cross-plugin se obtiene vía un `consumes:` declarado (`provider` + `contract` + `into` + `cast`); ningún plugin adopta la entity de otro. El cast es el único punto swappable. | Acoplamiento directo de entities | P-14 |
| **P-ICON** | Todo ícono referenciado por un manifest resuelve desde el registry (base + contribuciones del plugin); ninguno cae al fallback `bot`. | Íconos en PROTECTED central (F4) | P-12 |
| **P-PARITY** | El set de plugin ids es coherente entre stacks: todo plugin con superficie backend tiene su dir backend propio; todo manifest frontend referencia solo su propio backend. | Asimetría cross-stack (F1/F13) | P-9, P-13 |
| **P-NOEDIT** | Agregar un plugin no produce diff fuera de `plugins/<id>/` (salvo el registry generado, que es gitignored). | INV-1 global | P-8 |

---

## §5. El desacople perfecto de los casos actuales

Estado vivo (main e4d95a6): `eta` y `ads` son plugins frontend con backend dentro
de `chats`; el concern de evals/"Calidad LLM" quedó como frontend dentro de
`agents_admin` con backend (`sales_eval`) aún en `chats`. El operador decidió:
**`eta` y `ads` deben poder togglearse independientes de `chats`** → se EXTRAEN a
plugins propios. **`evals` NO se extrae: queda acoplado a los AGENTES** — cada
agente tendrá sus propios evals (es un concern per-agente, no un plugin
standalone). `ads` ya está extraído (commit `a5bc586`).

### §5.1 La pieza clave: el ruteo de inbounds (NO `platform/conversation`)

> **⚠️ CORRECCIÓN POST-EXTRACCIÓN (eta extraído, commit `d3a6289`).** Este §
> asumía que extraer `eta` exigía construir un gran `platform/conversation/`
> (session runtime + vault + WhatsApp edge). **FALSO** — verificado leyendo el
> worker: el runtime conversacional YA está compartido en `exoclaw_temporal`
> (build_prompt/record_turn/llm_chat) + `src.platform.*` (vault, whatsapp,
> session_history, dispatcher). `eta` NO importaba sales/remarketing. Extraer
> `eta` fue un `git mv` + repoint (igual que `ads`), NO una capability nueva.
> **La ÚNICA coupling real con `chats` es el ruteo de inbounds** (abajo), y se
> DIFIRIÓ (Opción A): `chats` sigue conteniendo `ROUTE_ETA` +
> `workflow_id = f"eta-{session_id}"`. Eso es **residuo conocido** (ver
> [§9 pre-mortem PM-2](#9-pre-mortem--modos-de-fallo-de-una-extracción)) — `eta`
> es togglable pero NO 100% aislado todavía. El route-registry de abajo sigue
> siendo el end-state limpio, pero es **diferible**, no bloqueante.

`eta` parecía el caso difícil porque es un **agente conversacional que comparte la
sesión de WhatsApp** con `sales` (un cliente rutea sales→eta→sales). Ese ruteo
está hardcodeado en `chats` (`constants.ROUTE_ETA` + `load_or_start_sales_session`
que rutea inbounds a `eta-{session}`). El end-state limpio (diferido) lo vuelve
**platform + declarativo**:

- Mover a `platform/conversation/` la scaffolding hoy implícitamente compartida:
  el runtime session-based de exoclaw (workspace + memory window + turn loop), el
  `FilesystemMetadataStore` (vault), y la **edge de WhatsApp** (send/ingest).
- **Route registry declarativo:** en vez de `chats` hardcodeando `ROUTE_ETA`, cada
  plugin-agente DECLARA en su manifest qué `active_route` posee
  (`agent.owns_route: eta`). El **inbound router de platform** lee los manifests,
  mapea `active_route → (plugin, workflow_id_template)`, y rutea el inbound al
  workflow dueño. **Ningún agente nombra a otro.** El handoff entre agentes sigue
  siendo declarativo (manifest transitions + dispatcher).

Resultado: `sales`, `remarketing`, `eta` son plugins-agente delgados sobre
`platform/conversation`, cada uno dueño de su ruta, togglables independientemente.

### §5.2 Extracciones concretas

| Plugin | De dónde sale hoy | A dónde va | Consume (platform) | Declara |
|---|---|---|---|---|
| **eta** ✅ EXTRAÍDO (`d3a6289`) | `chats.workers.eta` + `chats.api.eta` + `chats.agent.eta` | `plugins/eta/{agent/eta,workers,api}` (frontend ya estaba) | `exoclaw_temporal` (session runtime) + `src.platform.*` (vault, whatsapp, dispatcher, `platform/orders` query port) — **NO** `platform/conversation` (no existe; ver §5.1) | `agentic: true`; worker `eta`; es TARGET de las transitions SOFT de `orders`. **`depends_on: []`** (las transitions son soft — P-DEPS; el query port es platform, no un cast). `owns_route` NO implementado (Opción A: el ruteo quedó en `chats` — residuo PM-2) |
| **ads** ✅ EXTRAÍDO (`a5bc586`) | `chats.api.ads` | `plugins/ads/{api,frontend}` | `platform/whatsapp` (lee origin/last_touch del vault) | sin agente; `depends_on: []` |
| **evals** | — **NO se extrae** | se queda con el agente: `sales_eval` en `chats` (el eval del agente de ventas); el eval de `eta` irá dentro de `plugins/eta` cuando se extraiga | — | per-agente. La UI "Calidad LLM" (`agents_admin`) es el **plano de gestión** que muestra el eval de cada agente — a formalizar server-side (decisión 1) |

`chats` queda como el **núcleo conversacional**: webhook ingest + inbox/SSE +
handoff + `sales` + `remarketing` (+ el eval `sales_eval` del agente de ventas).
**`sales` y `remarketing` van SIEMPRE juntos — están completamente acoplados por el
funnel y nunca se separan** (decidido). Eso es lo que `chats` genuinamente posee;
`eta` y `ads` se extraen, `evals` queda per-agente.

**Decisiones cerradas (2026-06-05):**
1. **evals NO es un plugin** — queda acoplado al agente (cada agente tiene su
   propio eval: `sales_eval` en `chats`; el de `eta` irá en `plugins/eta`). La UI
   "Calidad LLM" (`agents_admin`) es el **plano de gestión** que muestra el eval de
   cada agente. Consumir el eval de un agente debe formalizarse server-side (el
   backend de `agents_admin` agrega los evals y sirve `/api/agents_admin/…`, igual
   que `discover_agents` escanea manifests) para no violar P-OWN. Open design point
   hasta que un 2º agente tenga eval propio.
2. **Entities:** cada plugin posee la suya + cast declarado (§5.3) — sin shared.
3. **sales+remarketing** permanecen juntos dentro de `chats` (acoplados por el funnel).

### §5.3 Entities (casting declarado) e íconos — el fix de INV-1 en frontend

**Cada plugin posee su entity; cross-plugin vía cast declarado (canal 3).** Se mudan
a `plugins/<id>/frontend/entities/`: `order`→orders, `tracked-order`→eta,
`chat`/`session`/`message`/`handoff`→chats, `ads-campaign`→ads,
`eval-trend`/`eval-candidate`→agents_admin (evals per-agente: las views viven en el
plano de gestión), `agent`→agents_admin, `catalog-sync`→catalog.
`src/entities/` central queda **vacío** — no hay shared entities.

El caso `order` (lo necesitan `orders` Y el canvas de pago de `chats`) es el ejemplo
canónico del **cast**:
- `orders` posee la entity `order` y publica un **contrato estable** versionado (`order@v1`).
- `chats` define su entity LOCAL (`order-ref`, solo los campos que el canvas usa) y
  declara el cast en su manifest:
  ```yaml
  # chats/plugin.yaml
  depends_on: [orders]
  consumes:
    - provider: orders
      contract: order@v1          # lo que orders promete (versionado)
      into:     order-ref         # entity LOCAL de chats
      cast:     ./casts/order_to_ref   # el ÚNICO punto de acoplamiento — swappable
  ```
- **El cast corre server-side** (backend de chats): chats consume `orders` por canal 1
  (platform port) o por el contrato publicado, castea a `order-ref`, y lo sirve bajo
  `/api/chats/...`. El frontend de chats solo ve `order-ref` → se preserva P-FECROSS.
- **Swap multitenant:** apagar `orders` y prender `orders-shopify` con otra entity →
  se actualiza SOLO `cast: order_to_ref`, `chats` no se toca. Es exactamente el
  mecanismo del [Eje B multitenant](MULTI_TENANT_COMMERCE_ARCHITECTURE.md): el cast
  es donde vive la "combinación" del provider, aislado del consumidor.

**Íconos:** el codegen `plugins-sync.ts` agrega los glifos que cada plugin provee en
su `frontend/` al registry generado; `Icon.tsx` queda como set base. Un plugin con
ícono nuevo lo trae consigo — cero edición de `Icon.tsx`.

### §5.4 Dispatcher + depends_on (el fix de INV-2 en backend)

- `dispatch_event_activity` lee `ENABLED_PLUGINS` y **skipea** transitions cuyo
  `target_plugin`/`target_worker` no esté habilitado (P-SKIP). → `orders` corre
  standalone; sin `eta`, las notificaciones simplemente no ocurren (degradación
  limpia), sin workflows pending al vacío.
- Los loaders (`main.py`, `run_workers.py`, `plugins-sync.ts`) **validan
  `depends_on` al boot** (P-ENABLED): habilitar X exige habilitar sus deps, o
  fail-fast con mensaje claro. `depends_on` deja de ser decorativo.

---

## §6. Cómo construir un plugin nuevo (checklist — la "definition of done")

El agente enforcement corre esta lista. Cada item es verificable.

**Backend (`hubara_agency/src/plugins/<id>/`):**
1. ☐ Todo el código vive bajo `src/plugins/<id>/`. Imports solo de `platform/` o de sí mismo (P-NOXIMPORT, P-SELF).
2. ☐ `api/__init__.py` expone `router` (si aporta HTTP), prefix `/api/<id>`.
3. ☐ Cada worker expone `async def main()` + declara `task_queue: queue-<...>` única (invariante existente).
4. ☐ Flujo cross-plugin SOLO vía `emits`/`transitions` (manifest) + eventos en `shared/contracts/`. Ningún import del target (P-NOXIMPORT).
5. ☐ Toda dep cross-plugin declarada en `depends_on` (P-DEPS).
6. ☐ Si es agéntico conversacional: consume `platform/conversation`, declara `agent.owns_route` si posee una ruta.

**Frontend (`frontend_dashboard/src/plugins/<id>/frontend/`):**
7. ☐ `index.ts` con `export default { Page }`.
8. ☐ Las entities del dominio viven en `frontend/entities/` (P-ENTITY). Solo se importan las propias; los datos de otro plugin entran por un `consumes:`/cast declarado (P-CAST), nunca importando la entity ajena.
9. ☐ Cero imports de `@plugins/<otro>`, `@/features`, `@/pages`, `@/app` (P-FECROSS).
10. ☐ Sidebar/sections/íconos declarados en `plugin.yaml > frontend.contributes`. Íconos nuevos provistos por el plugin (P-ICON). Cero edición de `Icon.tsx`.
11. ☐ El frontend llama solo a `/api/<id>/*` + platform/shared (no a `/api/<otro>/*`).

**Manifest + toggle:**
12. ☐ `id` == nombre del dir == token de `ENABLED_PLUGINS`, en AMBOS stacks (P-PARITY).
13. ☐ Agregar el plugin no produce diff fuera de `plugins/<id>/` salvo el registry generado (P-NOEDIT).
14. ☐ `ENABLED_PLUGINS` sin este plugin → desaparece de todos los stacks sin romper nada (P-ENABLED, P-SKIP).

**Gates a correr (todos verdes):**
```bash
cd hubara_agency && uv run pytest -m architecture && uv run lint-imports
cd frontend_dashboard && npm run test:arch && npx tsc -b
```

---

## §7. Catálogo de anti-patterns (síntoma → fix → test)

| # | Anti-pattern | Síntoma vivo | Fix | Test |
|---|---|---|---|---|
| **AP-1 Split plugin** | Backend de X dentro de Y | `eta` backend en `chats` (`ads` ✅ extraído; `evals` queda per-agente por decisión) | §5.2 extracción a `plugins/<id>/` + `platform/conversation` | P-1, P-2, P-9 |
| **AP-2 Central entity** | Entity de dominio en PROTECTED `src/entities/` | TODAS las entities (10) centrales; el merge agregó `eval-trend/` central | §5.3 entities por-plugin + cast declarado | P-11, P-14 |
| **AP-3 Hidden coupling** | Dep DURA sin declarar / target soft que dispara al vacío | `orders` 5 transitions →`chats/eta` (soft, **ya resuelto** por dispatcher-skip) | dispatcher-skip ✅ + `depends_on` solo duras | P-6, P-7, P-14 |
| **AP-4 Central icon** | Glifo nuevo edita `Icon.tsx` PROTECTED | `resolveIcon` contra registry central | §5.3 íconos contribuidos | P-12 |
| **AP-5 Mega-plugin** | Un plugin con N dominios togglables juntos | `chats` = 4 workers + 6 routers | extraer (§5.2); `chats` = núcleo conversacional | P-2, P-9 |
| **AP-6 Cross-stack asymmetry** | Frontend gateado por un id, backend por otro | `eta` FE por `eta`, backend por `chats` | P-PARITY + extracción | P-9, P-13 |
| **AP-7 Shell hardcode** | El shell hardcodea state/listas por plugin | `pluginProps` en `Dashboard.tsx` | Context/registry-driven props | P-8, P-10 |
| **AP-8 Unfiltered enumeration** | Backend enumera plugins ignorando `ENABLED_PLUGINS` | `agents_admin.discover_agents()` (destapado por la extracción de eta — PM-4) | filtrar por enabled en runtime de prod | P-6, P-17 |
| **AP-9 Rename orphan** | Renombrar (plugin,worker) deja el container/queue viejo activo | `chats-eta`→`eta-eta` con `queue-eta-agent` intacta; orphan sin `--remove-orphans` (PM-1) | `down`/`--remove-orphans`; verificar si la queue cambió | P-16 |
| **AP-10 Tolerant string misroute** | Coupling cross-plugin por string que hace fallback en vez de crashear | `ROUTE_ETA`/`eta-{session}` duplicado chats↔orders, sin test (PM-2) | route registry declarativo, o guard de consistencia del template | P-18 |
| **AP-11 Schema-code mismatch** | El schema del manifest documenta un gate que el código ignora | `agentic` (schema lo gatea; `service.py` lo ignora) (PM-3) | test que ate schema↔código, o el código honra el flag | P-17 |

---

## §8. Glosario

- **Vertical capability slice** — un plugin: una funcionalidad completa poseída de
  punta a punta, self-contained y togglable.
- **INV-1 / INV-2** — los dos invariantes (§1): aislamiento aditivo + toggle simétrico.
- **Los 4 canales** — platform ports · eventos declarativos · entities propias + casting declarado · contribution points (§3). Único acoplamiento cross-plugin permitido.
- **Cast / ACL (anti-corruption layer)** — un mapper declarado en el `consumes:` del manifest que traduce el contrato publicado del provider a la entity local del consumidor. Único punto de acoplamiento cross-plugin de datos, y es swappable: cambiar el provider = cambiar el cast, el consumidor queda intacto (§5.3).
- **Split plugin** — anti-pattern donde el backend de un plugin vive dentro de otro (AP-1). El problema central de hoy.
- **`platform/conversation`** — capability compartida (runtime de sesión + vault + WhatsApp edge + route registry) que permite que `sales`/`remarketing`/`eta` sean plugins-agente independientes (§5.1).
- **Route registry** — mecanismo declarativo donde cada plugin-agente declara
  `agent.owns_route` y platform rutea inbounds sin que un agente nombre a otro.
- **Contribution point** — lugar conocido (sidebar/section/icon/widget/agent) donde el plugin aporta y el shell agrega, sin que el shell conozca el plugin.

---

## §9. Pre-mortem — modos de fallo de una extracción

> **Qué es.** Las lecciones REALES del refactor de aislamiento (extracciones `ads`
> + `eta`), escritas como un pre-mortem: *"el PR mergeó y algo se rompió en
> producción — ¿qué fue?"*. Cada modo de fallo es algo que **pasó o casi pasa** en
> este branch, verificado contra el código vivo. Para el agente enforcement: esto
> es lo que tenés que mirar ANTES de dar OK a una extracción. Cada PM-# apunta a su
> guard (test existente o propuesto en
> [PLUGIN_ARCHITECTURE_TESTS.md](PLUGIN_ARCHITECTURE_TESTS.md)).

**La meta-lección (vale por todas):** una extracción "verde" a nivel de
**estructura** (imports limpios, manifests consistentes, AST resuelve) **NO
prueba** que el **comportamiento** sobreviva. La mayoría de estos PM-# son
acoplamientos que NINGÚN test de import/manifest atrapa porque viven en strings,
en convenciones, o en código que el gate no mira. Es el [gotcha #1 del
CLAUDE.md](../CLAUDE.md) ("verificá comportamiento, no schema") aplicado al refactor
de plugins.

| PM-# | Modo de fallo | Qué pasó / síntoma | Lección + guard |
|---|---|---|---|
| **PM-1** | **Rename de servicio deja huérfano** | El worker cambió de `(chats,eta)` a `(eta,eta)` → el service de compose pasó de `hubara-worker-chats-eta` a `hubara-worker-eta-eta`, pero la **task_queue NO cambió** (`queue-eta-agent`). `docker compose up` sin `--remove-orphans` deja el container VIEJO corriendo **código pre-extracción** sobre la MISMA queue → dos workers compiten, el huérfano sirve código stale. (k8s no sufre: el deployment `worker-eta` mantiene su nombre, solo cambia `command` → rolling update limpio.) | Tras renombrar (plugin,worker): `docker compose up --remove-orphans` (o `down` primero) es OBLIGATORIO local. Confirmá si la task_queue cambió: si cambió, los workflows in-flight en la queue vieja quedan **stranded**. |
| **PM-2** | **Coupling tolerante por string (el más peligroso)** | Opción A dejó `ROUTE_ETA` + `workflow_id = f"eta-{session_id}"` hardcodeado en `chats/.../load_or_start_sales_session.py`. El MISMO template `eta-{session}` vive DUPLICADO en el manifest de `orders` (`workflow_id_template: "eta-{event.session_id}"`). **No hay test que los ate.** Si uno cambia y el otro no, el inbound rutea a un workflow inexistente → el `except` cae a Sales **en silencio** (el cliente que hablaba con ETA recibe Ventas). Además "apagar eta" deja ese código muerto en `chats` (residuo INV-2). | Un coupling cross-plugin **tolerante** (que NO crashea, hace fallback) es peor que uno duro: falla silencioso. Si lo diferís, dejá un guard de consistencia (PM→ **P-18**) que ate las dos copias del template, y un TODO trackeado. `eta` NO está 100% aislado hasta cerrar esto. |
| **PM-3** | **Schema miente sobre el código (`agentic`)** | El schema dice "solo `agentic: true` aparece en `GET /api/agents_admin`". El código (`agents_admin/service.py`) **IGNORA `agentic`** — escanea TODOS los manifests por workers con bloque `dashboard:`. Puse `agentic: true` (correcto por contrato) pero no gobierna NADA. Poner `agentic: false` NO esconde el agente; un `dashboard:` perdido lo expone sin `agentic`. | Confiá en el CÓDIGO, no en el schema (gotcha #1). Guard propuesto **P-17**: `agentic: true` ⟺ el plugin tiene ≥1 worker con `dashboard:`. O el código filtra por `agentic`. Hasta entonces, `agentic` es decorativo. |
| **PM-4** | **`apagar` no es simétrico (la extracción lo DESTAPÓ)** | `agents_admin.discover_agents()` escanea todos los manifests del disco **ignorando `ENABLED_PLUGINS`** (F7/AP-8). Pre-extracción el `dashboard:` de eta vivía en `chats` (siempre on) → invisible. Post-extracción, con `ENABLED_PLUGINS` sin `eta`: desaparece la sección frontend (bien) pero **la card del Agente ETA SIGUE en el dashboard de Agents** (mal) → asimetría INV-2 viva. | Extraer un plugin puede **destapar** una asimetría latente en OTRO lugar. "Apagar no rompe" hay que verificarlo en TODAS las superficies (sección FE + card de agente + dispatcher + worker), no solo la que tocaste. Guard: **P-6** (enforce enabled) + agents_admin debe filtrar por `enabled_plugins()`. |
| **PM-5** | **Self-reference stringly-typed del worker** | El worker hardcodea su propia identidad: `get_task_queue("eta","eta")`. Tuve que cambiarlo de `("chats","eta")`. Si se me escapa: `WorkerNotDeclaredError` al boot (ruidoso, ok) — o peor, si un `(chats,eta)` stale resolviera, escucharía la queue equivocada (silencioso). Ningún test ata el `get_task_queue(p,w)` del worker a su dir de manifest. | Al extraer, grepeá la **auto-referencia** `(plugin,worker)` del worker, no solo imports cross-plugin. Guard propuesto **P-16**. |
| **PM-6** | **Path de workspace triple-codificado, sin test de existencia** | El workspace del agente vive en (a) `config/env.py` (relativo, `parents[1]/"workspace"` — sobrevive al move) y (b) el manifest `dashboard.workspace: .../eta/agent/eta/workspace` (string absoluto-desde-root, MANUAL). Tuve que actualizar (b) a mano. **NO hay test** que verifique que `dashboard.workspace` existe en disco → un path stale da una card de agente sin sus archivos de workspace, silencioso. | Guard propuesto **P-15**: todo `dashboard.workspace` resuelve a un dir existente. Preferí paths RELATIVOS computados (como `env.py`) sobre strings absolutos en el manifest. |
| **PM-7** | **El doc de anatomía contradecía el gate** | El §2 (antes de este PR) dibujaba `agent/` PLANO (`workflows + activities` directo). Pero `test_workflow_classes_exist_in_code` exige `agent/<worker>/workflows/`. El plan de extracción decía "aplanar a `eta/agent/`" → **rompió el gate**; tuve que reestructurar a `eta/agent/eta/`. Un plugin de UN agente IGUAL anida. | Arreglado en §2. Convención DURA: el subdir del agente == `agent.workers[].name`. El doc que guía al agente DEBE coincidir con el gate, o el agente programa lo incorrecto con confianza. |
| **PM-8** | **Cambio de path de API = breaking, sin alias** | `/api/chats/eta/*` → `/api/eta/*` sin alias de compat. Interno-only (grepeé: el único consumidor era la entity `tracked-order`), así que safe ACÁ. Pero cualquier consumidor fuera del repo (dashboards guardados, mobile, webhooks, Postman) se rompe silencioso. Trampa adjunta: el prefix se mueve al manifest → las rutas del router DEBEN soltar el segmento redundante (`/eta/tracked-orders` → `/tracked-orders`), o queda doble-prefix `/api/eta/eta/...`. | Extraer una API es un cambio **breaking**. Confirmá cero consumidores externos, o dejá un alias deprecado por una ventana. Y revisá el doble-prefix al mover el `prefix` al manifest. |
| **PM-9** | **"Extraído" tiene grados — backend sí, entity FE no** | El backend de `eta` quedó aislado, pero su entity frontend `tracked-order` SIGUE en el PROTECTED central `src/entities/` (P-11 diferido). Modificar la entity de eta TODAVÍA toca un path central → INV-1 sigue violado del lado FE. | "Extraído" ≠ "aislado". Backend-extraction ≠ full isolation. Trackeá explícitamente el acoplamiento central residual (la entity + el ruteo de PM-2) para que nadie crea que `eta` está 100% aislado. Guards: **P-11/P-14**. |
| **PM-10** | **Ruido de regenerar artefactos** | Regenerar `docker-compose.local.yml` **reordenó** servicios (eta se fue al final, tras chats) — un diff más grande que el cambio semántico. Riesgo: un cambio colateral se esconde en el ruido. (Contraste: los manifests k8s son **hand-maintained**, no generados del manifest → pueden driftear; el premortem-invariant solo testea EXISTENCIA `(plugin,worker)`, no paridad de CONTENIDO env/secrets.) | NUNCA edites a mano un archivo generado. Tras regenerar, DIFFeá y confirmá que el delta es exactamente el semántico esperado. Para k8s (hand-maintained), el contenido (env/secrets) puede driftear del manifest sin que ningún test lo cace. |
| **PM-11** | **Dos definiciones de "PROTECTED" que no coinciden** | `frontend_dashboard/CLAUDE.md` lista las entity-boundary (`entities/<id>/{api,contracts}.ts`) como PROTECTED, pero el **meta-gate** (`test_meta.arch.test.ts`) solo protege `src/test/architecture/`, `.dependency-cruiser.cjs`, `.archon/workflows/`, `.claude/skills/frontend-`. Edité `tracked-order/{api,contracts}.ts` y CI pasó. | El set ENFORZADO (meta-gate) es el real; el del doc es aspiracional/más amplio. El agente debe saber CUÁL "protected" bloquea CI de verdad antes de creer que necesita un ADR. |
| **PM-12** | **Rot de la razón del xfail** | El `reason` del xfail P-9 en `test_plugin_contract.py` todavía dice "eta sigue split" — ya falso. El test es PROTECTED → actualizar la razón pide `ARCH_CHANGE_APPROVED`. | Las razones de `xfail`/`skip` codifican estado de un momento y se pudren. Escribí razones que describan el **invariante**, no la lista de ofensores actual. |
| **PM-13** | **Consistencia de manifest ≠ comportamiento** | Verifiqué que las transitions `orders→eta/eta` RESUELVEN (targets existen, task_queue resuelve, workflow class por AST) pero NO corrí un test donde un `OrderStageChangedEvent` realmente ARRANQUE el workflow eta reubicado. Wiring consistente ≠ dispatch funcionando (gotcha #1). | La DoD de una extracción debe incluir un **smoke de comportamiento** (emitir el evento, assert que el workflow arranca en la queue nueva), no solo consistencia AST/manifest. Guard propuesto **P-19**. |

### §9.1 Checklist específico de EXTRACCIÓN (además del §6)

Cuando movés un plugin de adentro de otro (no es un plugin nuevo from-scratch), corré ADEMÁS:

- ☐ **Self-references del worker** repointeadas: `get_task_queue("<id>","<worker>")` apunta a la NUEVA ubicación (PM-5).
- ☐ **Convención `agent/<worker>/`** respetada — el agente anida bajo su worker-name, no aplanado (PM-7).
- ☐ **`dashboard.workspace`** del manifest actualizado al nuevo path Y existe en disco (PM-6).
- ☐ **Rutas de API** sueltan el segmento redundante al mover el `prefix` al manifest; cero doble-prefix; grep de consumidores del path VIEJO = 0 (PM-8).
- ☐ **Couplings residuales tolerantes** (ruteo por string, templates de workflow_id duplicados) identificados y trackeados; si se difieren, NO se afirma "100% aislado" (PM-2, PM-9).
- ☐ **Toggle-off verificado en TODAS las superficies** (sección FE, card de agente en agents_admin, dispatcher-skip, worker no arranca) — no solo la que tocaste (PM-4).
- ☐ **Artefactos generados** (compose) regenerados con el script, DIFFeados por colateral; k8s (hand-maintained) revisado a mano (PM-10).
- ☐ **`--remove-orphans`** al levantar compose si cambió el nombre del service (PM-1).
- ☐ **Smoke de comportamiento** del path cross-plugin afectado, no solo consistencia de manifest (PM-13).
- ☐ Confirmá que un fallo de gate es TUYO y no pre-existente: mirá QUÉ ítem falla y comparalo con HEAD antes de culpar tu cambio (caso `chats/sales_eval` k8s-parity).

---

**Fin del contrato.** El código vivo gana sobre cualquier `.md`. Este contrato
define el TARGET; los tests de [PLUGIN_ARCHITECTURE_TESTS.md](PLUGIN_ARCHITECTURE_TESTS.md)
lo hacen imposible de violar sin que CI lo cace.
