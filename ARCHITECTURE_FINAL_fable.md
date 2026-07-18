# Arquitectura AgencyHubara — documento final (fable)

> **Qué es.** La descripción COMPLETA y prescriptiva de la arquitectura de
> plugins post-refactor F1–F8 (2026-06-10). Este documento es la **semilla del
> skill de desarrollador**: un agente que lo internalice debe poder programar
> features/plugins en este repo **sin poder romper la arquitectura** — no por
> disciplina, sino porque conoce los invariantes, sigue las recetas, y sabe
> exactamente qué gate lo va a frenar y por qué.
>
> **Documento VIVO.** La §9 (Lecciones de validación) se alimenta durante la
> fase de validación en vivo y cada vez que un error real pase un gate o
> confunda a un desarrollador. Regla: cada lección entra con el formato de §9
> ANTES de cerrar el incidente que la generó.
>
> **Jerarquía de fuentes:** el código vivo > este doc > cualquier otro doc.
> Complementos: [PLUGIN_CONTRACT.md](PLUGIN_CONTRACT.md) (la ley),
> [PLUGIN_PROTOCOL_fable.md](PLUGIN_PROTOCOL_fable.md) (el protocolo),
> `hubara-architecture-guide` (sections detalladas por capa).

---

## §1. El modelo mental en una página

El sistema es un **monorepo de vertical capability slices**: cada plugin es
una funcionalidad completa (frontend + API + agente/worker opcional) poseída
de punta a punta, **self-contained y togglable**. Dos invariantes lo gobiernan
— si una decisión los viola, la decisión está mal:

- **INV-1 — Aislamiento aditivo:** agregar un plugin = crear archivos SOLO
  bajo `plugins/<id>/` (ambos stacks) + su `plugin.yaml`. CERO ediciones a
  archivos centrales. Si "necesitás" tocar un archivo central, falta un
  contribution point — se arregla el mecanismo, no se toca el archivo.
- **INV-2 — Toggle simétrico:** `ENABLED_PLUGINS` (csv) gobierna la presencia
  del plugin en TODAS las capas (API, workers, deploy, frontend, dispatcher,
  rutas, agentes visibles). Apagar = desaparece sin romper; prender = aparece
  entero. No existe estado parcial.

**Los 4 canales** (única comunicación cross-plugin permitida):

1. **Platform ports** — depender HACIA ABAJO de `src/platform/*`; jamás de
   lado hacia otro plugin.
2. **Eventos declarativos** — `emits`/`transitions` del manifest + dispatcher
   genérico (string-based). SOFT: el dispatcher skipea targets apagados.
3. **Casts declarados** — datos de otro plugin SOLO vía `consumes:` en el
   manifest + cast server-side bajo el API propio. Ninguna entity se comparte.
4. **Contribution points** — sections, sidebar, íconos, rutas de conversación,
   agentes: el plugin DECLARA en su manifest, el shell/platform AGREGA.

**El protocolo en 3 capas** (por qué no se puede mentir): compilación
(`assertPluginModule`, tipos del PluginHost) → boot fail-fast
(`validate_enabled`, `ensure_plugin_enabled`, route registry, router checks) →
CI (conformance gates que atan CADA campo del manifest al código). Regla de
oro: **ningún campo nuevo del manifest sin su check en el mismo PR.**

---

## §2. Mapa del sistema (dónde vive cada cosa)

```
AgencyHubara/
├── frontend_dashboard/src/plugins/<id>/
│   ├── plugin.yaml                  ← EL manifest (única copia; el backend lo lee de acá)
│   └── frontend/
│       ├── index.ts                 ← default-exporta el componente Page (verificado en compilación)
│       ├── pages/ features/         ← mini-FSD del plugin
│       ├── entities/<entity>/       ← api.ts + contracts.ts(Zod) + keys.ts + model.ts + index.ts
│       └── icons.tsx                ← (opcional) glifos propios: export const icons = {...}
├── hubara_agency/src/plugins/<id>/
│   ├── api/                         ← __init__.py expone `router` (prefix /api/<id>)
│   │   └── <cast>.py                ← casts server-side (si consume de otro plugin)
│   ├── agent/<worker>/              ← workflows/ activities/ tools/ use_cases/ workspace/
│   │   ↑ CONVENCIÓN DURA: anida por worker-name aunque haya UNO solo (PM-7)
│   ├── workers/<worker>.py          ← async def main() — 1ª línea: ensure_plugin_enabled("<id>")
│   └── shared/contracts/events.py   ← eventos que EMITE (frozen dataclasses)
├── hubara_agency/src/platform/      ← compartido: plugin_manifest, plugin_loader (P-6),
│   │                                  plugin_runtime (P-21), routing (registry F6),
│   │                                  plugin_protocol, orchestration/ (dispatcher), whatsapp/, orders/, ...
│   └── constants.py                 ← SOLO rutas core (ventas/remarketing/humano) — spinal
├── hubara_agency/scripts/render-compose.py  ← genera docker-compose.local.yml gateado por ENABLED_PLUGINS
├── hubara_agency/k8s/aws-produccion/        ← hand-maintained; cada deployment lleva ENABLED_PLUGINS
├── hubara_agency/.hubara/spinal-files.yaml  ← FUENTE ÚNICA de paths PROTECTED (ambos meta-gates la leen)
├── frontend_dashboard/scripts/plugins-sync.ts ← codegen registry (gateado, valida deps, mergea íconos)
├── frontend_dashboard/src/shared/lib/plugin-host.tsx ← usePluginHost / useSelection
├── frontend_dashboard/src/entities/  ← VACÍO POR CONTRATO (P-11)
└── frontend_dashboard/src/pages/Dashboard.tsx ← shell 100% data-driven; NO SE EDITA por plugin
```

Gates: `hubara_agency/tests/architecture/` (conformance + contract +
orchestration-consistency + DEHA R-rules) · `tests/plugins/
test_premortem_invariants.py` (deploy: queues, k8s, compose, P-20/P-25) ·
`frontend_dashboard/src/test/architecture/` (cruiser, P-11/P-22/P-23, íconos,
registry, zod, meta-gate). CI: `.github/workflows/architecture-gates.yml`
corre TODO en cada PR y **bloquea merge a main**.

---

## §3. Reglas duras (qué te frena cada gate)

| Si hacés esto… | Te frena | Fix |
|---|---|---|
| Importar `src.plugins.Y` desde el plugin X | P-3 (AST) | canal 1/2/3 |
| Importar `@plugins/Y` o la entity de otro plugin | dep-cruiser + P-22 | cast (canal 3) |
| String `/api/<otro>/` en tu frontend (hasta en comments) | P-9 + P-23 | tu cast bajo `/api/<tu-id>/` |
| Crear una entity en `src/entities/` central | P-11 | `plugins/<id>/frontend/entities/` |
| Import relativo `../../` cross-capa o alias `@/entities/` | dep-cruiser + P-11b | alias `@plugins/<id>/frontend/...` |
| `platform/` importando un plugin | P-4 + import-linter | invertir: port en platform |
| Manifest declarando módulos de otro plugin | P-1 | mover el código a tu plugin |
| Manifest con backend declarado sin código (o dir sin manifest) | P-2 / P-26 | crear el código o borrar |
| Habilitar un plugin sin sus `depends_on` | P-6 (boot + test) | habilitar deps o quitar el plugin |
| Worker sin `ensure_plugin_enabled("<id>")` primero en `main()` | P-21 (AST) | agregarlo |
| Worker con `get_task_queue("<otro>", ...)` | P-16 (AST) | su propio (plugin, worker) |
| `agentic` incoherente con bloques `dashboard:` | P-17 | corregir flag o bloque |
| `dashboard.workspace` que no existe en disco | P-15 | corregir path |
| `workflow_classes` sin `@workflow.defn(name=)` real | orchestration-consistency (AST) | alinear manifest↔decorator |
| Transition con `on_event` no declarado en `emits` / target inexistente | orchestration-consistency | declarar |
| Hardcodear el workflow-id de una ruta ajena / driftear su prefijo | P-18 ×3 | `owns_route` + registry |
| Ícono nuevo editando `Icon.tsx` | (P-12 te deja, INV-1 no) | `frontend/icons.tsx` del plugin |
| Editar compose generado a mano / olvidar regenerar | drift test + P-20 | `render-compose.py` |
| Worker en manifest sin k8s yaml (o viceversa) | paridad k8s ×2 | crear/borrar el yaml |
| `wiring_intents.env_vars_required` ausente del compose | P-25 | declarar el env en `compose.env` del worker |
| Queue duplicada / sin declarar | premortem ×2 | queue única en el manifest |
| Tocar un path PROTECTED sin label | meta-gates (ambos stacks) | label `architecture-change` + ADR |
| Entry de plugin sin default-component | `tsc -b` (assertPluginModule) | `export default Page` |
| `consumes:` sin provider∈depends_on o cast inexistente | P-14 | completar el contrato |

---

## §3.5 TDD obligatorio — rojo → verde → refactor (cómo se programa acá, SIN excepción)

> Las reglas de §3 dicen QUÉ no romper; ésta dice **CÓMO se escribe cada línea
> de producción**: detrás de un test que falla primero. No es opcional ni
> "cuando hay tiempo" — es el método. El panel §8 hace el "verde"
> determinístico; TDD agrega la única disciplina que el panel no puede
> imponer solo: **el rojo va primero**.

### Las tres leyes (adherencia máxima — se cumplen las tres, siempre)

1. **No escribís código de producción** hasta tener un test que **falla** y lo
   exige.
2. **No escribís más test** del mínimo suficiente para fallar (un error de
   import/colección NO cuenta como rojo válido — ver abajo).
3. **No escribís más código de producción** del mínimo suficiente para pasar
   ese único test rojo.

El ciclo es de **minutos, no de horas**: un comportamiento atómico por vuelta.
Si te encontrás escribiendo 5 tests antes de una línea de código, o 50 líneas
de producción para "un" test, parate — rompiste el tamaño de paso.

### El bucle, concreto en este repo

