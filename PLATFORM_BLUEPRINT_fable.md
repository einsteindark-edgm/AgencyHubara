# Plugin Platform Blueprint — base arquitectónica para proyectos nuevos (fable)

> **Qué es.** La descripción conceptual, **portable y agnóstica de dominio**,
> de la arquitectura de plataforma de plugins: SDK con Foundation + Kits,
> arquetipos enforced, CLI con compilador emulado, certificación con niveles,
> catálogo con cuarentena, y connectors para sistemas externos.
>
> **Para qué sirve.** Es la semilla de un proyecto NUEVO construido desde
> cero con esta filosofía. Este documento está escrito para ser entregado a
> un equipo (humano o agente de código) como **constitución del primer
> commit**: define los invariantes, los mecanismos y el orden de génesis.
> No describe ningún producto — describe la plataforma sobre la que se
> construye cualquier producto.
>
> **Stack semilla** (decidido, §12): frontend **Tauri** (React/TS adentro,
> Feature-Sliced Design, plugins que contribuyen menús/secciones) + backend
> **FastAPI** mínimo. Todo lo demás del documento es independiente del stack.
>
> **Origen.** Destilado de una plataforma en producción (commerce
> conversacional multi-plugin) y de su plan de SDK. Las cicatrices están
> convertidas en reglas; los nombres de dominio quedaron afuera a propósito.

---

## §0. La tesis

**No construyas una aplicación que tiene plugins; construí una plataforma
cuyo producto son los plugins.** La diferencia es quién paga el costo de la
disciplina: en una app-con-plugins, la arquitectura se sostiene por revisión
y buena voluntad (y se erosiona con cada dev nuevo); en una plataforma, la
arquitectura está **encarnada en herramientas** — violar las reglas es más
difícil que cumplirlas, porque cumplirlas es el camino generado y validado
automáticamente.

Tres modelos de industria, fusionados:

1. **Apple (Foundation/Kits + protocolos).** El desarrollador no edita el
   OS: importa frameworks, implementa *protocols*, y el sistema corre su
   código (Inversion of Control). Aquí: los plugins importan SOLO el SDK e
   implementan protocolos; la plataforma evoluciona por debajo sin romperlos.
2. **Google agents-cli / ADK (lifecycle + golden path).** Un CLI con verbos
   por fase (`create → check → certify → dev`), templates *production-shaped*
   (nacen con tests y CI), y gates entre fases. Aquí: `create` genera plugins
   que **nacen certificados**; `check` es el compilador local.
3. **Java TCK + Backstage Scorecards (certificación visible).** Los lenguajes
   dinámicos no compilan — se emula el compilador con un **Technology
   Compatibility Kit**: una suite de conformance que el SDK provee y cada
   plugin instancia, cuyo resultado es un **reporte de certificación** que un
   **catálogo** muestra (certificados en la vitrina; el resto, en cuarentena
   con diagnóstico y fix).

El lema operativo: **el manifest no puede mentir, el template no puede
driftear, y lo no certificado no puede mergear.**

---

## §1. Los invariantes (la constitución — si una decisión los viola, la decisión está mal)

- **INV-1 · Aislamiento aditivo.** Agregar una feature = crear archivos SOLO
  bajo `plugins/<id>/` + su manifest. CERO ediciones a archivos centrales.
  Si "necesitás" tocar el shell o la plataforma, falta un *contribution
  point*: se propone el mecanismo, no se perfora el archivo.
- **INV-2 · Toggle simétrico.** Un set de habilitación (`ENABLED_PLUGINS`)
  gobierna la presencia del plugin en TODAS las capas (API, frontend, menús,
  deploy, eventos). Apagar = desaparece sin romper; prender = aparece entero.
  No existe el estado parcial.
