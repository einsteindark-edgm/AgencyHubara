# Hubara Platform SDK — plan de arquitectura (fable)

> **Qué es.** El plan para convertir el plugin system post-F1–F8 en una
> **plataforma de desarrollo**: un SDK estilo Apple (Foundation + Kits +
> protocolos), un CLI estilo Google agents-cli (scaffold + check + certify),
> y un catálogo certificado (system-explorer v2) donde solo aparecen como
> "sanos" los plugins que pasan la certificación.
>
> **La frase que resume todo:** hoy tenemos *un monorepo con gates que te
> frenan*; el objetivo es *una plataforma con un SDK que te lleva* — y los
> gates pasan de ser policía a ser **el compilador**.
>
> Complementos: [ARCHITECTURE_FINAL_fable.md](ARCHITECTURE_FINAL_fable.md)
> (la arquitectura actual), [PLUGIN_CONTRACT.md](PLUGIN_CONTRACT.md) (la ley),
> [PLUGIN_PROTOCOL_fable.md](PLUGIN_PROTOCOL_fable.md) (el protocolo en 3 capas).

---

## §0. La visión en una página

Tres modelos de la industria, fusionados sobre lo que ya existe:

1. **Apple (Foundation/Kits + protocolos).** En Swift no editás el OS: importás
   frameworks, implementás *protocols*, y el OS corre tu código por vos
   (Inversion of Control). Traducción: los plugins **solo importan
   `hubara_sdk`** — nunca `src/platform/` directo — e implementan protocolos
   (`PluginAPI`, `PluginWorker`, `EventEmitter`…). La plataforma mantiene
   hermoso todo por debajo y puede evolucionar sin romper a nadie.

2. **Google agents-cli / ADK (lifecycle + golden path).** Un CLI con verbos por
   fase del ciclo de vida (`create → check → certify → dev → deploy`), templates
   que generan proyectos *production-shaped* (con tests y CI incluidos desde el
   minuto cero), y gates entre fases. Traducción: `hubara create plugin` genera
   un plugin que **nace certificado**; `hubara check` es el "compilador" local.

3. **Java TCK + Backstage Scorecards (certificación visible).** Python no
   compila — entonces emulamos el compilador: un **Technology Compatibility
   Kit** (suite de conformance que el SDK provee y cada plugin instancia) cuyo
   resultado es un **reporte de certificación** por plugin. El system-explorer
   se convierte en el **catálogo**: los certificados aparecen en el grafo; los
   que no, van a una sección de **cuarentena** con el diagnóstico y el fix.

**Regla de oro extendida** (hereda la de F1–F8): *ningún protocolo, arquetipo
o port nuevo del SDK sin (a) su check en el TestKit, (b) su template en el
CLI, y (c) su representación en el catálogo.* Las tres patas en el mismo PR.

**Qué NO cambia:** INV-1 (aislamiento aditivo), INV-2 (toggle simétrico), los
4 canales, DEHA R-rules, FSD. El SDK los hace *más fáciles de cumplir que de
violar* — no los reemplaza.

---

## §1. Los nombres técnicos (tu sueño → término de industria → pieza nuestra)