- **ROJO** — escribí el test del **siguiente incremento de comportamiento** y
  corré SU comando del panel §8; **velo fallar con un assert con sentido**
  (`AssertionError: esperaba X, vino Y`), no con `ImportError`/`fixture not
  found`/error de colección. *Un rojo por la razón equivocada es un falso
  rojo* — el test todavía no prueba nada. El nombre del test ES la spec
  (`test_<sujeto>_<condición>_<resultado_esperado>`).
- **VERDE** — el **mínimo** código de producción para que ese test pase.
  Hardcodear para pasar el primer caso es legítimo: el segundo test
  (triangulación) te obliga a generalizar. Corré el comando: verde.
- **REFACTOR** — con el test de red de seguridad, limpiá test Y producción
  (nombres, duplicación, diseño) y mantené **todo §8 verde**. El test es el
  primer consumidor de tu API: si es feo de testear, el diseño está mal —
  arreglá el diseño, no el test.

### Qué harness usa cada capa (dónde nace el test PRIMERO)

| Capa (DEHA / FSD) | Harness del test | Vive en |
|---|---|---|
| Dominio / use-case **puro** | pytest directo, sin mocks salvo ports inyectados | `tests/test_<x>.py` |
| Activity | `ActivityEnvironment().run(activity, ...)` + `monkeypatch`/`tmp_path` | `tests/test_<x>_activity.py` |
| Workflow | `WorkflowEnvironment.start_time_skipping()` + activities **fake** con tracker (R-DET lo hace 100% determinista) | `tests/test_<x>_workflow*.py` (patrón: `test_sales_workflow_debounce.py`) |
| Tool del agente | llamar `execute_with_context(ctx, **params)` con fakes; assert sobre el **decision payload** | `tests/test_<tool>_tool.py` |
| Frontend entity | Zod parsea un fixture del **shape real del backend** | `entities/<e>/contracts.test.ts` |
| Frontend feature | vitest sobre comportamiento (no implementación) | `features/<f>/...test.tsx` |
| **Gate / check nuevo** (regla de oro) | el **caso NEGATIVO primero**: fabricá el plugin/estado roto y probá que el gate lo CAZA | `test_testkit_selftest.py` ("el gate que nunca falla es un gate roto") |

### Bug encontrado en producción ⇒ guard ROJO antes del fix (cierra el gap de §9)

Cuando un run real revela un bug (el patrón de §9), el **primer** artefacto es
un test que **reproduce el incidente y falla** — recién entonces el fix lo pone
verde. El "Guard:" de cada lección L-# se escribe ANTES que el "Fix:", no
después. (Honestidad: varios L-# de esta campaña fueron fix-then-test bajo
presión de producción; la regla del skill es **reproduce-first** — el guard
rojo es la definición de "entendí el bug".)

### El caveat honesto (y por qué casi todo SÍ es TDD-able acá)

El comportamiento **emergente de un LLM** o una **carrera de workflow** no
siempre se puede TDD-ear de cero: no conocés el modo de fallo hasta verlo. Pero
(a) R-DET + `WorkflowEnvironment` (time-skipping) hacen que las secuencias de
workflow sean **deterministas y testeables** — la carrera de L-13 se reproduce
en un test; (b) una vez visto el modo de fallo, vale la regla de arriba (guard
rojo primero). Lo que NO es excusa: el dominio, los use-cases, los contratos
(Zod/DTOs), las tools y los gates son TDD puro — ahí no hay caveat.

### No es TDD (rechazá esto)

- Escribir el código y "después los tests" (test-after) — invierte la presión
  de diseño y normaliza el código no-testeable.
- Asertar sobre **implementación** (llamadas internas, orden de pasos privados)
  en vez de **comportamiento observable** (output, decision payload, estado).
- Over-mock: si tenés que mockear medio mundo para testear una unidad, esa
  unidad hace demasiado — es señal de diseño, no de "test difícil".
- Un test que **no puede fallar** (sin assert real, o que pasa con el código
  borrado). Si nunca lo viste rojo, no sabés si prueba algo.

### El atajo mental

Antes de tocar producción: *"¿cuál es el test que falla y exige esto?"* Si no
podés nombrarlo, todavía no entendés el incremento. La regla de oro de las 3
patas (campo/símbolo/check ⇒ su test en el MISMO PR) ya es TDD por contrato —
extendela a TODO cambio.

---

## §4. Recetas (paso a paso, sin pensar)

> Toda receta se ejecuta **test-first (§3.5)**: el primer archivo que tocás es
> el test que falla por la razón correcta; el código de producción de los pasos
> de abajo es lo mínimo para ponerlo verde. La receta dice QUÉ archivos tocar;
> el bucle rojo→verde→refactor dice EN QUÉ ORDEN y POR QUÉ.

### 4.1 Crear un plugin nuevo (full-stack)

1. `frontend_dashboard/src/plugins/<id>/plugin.yaml` — id == nombre del dir
   (regex `^[a-z][a-z0-9_]*$`): `display_name`, `frontend.contributes`
   (sections/sidebar con `icon`), `api.python_module` +
   `prefix: /api/<id>`, `agent.workers[]` si corresponde (name, module,
   `task_queue: queue-<...>` ÚNICA, `workflow_classes`, `dashboard:` si es
   agente visible ⇒ `agentic: true` top-level), `depends_on`/`consumes` SOLO
   si consume datos de otro plugin.
2. Backend `hubara_agency/src/plugins/<id>/`: `api/__init__.py` con `router`;
   workers con `ensure_plugin_enabled("<id>")` primera línea; agente bajo
   `agent/<worker-name>/` (anidado SIEMPRE); eventos propios en
   `shared/contracts/events.py` (`@dataclass(frozen=True)`, JSON-safe,
   `session_id` primero si rutea por sesión).
3. Frontend `frontend/`: `index.ts` default-exporta el Page; el Page usa
   `usePluginHost()` + `useSelection("<id>")` (NO recibe props); entities en
   `frontend/entities/<e>/` con Zod en `contracts.ts` y fetch SOLO a
   `/api/<id>/*`; glifos nuevos en `frontend/icons.tsx`.
4. `cd frontend_dashboard && npm run plugins:sync` → aparece en el registry.
5. Workers nuevos: `cd hubara_agency && uv run python scripts/render-compose.py`
   (+ crear `k8s/aws-produccion/worker-<name>.yaml` espejo, CON
   `ENABLED_PLUGINS`).
6. Verificación §8 completa. `git status` debe mostrar SOLO archivos bajo
   `plugins/<id>/` + el compose regenerado + (si worker) su k8s yaml.

### 4.2 Consumir datos de OTRO plugin (cast — canal 3)

1. En TU manifest: `depends_on: [<provider>]` + `consumes: [{provider,
   contract: <nombre>@v1, into: <entity-local>, cast: api/<modulo>}]`.
2. Implementá `src/plugins/<tu-id>/api/<modulo>.py`: router que reenvía al
   **contrato HTTP publicado** del provider (loopback
   `http://127.0.0.1:8000`, override por env) o usa un **platform port** si
   existe. Elegí HTTP cuando el endpoint del provider encapsula efectos que LE
   pertenecen (ej. `orders/schedule` emite el evento que arranca ETA);
   platform port cuando la capability ya está abstraída en platform.
3. Registralo en tu manifest (`legacy_routers` o tu router agregado) bajo
   `/api/<tu-id>/...`.
4. Tu frontend define la entity LOCAL (`into`) llamando SOLO a tu prefix.
5. NUNCA: importar código/entity del provider, llamar su `/api/` desde tu
   frontend, o invalidar sus query keys.

### 4.3 Agente conversacional con ruta propia

1. En tu worker del manifest: `owns_route: <ruta>` +
   `route_workflow_id_template: "<prefijo>-{session_id}"`. (Las core
   ventas/remarketing/humano están prohibidas; colisiones = boot error.)
2. Tu código lee SU ruta de su propio manifest
   (`get_worker_spec("<id>","<w>").get("owns_route")`) — no de constants.
3. El ruteo de inbounds (chats) la resuelve solo, vía
   `platform/routing.resolve_route_workflow_id`. Las transitions de otros
   manifests hacia tu worker deben usar TU prefijo (P-18 lo fuerza).
4. Si tu plugin NECESITA el ingest de WhatsApp (hoy vive en chats):
   `depends_on: [chats]` — honesto y enforced.

### 4.4 Toggle por deployment (runbook)

1. Editar el set EN EL ARTEFACTO: env de compose / ConfigMap k8s.
2. `cd hubara_agency && ENABLED_PLUGINS=<csv> uv run python scripts/render-compose.py`
   (falla si el set viola `depends_on` — leé el mensaje, lista TODO junto).
3. Diffear el artefacto (el delta debe ser EXACTAMENTE el semántico — PM-10).
4. `docker compose -f docker-compose.local.yml up -d --remove-orphans`
   (**SIEMPRE** `--remove-orphans`: un container huérfano viejo muere igual
   por P-21, pero mejor no tenerlo).
5. Frontend: re-build con EL MISMO set (el gating FE es build-time).
6. Verificar TODAS las superficies (PM-4): sección fuera · card de agente
   fuera · `skipped_disabled` en logs del dispatcher · `docker ps` sin su
   worker · in-flight workflows de su queue drenados o pérdida aceptada.

### 4.5 Agregar un campo al manifest (la regla de oro)

En el MISMO PR: (1) documentar el campo en `_schema/plugin.schema.yaml`;
(2) el código que LO CONSUME (si nadie lo lee, no existe); (3) el check de
conformidad en `tests/architecture/test_plugin_conformance.py` (o el gate
que corresponda) que ata campo ↔ código. El PR toca PROTECTED ⇒ label
`architecture-change`. Sin las 3 patas, el campo es una mentira en potencia
(así nació `agentic` decorativo — PM-3).

### 4.6 Extraer/mover código entre plugins

Usar el checklist §9.1 de PLUGIN_CONTRACT.md (PM-1..PM-13). Los que más
muerden: repointar `get_task_queue` self-ref (P-16 lo caza), la convención
`agent/<worker>/` anidada (PM-7), `dashboard.workspace` (P-15 lo caza),
`--remove-orphans`, y NO afirmar "100% aislado" si difieres un coupling.

### 4.7 Drenar un import `src.platform.*` grandfatherado al SDK (canal fachada)