- **INV-3 · Manifest veraz.** Toda declaración del manifest está atada a
  código real por un check automático, en tres capas: **compilación** (tipos,
  default-exports), **boot fail-fast** (deps, módulos, rutas existen) y **CI**
  (conformance). Regla de oro: *ningún campo nuevo del manifest sin su check
  en el mismo PR* — un campo que nadie verifica es una mentira en potencia.
- **INV-4 · Superficie única.** Los plugins importan SOLO el SDK (y sus
  propias entrañas). La plataforma interna es privada. En greenfield este
  candado nace DURO (sin allowlists ni migraciones): el primer commit ya
  tiene el contrato de imports activo.
- **INV-5 · El que genera, audita.** Cada arquetipo se define UNA vez (un
  perfil declarativo) y de esa única fuente derivan el scaffolder (lo
  genera), el TestKit (lo audita de por vida) y el catálogo (lo dibuja). Es
  estructuralmente imposible que el template y el gate diverjan.

---

## §2. Glosario (los nombres técnicos)

| Concepto | Qué es | Término industria |
|---|---|---|
| **Plugin** | Slice vertical de capability: frontend + API (+ workers), self-contained y togglable | vertical slice architecture |
| **Manifest** (`plugin.yaml`) | LA fuente de verdad declarativa del plugin: identidad, arquetipo, contribuciones, deps | schema-first contract |
| **SDK** | La única superficie de import legal para plugins: Foundation + Kits | stable public API boundary |
| **Foundation** | El núcleo del SDK: manifest tipado, protocolos, diagnósticos, lifecycle | Protocol-Oriented Programming (PEP 544) + IoC |
| **Kit** | Sub-librería del SDK por capability (uikit, connectorkit, testkit, + kits de dominio) | framework/kit layering (estilo Apple) |
| **Protocolo** | Interfaz estructural que el plugin implementa y la plataforma invoca | `typing.Protocol` / structural typing |
| **Arquetipo** | Identidad arquitectónica del plugin, declarada en el manifest y auditada de por vida (≠ template, que es el acto generador de un día) | conformance profile + ArchUnit-style rules |
| **TestKit (TCK)** | Suite de conformance que el SDK provee y cada plugin instancia en 3 líneas | Technology Compatibility Kit + architectural fitness functions |
| **Certificación C0–C3** | Niveles de conformidad computados por el TCK → reporte JSON | conformance program (modelo CNCF) |
| **Diagnóstico** | Error con código, mensaje y fix sugerido (`E-ARCH-003`), explicable desde el CLI | compiler diagnostics (estilo `rustc --explain`) |
| **CLI** | El ciclo de vida hecho comandos: `create / check / certify / dev / graph / explain / upgrade` | golden path / paved road |
| **Catálogo** | UI que muestra el grafo del sistema + el estado de certificación; lo no conforme va a cuarentena | software catalog + scorecards (Backstage) |
| **Port** | Contrato interno de una capability externa (vive en el SDK; es lo único que los plugins ven) | hexagonal: puerto del lado *driven* |
| **Connector** | Adapter por vendor que implementa N ports + traduce modelos | adapter + Anti-Corruption Layer (DDD) |
| **Fake** | Doble in-memory oficial de cada port (testear sin el vendor) | test double / in-memory adapter |
| **Contract suite** | Suite de contrato por port que todo connector instancia; corre contra fake Y vendor real | contract testing |
| **Contribution point** | Mecanismo por el que el plugin DECLARA y el shell AGREGA (menús, secciones, rutas, comandos) | extension points (modelo VS Code) |
| **Cast** | Consumo declarado de datos de otro plugin vía contrato versionado, bajo el API propio | published contract; no shared entities |

---

## §3. Los 4 canales de comunicación (lo único permitido entre partes)

1. **Ports de plataforma** — el plugin depende HACIA ABAJO de contratos del
   SDK; jamás de lado hacia otro plugin.
