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

## §4. Recetas (paso a paso, sin pensar)

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

## §8. Verificación (la definition-of-done de CUALQUIER cambio)

```bash
cd hubara_agency && uv run lint-imports && \
  MEDUSA_BASE_URL=http://medusa.invalid MEDUSA_ADMIN_TOKEN=ci-dummy \
  OTEL_SDK_DISABLED=true uv run pytest tests/architecture tests/plugins -q
cd frontend_dashboard && npm run plugins:sync && npx tsc -b && \
  npm run test:arch && npm test
```

- Tocaste PROTECTED ⇒ prefijo `ARCH_CHANGE_APPROVED=1` en los tests + label
  `architecture-change` en el PR.
- Los dummies Medusa son para architecture/plugins; NO los uses con
  `tests/platform/` (cuelga en retries HTTP).
- 3 fallos conocidos PRE-existentes en `tests/plugins/chats` (voseo + 2
  watchdog) — no son tuyos; cualquier OTRO rojo sí.
- Cambios de comportamiento visibles ⇒ verificá contra el stack Docker real
  (puertos en CLAUDE.md raíz §12) — tests verdes ≠ feature viva (gotcha #1).

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
