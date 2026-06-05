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
> **Estado.** PROPUESTO. Documenta el TARGET. La implementación (refactor de
> código + tests) es la fase siguiente.

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
│   ├── agent/          workflows + activities + tools + use_cases + workspace (si es agéntico)
│   ├── workers/        cada worker expone `async def main()`  — task_queue propia
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
| **P-OWN** | Todo el comportamiento de X vive bajo `plugins/X/` en ambos stacks. Ningún plugin frontend "frontend-only" depende del backend de otro plugin sin declararlo. | Split plugins (eta/ads/evals) | P-2, P-9 |
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
**`eta`/`ads`/`evals` deben poder togglearse independientes de `chats`** → se
EXTRAEN a plugins propios.

### §5.1 La pieza clave: `platform/conversation/` (capability compartida)

`eta` es el caso difícil porque es un **agente conversacional que comparte la
sesión de WhatsApp** con `sales` (un cliente rutea sales→eta→sales). Hoy ese
ruteo está hardcodeado en `chats` (`constants.ROUTE_ETA` +
`load_or_start_sales_session` que rutea inbounds a `eta-{session}`). Para extraer
`eta` sin que `chats` lo nombre, el ruteo debe volverse **platform + declarativo**:

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
| **eta** | `chats.workers.eta` + `chats.api.eta` + `chats.agent.eta` (+ shell vacío `plugins/eta/__init__.py`) | `plugins/eta/{agent,workers,api,frontend}` | `platform/conversation` + `platform/orders` (query port) | `agent.owns_route: eta`; es TARGET de las transitions de `orders`; `depends_on: [orders]` (consume sus eventos) |
| **ads** | `chats.api.ads` | `plugins/ads/{api,frontend}` | `platform/whatsapp` o `platform/attribution` (lee origin/last_touch del vault) | sin agente; `depends_on: []` |
| **evals** | `chats.workers.sales_eval` + `chats.api.evals` + `chats.agent.sales_eval` | `plugins/evals/{workers,api}` (backend) | `platform/conversation` (lee vault) + judge LLM | la UI "Calidad LLM" se queda en `agents_admin` → `agents_admin.depends_on: [evals]` + consume `/api/evals/*` (decidido) |

`chats` queda como el **núcleo conversacional**: webhook ingest + inbox/SSE +
handoff + `sales` + `remarketing`. **`sales` y `remarketing` van SIEMPRE juntos —
están completamente acoplados por el funnel y nunca se separan** (decidido). Eso es
lo que `chats` genuinamente posee; `eta`/`ads`/`evals` se extraen.

**Decisiones cerradas (2026-06-05):**
1. **evals UI** se queda en `agents_admin` (sin frontend propio); `agents_admin`
   declara `depends_on: [evals]` y consume `/api/evals/*` (canal 2/4 declarado).
2. **Entities:** cada plugin posee la suya + cast declarado (§5.3) — sin shared.
3. **sales+remarketing** permanecen juntos dentro de `chats` (acoplados por el funnel).

### §5.3 Entities (casting declarado) e íconos — el fix de INV-1 en frontend

**Cada plugin posee su entity; cross-plugin vía cast declarado (canal 3).** Se mudan
a `plugins/<id>/frontend/entities/`: `order`→orders, `tracked-order`→eta,
`chat`/`session`/`message`/`handoff`→chats, `ads-campaign`→ads,
`eval-trend`/`eval-candidate`→evals, `agent`→agents_admin, `catalog-sync`→catalog.
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
| **AP-1 Split plugin** | Backend de X dentro de Y | `eta`/`ads` backend en `chats`; `evals` backend en `chats`+UI en `agents_admin` | §5.2 extracción a `plugins/<id>/` + `platform/conversation` | P-1, P-2, P-9 |
| **AP-2 Central entity** | Entity de dominio en PROTECTED `src/entities/` | TODAS las entities (10) centrales; el merge agregó `eval-trend/` central | §5.3 entities por-plugin + cast declarado | P-11, P-14 |
| **AP-3 Hidden coupling** | Dep DURA sin declarar / target soft que dispara al vacío | `orders` 5 transitions →`chats/eta` (soft, **ya resuelto** por dispatcher-skip) | dispatcher-skip ✅ + `depends_on` solo duras | P-6, P-7, P-14 |
| **AP-4 Central icon** | Glifo nuevo edita `Icon.tsx` PROTECTED | `resolveIcon` contra registry central | §5.3 íconos contribuidos | P-12 |
| **AP-5 Mega-plugin** | Un plugin con N dominios togglables juntos | `chats` = 4 workers + 6 routers | extraer (§5.2); `chats` = núcleo conversacional | P-2, P-9 |
| **AP-6 Cross-stack asymmetry** | Frontend gateado por un id, backend por otro | `eta` FE por `eta`, backend por `chats` | P-PARITY + extracción | P-9, P-13 |
| **AP-7 Shell hardcode** | El shell hardcodea state/listas por plugin | `pluginProps` en `Dashboard.tsx` | Context/registry-driven props | P-8, P-10 |
| **AP-8 Unfiltered enumeration** | Backend enumera plugins ignorando `ENABLED_PLUGINS` | `agents_admin.discover_agents()` | filtrar por enabled en runtime de prod | P-6 |

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

**Fin del contrato.** El código vivo gana sobre cualquier `.md`. Este contrato
define el TARGET; los tests de [PLUGIN_ARCHITECTURE_TESTS.md](PLUGIN_ARCHITECTURE_TESTS.md)
lo hacen imposible de violar sin que CI lo cace.