2. **Eventos declarativos** — el manifest declara `emits` y `transitions`;
   un dispatcher genérico (string-based) rutea. SOFT por diseño: targets
   apagados se skipean (INV-2). Nadie importa a nadie.
3. **Casts declarados** — datos de otro plugin SOLO vía `consumes:` en el
   manifest (provider + contrato versionado) y un endpoint propio que
   traduce. Ninguna entity se comparte entre plugins.
4. **Contribution points** — menús, secciones, íconos, comandos, widgets: el
   plugin declara en su manifest; el shell, 100% data-driven, agrega.

Si una necesidad no entra en los 4 canales, la respuesta NO es una excepción:
es proponer un canal/contribution point nuevo en el SDK (con ADR).

---

## §4. Anatomía del sistema (layout greenfield recomendado)

La ventaja de nacer con la plataforma: el slice vertical puede ser FÍSICO —
**un directorio por plugin con ambos stacks adentro** (el proyecto madre no
pudo; un greenfield sí):

```
<repo>/
├── plugins/<id>/                  ← EL plugin entero, un solo lugar
│   ├── plugin.yaml                ← manifest (única copia)
│   ├── frontend/                  ← mini-FSD: index.ts (default-export Page),
│   │                                 pages/ features/ entities/ icons.tsx
│   ├── backend/                   ← api/ (router) · domain/ (puro) · según arquetipo
│   └── tests/                     ← conformance (3 líneas) + tests de dominio
├── sdk/
│   ├── python/                    ← foundation/ kits/ testkit/ archetypes/ cli/ diagnostics/
│   └── ts/                        ← uikit: plugin-host, tipos PluginModule, helpers Zod
├── platform/                      ← implementación PRIVADA (solo el SDK la importa)
│   ├── backend/                   ← config, logging, loader, graph builder, dispatcher
│   └── connectors/<vendor>/       ← adapters de vendors detrás de ports (§11)
├── shell/
│   ├── tauri/                     ← el cascarón nativo (Rust, mínimo)
│   └── web/                       ← app React: bootstrap + shell data-driven +
│                                     plugin-registry.generated.ts (codegen)
├── server/main.py                 ← composition root FastAPI (monta routers habilitados)
├── schema/plugin.schema.yaml      ← LA fuente del manifest (deriva pydantic + Zod)
└── .platform/certification/       ← reportes JSON por plugin (gitignored; CI los publica)
```

*Fallback honesto:* si el tooling de tu stack pelea con el layout unificado
(resolución de packages Python dentro de `plugins/*/backend`, aliases del
bundler), el plan B probado es espejo-por-stack con **manifest único** — el
invariante es "una copia del manifest y slices completos", no el árbol exacto.

---

## §5. El SDK: Foundation + Kits

**Foundation** (lo que TODO plugin usa):
- `PluginManifest` tipado (pydantic y Zod **derivados del mismo
  `plugin.schema.yaml`** — un solo schema, cero drift entre stacks).
- Los protocolos base: `PluginAPI` (expone `router`), `PluginPage`
  (default-export del frontend), `EventEmitter`, `CastProvider/Consumer` — y
  los que cada proyecto agregue.
- `Diagnostics`: el catálogo machine-readable de reglas (código → mensaje →
  fix → docs). Una sola fuente para los errores del TestKit, el `explain` del
  CLI y los tooltips del catálogo.
- Lifecycle: `validate_enabled` (deps al boot), `ensure_plugin_enabled`
  (autogate de procesos), registro/descubrimiento de manifests.

**Kits núcleo** (todo proyecto los tiene):
- **uikit** (TS): PluginHost (contexto shell↔plugin: selección, paneles — los
  Pages NO reciben props), helpers de entity (api + contracts Zod + query
  keys), tipos del registry.
- **connectorkit**: ports de capability + base classes de connector (§11).
- **testkit**: el TCK (§8).