Cuando un plugin importa `src.platform.x` directo (entrada congelada en
`tests/architecture/p28_platform_import_allowlist.txt`), el progreso es
moverlo a la fachada `src.sdk` y BORRARLO del ratchet. Pasos:

1. **Superficie**: elegí/creá el kit de su ROL en `src/sdk/<kit>.py` (canal de
   eventos UI ⇒ `dashboardkit`; orquestación ⇒ `eventkit`; etc. — un kit por
   rol, no God-module). Re-export con **alias idiom** (`from src.platform.x
   import y as y`), CERO lógica. El `ruff --fix` post-edit poda un re-export
   sin alias (L-0).
2. **Migrar** los imports de plugins a `from src.sdk.<kit> import ...`. isort
   los ordena (orden canónico: `src.platform` < `src.plugins` < `src.sdk`).
3. **Drenar**: regenerá la allowlist
   (`cd hubara_agency && MEDUSA_BASE_URL=... uv run python -m
   tests.architecture.test_p28_sdk_surface`). Las entradas migradas
   DESAPARECEN. Verificá el diff = SOLO los `-N` esperados (más = otro drift).
4. **Las 3 patas (regla de oro, MISMO PR)**: (a) **check de IDENTIDAD** en
   `tests/architecture` — `sdk.<kit>.sym is platform.x.sym` (re-export, NO
   re-implementación: si la fachada redefine un singleton como
   `get_*_bus`, los plugins usan un objeto distinto del de platform y el
   fan-out se parte en silencio); (b) template/CLI si aplica (si no,
   documentá el N/A y por qué); (c) doc en `docs/_sdk/NN-<kit>.md` + filas en
   `sdk/__init__` docstring, `sdk/CLAUDE.md` y el índice del README.
5. **Verificar**: import smoke de los kits + `lint-imports` (`sdk-no-plugins`,
   `platform-no-sdk` aguantan; `sdk → platform` es la dirección permitida) +
   `tests/architecture` con `ARCH_CHANGE_APPROVED=1`. PR toca PROTECTED ⇒
   label `architecture-change` (ver L-14 sobre cómo CI lo ve).

Ejemplo ejecutado: `dashboardkit` (canal 1, bus del dashboard) — `docs/_sdk/09`.

---

## §5. Backend esencial (DEHA, en 10 líneas)

Las 5 R-rules siguen vigentes (detalle: `hubara-architecture-guide`):
**R-DET** (workflows determinísticos — side effects a activities) · **R-JSON**
(DTOs frozen JSON-safe en el boundary; sin `from __future__ import
annotations` en módulos con dataclasses que cruzan a Temporal) · **R-STATELESS**
(estado en composition con `@lru_cache`, no en activities) · **R-HEARTBEAT**
(activities >10s) · **R-DIP** (platform ↛ plugins; siblings ↛ siblings; tools
↛ temporalio). Cross-worker = dispatcher declarativo, NUNCA imports. El
dispatcher es genérico: si te encontrás escribiendo `if plugin ==` en
platform, parate — falta una declaración en el manifest.

## §6. Frontend esencial (FSD, en 10 líneas)

Flujo de imports estricto bottom-up: `shared → entities(del plugin) →
features → pages`. Un plugin importa de `@/shared` y de SÍ MISMO (alias
`@plugins/<id>/frontend/...`); JAMÁS de otro plugin, de `@/features`,
`@/pages`, `@/app`. Zod parsea TODO boundary HTTP (en `entities/<e>/api.ts`
del plugin — el gate cubre ambas ubicaciones). `fetch` solo existe en
`shared/api/client.ts`. Los Pages no reciben props (PluginHost). El shell
(`Dashboard.tsx`, `Toolbar`) es data-driven: si tu feature "necesita" tocarlo,
falta un contribution point — proponelo como mecanismo, no como edición.

## §7. Deploy esencial

`ENABLED_PLUGINS` viaja EXPLÍCITO en todos los artefactos (compose lo inyecta
el render; k8s a mano — P-20 lo audita). El default sin env = "todos" existe
SOLO para dev local. La imagen backend copia `frontend_dashboard/src/plugins/`
(los manifests son la SSoT y el backend los lee en runtime — no rompas ese
COPY del Dockerfile). Backend `.py` ⇒ rebuild del container; frontend en local
es HMR sobre bind-mount de MAIN (ver CLAUDE.md frontend §verificación visual).

---

## §8. Verificación determinística — el panel de comandos (definition-of-done)

> **El principio.** Cada gate es un **comando exacto con exit code**, no una
> receta a interpretar. La programación es determinística porque el "¿está
> bien?" no se razona: se ejecuta el verbo y se lee el `0`/`1`. Copiá-pegá.
>
> **Obligatorio**: el prefijo `cd hubara_agency &&` / `cd frontend_dashboard &&`
> (lo enforcea el hook pre-bash, incluso si ya estás parado ahí:
> `cd /abs/repo && cd hubara_agency && uv run ...`).
>
> **Los dummies** = el trío literal `MEDUSA_BASE_URL=http://medusa.invalid
> MEDUSA_ADMIN_TOKEN=ci-dummy OTEL_SDK_DISABLED=true`, **antepuesto a cada**
> `pytest`/CLI de backend (vale para `tests/architecture`, `tests/plugins`,
> `tests/conformance`, el CLI — **NUNCA** para `tests/platform/`: cuelga en
> retries HTTP). Un agente los inline en CADA comando: **el shell no persiste
> env entre llamadas**, así que un `export` no alcanza. En las tablas de abajo
> aparecen abreviados como `$DUMMIES` — expandilo SIEMPRE al trío literal.
>
> **SDK (`src/sdk/`)**: las acciones §8.3 (certificación) y §8.4 (CLI) llegan a
> `main` con el **PR #67**; hasta el merge corren en esa rama.

### 8.1 El comando único (definition-of-done de CUALQUIER cambio)

```bash
# Backend (corre hoy en main): R-DIP + R-rules + plugin-contract + orquestación + meta-gate + premortem
cd hubara_agency && uv run lint-imports && \
  MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy OTEL_SDK_DISABLED=true \
  uv run pytest tests/architecture tests/plugins -q
# Frontend: codegen + type-check composite + FSD/íconos/meta-gate + unit
cd frontend_dashboard && npm run plugins:sync && npx tsc -b && \
  npm run test:arch && npm test
```