| Lo que querés | Nombre técnico (industria) | Pieza concreta en el repo |
|---|---|---|
| "Programar como en Apple: implementar protocolos y ellos mantienen el OS" | **Protocol-Oriented Programming** (PEP 544 `typing.Protocol`) + **Inversion of Control** ("Hollywood principle") + **Stable Public API / SDK boundary** | `hubara_sdk` (Python) + `@hubara/sdk` (TS): la ÚNICA superficie de import legal para plugins |
| "Core Foundation, XKit Foundation…" | **Framework / Kit layering** | `hubara_sdk.foundation` + kits por capability: `agentkit`, `eventkit`, `castkit`, `testkit`, `uikit` (TS) |
| "El CLI de Google que garantiza que se implementen los protocolos" | **Scaffolding CLI / generator** sobre un **golden path** ("paved road", Spotify/Netflix) con **lifecycle gates** (agents-cli: spec→scaffold→build→eval→deploy) | CLI `hubara` (typer): `create / check / certify / dev / graph / explain / upgrade` |
| "Si no existen los unit tests de arquitectura, no compila" | **TCK — Technology Compatibility Kit** (término JCP/Java) + **architectural fitness functions** (Thoughtworks) + **conformance testing** | `hubara_sdk.testkit`: suite parametrizada que cada plugin instancia en `tests/conformance/`; gate P-27 la exige |
| "Emular un compilador antes de mandar a master" | **Pre-merge verification / required status check** + estilo `cargo check` | `hubara check` local (estático, segundos) + job CI `plugin-certification` requerido para merge |
| "La UI solo muestra lo que cumple; lo demás va a una sección de no-cumple" | **Software Catalog + Scorecards** (Backstage/Cortex/OpsLevel; Soundcheck de Spotify) + **quarantine lane** | system-explorer v2: badge de certificación por plugin + sección Cuarentena con diagnóstico y fix sugerido |
| "Niveles de cumplimiento" | **Certification tiers / conformance program** (modelo CNCF Certified Kubernetes; "notarization" de Apple) | Niveles C0→C3 (§4.3) |
| "Errores que te dicen cómo arreglar" | **Compiler diagnostics with error codes** (estilo `rustc --explain E0382`) | Catálogo de diagnósticos: cada P-rule con código, mensaje y fix; `hubara explain P-16` |
| "Un SDK como el ADK" | **Agent/Plugin Development Kit** versionado (semver + deprecation policy; `requires_sdk` como el `minSdkVersion` de Android) | Campo `requires_sdk` en `plugin.yaml` + política de evolución (§4.7) |
| "Verificar comportamiento, no solo estructura" (gotcha #1) | **Behavioral conformance / contract testing + golden evals** | Nivel C3: capability specs (`.hubara/specs/`) vinculadas en el manifest + golden evals + smoke E2E |
| "Que el template no degenere en spaghetti interno" | **Archetype** (identidad arquitectónica de por vida, no acto de scaffold) + **conformance profile** por tipo + tests de arquitectura estilo **ArchUnit** (reglas de import intra-plugin) | Campo `archetype:` en el manifest + perfiles en `sdk/archetypes/` que alimentan scaffolder, TCK (P-29) y catálogo (§4.5) |
| "Conectar Medusa hoy, otro CRM mañana" | **Ports & Adapters** (lado *driven* del hexágono) + **Anti-Corruption Layer** (DDD) + **connector pattern** con binding **Strategy/Registry** + **port contract tests** + **fakes** oficiales | `hubara_sdk.connectorkit`: ports de capability en el SDK + `connectors/<vendor>/` + `hubara create connector` (§4.6) |
| "Bajar el catálogo de Medusa a local y sincronizar con Meta" | **ETL / data pipeline** con **source & sink connectors** (vocabulario Kafka Connect) + **read model / materialized view** local + **reconciliation loop** idempotente (convergencia estilo controller de Kubernetes) | Arquetipo `sync` (§4.5) + ports `CatalogSourcePort` / `SnapshotStorePort` / `CatalogSinkPort` + connectors `medusa/` y `meta/` (§4.6, caso catalog) |

---

## §2. Inventario honesto: qué ya existe (y nos abarata todo)

La sorpresa del mapeo: **~60% de los cimientos ya están construidos.**

| Pieza del sueño | Estado HOY | Dónde |
|---|---|---|
| Protocolos estructurales | ✅ EXISTEN (F8): `ApiModule`, `WorkerModule`, `ConversationRouteOwner` | `src/platform/plugin_protocol.py` |
| Manifest como contrato único | ✅ SSoT con schema de 360 líneas | `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml` |
| Gates que auto-descubren plugins | ✅ parametrizan sobre `glob("*/plugin.yaml")` — cero listas a mano | `tests/architecture/*`, `plugin_manifest.enumerate_manifest_workers()`, `plugin_loader.all_manifests()` |
| Fitness functions (P-1…P-26, R-rules) | ✅ ~12 archivos de gates backend + 11 frontend + import-linter | `tests/architecture/`, `src/test/architecture/`, `.importlinter` |
| Boot fail-fast | ✅ `validate_enabled` (P-6), `ensure_plugin_enabled` (P-21), route registry (F6) | `plugin_loader.py`, `plugin_runtime.py`, `routing.py` |
| Catálogo visual | ✅ system-explorer (React Flow + ELK) alimentado por `GET /api/system-map/graph`; detecta huérfanos y warnings de drift | `system_explorer/` + `src/plugins/system_map/domain/builder.py` |
| Codegen de registry frontend | ✅ `plugins-sync.ts` valida y genera `plugin-registry.generated.ts` | `frontend_dashboard/scripts/plugins-sync.ts` |
| Evals con juez calibrado | ✅ rúbrica + golden-eval en CI + calibración | `.hubara/evaluator-rubric.yaml`, `evaluator-calibration/` |
| Specs de comportamiento | ✅ capability specs persistentes | `hubara_agency/.hubara/specs/` |
| Librería interna instalable (precedente) | ✅ `exoclaw-temporal` es package pip separado | `exoclaw-temporal/` |
| Ports hexagonales (lado driven) | ✅ 9 ports `typing.Protocol`: `OrderQueryPort`, `OrderCommandPort`, `OrderRegistrationPort`, `CatalogPort`, `CheckoutVerificationPort`, `MetaCatalogPort`, `AudioTranscriptionPort`, `ImageVisionPort`, `CustomerScoringPort` — y fakes embrionarios (`stub.py`, `empty_query.py`, `local_snapshot.py`) | `src/platform/*/port.py` |
| Arquitectura interna por tipo de plugin | ⚠️ existe como CONVENCIÓN (PM-7 `agent/<worker>/`, mini-FSD, R-rules globales) pero no como contrato per-tipo enforced | recetas §4 de ARCHITECTURE_FINAL |
| **SDK como superficie única** | ❌ los plugins importan `src.platform.*` directo (26× `WORKSPACE_VAULT_DIR`, 12× `get_temporal_client`, 10× `get_task_queue`…) | — |
| **TestKit instanciable por plugin** | ❌ los gates son una suite central que "policía"; el plugin no trae su archivo de conformance | — |
| **Scaffolder / CLI** | ❌ el patrón es copy-paste de un plugin parecido | — |
| **Certificación visible en la UI** | ❌ el explorer muestra estructura y orfandad, no conformance | — |
| **Manifest tipado compartido** | ❌ 3 lectores independientes del YAML (plugins-sync.ts, main.py, run_workers.py) sin modelo tipado común | — |
| **Connector aislado por vendor** | ❌ los adapters Medusa viven MEZCLADOS con los ports (`platform/orders/medusa_order*.py` ≈110 KB junto a `query_port.py`; client crudo en `platform/medusa/`) y 4 plugins tocan código medusa-specific directo (sales worker lo construye a nivel módulo) | — |

Conclusión: **no hay que inventar gates ni grafo — hay que re-empaquetar lo
que existe detrás de una superficie pública, ponerle un CLI delante y cablear
el resultado al explorer.**

---

## §3. Arquitectura objetivo (capas)

```
┌─────────────────────────────────────────────────────────────────────┐
│  CATÁLOGO (system-explorer v2)                                      │
│  grafo + badges C0–C3 + sección Cuarentena + scorecard por plugin   │
└──────────────────────────────▲──────────────────────────────────────┘
                               │ lee certification-reports + grafo
┌──────────────────────────────┴──────────────────────────────────────┐
│  CLI `hubara`  (el ciclo de vida)                                   │
│  create · check · certify · dev · graph · explain · upgrade        │
└───────┬──────────────────────┬──────────────────────────────────────┘
        │ genera desde         │ ejecuta
        ▼ arquetipos           ▼
┌────────────────────────┐  ┌──────────────────────────────────────────┐
│  ARQUETIPOS             │  │  TESTKIT (TCK)  hubara_sdk.testkit      │
│  api_only · full_stack  │  │  P-1…P-2x parametrizado por plugin_id    │
│  agentic · notifier     │  │  + perfil del arquetipo (P-29): capas    │
│  sync (source→sink)     │  │    internas + import rules intra-plugin  │
│  cada uno = template    │  │  + contract suites de ports (connectors) │
│  (día 0) + PERFIL       │  │  + diagnósticos con código y fix         │
│  interno enforced de    │  │  → emite .hubara/certification/<id>.json │
│  por vida (día 400)     │  └──────────────────────────────────────────┘
└────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│  HUBARA SDK  (la única superficie de import para plugins)           │
│  foundation: PluginManifest tipado · protocolos · diagnósticos      │
│  agentkit:   worker conversacional · CONVERSATIONAL_TURN_ACTIVITIES │
│  eventkit:   eventos frozen + transitions + dispatcher helpers      │
│  castkit:    casts canal-3 (timeouts honestos L-1, 4 paths testeados)│
│  connectorkit: PORTS de capability (OrderQuery, Catalog, Crm, …)    │
│  uikit (TS): PluginModule · plugin-host · entity factory · Zod      │
└──────────────────────────────▲──────────────────────────────────────┘
                               │ fachada (re-export curado)
┌──────────────────────────────┴──────────────────────────────────────┐
│  src/platform/*  →  pasa a ser IMPLEMENTACIÓN PRIVADA               │
│  (los plugins ya no la importan; solo el SDK la importa)            │
│  └── connectors/  ← adapters por VENDOR detrás de los ports:        │
│      medusa/ (commerce hoy) · hubspot/ (CRM mañana) · whatsapp/ …   │
│      binding por deployment: CONNECTOR_ORDERS=medusa (Strategy)     │
└─────────────────────────────────────────────────────────────────────┘
```

El flujo de desarrollador (humano o agente del pipeline) queda:

```bash
hubara create plugin reviews --archetype full_stack   # nace certificado
# … programar implementando protocolos del SDK …
hubara check reviews        # el "compilador": segundos, estático, local
hubara certify reviews      # TCK completo + report JSON → catálogo
git push                    # CI re-corre certify; merge bloqueado si < C2
```

---

## §4. Diseño componente por componente

### 4.1 Hubara SDK — la superficie pública (Fase 0–1)

**Decisión de packaging (recomendación):** empezar como **fachada interna**
`hubara_agency/src/sdk/` que re-exporta la superficie curada de
`src/platform/` — NO como wheel separado todavía. Razones: cero overhead de
versionado/publicación en monorepo; el lockdown se consigue igual con
import-linter; se promueve a package instalable (como `exoclaw-temporal`)
recién cuando haya un segundo repo/tenant que lo consuma. El módulo análogo
TS: `frontend_dashboard/src/shared/sdk/` (re-export de plugin-host + helpers).

**Contenido inicial de la fachada — exactamente la superficie de facto medida**
(ranking real de imports de plugins hoy):

```python
# src/sdk/__init__.py  (Foundation: lo que TODO plugin necesita)
from src.sdk.foundation import (
    PluginManifest,            # modelo pydantic tipado del plugin.yaml (nuevo)
    get_manifest, get_task_queue,            # lectura del manifest (existe)
    ensure_plugin_enabled,                   # P-21 (existe)
    get_worker_spec, resolve_route_workflow_id,  # routing F6 (existe)
    HubaraDiagnostic,          # error con código P-x + fix sugerido (nuevo)
)
from src.sdk.runtime import (
    get_temporal_client, with_heartbeat,     # existe (platform/temporal)
    FilesystemMetadataStore, WORKSPACE_VAULT_DIR,  # existe (state/config)
    setup_logging,                           # existe
)
# src/sdk/eventkit.py  → dispatch_event_activity, EventEnvelope, transitions
# src/sdk/agentkit.py  → CONVERSATIONAL_TURN_ACTIVITIES, run_agent_turn,
#                        register_tool_extension, TURN_ENDING_TOOLS (L-11)
# src/sdk/castkit.py   → helper de cast HTTP con timeouts honestos (L-1):
#                        4 paths (éxito/passthrough/504-timeout/502-connect)
# src/sdk/testkit/     → el TCK (§4.3)
```

**El lockdown (lo que lo vuelve "Apple"):** nuevo contrato import-linter +
gate AST espejo (P-28): *los plugins solo importan `src.sdk`, `exoclaw*`,
`temporalio` y stdlib/terceros — `src.platform.*` queda prohibido para
plugins.* Migración con **ratchet**: arranca con allowlist de los imports
existentes (medidos: ~100 sitios) y CI prohíbe que el contador SUBA; se
drena plugin por plugin. El mismo patrón en TS: dep-cruiser pasa de "no
imports cross-plugin" a "plugins importan `@/shared/sdk` y a sí mismos".

Con esto, R-DIP se endurece: `plugins → sdk → platform` (DIP en dos saltos), y
la plataforma gana libertad de refactor interno real — el contrato con los
plugins es el SDK, no los módulos.

### 4.2 Foundation: manifest tipado + protocolos completos (Fase 1)

1. **`PluginManifest` (pydantic)** generado/validado contra
   `plugin.schema.yaml`. El schema YAML (que ya existe y es JSON-Schema-shaped)
   pasa a ser la **fuente única ejecutable**: de él derivan (a) el modelo
   pydantic del backend, (b) el Zod del `plugins-sync.ts` y del explorer
   (contract test que compara, o codegen). Esto mata la clase de bug L-10
   (drift backend↔frontend de enums) para el manifest, y arregla el hecho de
   que hoy hay 3 lectores independientes del YAML.

2. **Protocolos faltantes** (los 3 de F8 ya existen; se completan):

   | Protocolo (`typing.Protocol`) | Quién lo implementa | Check que lo ata |
   |---|---|---|
   | `PluginAPI` (`router: APIRouter`) | `api/__init__.py` | P-2 (existe) |
   | `PluginWorker` (`async main()`, queue propia) | `workers/<w>.py` | P-16/P-21 (existen) |
   | `ConversationRouteOwner` | workers con `owns_route` | P-18 (existe) |
   | `EventEmitter` (eventos frozen JSON-safe en `shared/contracts/events.py`) | plugins que emiten | orchestration-consistency (existe) + nuevo check de frozen/JSON-safe |
   | `CastProvider` / `CastConsumer` (canal 3) | casts `api/<cast>.py` | P-14 (existe) + nuevo: tests de 4 paths del cast (L-1) |
   | `DashboardContribution` (TS `PluginModule`, default-export Page) | `frontend/index.ts` | `assertPluginModule` + tsc (existe) |
   | `ToolExtension` (`register_tool_extension`) | plugins agentic | nuevo: import-at-top check (gotcha #6, ya cazado por F821) |

3. **`HubaraDiagnostic`** — el sistema de diagnósticos del "compilador": cada
   regla con `code` (P-x), `message`, `fix_hint`, `docs_url`. La tabla §3 de
   ARCHITECTURE_FINAL_fable.md ("Si hacés esto… te frena… fix") se convierte en
   este catálogo **machine-readable** (`src/sdk/diagnostics.py` o YAML), y lo
   consumen: los mensajes de error del TestKit, `hubara explain P-16`, y los
   tooltips de la cuarentena del explorer. Una sola fuente para humanos,
   agentes y UI.

### 4.3 TestKit (el TCK) + niveles de certificación (Fase 2)

**El movimiento clave:** los gates dejan de ser solo "suite central que
policía" y se empaquetan como **suite importable parametrizada por plugin**.
El plugin no copia tests — los **instancia** (así un gate nuevo en el SDK
upgradea a TODOS los plugins de una, sin drift de copy-paste; lección L-3:
nada de listas/copias a mano):

```python
# hubara_agency/tests/conformance/test_eta_conformance.py  (TODO el archivo)
from src.sdk.testkit import conformance_suite

# Genera ~40 tests parametrizados: P-rules estructurales, manifest↔código,
# eventos frozen, casts con 4 paths, k8s/compose parity, ...
globals().update(conformance_suite("eta"))
```

- **P-27 (el "no compila"):** gate central que exige que **exista** el módulo
  de conformance de cada plugin descubierto en disco y que invoque
  `conformance_suite("<id>")` (chequeo AST — mismo patrón que P-21). Plugin
  sin su archivo TCK → CI rojo → no mergea. Eso es la emulación del
  compilador: *el artefacto de test es parte del artefacto de código.*
- Los tests **propios** del plugin (comportamiento de SU dominio) conviven en
  el mismo módulo o al lado — el TCK cubre arquitectura; el plugin cubre su
  lógica.
- **Reporte de certificación:** `hubara certify` corre la suite con un
  pytest-plugin que escribe `hubara_agency/.hubara/certification/<id>.json`:

```json
{
  "plugin": "eta", "level": "C2", "sdk": "1.0.0", "git_sha": "ba7f738",
  "generated_at": "2026-06-11T14:00:00Z",
  "checks": [
    {"id": "P-6",  "name": "depends_on habilitables", "status": "pass"},
    {"id": "P-16", "name": "task queue self-ref",     "status": "pass"},
    {"id": "P-27", "name": "TCK instanciado",          "status": "pass"},
    {"id": "C3/evals", "status": "skip", "reason": "golden evals pendientes"}
  ]
}
```

**Niveles (el conformance program):**

| Nivel | Significa | Cómo se computa |
|---|---|---|
| **C0 — Declarado** | el manifest parsea y valida contra el schema | en vivo (barato), lo computa el builder del system_map |
| **C1 — Cargable** | pasa los boot checks: deps, rutas, queues, módulos existen | en vivo (los checks ya existen en loader/registry) |
| **C2 — Certificado** | TCK completo verde (P-rules + P-27 + P-28 SDK-only) | reporte de `hubara certify` (local o CI) |
| **C3 — Conducta verificada** | specs vinculadas + golden evals pass + smoke E2E + replay (L-9) | reporte extendido; aplica sobre todo a plugins agentic |

La certificación **gobierna el merge y el catálogo, no el runtime**: en
producción sigue mandando `ENABLED_PLUGINS` + boot fail-fast (un reporte
viejo jamás debe poder tumbar prod). Lo que sí: el explorer muestra en rojo
lo no certificado, y CI no deja mergear < C2.

### 4.4 El CLI `hubara` (Fase 3)

Python + typer, dentro de `hubara_agency` (`[project.scripts] hubara =
"src.sdk.cli:app"` → `uv run hubara …`); para lo frontend orquesta los npm
scripts existentes. **Orden de construcción deliberado: primero el
verificador, después el generador** (un `create` sin `check` genera basura
con confianza).

| Verbo | Qué hace | Sobre qué se apoya (ya existe) |
|---|---|---|
| `hubara check [<id>]` | el **compilador rápido**: schema del manifest + protocolos + import contract + paridad manifest↔código. Estático, segundos, sin red. Salida estilo rustc: `error[P-16]: … fix: …` | gates existentes refactorizados a funciones puras reutilizables |
| `hubara certify [<id>]` | `check` + TCK completo (pytest) + escribe el reporte JSON + resumen de nivel C0–C3 | suite §4.3 |
| `hubara create plugin <id> --archetype <a>` | scaffolds ambos stacks + manifest (con `archetype:`) + **archivo TCK** + k8s yaml + corre `plugins:sync` y `render-compose.py` + `check` final. **Nace C2.** | perfiles de arquetipo §4.5 |
| `hubara create connector <vendor> --ports <p,...>` | scaffolds `connectors/<vendor>/`: skeleton que implementa los `Protocol` de cada port + `acl.py` + contract suites instanciadas + `connector.yaml` con env requeridas | ConnectorKit §4.6 |
| `hubara explain <P-x>` | imprime diagnóstico + fix + ejemplo (catálogo §4.2.3) | diagnostics |
| `hubara graph` | dump del grafo (JSON/mermaid) o abre el explorer (:5175) | builder del system_map |
| `hubara dev` | levanta/verifica el stack local (docker compose, puertos canon — gotcha #12) | compose existente |
| `hubara upgrade` | re-scaffold con **3-way merge** cuando el template/SDK evoluciona (la killer feature del agents-cli: mantener la flota de plugins al día) | Fase 7 |

**Arquetipos** (production-shaped, como los ~72 files del agents-cli — cada
uno modelado de un plugin real del repo y alineado con la clasificación
A/B/C/D que ya usa el pipeline; su arquitectura interna obligatoria y cómo se
enforcea de por vida: §4.5):

- `api_only` (modelo: `system_map`) — backend puro: manifest + `api/`, sin
  bloque `frontend:` (su UI, si existe, vive en una app aparte).
- `full_stack` (modelo: `orders`, `ads`) — api + frontend FSD completo + sección.
- `agentic` (modelo: `chats`/`eta` conversacional) — worker Temporal + workspace
  (IDENTITY/SOUL/TOOLS) + `agentkit` + transitions declaradas.
- `notifier` (modelo: `eta` post-L-4) — worker push puro, sin `owns_route`
  (la lección L-4 horneada como template: notificar ≠ poseer el turno).
- `sync` (modelo: `catalog`) — pipeline source→sink entre sistemas externos
  con read model local; workflow Temporal de reconciliación idempotente, sin
  conversación ni LLM. Se parametriza: `--source <port> --sink <port>`.

Cada template incluye: archivo TCK instanciado, tests de dominio de ejemplo,
eventos frozen de ejemplo, k8s yaml espejo, y `GEMINI.md`-equivalente — un
`PLUGIN.md` con el contexto para agentes de código (los skills del pipeline
lo leen).

### 4.5 Arquetipos — la arquitectura interna obligatoria (Fase 2–3)

**El problema que resuelve:** un template garantiza el día 0; nada impide que
el día 400 el plugin sea un spaghetti que "implementa los kits" pero perdió
toda forma interna. La solución es separar dos conceptos: **template** (acto
generador, dura un día) y **arquetipo** (identidad arquitectónica declarada
en el manifest y **auditada de por vida**).

1. **El manifest declara la identidad:** campo `archetype: api_only |
   full_stack | agentic | notifier | sync` (obligatorio para plugins nuevos;
   los 7 existentes se clasifican en la migración).
2. **Una sola fuente por arquetipo:** `src/sdk/archetypes/<nombre>.py` — un
   perfil declarativo: dirs requeridos/permitidos, DAG de capas internas,
   reglas de import entre capas, protocolos que debe implementar, patrones
   prohibidos. De ese perfil derivan LAS TRES COSAS: el scaffolder lo
   **genera**, el TCK lo **audita** (P-29), el catálogo lo **dibuja**. El que
   genera y el que audita son el mismo artefacto — no puede haber drift entre
   "lo que el template crea" y "lo que el gate exige".
3. **P-29, el anti-spaghetti:** suite del TestKit parametrizada por
   (plugin, arquetipo) con los mismos mecanismos AST que ya usan
   P-3/P-16/P-21: estructura de dirs + **import-graph interno del plugin
   contra el DAG de capas del perfil** (estilo ArchUnit: "domain no importa
   adapters", "tools no importan temporalio"). Hoy las R-rules auditan
   global; P-29 las baja al interior de cada plugin según su tipo.
4. **No existe `archetype: custom`.** Si un plugin nuevo no encaja en ningún
   perfil, no se lo exime: se crea/extiende un arquetipo EN EL SDK (ADR +
   label `architecture-change`) — el mismo espíritu que los contribution
   points: se arregla el mecanismo, no se perfora el contrato. Cambiar un
   plugin de arquetipo = editar el manifest y pasar el perfil nuevo ENTERO.

**Los 5 perfiles iniciales** (formalizan convenciones que YA existen — PM-7,
mini-FSD, R-rules — no inventan estructura nueva):

| Arquetipo | Capas internas obligatorias (backend) | Reglas de import internas (ejemplos enforced) |
|---|---|---|
| `api_only` (modelo: system_map) | `api/` (routers delgados) → `domain/` (lógica pura) → `adapters/` opcional; sin bloque `frontend:` | `domain/` sin I/O (httpx/fs/DB prohibidos); `api/` solo importa domain + sdk (jamás vendors); contracts frozen |
| `full_stack` (modelo: orders, ads) | = api_only + frontend mini-FSD `entities → features → pages` | las reglas FSD existentes aplicadas por-plugin: Zod en boundary, Page sin props (PluginHost), fetch solo vía client compartido |
| `agentic` (modelo: chats) | `agent/<worker>/{workflows, activities, tools, use_cases, workspace}` + `workers/` + `shared/contracts/events.py` | R-DET (workflows sin I/O), R-DIP#7 (tools ↛ temporalio), activities = único lugar con I/O, use_cases puros orquestando ports, events frozen JSON-safe, spread de `CONVERSATIONAL_TURN_ACTIVITIES` (L-3), tools de UI ∈ `TURN_ENDING_TOOLS` (L-11), workspace completo (P-15) |
| `notifier` (modelo: eta post-L-4) | = agentic SIN superficie conversacional | sin `owns_route`, sin tools de conversación registradas, NUNCA escribe `active_route` (L-4 horneada como regla de perfil) |
| `sync` (modelo: catalog) | `agent/<worker>/{workflows, activities, use_cases}` + `workers/` + `api/` (trigger + estado de jobs) + frontend de jobs opcional — SIN `tools/` ni `workspace/` (un sync no conversa ni usa LLM) | workflows = ciclo pull→diff→apply→checkpoint (R-DET, `continue-as-new` si pagina); activities = único I/O y SOLO vía ports source/sink/store (P-31); use_cases PUROS computan el **diff plan** (el corazón testeable sin red); apply idempotente (fingerprint + pre-check) con R-HEARTBEAT |

### 4.6 ConnectorKit — integraciones externas: Medusa hoy, cualquier CRM mañana (Fase 4)

**El problema que resuelve:** Medusa es el core comercial externo, pero su
acople hoy es privilegiado y está regado. Los 9 ports `Protocol` que ya
existen (¡la mitad buena del hexágono!) conviven en el MISMO paquete con sus
adapters Medusa (`platform/orders/` contiene `query_port.py` Y
`medusa_order_query.py` — ~110 KB de adapter en 3 archivos), el client crudo
vive en `platform/medusa/`, y 4 plugins tocan código medusa-specific directo
(el sales worker lo construye a nivel módulo — por eso los arch-tests piden
env dummies de Medusa). Conectar "otro CRM" hoy sería cirugía.

**El modelo (hexagonal, lado driven) — dos conceptos con nombre:**

- **Capability ports** — el contrato INTERNO, vive en el SDK
  (`hubara_sdk.connectorkit.ports`): los 9 existentes promovidos
  (`OrderQueryPort`, `OrderCommandPort`, `CatalogPort`, …) + los que vengan
  (`CrmPort`). Es lo ÚNICO que plugins y platform consumen.
- **Connectors** — el adapter por vendor: `connectors/<vendor>/` implementa N
  ports + su **ACL (Anti-Corruption Layer)**: los modelos del vendor JAMÁS
  cruzan la frontera del connector; se traducen a los DTOs del port.

```
src/platform/connectors/
├── medusa/
│   ├── connector.yaml      ← manifest: provides [orders.query, orders.command,
│   │                          catalog, checkout], env requeridas, healthcheck
│   ├── client.py           ← hoy platform/medusa/client.py
│   ├── orders_query.py     ← hoy platform/orders/medusa_order_query.py (33K)
│   ├── orders_command.py   ← hoy medusa_order_command.py (41K)
│   ├── acl.py              ← traducción modelos Medusa → DTOs de los ports
│   └── tests/              ← instancia las contract suites de sus ports
├── meta/                   ← Meta es OTRO vendor: provee [catalog.sink] hoy;
│                              [whatsapp.messaging, capi.events, ads.insights]
│                              después — un connector puede proveer N ports
└── hubspot/                ← el CRM futuro: misma forma, CERO cambios en plugins
```

**Las 5 reglas del kit:**

1. **Binding config-driven (Strategy/Registry):** `CONNECTOR_ORDERS=medusa`
   por deployment; `get_order_query_port()` resuelve del registry. Cambiar de
   vendor = escribir el connector + cambiar 1 env. (Es EL MISMO mecanismo que
   los "commerce profiles + registry de estrategias" del plan multi-tenant —
   un diseño, dos usos.)
2. **Ningún port sin fake:** cada port shippea su doble in-memory oficial en
   el SDK (los embriones ya existen: `stub.py`, `empty_query.py`,
   `local_snapshot.py` — se promueven). Los plugins testean SIN Medusa y sin
   env dummies (mata esa fricción de los arch-tests de raíz).
3. **Ningún connector sin contract suite:** el SDK shippea por port una suite
   de contrato reutilizable (mismo patrón que el TCK: el connector la
   INSTANCIA en 3 líneas). La MISMA suite corre contra el fake (CI siempre) y
   contra el vendor real (smoke opt-in) — si el fake y Medusa divergen, lo
   caza la suite, no producción.
4. **`HttpConnectorBase`** con las lecciones horneadas: taxonomía honesta de
   timeouts (L-1: connect-error ⇒ 502 "NO se aplicó"; read-timeout ⇒ 504
   "desconocido"), timeout dimensionado por la cadena del upstream, retries
   con idempotencia real (fingerprint + pre-check), cache de mapeos
   inmutables (L-2), heartbeats, healthcheck, structured logging.
5. **P-31, la regla dura:** los plugins importan PORTS, jamás connectors ni
   clients de vendor. `grep -ri medusa src/plugins` debe tender a 0 (ratchet
   sobre los 4 usos actuales). El nombre del vendor es un detalle de
   deployment, no de dominio.

En el catálogo, los connectors aparecen como nodos `connector` con sus ports
provistos, su certificación y el binding activo del deployment.

#### El caso completo de punta a punta: `catalog`, el primer `sync`

El plugin catalog ES hoy el pipeline Medusa→local→Meta, pero con el acople
típico pre-SDK: sus activities tocan el client de Medusa directo
(`agent/activities/pull.py`, `composition.py`), el adapter de checkout vive
pegado al port (`platform/catalog/medusa_checkout.py`), y el snapshot local
(`platform/catalog/local_snapshot.py`) — que es un **read model** consumido
por el agente de ventas (productos, variantes, verificación de precios) —
está mezclado con todo lo demás. La arquitectura objetivo, con TODOS los
conceptos del plan trabajando juntos:

```
   Medusa (vendor)                        Meta Commerce (vendor)
        │ CatalogSourcePort                    ▲ CatalogSinkPort
        ▼                                      │
┌─ connectors/medusa ─┐              ┌─ connectors/meta ─┐
└──────────┬──────────┘              └─────────▲─────────┘
           │ pull (activity)                   │ apply (activity idempotente)
           ▼                                   │
   plugins/catalog  (archetype: sync) ─────────┘
   · workflows/: pull → diff → apply → checkpoint  (R-DET, continue-as-new)
   · use_cases/: normalize + DIFF PLAN (create/update/delete) — PUROS
           │ SnapshotStorePort (único WRITER)
           ▼
   platform/catalog_store  ← READ MODEL local (materialized view)
           │ CatalogReadPort (readers)
           ▼
   chats/sales (picker, precios) · checkout verification · quien venga
```

Pasos de la migración (dentro de F-SDK-4):

1. **Split de ports** — hoy `CatalogPort` mezcla concerns; queda:
   `CatalogSourcePort` (pull del vendor commerce), `SnapshotStorePort`
   (write/read del read model local; su fake = dict in-memory),
   `CatalogReadPort` (la vista read-only que consumen sales/checkout) y
   `CatalogSinkPort` (el `MetaCatalogPort` actual, renombrado al vocabulario
   source/sink). Nombres finales en el ADR.
2. **Meta se vuelve connector** (`connectors/meta/`): un vendor, N ports —
   `catalog.sink` hoy; `whatsapp.messaging` y `capi.events` son candidatos
   posteriores del MISMO connector.
3. **El plugin se re-perfila** a `archetype: sync`: el diff plan (qué
   crear/actualizar/borrar en el sink) se computa PURO en use_cases —
   testeable sin red, el corazón del sync — y las activities solo ejecutan el
   plan vía ports, con heartbeat e idempotencia por fingerprint + pre-check
   (la lección de atomicidad: converger, no asumir).
4. **El snapshot es plataforma, no plugin**: es un read model COMPARTIDO
   (sales lo lee) → vive detrás de `SnapshotStorePort` en platform. Regla del
   perfil: el plugin sync es su único WRITER; el resto son READERS vía
   `CatalogReadPort` — la analogía de "ninguna entity se comparte" para datos
   derivados.
5. **Contract suites + golden de idempotencia**: la suite de
   `CatalogSourcePort` corre contra el fake y contra Medusa real (smoke
   opt-in). Y el golden test del arquetipo `sync`: fake source con N
   productos → correr el workflow → store y sink convergen **y la segunda
   corrida produce un diff VACÍO** (idempotencia probada, no asumida).
6. **Drenaje P-31**: los imports de medusa en `plugins/catalog/agent/*`
   migran a ports — catalog es uno de los 4 plugins del ratchet.

`catalog` es la PRIMERA instancia del arquetipo, no la última: inventario,
precios, contactos de un CRM — misma forma. Por eso el template se
parametriza: `hubara create plugin inventory_sync --archetype sync
--source inventory.source --sink erp.sink`.

#### El caso ads: la atribución como read model + Meta como UN solo connector

`ads` es el espejo interesante de catalog: hoy **no llama a ningún vendor**
(verificado: cero httpx/Graph API en su código — ya cumple P-31 de
nacimiento). Es agregación LOCAL pura: sus KPIs (ROAS/CAC, embudo) se
computan sobre la atribución CTWA (`ctwa_clid`, `origin`, `last_touch`) que
el **ingest de WhatsApp escribe** en el metadata de cada sesión… y que ads
hoy lee **escaneando el vault a mano**. Tres formalizaciones:

1. **La atribución es un read model de plataforma** (mismo patrón que el
   snapshot de catálogo): `AttributionReadPort` en el SDK con su fake
   in-memory. Writer = el ingest (chats); readers = ads hoy y **CAPI mañana**
   (el evento de conversión `Purchase`/`Lead` necesita exactamente el mismo
   `ctwa_clid`). Ads deja de conocer el layout del vault; sus tests corren
   contra el fake sin filesystem.
2. **Re-perfilado a `full_stack` (P-29):** ads tiene sección frontend
   completa (entities/features) — no es `api_only`. Su backend hoy es plano:
   `aggregation.py` y `classification.py` viven en la raíz del plugin;
   migran a `domain/` tal cual (son lógica pura — encajan en el perfil sin
   tocar una línea).
3. **Evolución sin acople nuevo:** cuando se quiera spend REAL para el ROAS,
   entra como port `ads.insights` (Marketing API) del MISMO
   `connectors/meta/`; las conversiones salientes, como `capi.events`. El
   plugin solo suma consumo de ports — Meta sigue siendo UN vendor, UN
   connector, N ports.

**Candidatos siguientes del ConnectorKit** (mismo molde, no solo commerce):
el resto de Meta (`whatsapp.messaging`, `capi.events`, `ads.insights`)
consolidándose en `connectors/meta/`, y SigNoz/observabilidad.

### 4.7 Catálogo: system-explorer v2 (Fase 5)

El builder del `system_map` ya parsea manifests y detecta huérfanos/drift —
se le suma la dimensión de certificación:

1. **Backend:** el grafo (`GET /api/system-map/graph`) agrega por plugin
   `certification: {level, checks_failed[], report_age}`. C0/C1 se computan en
   vivo (baratos, ya existen como funciones); C2/C3 se leen del último
   `.hubara/certification/<id>.json` (generado local o bajado de CI). Reporte
   ausente/stale → se muestra como "sin certificar", nunca se inventa.
2. **UI:** badge por nodo plugin (verde C2+/ámbar C1/rojo C0 o checks rojos);
   el Sidebar gana la sección **"⚠ No certificados"** (la cuarentena, junto a
   la de huérfanos que ya existe): plugin → checks fallidos → fix de cada uno
   (del catálogo de diagnósticos) → "corré `hubara explain P-x`".
3. **Scorecard por plugin** (click): nivel, tabla de checks pass/fail, edad
   del reporte, protocolos implementados, deps/consumers del grafo.

Así el explorer deja de ser un mapa pasivo y se vuelve **el lugar donde un
dev valida su plugin antes del PR** — tu "lugar para validar si está bien
creado": `hubara certify && abrir :5175`.

### 4.8 Gate de merge + integración con el pipeline Archon (Fase 6)

- **CI:** job `plugin-certification` en `architecture-gates.yml`: corre
  `hubara certify --all`, publica los reportes como artifact, comenta el
  scorecard-diff en el PR, y es **required status** para mergear a main
  (= "no compila → no mergea").
- **Pipeline:** los skills (`hubara-implementer-archon`, planners, reviewers)
  reemplazan recetas en prosa por el CLI determinista: el implementer §0.5
  corre `hubara check` como smoke; el premortem y los reviewers leen el
  reporte JSON en vez de re-derivar; `hubara create` es la receta 4.1 hecha
  comando (esto es exactamente el `agents-cli setup`: el CLI diseñado para
  ser *driven by coding agents*).
- **Docs:** `hubara-architecture-guide` gana la sección "programar contra el
  SDK"; ARCHITECTURE_FINAL_fable.md §4 (recetas) apunta a los comandos.

### 4.9 Evolución y versionado del SDK (Fase 8)

- `requires_sdk: ">=1.0"` en el manifest (el `minSdkVersion`); el loader
  valida compatibilidad al boot; el TCK la audita.
- Semver del SDK + política de deprecación: `@deprecated` con warning →
  diagnóstico en `check` → remoción en major; `hubara upgrade` aplica
  codemods/re-scaffold 3-way para mover la flota.
- Promoción a wheel instalable (estilo `exoclaw-temporal`) recién cuando un
  segundo repo/tenant lo necesite — preparado para el plan multi-tenant.

---

## §5. Plan de ejecución (fases PR-sized)

> Cada fase cierra con su gate (regla de oro) y deja el repo verde y usable.
> Orden deliberado: **superficie → tipos → TCK+arquetipos → CLI → conectores
> → catálogo → gate → conducta**. El compilador antes que el generador; el
> generador antes que la vitrina… pero la vitrina (Fase 5) puede adelantarse
> en paralelo si querés impacto visual temprano (solo depende de la Fase 2),
> y el ConnectorKit (Fase 4) es paralelizable desde que existe la fachada.

| Fase | Entregable | Gate nuevo que la ata | Done cuando… |
|---|---|---|---|
| **F-SDK-0 · ADR + fachada** (1 PR) | ADR del SDK; `src/sdk/` re-exportando la superficie de facto (ranking §2); espejo TS `shared/sdk/` | contrato import-linter `plugins → sdk only` en modo **ratchet** (allowlist congelada, el contador no puede subir) + P-28 AST | un plugin de juguete compila importando SOLO `src.sdk` |
| **F-SDK-1 · Foundation tipada** (1–2 PRs) | `PluginManifest` pydantic validado contra `plugin.schema.yaml` + campo **`archetype:`** en el schema; protocolos faltantes (`EventEmitter`, `CastProvider/Consumer`, `ToolExtension`); catálogo `HubaraDiagnostic` (P-x → fix) | contract test schema↔pydantic↔Zod (mata drift L-10 del manifest); checks nuevos de eventos frozen y casts 4-paths | los 3 lectores del manifest comparten el modelo; `validate_manifest(path)` es una función pública |
| **F-SDK-2 · TestKit (TCK) + perfiles de arquetipo** (2 PRs) | `src/sdk/testkit/` empaquetando los gates existentes parametrizados por plugin; perfiles declarativos en `src/sdk/archetypes/` (§4.5) y clasificación de los 7 plugins existentes; archivos `tests/conformance/test_<id>_conformance.py` (3 líneas); writer de `certification/<id>.json` | **P-27**: plugin sin TCK instanciado → rojo. **P-29**: estructura interna + import-graph del plugin cumplen el perfil de su `archetype:` declarado | `uv run hubara certify eta` (o el pytest equivalente) emite el reporte con nivel C2, incluyendo el veredicto del perfil interno |
| **F-SDK-3 · CLI** (2 PRs) | PR-a: `hubara check / certify / explain / graph` (verificador). PR-b: `hubara create plugin` con los 5 arquetipos — el skeleton se genera DESDE los perfiles de §4.5, no de copias — + `hubara dev` | test de oro del scaffolder: **crear un plugin de cada arquetipo en CI y exigir que nazca C2** (luego se borra) | un dev (o el pipeline) crea un plugin funcional sin copiar-pegar y sin tocar archivos centrales |
| **F-SDK-4 · ConnectorKit** (2–3 PRs; paralelizable desde F-1) | ports promovidos a `sdk.connectorkit.ports`; `connectors/medusa/` (mover client + `medusa_order*.py` + ACL); `connectors/meta/` (catalog.sink); split de ports de catálogo (source/store/read/sink) y re-perfilado de `catalog` a `archetype: sync` con su golden de idempotencia (§4.6 caso completo); `AttributionReadPort` (read model de atribución CTWA: writer = ingest, readers = ads/CAPI — §4.6 caso ads); fakes oficiales por port; contract suites por port; binding por registry/env; `hubara create connector` | **P-31**: plugins ↛ vendors/connectors — solo ports del SDK (ratchet sobre los 4 plugins que hoy tocan medusa) + regla "ningún port sin fake; ningún connector sin contract suite" | cambiar de vendor = escribir un connector + 1 env de binding, CERO cambios en plugins; `grep -ri medusa src/plugins` → 0; el sync de catálogo corre ENTERO contra fakes en CI (segunda corrida = diff vacío) |
| **F-SDK-5 · Catálogo (explorer v2)** (1–2 PRs) | grafo con `certification` por plugin; badges; sección Cuarentena con fixes; scorecard drawer; nodos `connector` con sus ports y binding activo | test del builder: plugin con reporte fallido aparece en cuarentena con el diagnóstico correcto; reporte stale → "sin certificar" | en :5175 se ve qué plugins están certificados y por qué los demás no |
| **F-SDK-6 · Merge gate + pipeline** (1 PR) | job CI `plugin-certification` required; scorecard-diff comment en PR; skills del pipeline llaman `hubara check`/leen reportes | branch protection actualizado; el implementer §0.5 usa el CLI | un PR con plugin < C2 NO puede mergear; el pipeline corre el CLI en vez de recetas |
| **F-SDK-7 · Conducta (C3)** (2+ PRs) | manifest linkea capability specs (`specs:`); golden evals por plugin agentic enchufadas al certify; smoke E2E mínimo (1 turno real — L-3); harness de replay (L-9) | check "spec linkeada existe y sus Scenarios tienen test"; eval threshold en certify | el catálogo distingue C2 de C3; gotcha #1 (tests verdes, feature muerta) tiene gate |
| **F-SDK-8 · Versionado** (continuo) | `requires_sdk`; semver + deprecations; `hubara upgrade` 3-way; (opcional) wheel | check de compatibilidad en boot + TCK | evolucionar el SDK no rompe plugins viejos sin avisar |

**Drenaje de los ratchets (transversal, F-SDK-1→6):** migrar los ~100 imports
`src.platform.*` existentes a `src.sdk` (P-28) y los toques directos a código
medusa desde plugins a ports del SDK (P-31), plugin por plugin (PRs
mecánicos); las allowlists terminan vacías y ambos contratos pasan de ratchet
a prohibición dura.

---

## §6. Decisiones abiertas (para resolver en el ADR de F-SDK-0)

1. **Fachada interna vs wheel desde el día 1.** Recomendación: fachada
   (§4.1); el wheel es un refactor mecánico posterior. Contra-argumento: el
   wheel fuerza disciplina de versionado desde el inicio.
2. **Dónde viven los reportes de certificación.** Recomendación:
   `.hubara/certification/` **gitignored** + artifact de CI (el reporte es
   derivable, commitearlo invita a drift). Contra: commitearlos da historia
   barata al explorer sin correr nada.
3. **Cuarentena en boot.** ¿Un plugin C0 (manifest inválido) debe poder
   levantar en dev? Recomendación: dev sí con warning ruidoso (para poder
   debuggearlo), staging/prod no (fail-fast actual). El catálogo lo muestra
   en cuarentena siempre.
4. **Codegen Zod desde el schema vs contract-test.** Codegen es la verdad
   única perfecta pero agrega toolchain; contract-test (comparar shapes en
   CI) es 90% del valor con 10% del costo. Recomendación: contract-test
   primero, codegen si duele.
5. **Naming.** `hubara_sdk` vs `src/sdk` a secas; "TestKit/TCK" vs
   "Certification Kit"; niveles C0–C3 vs nombres (Declarado/Cargable/
   Certificado/Verificado). Bikeshed: decidir en el ADR y no volver.
6. **Ubicación de los connectors.** `src/platform/connectors/<vendor>/`
   (recomendado: los connectors SON plataforma — los plugins no los ven) vs
   `src/connectors/` top-level. Y si un connector pesado merece su propio
   toggle de deployment (hoy: el binding por env ya lo da implícito).
7. **Granularidad de ports y shape del binding.** ¿Ports finos como hoy
   (`orders.query` / `orders.command` separados — recomendado) o un
   `CommercePort` gordo? ¿Binding por env por capability
   (`CONNECTOR_ORDERS=medusa` — recomendado, espíritu INV-2) o un
   `connectors.yaml` por deployment? Alinear con los "commerce profiles" del
   plan multi-tenant para no diseñar el mismo registry dos veces.

---

## §7. Riesgos y cómo el plan ya los mitiga

| Riesgo | Mitigación horneada |
|---|---|
| El SDK se vuelve un God-module que re-exporta todo | la fachada arranca de la **superficie medida** (ranking de imports reales), no de "lo que podría servir"; agregar un símbolo al SDK exige las 3 patas de la regla de oro |
| Listas a mano que driftean (familia L-3) | TCK instanciado (no copiado); suites y CLI descubren plugins por glob del manifest, igual que los gates actuales |
| "Tests verdes, feature muerta" (gotcha #1) | la certificación estructural se llama C2, no "listo": C3 existe justamente para conducta (specs + evals + smoke E2E de 1 turno real) |
| Un reporte stale pinta verde algo roto | el reporte lleva `git_sha` + edad; el explorer degrada a "sin certificar"; CI siempre re-certifica |
| El lockdown rompe el día a día | ratchet con allowlist congelada: nada existente se rompe; solo lo nuevo nace limpio y lo viejo se drena con PRs mecánicos |
| El CLI miente (genera algo que no pasa) | test de oro en CI: cada template se instancia y debe nacer C2 — el scaffolder está atado al TCK por construcción |
| Certificación tumba runtime | decisión explícita: certificación gobierna **merge y catálogo**; el runtime sigue con boot fail-fast + `ENABLED_PLUGINS` (INV-2 intacto) |
| Más burocracia por plugin | neta NEGATIVA: el archivo TCK son 3 líneas, y a cambio desaparece el copy-paste de scaffolding y la receta 4.1 manual entera |
| Se implementan los kits pero el INTERIOR del plugin degenera en spaghetti | el arquetipo es un CONTRATO de por vida, no un template de un día: el mismo perfil que genera el skeleton lo audita para siempre (P-29: dirs + import-graph interno contra el DAG de capas); y no existe `archetype: custom` — si un plugin no encaja, se agrega un arquetipo nuevo AL SDK con ADR |
| El modelo del vendor (Medusa/CRM) se filtra al dominio | ACL obligatoria dentro del connector (los tipos del vendor no cruzan su frontera), P-31 (plugins solo ven ports), y la contract suite corre contra el fake Y contra el vendor real — la divergencia se caza en CI, no en producción |

---

**Fin.** El primer paso concreto es el ADR + fachada de F-SDK-0 — un PR
chico, reversible, que no toca comportamiento y habilita todo lo demás. Si
algo de este plan contradice el código vivo cuando se ejecute, gana el código
y la contradicción entra como lección L-# en ARCHITECTURE_FINAL_fable.md §9.