**Kits de dominio** (se agregan cuando el dominio los pide, como ADR + fase):
un proyecto con agentes LLM agregará un `agentkit`; uno con jobs durables, un
kit de workflows (Temporal u otro); uno con facturación, un `billingkit`. La
regla es siempre INV-5: kit nuevo ⇒ protocolos + checks + template + catálogo
en el mismo movimiento.

**El lockdown (INV-4):** contrato de imports activo desde el commit 1 — los
plugins importan `sdk.*` y a sí mismos; `platform.*` les es invisible
(import-linter en Python, dependency-cruiser en TS, y un check AST espejo en
el TestKit). En greenfield NO hay modo ratchet: es prohibición dura de
nacimiento.

---

## §6. El manifest (schema-first)

Campos núcleo mínimos de la v1 (cada proyecto extiende — siempre con INV-3):

```yaml
id: invoices                 # == nombre del dir; regex ^[a-z][a-z0-9_]*$
version: 0.1.0
archetype: full_stack        # identidad auditada de por vida (§7)
requires_sdk: ">=1.0"        # compatibilidad; el loader la valida al boot
display_name: Facturas
depends_on: []               # deps duras entre plugins (boot las exige)
consumes: []                 # casts declarados (canal 3)
frontend:
  contributes:
    sidebar:                 # ítems de menú del shell (contribution point)
      - { route: /invoices, label: Facturas, icon: receipt }
    sections:
      - { key: invoices, label: Facturas, order: 30 }
api:
  module: plugins.invoices.backend.api    # expone `router`
  prefix: /api/invoices                   # SIEMPRE bajo el id propio
emits: []                    # eventos que publica (canal 2)
```

Reglas estructurales: el `id` es la llave de TODO (dir, prefix de API, alias
de imports, queue si hubiera workers); el prefix de API de un plugin jamás
aparece en el código de otro (los datos ajenos llegan por cast); el schema es
LA fuente y los modelos tipados de ambos stacks se derivan/contract-testean
contra él.

---

## §7. Arquetipos: la arquitectura interna obligatoria

El concepto central anti-spaghetti: **template ≠ arquetipo**. El template es
un acto generador (día 0); el arquetipo es una **identidad declarada en el
manifest y auditada de por vida** (día 400). Sin esto, la gente "implementa
los kits" y por dentro el plugin degenera.

- Cada arquetipo es un **perfil declarativo** en el SDK
  (`sdk/archetypes/<nombre>`): dirs requeridos/permitidos, DAG de capas
  internas, reglas de import entre capas, protocolos que debe implementar,
  patrones prohibidos.
- De ese perfil único derivan tres cosas (INV-5): el scaffolder lo genera, el
  TestKit lo audita (familia `E-ARCH-*`: estructura + **import-graph interno
  del plugin** contra el DAG de capas, estilo ArchUnit), el catálogo lo
  dibuja.
- **No existe `archetype: custom`.** Si un plugin no encaja, no se lo exime:
  se crea/extiende un arquetipo EN el SDK (con ADR). El mecanismo se
  arregla; el contrato no se perfora.

**Arquetipos semilla del proyecto nuevo** (el set crece con el dominio):

| Arquetipo | Forma | Reglas internas (ejemplos enforced) |
|---|---|---|
| `panel` | frontend-only: menú + sección del shell | mini-FSD `entities → features → pages`; Zod en todo boundary HTTP; Page sin props (PluginHost); fetch solo vía client compartido |
| `full_stack` | panel + router API propio | lo anterior + backend `api/` (routers delgados) → `domain/` (lógica pura: sin I/O, sin vendors) → `adapters/` opcional vía ports |

Cuando el dominio lo pida se agregan más (ejemplos del mundo real: `agentic`
para workers conversacionales, `notifier` para push puro, `sync` para
pipelines source→sink con read model local y reconciliación idempotente —
cada uno con su perfil, nunca como excepción ad-hoc).

---

## §8. El compilador emulado: TestKit + certificación