(Ese bloque backend es el trío literal de dummies expandido — el canónico para
copiar.) Con el **SDK** en main (PR #67) la DoD suma la certificación TCK:
`… uv run pytest tests/conformance -q` (§8.3). Verde todo = mergeable por
arquitectura. Lo que sigue es el **detalle por gate** (qué caza cada uno).

### 8.2 Backend — gates de arquitectura (DEHA + aislamiento de plugins)

| Comando (`cd hubara_agency &&`) | Caza | Exit |
|---|---|---|
| `uv run lint-imports` | R-DIP: `platform ↛ plugins`, `plugins ↛ siblings`, `sdk ↛ plugins`, `platform ↛ sdk`, `tools ↛ temporalio` | 0 ok · 1 broken |
| `$DUMMIES uv run pytest tests/architecture -q` | R-DET/JSON/STATELESS/HEARTBEAT/DIP · plugin-contract P-1..P-31 · orquestación (transitions→workers reales) · **meta-gate** (PROTECTED sin label) | 0 · 1 |
| `$DUMMIES uv run pytest tests/plugins/test_premortem_invariants.py -q` | drift de `docker-compose.local.yml` vs `render-compose` · paridad worker↔k8s · unicidad de `task_queue` | 0 · 1 |

PROTECTED tocado (`tests/architecture/**`, `.importlinter`, `_schema/`,
allowlists P-28/P-31, `.archon/workflows/**`) ⇒ prefijo `ARCH_CHANGE_APPROVED=1`
en local **y** label `architecture-change` en el PR. (Cómo lo ve CI: **L-14** —
el label viaja en el contexto del evento, no se re-evalúa al re-correr.)

### 8.3 Certificación — el TCK / niveles C0–C2 (SDK, PR #67)

```bash
# pytest (cada plugin INSTANCIA su TCK en tests/conformance/test_<id>_conformance.py — P-27):
cd hubara_agency && $DUMMIES uv run pytest tests/conformance -q
# certificar un plugin → escribe el reporte (gitignored: derivable, no se committea):
cd hubara_agency && $DUMMIES uv run python -m src.sdk.cli certify eta
#   → hubara_agency/.hubara/certification/eta.json  (git_sha + generated_at)
```

| Nivel | Significa | Se computa |
|---|---|---|
| `none` | el manifest ni siquiera valida | falla `C0-SCHEMA` / `P-29A` |
| `C0` Declarado | manifest válido, algo declarado NO existe | falla algún `C1-*` |
| `C1` Cargable | todo lo declarado existe, una P-rule falla | falla algún `P-*` |
| `C2` Certificado | TCK completo verde (warnings listados, permitidos) | cero `fail` |
| `C3` Verificado | conducta (specs + evals + smoke) | reservado (F-SDK-7) |

La certificación **gobierna merge y catálogo, nunca el runtime**. Un reporte
stale (git_sha viejo) se degrada a "sin certificar" — jamás inventa verde.

### 8.4 El CLI `hubara` — verbos deterministas (SDK, PR #67)

```bash
cd hubara_agency && uv run python -m src.sdk.cli <verbo>
```

| Verbo | Qué hace | Exit |
|---|---|---|
| `check [<id>...]` | el **compilador rápido**: TCK estático (sin red, segundos), salida estilo rustc (`error[P-x] + fix + ref`). Sin args = los 7 plugins | 0 ok · 1 violaciones |
| `certify [<id>...]` | `check` + escribe `.hubara/certification/<id>.json` + tabla de niveles | 1 si algún plugin < C2 |
| `explain <código>` | el diagnóstico completo de una regla (`P-27`, `C1-DEPS`, …) | 2 si el código no existe |
| `graph [--format=mermaid\|json]` | grafo del sistema desde los manifests (nodos con arquetipo + edges `depends_on`/`event:*`) | 0 |
| `create plugin <id> --archetype <a>` | scaffold que **nace C2** + corre su TCK + imprime próximos pasos | 1 si no nace C2 · 2 input inválido |

Una fuente (`src/sdk/testkit/checks.py`), tres frontends (pytest · reporte JSON ·
CLI). El CLI no implementa reglas: delega en el TestKit.

### 8.5 Frontend — gates FSD (`cd frontend_dashboard &&`)

| Comando | Caza | Exit |
|---|---|---|
| `npm run plugins:sync` | regenera el plugin registry (codegen requerido por `tsc`) | 0 · 1 |
| `npx tsc -b` | type-check composite — un import roto entre slices/plugins = fallo | 0 · ≠0 |
| `npm run test:arch` | dep-cruiser (FSD bottom-up + plugins-no-cross) · íconos de manifest · paridad · meta-gate frontend | 0 · 1 |
| `npm test` | vitest unit (entities/features/contracts Zod) | 0 · 1 |

### 8.6 Deploy parity (solo si tocás workers o el toggle)

```bash
cd hubara_agency && ENABLED_PLUGINS=<csv> uv run python scripts/render-compose.py
#   falla si el set viola depends_on (lista TODO junto); diffeá el artefacto (PM-10)
docker compose -f docker-compose.local.yml up -d --remove-orphans   # SIEMPRE --remove-orphans
```

Worker nuevo ⇒ además su `k8s/aws-produccion/worker-<name>.yaml` espejo CON
`ENABLED_PLUGINS` (P-20 audita la paridad). Backend `.py` ⇒ rebuild del
container (`--build`); frontend en local es HMR.

### 8.7 Lo que no se negocia

- 3 fallos conocidos PRE-existentes en `tests/plugins/chats` (voseo + 2
  watchdog) — no son tuyos; cualquier OTRO rojo SÍ.
- Cambio de comportamiento visible ⇒ verificá contra el stack Docker real
  (puertos en CLAUDE.md raíz §12) — **tests verdes ≠ feature viva** (gotcha #1).
- Repro local verde + CI rojo en un gate de allowlist/ratchet ⇒ **staleness**
  (CI testea `refs/pull/NN/merge`): mergeá main + regenerá el ratchet, no edites
  a mano (**L-15**).

---

## §9. LECCIONES DE VALIDACIÓN (sección VIVA — append-only)

> Formato de cada entrada (copiar tal cual):
>
> ```
> ### L-<n> · <título corto> (<fecha>, <contexto: validación en vivo / HU-XXX / incidente>)
> - **Síntoma:** qué se vio (error literal, comportamiento).
> - **Causa raíz:** qué regla/mecanismo se malentendió o qué hueco existía.
> - **Fix aplicado:** commit/PR + qué cambió.
> - **Regla para el skill:** la instrucción imperativa de 1-2 líneas que el
>   desarrollador debe internalizar para no repetirlo.
> - **Guard:** el gate que ahora lo caza (o "PENDIENTE: <propuesta P-#>").
> ```

### L-0 · Lecciones operativas del propio refactor F1–F8 (2026-06-10, ejecución)

- **Síntoma:** imports recién agregados desaparecían entre dos Edits; `NameError` en tests.
- **Causa raíz:** el hook post-edit corre `ruff --fix` tras CADA edit — un import agregado antes que su uso es "unused" y lo poda.
- **Fix aplicado:** orden de edición uso-primero-import-después (2 incidencias en F1/F8).
- **Regla para el skill:** al editar Python en este repo, agregá primero el USO del símbolo y después su import (o ambos en el mismo edit); tras cada edit asumí que ruff reformateó — re-leé antes del próximo edit a la misma región.
- **Guard:** ninguno posible (comportamiento del hook); regla de procedimiento.

Otras micro-lecciones del refactor: BSD `sed` no soporta `\b` (usar `perl -pi -e`) · `git mv` necesita `mkdir -p` del directorio destino · el meta-gate corre contra `origin/main` — en branches con PROTECTED tocados, TODA corrida local de arch-tests lleva `ARCH_CHANGE_APPROVED=1` · los smokes de toggle se hacen con render filtrado + restaurar el render full ANTES de commitear (el drift test compara el artefacto canónico).

### L-1 · Cast HTTP con timeout dimensionado para el hop local, no para el upstream del provider (2026-06-10, validación en vivo)

- **Síntoma:** `PATCH /api/chats/order-actions/{id}/schedule` → **502** en el canvas al agendar un pedido (chat en humano)… pero el pedido SÍ se agendó: labels cambiaron y el Agente ETA arrancó. La UI reportó fallo de un comando que se aplicó.
- **Causa raíz:** el cast chats→orders (`_forward_patch`) tenía `timeout=15s` razonado como "self-call loopback = rápido". Pero el timeout efectivo de un cast lo dicta la CADENA del provider: orders habla con Medusa cloud (30s/request × 3 retries tenacity ≈ 95s peor caso, un GET simple medido en 7.6s) y `schedule` encadena varias llamadas. httpx abortó a los 15s → uvicorn **canceló el request interno** (su access-log ni aparece) → el `asyncio.create_task(_emit_stage_changed_event)` del provider nunca corrió. El cambio en Medusa ya estaba aplicado; el **reconcile** lo detectó y emitió el evento (`source_worker=reconcile` en el log) — la red de seguridad idempotente convergió, pero el usuario vio un error falso. Segundo hueco: TODO error de transporte (incluido timeout) se traducía a 502 "no respondió", afirmando implícitamente que el comando no pasó.
- **Fix aplicado:** branch `fix/validation-l1-cast-timeout` — timeout default 120s + override `ORDERS_CAST_TIMEOUT_S`; `ConnectError/ConnectTimeout` → 502 "el comando NO se aplicó" (única garantía real), `ReadTimeout/WriteTimeout/PoolTimeout` → **504** "PUEDE haberse aplicado — refrescá antes de reintentar". Tests unit del cast creados (no existían — el F4 nació sin ellos).
- **Regla para el skill:** al escribir un cast HTTP, dimensioná el timeout por la cadena completa del provider (su upstream + sus retries), nunca por el hop local. Y nunca traduzcas un timeout a un error que afirme "no pasó nada": timeout = resultado DESCONOCIDO (504 + mensaje honesto); solo el fallo de conexión garantiza no-aplicación (502). Todo cast nuevo nace con tests de sus 4 paths: éxito, error-del-provider passthrough, timeout, no-disponible.
- **Guard:** `tests/plugins/test_chats_order_actions_cast.py` (9 tests: timeout→504 honesto, connect→502, passthrough, default ≥ cadena Medusa).

### L-2 · La latencia de un provider cloud se ataca eliminando llamadas, no adelgazándolas (2026-06-10, validación en vivo)

- **Síntoma:** toda acción de la pestaña orders tarda 5-60s (schedule medido: 56.1s; detail por `#6`: 9-12s). Infra 100% local salvo Medusa (Railway).
- **Causa raíz:** dos capas. (1) Amplificación nuestra: el frontend navega por `display_id` ("#6", contrato premortem A1) y AMBOS adapters lo resolvían con page-scan de `/admin/orders` + `/admin/draft-orders` con los fields default (items/addresses/customer) — 2 llamadas extra de 3-10s c/u, secuenciales, EN CADA acción. (2) El techo real: el endpoint de Medusa@Railway tiene variabilidad salvaje — el MISMO GET de 5 campos tarda 1.5s o 30s (timeout del cliente) + retry de 20s en el mismo minuto (medido con spans SigNoz). Dato clave: adelgazar los fields del scan a `id,display_id` NO movió la aguja — el costo es el endpoint en sí, no el payload.
- **Fix aplicado:** cache process-wide `display_id→backend_id` (`platform/orders/display_id_cache.py`) compartido por query+command adapters — el mapeo es INMUTABLE (Medusa no reasigna display_ids; `convert_draft_to_order` preserva el id), así que no lleva TTL. `list()` lo puebla gratis (el operador siempre ve la lista antes de actuar sobre un pedido). Scans en miss: paralelos (`gather`) + fields mínimos. Resultado medido: detail por display_id 9-12s → **1.5s** con Railway sano.
- **Regla para el skill:** contra un upstream cloud lento, medí PRIMERO el desglose por llamada (SigNoz: `signoz_traces.distributed_signoz_index_v3`) — la optimización que funciona es **eliminar llamadas** (cachear mapeos inmutables, reusar datos que una lista ya bajó), no adelgazar las que quedan. Todo cache process-wide nuevo exige `clear()` + fixture autouse en el conftest de sus tests (estado global = contaminación entre tests). Documentá el techo que NO podés atacar desde el repo.
- **Guard:** `tests/platform/orders/test_display_id_cache.py` (5 tests: cache-first sin I/O en ambos adapters, `list()` puebla, fields del scan, cap defensivo). Techo restante documentado: la variabilidad 1.5s↔50s es del deploy de Medusa en Railway (decisión operativa fuera del repo: plan/recursos/región).

### L-3 · Activity invocada por helper compartido pero no registrada en el worker — muere en runtime, no en boot (2026-06-10, validación en vivo)

- **Síntoma:** primera conversación real con el agente ETA (run `8afac276`): el cliente escribe, el agente queda MUDO, y el workflow `eta-wa_*` termina **FAILED** tras 1h20m de retries con `NotFoundError: Activity function record_episode_llm_usage ... is not registered on this worker`. Al operador le pareció además que "ETA secuestró la conversación nueva" — pero eso era by-design (ver abajo); lo roto era el silencio.
- **Causa raíz:** `run_agent_turn` (helper compartido en `platform/workflow_helpers.py`) ejecuta 6 activities; cada worker conversacional las registraba A MANO. El worker eta registró 5 de 6 — y la faltante (`record_episode_llm_usage`) vive detrás de `workflow.patched("episode-llm-cost-v1")` + `if episode_id and tokens`, así que NADA la ejercitó (boot limpio, tests verdes, notificación saliente OK) hasta que un cliente real respondió. Misma familia que el gotcha #6 (carga limpia, truena en runtime), pero la variante es **registro incompleto**, indetectable por F821.
- **Fix aplicado:** fuente única — `CONVERSATIONAL_TURN_ACTIVITIES` exportada por `workflow_helpers.py` (vive JUNTO al código que las invoca); sales/remarketing/eta hacen `*CONVERSATIONAL_TURN_ACTIVITIES` en su `activities=[...]`. Guard AST que extrae los `execute_activity(X)` del helper y exige X ∈ tupla, + guard de que los workers spread-ean (no listan a mano).
- **Regla para el skill:** si un workflow usa helpers compartidos, su worker NUNCA lista las activities del helper a mano — spread-ea la tupla exportada por el helper. Si agregás un `execute_activity` a un helper compartido, sumá la activity a la tupla EN EL MISMO COMMIT (el guard AST te frena si no). Y al estrenar un agente conversacional, el smoke mínimo es UN turno de ida y vuelta real (cliente→agente→respuesta), no solo la notificación saliente.
- **Guard:** `tests/plugins/test_conversational_activities_parity.py` (AST de invocaciones ⊆ tupla + spread literal en los 3 workers).
- **Nota de diseño (no-bug) que confundió al operador:** `start_eta_tracking` setea `active_route=eta` + `tag=ETA` al agendar el pedido — mientras el pedido esté en tránsito, el agente ETA es el dueño legítimo de la conversación (responde "¿cuándo llega?", respeta `humano`, y transfiere a Ventas vía `TransferToSalesAgentTool` si el cliente quiere comprar). Un "mensaje nuevo del cliente" NO abre episodio de ventas mientras haya tracking activo.

### L-4 · Notificar ≠ poseer el turno — el ownership conversacional acoplado al tracking secuestraba el chat (2026-06-10, validación en vivo → cambio de comportamiento)

- **Síntoma:** el operador agenda un pedido → el cliente escribe DESPUÉS un mensaje nuevo (quería comprar) → atiende el agente ETA, no Ventas. Además `start_eta_tracking` pisaba `active_route=humano` (el claim de notificaciones respetaba humano, el start no — inconsistencia interna), y el tracking era de UN solo pedido (uno nuevo reseteaba el anterior, descartando sus notificaciones como "stale").
- **Causa raíz:** el diseño original acoplaba dos conceptos ortogonales: **notificación push** (no necesita el turno — WhatsApp es un solo hilo y el aviso se intercala) y **ownership conversacional** (quién responde inbounds). `start_eta_tracking` tomaba la ruta como efecto colateral de empezar a notificar.
- **Fix aplicado:** branch `feat/eta-pure-notifier` — convivencia ETA/Sales (variante A): el ETA es **notificador puro** (sin `send_message`, sin `owns_route` en el manifest, sin tools de conversación), `eta_tracking` es mapa multi-pedido por `order_id` (shape v2, migración on-read del v1), y Sales absorbe las preguntas de entrega con la tool `check_order_status` (lee el estado compartido del metadata — local, sin Medusa en el turno, L-2). Sesiones legacy con `active_route=eta` migran SOLAS: ruta no registrada → fallback del router a Sales (el mecanismo F6 hizo la migración gratis). El dispatch de notificaciones no se tocó (las transitions de orders llevan su propio `workflow_id_template`).
- **Regla para el skill:** un agente que EMPUJA mensajes (notificador, recordatorio, watchdog) NO toma `active_route` — el turno conversacional solo cambia por decisión explícita de conversación (escalación a humano, transferencia). Si te encontrás escribiendo `active_route` desde un flujo que no es una conversación, pará: estás acoplando push con ownership. Y toda escritura de `active_route` respeta `humano` (la única excepción es la propia escalación).
- **Guard:** `test_start_tracking_does_not_touch_route_or_tag` + `test_start_tracking_preserves_route_humano` + `test_claim_dedup_is_per_order` (tests/plugins/eta) + P-18 adaptado (`eta` NOT in registry, con mensaje de "si es deliberado, revertí la convivencia completa") + 4 tests de `check_order_status`.

### L-5 · El fix "no pierdas el texto pre-tool" convirtió el pensamiento del LLM en burbujas al cliente (2026-06-10, validación en vivo, run 844745bd)

- **Síntoma:** en el happy path de Sales el cliente lee frases duplicadas ("Déjame mostrarte las opciones" + "Ahora los colores disponibles:"), verificación en voz alta ("Todo está verificado y los precios coinciden"), narración de sistemas internos ("quedó registrado exitosamente en Medusa. Ahora procedo con el protocolo de cierre"), anuncios de reglas internas ("si quieres más de 20 lo coordino con un colega") y preguntas de relleno ("¿Cómo sigue tu pedido?").
- **Causa raíz:** triple. (1) El patch `send-pre-tool-messages-v1` (fix legítimo del saludo perdido — ver memoria `llm_content_with_toolcalls_dropped`) envía TODO el texto que el LLM emite junto a una tool call — incluida su narración de acciones, que antes se descartaba sola. (2) El TOOLS.md INSTRUÍA texto redundante ("Tu próximo texto: SOLO una línea breve de transición" antes del resumen que ya tiene título y botón) y era ambiguo sobre CUÁNDO va el comentario de una UI tool (junto a la call vs en la respuesta final → el LLM escribía ambos). (3) Ninguna regla prohibía anunciar umbrales internos ni mencionar sistemas (Medusa/protocolos).
- **Fix aplicado:** prompts, no código — SOUL.md: sección "El texto junto a una tool call TAMBIÉN llega al cliente" (no narrar acciones, no verificar en voz alta, no mencionar sistemas, content vacío salvo lo que el cliente necesita y la tool no dice, sin preguntas de relleno post-presentación) + sección "Las reglas internas no se anuncian" (los umbrales gobiernan conducta, no conversación). TOOLS.md: `present_order_confirmation` pasa a "Tu próximo texto: NINGUNO"; el branching post-`register_order` marcado como pasos INTERNOS; regla "UN solo comentario y va en la respuesta FINAL" en el intro de UI tools.
- **Regla para el skill:** cuando un canal técnico se vuelve visible al usuario (pre-tool text → burbujas), TODO el prompt que asumía que ese canal era invisible queda obsoleto — auditá las instrucciones de "texto de acompañamiento" de cada tool. Y las instrucciones de prompts que piden texto extra ("una línea de transición") son fuente directa de duplicación cuando el componente UI ya trae título/CTA: el default correcto es content VACÍO junto a tool calls.
- **Guard:** PENDIENTE — candidato: eval de conversación en `evaluator-calibration/` que penalice (a) dos burbujas consecutivas del agente con >70% de similitud, (b) menciones de sistemas internos (Medusa, protocolo, tag, registro) en mensajes al cliente. Por ahora: validación en vivo del operador.

### L-6 · Guard heredado de un modelo viejo bloqueaba el caso de negocio principal (2026-06-10, validación en vivo, run 19ee6679)

- **Síntoma:** mover un pedido de "en preparación" a "listo" arranca el workflow ETA, que queda RUNNING "sin hacer nada": cero notificación al cliente. Log: `claim_eta_notification: en ruta humano — skip`.
- **Causa raíz:** el claim conservaba el guard `active_route == humano → no notificar`, diseñado para el modelo VIEJO (notificar implicaba que el agente conversacional ETA tomara el turno — pisarle el turno a un humano era incorrecto). Tras L-4 (notificador puro) el guard quedó sin propósito… y se volvió dañino: **toda venta exitosa termina con `route=humano`** (verificación de pago, estado terminal por diseño — el bot no retoma ventas cerradas), así que el guard bloqueaba las notificaciones de TODOS los pedidos vendidos. La feature entera quedaba muerta en su caso de uso principal y los tests seguían verdes (el test `test_claim_skips_when_route_humano` codificaba el guard como comportamiento deseado).
- **Fix aplicado:** branch `fix/eta-notify-despite-humano` — el claim solo skipea por dedup de stage; la notificación sale siempre (es push informativo, no toma turno). Test invertido a `test_claim_notifies_even_when_route_humano`.
- **Regla para el skill:** cuando cambies el MODELO de un subsistema (p.ej. "notificar ya no implica poseer el turno"), buscá TODOS los guards que existían por el modelo anterior y re-justificá cada uno bajo el modelo nuevo — un guard sin re-justificar no es conservador, es un bug latente con tests verdes. Preguntate: "¿este check protege algo que todavía existe?". Y al validar, probá el ciclo de negocio COMPLETO (venta → cierre → tracking), no cada pieza aislada: este bug solo aparece encadenando venta exitosa + cambio de stage.
- **Guard:** `test_claim_notifies_even_when_route_humano` (tests/plugins/eta) — codifica la decisión nueva con el porqué en el docstring.

### L-7 · Fire-and-forget sin referencia: el GC mata la task pendiente sin log (2026-06-10, validación en vivo)

- **Síntoma:** mover pedidos de stage devuelve `success=true` pero el ETA nunca se activa. Cero logs del emisor (`eta_emit`) — ni éxito, ni "sin sesión", ni el except. La coroutine ni empezó.
- **Causa raíz:** `asyncio.create_task(_emit_stage_changed_event(...))` sin guardar referencia. Los docs de asyncio lo advierten: el event loop solo guarda referencia débil — si la task espera I/O largo, el GC puede recolectarla PENDIENTE. Con Medusa@Railway rápido la task terminaba antes de cualquier GC (por eso funcionó temprano); con Railway degradado (GETs de 30s+, L-2) la ventana se abre y las tasks mueren en silencio. Heisenbug dependiente de la latencia del upstream.
- **Fix aplicado:** branch `fix/eta-emit-task-gc` — patrón estándar: set module-level de referencias fuertes + `add_done_callback(set.discard)` (`_spawn_emit`), aplicado a los 3 emisores (schedule, /stage, confirm-payment).
- **Regla para el skill:** NUNCA `asyncio.create_task(...)` a secas para fire-and-forget — siempre el patrón referencia-fuerte + done_callback (o un helper `_spawn_safe` existente). Cualquier task sin referencia es un heisenbug que aparece justo cuando el I/O se pone lento. Grepeá `create_task` en code review: cada uso debe guardar la referencia.
- **Guard:** PENDIENTE — candidato P-#: gate AST que rechace `asyncio.create_task` cuyo resultado no se asigna ni registra.

### L-8 · `via: signal` a un workflow efímero es una carrera perdida — signal_with_start, y el mapping debe cubrir el START (2026-06-10, validación en vivo)

- **Síntoma:** mover pedidos de stage no activa el ETA. El dispatcher loguea NOT_FOUND (`sql: no rows in result set`) al signalear `eta-wa_*`: el workflow destino ya terminó (idle timeout) o nunca existió.
- **Causa raíz:** la transition del manifest usaba `via: signal`, que exige el workflow CORRIENDO. El ETA es de vida finita por diseño (idle 7d, cierre proactivo) → todo evento post-cierre se pierde. El primer fix destapó la segunda mitad: en `signal_with_start` el `input_mapping` alimenta TAMBIÉN el start del workflow → sin `session_id` mapeado, el run arranca con input vacío ("The parameter to is required") y encima queda VIVO absorbiendo los signals siguientes (hubo que terminarlo a mano para que el retry arrancara uno sano).
- **Fix aplicado:** rama `via: signal_with_start` en el dispatcher (`client.start_workflow(..., start_signal=..., start_signal_args=...)` — atómico nativo de Temporal); `session_id` agregado a los 4 input_mappings orders→eta; `Via` Literal extendido.
- **Regla para el skill:** hacia targets de vida finita (sesiones con idle timeout o cierre proactivo), `via: signal` está PROHIBIDO — siempre `signal_with_start`. El input_mapping de un signal_with_start debe satisfacer el contrato COMPLETO del start input, no solo el payload del signal. Y si un run quedó arrancado con input inválido, terminate primero: mientras viva se traga los signals buenos.
- **Guard:** PENDIENTE — candidato P-#: gate de manifest que rechace `via: signal` hacia workflows con idle timeout y verifique que el mapping de un `signal_with_start` cubre los campos requeridos del start input.

### L-9 · Deploy de un workflow con runs vivos sin `workflow.patched()` — el sticky cache esconde el nondeterminism hasta el restart (2026-06-10, validación en vivo, run 4d5e7baf)

- **Síntoma:** pedido movido a "entregada" y el cliente no recibe nada. El run eta sigue `Running`, pero su último evento es `WorkflowTaskFailed` con TMPRL1100 ("Activity machine does not handle this event: TimerStarted"). El signal quedó encolado para siempre: Temporal reintenta el workflow task en loop — run vivo pero congelado, sin notificar y sin morir.
- **Causa raíz:** el cierre proactivo (fde88d4) agregó un `execute_activity` al loop de `HubaraEtaSessionWorkflow`. Los runs `eta-*` viven días: el run nació con el código viejo. Mientras el worker viejo siguió en pie, su sticky cache aplicaba solo eventos nuevos (sin replay) — todo parecía sano. El rebuild del worker borró el cache; el siguiente signal forzó replay COMPLETO del historial viejo con el código nuevo → donde el viejo armó el timer de idle, el nuevo emite una activity → nondeterminism. Doble trampa: los tests no lo ven (arrancan runs frescos) y el error aparece HORAS después del deploy (al primer restart de worker), lejos del cambio que lo causó.
- **Fix aplicado:** `workflow.patched("eta-proactive-close-v1")` gateando el bloque nuevo — el replay de historial pre-deploy toma la rama vieja (sin activity) y la ejecución fresca la nueva. El run atascado se auto-recupera en el siguiente retry del workflow task: ni terminate, ni pérdida del signal encolado.
- **Regla para el skill:** los workflows de sesión viven DÍAS — todo cambio que altere su secuencia de comandos (nuevo `execute_activity`/timer/child, reordenamiento, eliminación) va detrás de `workflow.patched("<feature>-v1")`. La alternativa (drenar/terminate los runs vivos en el rollout) es válida SOLO si el estado real vive fuera del workflow y algo lo revive (acá: `metadata.eta_tracking` + signal_with_start de L-8) — y es una decisión explícita del deploy, no un default. "Los tests pasan" no cubre esta clase: solo un replay de historial viejo la caza.
- **Guard:** PENDIENTE — candidato P-#: test de replay con `temporalio.worker.Replayer` sobre historiales JSON de runs reales (descargados con `temporal workflow show --output json`) como fixtures de CI.

### L-10 · El dominio creció en el backend y el contrato Zod del frontend quedó atrás — parse estricto sin estado de error = sección vacía en silencio (2026-06-10, validación en vivo)

- **Síntoma:** la sección ETA del dashboard no muestra NADA — ni pedidos ni error — aunque `/api/eta/tracked-orders` responde 200 con 7 pedidos válidos.
- **Causa raíz (dos mitades):** (1) el agente ETA ahora notifica cancelaciones (PR #54) → el timeline de un pedido puede traer un evento `stage: "cancelled"`, valor que el enum Zod del frontend no aceptaba → `.parse()` rechaza la respuesta ENTERA (un evento mató 7 pedidos). (2) La Page hacía `const { data = [] } = useQuery(...)` SIN mirar `isError` → el fallo de validación se degradó a "tablero vacío", indistinguible de "no hay pedidos". El parse estricto en el boundary es correcto y deliberado — lo que faltó fue actualizar el contrato junto con el dominio y hacer el error VISIBLE.
- **Fix aplicado:** `trackedEventStageSchema` = stages del tablero + `cancelled` (el stage ACTUAL del pedido sigue estricto — cancelled no se lista, filtra el backend); `TrackedEventStage` en el model; `EtaSection` renderiza estado de error explícito en vez de tablero vacío; test de regresión del contrato (`contracts.test.ts`) + verificación del parse contra la respuesta real del backend.
- **Regla para el skill:** cuando un cambio de backend agrega un VALOR nuevo a un campo enumerado que viaja al dashboard (stage, status, tipo), el contrato Zod del boundary frontend es parte del MISMO cambio — buscá los `z.enum` que validan ese campo antes de mergear. Y toda Page que consuma un query con default `= []` debe mostrar `isError`: un boundary estricto sin estado de error visible convierte cualquier drift en una sección vacía sin diagnóstico.
- **Guard:** el test de contrato cubre el valor nuevo; PENDIENTE candidato P-#: derivar los enums compartidos de una fuente única (backend exporta el dominio → codegen o fixture compartida) para que el drift truene en CI, no en producción.

### L-11 · El tool-loop sin corte deja al LLM "responderse a sí mismo" — las reglas de prompt no frenan al modelo, el código sí (2026-06-10, validación en vivo, run b730c006)

- **Síntoma:** en un happy path de venta, tras elegir el cliente el aroma de la segunda vela, el agente mandó EN UN SOLO TURNO: el picker de colores + un `set_order_slot` con un color que el cliente nunca dijo ("Lila") + la cantidad asumida + el formulario de datos de envío pre-llenado con la dirección de un pedido VIEJO de la memoria. El color inventado llegó hasta el `register_order`. Además reincidió la narración de proceso ("Todo está verificado y los precios coinciden…") que el SOUL ya prohibía desde el run 844745bd — con el fix VERIFICADO como deployado.
- **Causa raíz (dos mitades):** (1) el `while` de `run_agent_turn` deja al LLM encadenar tools hasta que él decida parar — cuando una tool deja la conversación ESPERANDO al cliente (picker/formulario/confirmación), "seguir" solo es posible inventando la respuesta, y el modelo lo hizo aunque TOOLS.md lo prohibía explícitamente. (2) El patch que envía el `content` junto a tool calls (creado para no perder el saludo, run ddd0d472) no distinguía a QUÉ tool acompañaba: junto a tools internas (`verify`, `set_order_slot`, `load_skill`) ese content es narración de proceso por definición.
- **Fix aplicado:** estructural en `workflow_helpers.py` — (A) `TURN_ENDING_TOOLS` (`present_variant_picker`, `request_shipping_details`, `present_order_confirmation`, `send_quick_replies`): tras ejecutar el batch que contenga una, el loop CORTA (`final_content=""` explícito para no caer en el fallback "se me cortó un segundito"). (B) el content pre-tool solo se conserva si el batch incluye una tool PRESENTACIONAL (`PRESENTATIONAL_TOOLS`); junto a tools internas se descarta con log. Ambos detrás de `workflow.patched("turn-ending-tools-v1")` (L-9 — session workflows vivos). (C) prompts alineados con la mecánica (cada tool terminal lo declara) + regla "el cliente elige; tú nunca eliges por él" con los ejemplos del run.
- **Regla para el skill:** cuando el modelo viola sistemáticamente una regla de prompt que ya estaba escrita, el fix NO es más prompt — es quitarle al modelo la POSIBILIDAD mecánica (cortar el loop, filtrar el output, validar el input). El prompt explica el comportamiento; el código lo garantiza. Y toda tool de UI que espere respuesta del cliente debe ser turn-ending — si agregás una nueva (`present_*`/formularios), agregala al set.
- **Guard:** tests puros de `_ends_turn` / `_keeps_pre_tool_content` con los batches reales del run (14 tests en `test_run_agent_turn.py`). PENDIENTE candidato: eval del pipeline (`hubara-evaluator`) que detecte `set_order_slot` de un atributo de elección en el MISMO turno que su picker.

### L-12 · Una tool de transferencia registrada en el agente DESTINO se vuelve autotransferencia — jerga interna al cliente, handoff ajeno pisado y falso ghosting en loop (2026-06-12, validación en vivo, runs cddd0895/3607aecc)

- **Síntoma:** el cliente respondió "A si" al gancho de remarketing y 3s después "Dame 3" (la cantidad que ventas le había preguntado). Recibió UNA burbuja: "El control ha sido transferido al agente de ventas." — jerga interna. Nadie le respondió al "Dame 3"; 60s después sales declaró ghosting (tag INTERESADO) y RE-ABRIÓ remarketing, que le mandó OTRO gancho 2 minutos después de haber dado la cantidad. Loop remarketing→sales→remarketing.
- **Causa raíz (cadena de cuatro):** (1) `TransferToSalesAgentTool` — cuya docstring dice "Used **only** by the Remarketing agent" — estaba registrada TAMBIÉN en el worker de sales (`workers/sales.py`, herencia del NEW-5): el LLM de ventas podía "transferirse a ventas". (2) El handoff llegaba al LLM como user message CRUDO en tercera persona ("Cliente respondió 'A sí' al recordatorio… Muestra disposición a retomar la compra" — `coalesce_pending` branch handoff-only), indistinguible del trigger que ve remarketing → patrón-match: "respondió al remarketing ⇒ transferir a ventas", y la tool estaba ahí para obedecer. (3) El branch `transfer_decision` de sales ejecutaba el "self-loop" `start_or_signal_sales_workflow` — que ESCRIBE `pending_handoff_summary` — pisando el handoff "Usuario respondió: Dame 3" que remarketing había escrito 2s antes (mensaje del cliente perdido para siempre); y a diferencia de remarketing (que suprime el texto post-transfer con `_force_shutdown`), sales enviaba el `final_content` — el LLM regurgitó el `message` interno del tool result. (4) El idle path de sales appendeaba el trigger de ghosting ANTES de leer el handoff refresh: un handoff dormido (no despierta el `wait_condition` — viaja por metadata, no por signal) se coalesceaba CON el ghosting y `_force_shutdown` suprimía la respuesta.
- **Fix aplicado:** (A) la tool fuera del registry de sales — solo remarketing la tiene. (B) `transfer_decision` dentro de sales = noop total: warning + `final_content=""`, sin activity que pise handoffs (`workflow.patched("sales-self-transfer-noop-v1")`, defensa en profundidad para el window). (C) el handoff-only en `coalesce_pending` viaja con framing inequívoco: "[SISTEMA — HANDOFF DE REMARKETING A VENTAS]: Eres el agente de ventas y el control YA ES TUYO… retoma la venta donde quedó". (D) al idle timeout, PRIMERO se lee el handoff pendiente: si hay (o llegó un signal en la race), turno normal y sin ghosting ese ciclo (`workflow.patched("ghost-checks-handoff-first-v1")`).
- **Regla para el skill:** una tool de transferencia/ruteo se registra SOLO en el worker ORIGEN de la transición — en el destino es una autotransferencia esperando ocurrir. Todo mensaje sintético que entre al rol "user" debe declarar QUIÉN es el agente y QUÉ debe hacer (un resumen en tercera persona patrón-matchea el flujo equivocado). Y los caminos que el workflow recorre al despertar por timeout deben drenar TODAS las fuentes de input (signals + metadata handoff) antes de decidir que el cliente desapareció.
- **Guard:** `test_self_transfer_decision_is_noop_and_sends_nothing` + `test_idle_timeout_with_pending_handoff_processes_it_not_ghosting` (workflow tests con el escenario real del run) + `test_coalesce_handoff_only_wraps_summary_with_sales_framing`. PENDIENTE candidato P-#: meta-gate que cruce el registry de tools de cada worker contra un campo `allowed_workers` declarado en la tool (la regla "origen-only" hoy vive en docstrings).

### L-13 · El primer mensaje post-handoff sale con información incompleta — el origen drena mensajes buffered con un turno LLM entero mientras el destino ya respondió (2026-06-12, validación en vivo, runs 155fcba4/8894825b)

- **Síntoma:** el cliente respondió "A si" + "Dame 2" al gancho de remarketing. El primer mensaje de ventas fue vago ("¿En qué estábamos? Cuéntame si ya tienes claro qué vela quieres") — re-preguntando lo ya elegido e ignorando la cantidad recién dada. 60 segundos después llegó el mensaje correcto (cantidad registrada, precio, formulario de envío). Los fixes L-12 operaron (sin autotransferencia, sin jerga, sin loop) — pero la primera impresión fue de un agente que no leyó la conversación.
- **Causa raíz (tres mitades):** (1) **carrera de drenado**: el "Dame 2" llegó mientras remarketing transfería; el cancel-shutdown lo re-procesó con un TURNO LLM COMPLETO (~9s, build_prompt + llm_chat + record_turn) cuyo único output posible post-transfer es "Ok" + el force-handoff determinista — mientras tanto sales ya había leído el handoff (8s antes) y respondido sin ese dato. (2) **handoffs pisables**: cada `write_pending_handoff` REEMPLAZABA el campo — N writes antes de una lectura = solo sobrevive el último (así murió el "Dame 3" en L-12). (3) **summary pobre**: el briefing del LLM de remarketing fue "Parece estar retomando la conversación. Sales debe tomar el control" — cero contenido accionable; y el framing del handoff no obligaba a sales a anclarse al historial.
- **Fix aplicado:** (M1) en remarketing, `_force_shutdown=True` ⟺ "ya transferí" (sus 2 únicos set-sites): los pendientes post-transfer van DIRECTO a `_handoff_to_sales("Usuario respondió: <crudos>")` sin turno LLM — la ventana de carrera baja de ~9s a ~1s (`workflow.patched("drain-pending-to-handoff-v1")`). (M2) `pending_handoff_summary` es APPEND con `\n` + idempotencia ante retries — N writes acumulan, `read_and_clear` entrega el blob completo (helper único `_append_pending_handoff` para la activity y el inline del self-loop). (M3) el schema de la tool + TOOLS.md de remarketing exigen briefing con (texto literal del cliente + elecciones confirmadas + siguiente dato pendiente); el framing del handoff en `coalesce_pending` ordena "NO re-preguntes lo ya elegido, nada de '¿en qué estábamos?'".
- **Regla para el skill:** en una transferencia entre agentes, el origen NO razona sobre mensajes que lleguen después de transferir — los reenvía deterministas (cada turno LLM del origen es latencia que el destino paga respondiendo con datos viejos). Todo buzón escrito-por-muchos/leído-por-uno (metadata handoff) debe ser append-mode: un campo overwrite es una carrera de pérdida de datos esperando testigos. Y un handoff es un BRIEFING, no una notificación: si el destino no puede actuar sin re-preguntar, el summary falló su contrato.
- **Guard:** `test_write_pending_handoff_append.py` (4 tests: append, orden, idempotencia, round-trip con read_and_clear) + replay-check de ambos histories reales contra el código nuevo. El drain M1 se valida en vivo (log "Drain post-transfer remarketing"). PENDIENTE candidato: workflow-test harness de remarketing (hoy solo existe para sales) para testear el drain mecánicamente.

### L-14 · Un check de CI condicionado por un label lee el label del CONTEXTO DEL EVENTO, no del estado actual del PR (2026-06-16, validación en vivo, PR #67)

- **Síntoma:** el PR #67 "no pasaba los unit tests" — el run rojo fallaba en el meta-gate (`test_protected_files_unchanged_vs_main`) AUNQUE el PR tenía el label `architecture-change` que debería activar el bypass.
- **Causa raíz (tres mecanismos que se enmascaran):** el run que falló corrió con `ARCH_CHANGE_APPROVED=''` (vacío) en su contexto. (1) **Re-run reusa el payload del evento original**: el run venía de un `pull_request` previo al label (push sin label todavía); re-correrlo NO re-evalúa el label actual — GitHub reusa `github.event.pull_request.labels` del evento original. (2) **`gh pr edit --add/remove-label` fallaba SILENCIOSO**: abortaba por el error GraphQL de "Projects classic deprecation" (`repository.pullRequest.projectCards`) → el label nunca cambiaba de verdad → ningún evento `labeled`. (3) **Toggle remove+add demasiado rápido del MISMO label se "debounce-a"**: estado neto sin cambio → GitHub no emite webhook.
- **Fix aplicado:** togglear el label por **REST API** (`gh api -X DELETE .../issues/NN/labels/<name>` y `-X POST .../labels`) con pausa entre los dos → dispara un `labeled` fresco con el label EN el contexto → `ARCH_CHANGE_APPROVED=1` → bypass → verde. La REST evita el path GraphQL roto. Confirmado con el diff de runs: apareció un run nuevo de hoy donde el `unlabeled` quedó cancelado por concurrencia y el `labeled` sobrevivió con el bypass.
- **Regla para el skill:** un gate de CI que depende de un label se evalúa contra el **payload del evento que lo disparó**, no contra el PR "ahora". Para que un run lo vea: disparar un evento fresco que lo cargue (un push/`synchronize` con el label ya puesto, o un `labeled`). **Re-correr el run viejo no alcanza.** Y si `gh pr edit` parece no hacer nada, sospechá del GraphQL roto → caé a `gh api` (REST) y verificá el estado tras cada llamada.
- **Guard:** documentado acá. Candidato §10: que el meta-gate, cuando falla por falta de label, imprima "este PR toca PROTECTED y el run no ve el label en su contexto — re-dispará con un push o re-aplicá el label por REST" en lugar del críptico `Meta-gate violation` (un humano/agente pierde 30 min creyendo que el código está mal).

### L-15 · CI testea `refs/pull/NN/merge` (PR ⊕ main actual), no el HEAD del PR — un ratchet congelado en un PR stale diverge del baseline real (2026-06-16, validación en vivo, PR #67)

- **Síntoma:** con el meta-gate ya bypasseado (L-14), el PR #67 igual fallaba en DOS tests P-28 (`test_p28_no_new_platform_imports_in_plugins` + `test_p28_allowlist_has_no_stale_entries`) que mi repro local sobre el HEAD del PR **no mostraba** (119 passed local, rojo en CI).
- **Causa raíz:** `actions/checkout@v4` en un `pull_request` chequea `refs/pull/NN/merge` = el PR **mergeado con el main ACTUAL**, no el HEAD del PR. El PR estaba 4 días stale y main se movió debajo: (1) main había **drenado** `sales.py -> src.platform.tools.routing` (mi propio L-12) → la allowlist congelada del PR quedó con esa entrada **stale**; (2) main había **agregado** 3 imports legacy `*/api -> src.platform.events` → aparecen como **nuevos** contra la allowlist congelada. Las dos son **contradictorias de arreglar sin traer main**: en la rama del PR sola, el import de routing todavía existe (no se puede borrar de la allowlist) y los de events no existen (no se pueden agregar).
- **Fix aplicado:** mergear `origin/main` en la rama del PR (limpio, 21 commits, sin conflictos) + **regenerar** el ratchet (`uv run python -m tests.architecture.test_p28_sdk_surface`) → reconcilia la allowlist con el baseline mergeado (drena 1, congela 3). El diff de la allowlist debe ser **exactamente** el delta esperado (lo verifiqué: −1 routing, +3 events) — un diff más grande significa otro drift que hay que mirar.
- **Regla para el skill:** si tu repro LOCAL pasa pero CI falla en un gate de allowlist/ratchet, sospechá **staleness**: CI testea el merge con main, vos testeás el HEAD. El fix es mergear main y **regenerar el ratchet con su comando canónico**, NUNCA editar la allowlist a mano (te desincronizás del escaneo real). Corolario peligroso: un fix que **drena** un import en main (quita una línea de una allowlist congelada) puede poner en rojo **cualquier PR abierto** que congele esa línea — al drenar algo de un ratchet, revisá los PRs en vuelo que lo tocan.
- **Guard:** el propio P-28 (igualdad exacta `current == allowed`) caza el drift; el comando de regeneración lo resuelve mecánicamente. Candidato §10: un check que avise en el PR cuando está N commits atrás de main Y toca archivos de ratchet/allowlist (el drift se vuelve visible antes del merge, no en el `refs/pull/merge`).

### L-16 · Un cast loopback cross-plugin cruza el trust boundary de auth sin portar la identidad — al colgar `require_auth` en el edge, el 2º hop interno se auto-rechaza (2026-06-23, incidente en prod)

- **Síntoma:** `/api/agents/evals/{history,conversations}` (UI "Calidad LLM") y el canvas de pago (`/api/chats/order-actions/*`) devolvían `{"detail":"{\"detail\":\"Falta el bearer token\"}"}` (detail **doble-anidado**) con un token válido en el edge; el resto de la API andaba. El operador veía secciones rotas sin causa aparente.
- **Causa raíz:** el plugin system usa **casts** (Canal 3) = self-calls HTTP loopback a la MISMA app (`127.0.0.1:8000`) para consumir el contrato publicado de otro plugin (no imports — P-3). Al colgar `require_auth` (JWT/Cognito) en TODOS los routers (`main.py`), el 2º hop del cast volvió a pasar por la auth — pero cada `_forward` copy-pasteado hacía `httpx` **sin reenviar el header `Authorization`**. El provider respondía 401 y el cast re-envolvía `resp.text` (el JSON `{"detail":...}` entero) dentro de su propio `detail` → doble-anidado. El edge NO estaba roto: el hop interno se auto-rechazaba porque cruza el trust boundary sin credencial.
- **Fix aplicado:** centralizar el Canal 3 en `src.sdk.castkit.forward` con UNA regla — **todo cast loopback PORTA el `Authorization` del request entrante**. El kit además desanida el `detail` (devuelve el interno, no re-envuelve `resp.text`) y generaliza la semántica honesta 502/504 de L-1 a TODO cast. Los 2 casts (`agents_admin→chats`, `chats→orders`) migraron; sus 9 endpoints toman `request: Request`. (TDD: el rojo fue un guard de integración con auth ON que reentra a la app vía `ASGITransport` y reproduce el doble-detail EXACTO.)
- **Regla para el skill:** cuando agregás auth (o cualquier middleware fail-closed) a un edge, auditá TODO hop service-to-service interno — un self-call que antes funcionaba sin credencial ahora cruza el boundary y se rechaza. El tráfico interno NO hereda la identidad del edge gratis: hay que portarla explícito (y centralizado, no copy-paste por cast). Corolario: un proxy que re-envuelve `resp.text` produce errores anidados ilegibles — devolvé el `detail` parseado.
- **Guard:** `tests/architecture/test_castkit_loopback.py` prohíbe `httpx` loopback `:8000` fuera del castkit (para todo cast futuro, incl. el import aliased `from httpx import AsyncClient`); `tests/test_casts_auth_integration.py` reproduce el incidente con auth enforced; `tests/test_castkit.py` fija el contrato (propaga / desanida / 502-504). **Candidato §10:** la suite corre con auth no-op por default — por eso el bug pasó verde (gotcha #1: schema verde ≠ comportamiento). Un perfil de CI que levante cada cast con Cognito enforced lo cazaría sin el guard dedicado.

### L-17 · Una integración hubara→GraphAgents no existe en el mapa hasta declarar su seam — y una regla que vive solo en prosa se olvida: se hace ley con guard (2026-07-10, window-strategist y order-sentinel invisibles en el workspace de Acktos Studio)

- **Síntoma:** el operador abrió el workspace de Acktos Studio y los agentes `window-strategist` y `order-sentinel` — ambos VIVOS en prod, dispatchados por sus plugins vía Temporal Schedule — no tenían línea de relación con el System Map. Dos sistemas conectados en producción, dibujados como islas.
- **Causa raíz (DOS capas):** (1) las conexiones cross-sistema NO se auto-detectan — viven declaradas en `vscode-hubara/seams.yaml`, y la regla "declarar la costura al cerrar la integración" existía SOLO como prosa en el recipe 2.7 del skill de GraphAgents: se olvidó en las DOS integraciones siguientes a la que la escribió. (2) Distribución: la extensión resolvía seams como `<repoRoot>/seams.yaml` → bundled del VSIX; como el archivo vivo está en `vscode-hubara/seams.yaml` (no en el root), TODA instalación usaba la copia CONGELADA al empaquetar — aun editando el archivo del repo, el operador no veía las costuras nuevas sin re-empaquetar.
- **Fix aplicado:** (a) las 2 seams declaradas (`window-strategist-cycle`, `order-sentinel-cycle`) con el call-site real en el label; (b) guard `tests/platform/test_graphagents_seams.py` — todo `launcher.dispatch("<agent>")` LITERAL en `src/plugins/` exige su seam `hub:plugin:<id> → ga:agent:<id>`, y toda seam declarada debe resolver contra `plugin.yaml` + manifests de GraphAgents reales (los dispatch dinámicos, caso ads, se declaran a mano y los cubre el chequeo de resolución); (c) `seamsFilePath` de la extensión ahora busca `<repoRoot>/vscode-hubara/seams.yaml` antes del bundled — las costuras llegan por git pull, sin VSIX nuevo.
- **Regla para el skill:** al integrar un plugin con GraphAgents (o cualquier sistema externo visible en el Studio), la definición de HECHO incluye la seam declarada en `vscode-hubara/seams.yaml` con label citando el código vivo. Meta-regla: una obligación de proceso que solo vive en prosa de un skill SE VA A OLVIDAR — si el olvido es detectable estáticamente, escribí el guard en el mismo PR que la regla. Y todo archivo de configuración que la extensión bundlea es una COPIA CONGELADA: si describe el repo, la resolución debe preferir el archivo del repo.
- **Guard:** `tests/platform/test_graphagents_seams.py` (bidireccional: dispatch literal sin seam = rojo; seam que no resuelve = rojo).

### L-18 · Un plugin que escribe en conversaciones sin `depends_on: [chats]` es una isla en el System Map — y la dependencia funcional queda sin enforcement (2026-07-18, marketing invisible en Acktos Studio)

- **Síntoma:** el operador abrió Acktos Studio y el plugin `marketing` — vivo en prod, enviando campañas reales — no aparecía dibujado/conectado en el System Map. El plugin certificaba C2, todos los gates verdes, el payload del bridge lo traía… como componente AISLADO: cero edges hacia el resto del sistema.
- **Causa raíz:** el edge plugin→plugin del builder del System Map sale EXCLUSIVAMENTE de `depends_on:` del manifest. Marketing declaró `depends_on: []` pese a tener dependencia funcional DURA con chats: (1) escribe templates en las sesiones de conversación que chats posee, (2) su promesa de opt-out ("respóndeme NO MÁS y te doy de baja", exigida por Meta en el copy aprobado) la cumple el INGEST de chats (`detect_marketing_opt_out`). eta y reengagement sí lo declaraban — la regla era prosa/patrón, no ley (misma meta-regla de L-17: lo que vive en prosa se olvida).
- **Consecuencias más allá del dibujo:** con `ENABLED_PLUGINS` sin chats, marketing bootea y ENVÍA — pero nadie procesa respuestas ni bajas: sanción de Meta en potencia, y P-6 no lo caza porque solo protege lo declarado.
- **Fix aplicado:** (a) `depends_on: [chats]` en el manifest de marketing con el porqué comentado; (b) guard `tests/platform/test_session_plugins_depend_on_chats.py` — todo plugin cuyo código backend referencie los símbolos de envío a sesiones (`send_whatsapp_template_activity` / `send_template_to_session` / `send_whatsapp_message_activity`) DEBE declarar `depends_on: [chats]`; con self-test del detector (chats+eta+marketing deben aparecer en el scan, o el guard está roto).
- **Regla para el skill:** al crear un plugin, la definición de HECHO incluye (1) declarar TODA dependencia funcional en `depends_on:` — la pregunta gatillo: "¿escribo en conversaciones? ¿otro plugin cumple promesas mías?" — y (2) verificar el dibujo: `build_system_graph()` debe mostrar el plugin CONECTADO (edges hacia afuera), no como isla, antes de cerrar. El manifest no es solo config de deploy: es la fuente del mapa.
- **Guard:** `tests/platform/test_session_plugins_depend_on_chats.py`.

<!-- AÑADIR NUEVAS LECCIONES ARRIBA DE ESTA LÍNEA, NUMERADAS L-1, L-2, ... -->

---

## §10. Qué le falta a esta arquitectura (consciente, para no "descubrirlo")

- **P-19b**: smoke FUNCIONAL del dispatch (evento real → workflow eta arranca)
  en `tests/functional/` con stack arriba. Hasta entonces, el wiring estático
  está probado; el dispatch end-to-end se verifica a mano en validación.
- **D4b**: el ingest de WhatsApp vive en `chats` (dep dura declarada de eta).
  Moverlo a `platform/ingest` eliminaría esa dep — hacerlo cuando un tenant
  necesite eta sin chats.
- **D2**: gating frontend es build-time (un build por tenant). Registry
  runtime solo si el número de tenants lo justifica.
- CI corre gates de arquitectura + premortem; la suite unit completa corre
  local — un job adicional la cubriría.

**Fin.** Si algo de este doc contradice el código vivo, gana el código — y
esa contradicción es una lección L-# que hay que escribir.