Python/TS no compilan la arquitectura — se emula el compilador en tres
piezas:

**1) El TCK instanciado.** El TestKit vive en el SDK (una suite parametrizada
por plugin); cada plugin lo INSTANCIA — no lo copia — así un check nuevo en
el SDK upgradea a toda la flota sin drift:

```python
# plugins/invoices/tests/test_conformance.py   (el archivo ENTERO)
from sdk.testkit import conformance_suite
globals().update(conformance_suite("invoices"))
```

Y el check `E-MAN-001` exige que ese archivo exista e invoque la suite:
**plugin sin su TCK = no compila** (CI rojo, no mergea). Los tests de dominio
del plugin conviven al lado; el TCK cubre arquitectura, el plugin su lógica.

**2) Diagnósticos con código.** Las reglas se agrupan en familias, cada una
con mensaje y fix sugerido, explicables (`<cli> explain E-ISO-002`):

| Familia | Cubre |
|---|---|
| `E-MAN-*` | manifest: schema, veracidad declaración↔código, TCK presente |
| `E-ISO-*` | aislamiento: imports cross-plugin, ediciones a archivos centrales, prefix ajeno |
| `E-DEP-*` | deps y toggle: `depends_on` habilitables, `requires_sdk`, estado parcial |
| `E-ARCH-*` | perfil del arquetipo: capas, import-graph interno, protocolos |
| `E-UI-*` | frontend: FSD, Zod en boundary, registry, contribution points |
| `E-CON-*` | connectors: port sin fake, connector sin contract suite, vendor filtrado |
| `E-BHV-*` | comportamiento (C3): specs sin escenario testeado, eval bajo umbral |

**3) Niveles de certificación** (computados a reporte JSON por plugin, en
`.platform/certification/<id>.json`, con `git_sha` y edad — un reporte stale
se degrada a "sin certificar", nunca se inventa):

| Nivel | Significa | Cómo se computa |
|---|---|---|
| **C0 — Declarado** | el manifest parsea y valida contra el schema | en vivo, barato |
| **C1 — Cargable** | boot checks: deps, módulos, rutas, contribuciones existen | en vivo |
| **C2 — Certificado** | TCK completo verde (incluido el perfil del arquetipo) | `certify` local o CI |
| **C3 — Verificado** | conducta: specs de capability vinculadas y testeadas + smoke E2E (+ evals si hay LLM) | reporte extendido |

Decisión deliberada: **la certificación gobierna el MERGE y el catálogo, no
el runtime.** En producción mandan el toggle y el boot fail-fast; un reporte
viejo jamás puede tumbar un proceso vivo. Y la lección que motiva C3: *tests
verdes ≠ feature viva* — la estructura perfecta no garantiza que el sistema
EMITA lo que la UI espera; eso solo lo prueba el comportamiento.

---

## §9. El CLI (el ciclo de vida hecho comandos)

Un solo binario (acá `plat` — renombralo a tu producto), pensado para humanos
Y para agentes de código (los pipelines lo invocan en vez de seguir recetas
en prosa). **Orden de construcción deliberado: el verificador antes que el
generador** — un `create` sin `check` genera basura con confianza.

| Verbo | Qué hace |
|---|---|
| `plat check [<id>]` | el **compilador rápido**: schema + protocolos + import contract + paridad manifest↔código. Estático, segundos, sin red. Salida estilo rustc: `error[E-ARCH-003]: … fix: …` |
| `plat certify [<id>]` | `check` + TCK completo + escribe el reporte JSON + resumen C0–C3 |
| `plat create plugin <id> --archetype <a>` | scaffold completo desde el PERFIL (no desde copias): manifest + ambos stacks + archivo TCK + registry/codegen actualizado + `check` final. **Nace C2** |
| `plat create connector <vendor> --ports <p,…>` | scaffold del connector: skeletons de los ports + ACL + contract suites instanciadas + manifest del connector (§11) |
| `plat explain <código>` | diagnóstico + fix + ejemplo, desde el catálogo de diagnósticos |
| `plat graph` | grafo del sistema (JSON/mermaid) o abre el catálogo |
| `plat dev` | levanta el stack local (shell Tauri + FastAPI sidecar + watch) |
| `plat upgrade` | re-scaffold con 3-way merge cuando el SDK/los perfiles evolucionan (mantener la flota al día) |

El test de oro del generador, en CI: **crear un plugin de cada arquetipo y
exigir que nazca C2** (después se borra). El scaffolder queda atado al TCK
por construcción.

---

## §10. El catálogo (developer portal con cuarentena)

El catálogo es parte de la plataforma desde el día 0 — y se **dogfoodea**: es
un plugin más del sistema (arquetipo `panel` + un endpoint de plataforma
`/api/platform/graph` + lectura de los reportes de certificación).

- **Grafo**: nodos = plugins, contribuciones, routers, connectors; edges =
  depends_on, casts, eventos, provides-port. Auto-derivado de los manifests.
- **Badges C0–C3** por plugin; sección **Cuarentena**: lo que no certifica
  aparece (no se oculta) con sus checks fallidos, el fix de cada uno y el
  comando `plat explain` correspondiente.
- **Scorecard** por plugin: nivel, checks, edad del reporte, protocolos
  implementados, deps/consumers.

Es "el lugar donde validás tu plugin antes del PR": `plat certify` + abrir el
catálogo. Y para el equipo, el mapa vivo del sistema que nunca driftea porque
se deriva de la misma fuente que el compilador.

---

## §11. ConnectorKit: los sistemas externos

Todo sistema externo (commerce, CRM, mensajería, pagos, observabilidad) entra
por el mismo molde — **el lado driven del hexágono, formalizado**:

- **Capability ports** — contratos internos en el SDK
  (`sdk.connectorkit.ports`): `OrderQueryPort`, `CrmContactsPort`,
  `MessagingPort`… Es lo ÚNICO que los plugins ven.
- **Connectors** — adapters por vendor (`platform/connectors/<vendor>/`):
  implementan N ports + su **ACL**: los modelos del vendor JAMÁS cruzan la
  frontera del connector. Un vendor = un connector que puede proveer varios
  ports.
- **Binding config-driven** (Strategy/Registry): `CONNECTOR_ORDERS=<vendor>`
  por deployment. Cambiar de vendor = escribir un connector + 1 env; CERO
  cambios en plugins.

Las cuatro reglas duras (`E-CON-*`):

1. **Ningún port sin fake** — cada port shippea su doble in-memory oficial;
   los plugins testean sin red y sin credenciales.
2. **Ningún connector sin contract suite** — el SDK provee una suite de
   contrato por port; el connector la instancia (3 líneas). La MISMA suite
   corre contra el fake (CI siempre) y contra el vendor real (smoke opt-in):
   si el fake y el vendor divergen, lo caza CI, no producción.
3. **Plugins importan ports, jamás vendors** — el nombre del vendor es un
   detalle de deployment, no de dominio.
4. **HTTP honesto por defecto** — la base class de connectors trae las
   cicatrices de producción: timeout dimensionado por la CADENA del upstream
   (no por el hop local); taxonomía de errores que no miente (fallo de
   conexión ⇒ "NO se aplicó"; timeout ⇒ "resultado DESCONOCIDO, verificá
   antes de reintentar" — nunca traducir un timeout a "no pasó nada");
   retries con idempotencia real (fingerprint + pre-check, converger en vez
   de asumir); cache de mapeos inmutables (contra upstreams lentos se
   ELIMINAN llamadas, no se adelgazan).

Para flujos de sincronización entre dos externos (vendor A → read model
local → vendor B), el patrón es **source/sink**: un arquetipo de pipeline
cuyo corazón es un *diff plan* computado puro (testeable sin red) y un apply
idempotente — con el golden test "la segunda corrida produce diff vacío".

---

## §12. La semilla concreta: Tauri + FSD + FastAPI

Lo que el proyecto nuevo adopta como stack inicial (todo lo anterior no
depende de esto; esto es la encarnación v1):

**Frontend — Tauri 2 + React/TS + FSD estricta:**
- El cascarón Tauri (Rust) es mínimo: ventana, lifecycle del sidecar, y a
  futuro contribution points nativos (tray, atajos globales, deep links —
  cuando existan, serán campos del manifest con su check, INV-3).
- Adentro, la app React con **Feature-Sliced Design** y flujo de imports
  bottom-up: `shared → entities → features → pages`; los plugins traen su
  mini-FSD y solo importan de `shared/sdk` y de sí mismos.
- **El shell es 100% data-driven**: sidebar/menús/secciones se renderizan del
  `plugin-registry.generated.ts` (codegen prebuild desde los manifests,
  gateado por `ENABLED_PLUGINS` — el gating frontend es build-time: una
  distribución por set de plugins). Ningún plugin edita el shell (INV-1).
- PluginHost por contexto (los Pages no reciben props); lazy import por
  plugin (code-splitting natural); Zod parsea TODO boundary HTTP, y toda
  query con default `[]` muestra su estado de error (un boundary estricto sin
  error visible convierte cualquier drift en "pantalla vacía sin
  diagnóstico").

**Backend — FastAPI mínimo:**
- `server/main.py` es el composition root: lee manifests → `validate_enabled`
  (deps, INV-2) → monta `/api/<id>` por plugin habilitado → expone
  `/healthz` y `/api/platform/{graph,certification}` para el catálogo.
- Corre como **sidecar** del shell Tauri en desktop (el shell lo lanza,
  health-checkea y apaga; puerto por env) — y como servicio normal en
  server/web. El SDK es agnóstico del transporte: la frontera siempre es
  HTTP + Zod.
- Sin workers ni colas en v1. Cuando aparezca el primer job durable, entra
  como kit + arquetipo nuevos (con su perfil y sus checks), no como carpeta
  suelta.

**Tooling:** `uv` + ruff + pytest (backend) · vite + vitest +
dependency-cruiser (frontend) · el CLI `plat` como entry point de todo.

---

## §13. Génesis: el orden de construcción desde cero

La ventaja day-zero sobre una migración: **no hay ratchets ni allowlists** —
cada candado nace duro. Cada fase es PR-sized, cierra con su gate activo y
deja el repo verde.

| Fase | Entregable | Gate que nace con ella |
|---|---|---|
| **G0 · Génesis** | monorepo + `schema/plugin.schema.yaml` v1 + `sdk/` esqueleto + CI esqueleto | contrato de imports DURO (INV-4): `plugins → sdk` only, ambos stacks |
| **G1 · Foundation** | manifest tipado (pydantic+Zod del mismo schema) + diagnostics + loader/`validate_enabled` + codegen del registry frontend | contract test schema↔modelos; `E-MAN-*`, `E-DEP-*` |
| **G2 · Compilador** | testkit + `conformance_suite()` + reporte JSON + `plat check / certify / explain` | `E-MAN-001` (TCK obligatorio); CI corre certify |
| **G3 · Generador** | perfiles `panel` y `full_stack` + `plat create plugin` | `E-ARCH-*` (P-perfil); test de oro "nace C2" en CI |
| **G4 · Shell vivo** | Tauri shell data-driven + FastAPI composition root + wiring sidecar + el PRIMER plugin real (creado por el CLI, no a mano) | `E-UI-*` (FSD, Zod, registry); smoke: app levanta con N y con N−1 plugins (INV-2) |
| **G5 · Catálogo** | plugin `catalog` (dogfooding) + `/api/platform/graph` + badges/cuarentena/scorecards | test del builder: plugin roto aparece en cuarentena con diagnóstico correcto |
| **G6 · Merge gate** | job CI `plugin-certification` required + branch protection + scorecard-diff en PRs | < C2 no mergea |
| **G7 · ConnectorKit** | (cuando llegue el primer vendor) ports + connector + fakes + contract suites + binding | `E-CON-*` completo |
| **G8+ · Kits de dominio** | agentkit / workflows durables / lo que el producto pida — siempre: protocolos + perfil + checks + template + catálogo juntos (INV-5) | los suyos |

G4 es paralelizable desde G1 si se quiere app visible temprano — pero el
primer plugin real se crea con el CLI o no se crea (es el test de que la
plataforma funciona).

---

## §14. Reglas de evolución

- **Regla de oro (INV-3/INV-5 operativa):** protocolo, campo de manifest,
  port o arquetipo nuevo ⇒ su check en el TestKit + su template en el CLI +
  su representación en el catálogo, EN EL MISMO PR.
- **Versionado del SDK:** semver + `requires_sdk` en el manifest (el loader
  valida compatibilidad al boot); deprecación = warning → diagnóstico en
  `check` → remoción en major; `plat upgrade` (3-way merge) mueve la flota.
- **Plugins instalables (evolución, no v1):** como los plugins solo dependen
  del SDK, empaquetarlos como artefactos instalables (wheel/npm) es un paso
  mecánico posterior — el boundary ya existe. Hacerlo cuando haya segundo
  repo/tenant que lo justifique.
- **Lecciones vivas:** el documento de arquitectura del proyecto lleva una
  sección append-only de lecciones (`L-1, L-2, …`) con formato fijo
  (Síntoma → Causa raíz → Fix → Regla → Guard). Cada incidente real que pase
  un gate o confunda a un dev entra ANTES de cerrar el incidente — y su
  "Guard" se convierte en check del TestKit cuando es mecanizable.
- **Specs de comportamiento:** cada capability mantiene su spec persistente
  (Requirements SHALL/MUST + Scenarios Gherkin); C3 las exige vinculadas en
  el manifest y testeadas. El código dice CÓMO; las specs dicen QUÉ.
- **Jerarquía de fuentes:** código vivo > este blueprint > cualquier otro
  doc. Una contradicción no se discute: se corrige el doc y se escribe la
  lección.

---

## §15. Qué NO es (anti-scope deliberado)

- **No es un marketplace** ni carga dinámica de plugins en runtime: el set se
  define por build/deployment (INV-2). Simple, auditable, suficiente.
- **No son microservicios:** monorepo de slices verticales con un boundary de
  SDK. La distribución física llega (si llega) después, gratis, gracias al
  boundary.
- **No es un framework de UI ni de agentes:** es la PLATAFORMA donde esos
  frameworks viven como kits.
- **No es burocracia:** el costo por plugin es un manifest + un archivo de
  conformance de 3 líneas; a cambio desaparecen el copy-paste, las recetas
  manuales y las revisiones de arquitectura a ojo.

---

## §16. Cómo usar este documento

1. Es la constitución del repo nuevo: va en la raíz desde el commit 1 y el
   `CLAUDE.md` / guía del agente lo referencia como fuente de los invariantes.
2. Ejecutá la génesis en orden (§13); no construyas features antes de G3 —
   el primer plugin real DEBE nacer del CLI.
3. Toda decisión que tense un invariante se resuelve con ADR que modifica el
   MECANISMO (canal, contribution point, arquetipo, port nuevos) — nunca con
   una excepción puntual.
4. Renombrá `plat`, los niveles y las familias de diagnósticos al gusto del
   producto; los conceptos y los invariantes no se renegocian sin ADR.

**Fin.** Si algo de este blueprint contradice el código vivo del proyecto
que lo adopte, gana el código — y esa contradicción es la primera lección
L-1 de su sección viva.
