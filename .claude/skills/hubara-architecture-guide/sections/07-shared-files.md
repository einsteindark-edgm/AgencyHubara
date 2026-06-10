# Sección 07 — Shared files (qué es spinal + wiring_intents vocabulary + merger criterion)

> **Cuándo leer esto:** tu task toca o podría tocar un archivo "shared"
> (cross-plugin). O sos el `hubara-merger-archon` consolidando intents.
> **Pre-requisito:** `sections/01-general.md`.
> **Tamaño:** ~10 KB.

---

## §1. ¿Qué es un "spinal file"?

Un **spinal file** es un archivo shared cross-plugin que **2+ tasks
paralelas podrían modificar**. Cuando N implementers corren en paralelo
y todos agregan al mismo barrel / al mismo registry / al mismo schema,
git's 3-way merge falla.

La solución es **wiring_intents**: cada implementer emite metadata
declarativa de **qué quería agregar al spinal file**, y un **merger**
consolida los intents después.

---

## §2. Inventario completo de shared files (post-PR11)

| Archivo | Spinal? | Kind | Conflict source |
|---|---|---|---|
| `frontend_dashboard/src/plugins/_schema/plugin.schema.yaml` | ✅ | `yaml_dict_keys_append` | 2+ plugins agregan campo nuevo al schema |
| `frontend_dashboard/scripts/plugins-sync.ts` | ✅ (raro) | `ts_function_body` | Cambiar el validator del registry |
| `hubara_agency/src/platform/contracts.py` | ✅ | `python_dataclass_module` | 2+ plugins agregan DTO cross-plugin |
| `hubara_agency/src/platform/registries.py` | ✅ (raro) | `python_factory_module` | Pattern de registries cambia |
| `hubara_agency/src/platform/tool_extensions.py` | ✅ (raro) | `python_factory_module` | DI invertida cambia |
| `hubara_agency/tests/architecture/conftest.py` — `R_JSON_FROZEN_EXEMPTIONS` / `R_HEARTBEAT_EXEMPTIONS` | ✅ | `python_dict_entries_append` | 2+ plugins piden exemption |
| `frontend_dashboard/src/shared/ui/Icon.tsx` | ✅ (raro) | `ts_object_entries_append` | SOLO glifos genuinamente compartidos cross-plugin; el glifo de UN plugin va en su `frontend/icons.tsx` (NO spinal) — gate P-12 |
| `frontend_dashboard/src/shared/*/index.ts` | ✅ | `ts_barrel` | 2+ plugins agregan primitivas shared |
| `frontend_dashboard/src/plugins/<id>/frontend/entities/**` | ❌ | single-owner | Post-refactor F1-F8: cada entity tiene UN dueño (su plugin) — dos tasks paralelas no comparten entity |
| `frontend_dashboard/src/app/providers/index.tsx` | ✅ | `app_provider_composition` | 2+ plugins agregan provider |
| `frontend_dashboard/src/pages/Dashboard.tsx` | ❌ | — | 100% data-driven; no se edita por feature |
| `frontend_dashboard/src/index.css` (`@theme {...}`) | ✅ | `css_theme_block` | 2+ plugins agregan tokens Tailwind |
| `hubara_agency/docker-compose.local.yml` | ❌ | autogen | `render-compose.py` lo regenera |
| `hubara_agency/uv.lock` | ❌ | lock file | Conflict mecánico resuelto por `uv lock` |
| `frontend_dashboard/package-lock.json` | ❌ | lock file | Conflict mecánico resuelto por `npm install` |
| `PLUGIN_REFACTOR_LOG.md` | ❌ | append-only | Conflict trivial |
| `frontend_dashboard/src/plugins/<id>/plugin.yaml` | ❌ | propio del plugin | Cada plugin tiene el suyo |
| `hubara_agency/src/plugins/<id>/**` (Python) | ❌ | propio del plugin | No cross |
| `frontend_dashboard/src/plugins/<id>/frontend/**` | ❌ | propio del plugin | No cross |
| `hubara_agency/k8s/aws-produccion/worker-<name>.yaml` | ❌ | file-per-worker | No cross |

> **(post-refactor F1-F8)** Las entries glob de `src/entities/*` que vivían
> en esta tabla ya no aplican: `src/entities/` central quedó VACÍO (gate
> P-11) y las entities son **single-owner del plugin** — nunca spinal. El
> dato que 2+ plugins consumen va por **cast declarado** (`consumes:` del
> manifest + cast server-side; PLUGIN_CONTRACT.md §5.3), no por una entity
> compartida. Y `Icon.tsx` dejó de ser el único camino para glifos: el
> default es `plugins/<id>/frontend/icons.tsx` (mergeado a `PLUGIN_ICONS`
> por `plugins:sync`).

### §2.1 NUNCA spinal (operator-owned)

| Archivo | Por qué |
|---|---|
| `hubara_agency/k8s/aws-produccion/api-deployment.yaml` | Cambia raro; ADR + PR explícito |
| `hubara_agency/run_api.py` | Entry point fijo |
| `hubara_agency/src/main.py` | Loader fijo; cambios = ADR |
| `hubara_agency/src/run_workers.py` | Meta-launcher fijo |
| `hubara_agency/pyproject.toml` | Dependencies — el planner rechaza cambios sin ADR |
| `frontend_dashboard/package.json` | Mismo |
| `frontend_dashboard/vite.config.ts`, `tsconfig*.json` | Config; cambios raros |
| `frontend_dashboard/src/main.tsx` | Mount fijo |
| `frontend_dashboard/src/test/architecture/` | Tests architecture — out-of-scope para features |
| `frontend_dashboard/.dependency-cruiser.cjs` | Idem |
| `hubara_agency/tests/architecture/` (excepto las exemption dicts) | Tests architecture |
| `hubara_agency/.importlinter` | Contratos R-DIP |

> **Fuente única de los protected (F8):** la lista de paths protegidos
> vive ÚNICA en `hubara_agency/.hubara/spinal-files.yaml` (entries
> `protected: true`); los meta-gates de ambos stacks (backend
> `tests/architecture/conftest.py`, frontend
> `src/test/architecture/helpers.ts`) la DERIVAN de ahí. Para editar un
> protected: localmente correr los tests con `ARCH_CHANGE_APPROVED=1`; el
> PR lleva el label `architecture-change`.

---

## §3. Vocabulario de `wiring_intents` (kinds soportados)

Cada `kind` corresponde a un tipo de mutación append-only. El merger
consume estos kinds y aplica de forma determinística.

### §3.1 Python kinds

| Kind | Target files | Qué hace |
|---|---|---|
| `python_dataclass_module` | `contracts.py` | Append `@dataclass(frozen=True) class X: ...` |
| `python_factory_module` | `composition.py`, `prompts.py`, `registries.py` | Append `def factory(...): ...` o constante |
| `python_workflow_list` | `worker.py` (legacy exoclaw) | Append a `workflows=[...]` / `activities=[...]` / `register_tool_extension(...)` |
| `python_constants_module` | `constants.py` | Append `MY_CONST = "value"` |
| `python_dict_entries_append` | `R_JSON_FROZEN_EXEMPTIONS`, `R_HEARTBEAT_EXEMPTIONS` | Append key/value al dict literal |
| `markdown_section_append` | `workspace/TOOLS.md`, `IDENTITY.md`, etc. | Append `## <heading>` + body bajo un anchor |

### §3.2 TypeScript kinds

| Kind | Target files | Qué hace |
|---|---|---|
| `ts_barrel` | `shared/*/index.ts` (las entities del plugin son single-owner — sin intent) | Append `export { X } from "./Y";` |
| `ts_factory_module` | (histórico: `entities/*/api.ts` central — hoy esas entities viven en el plugin y NO emiten intent) | Append función / hook |
| `ts_dataclass_module` | (histórico: `entities/*/model.ts` + `contracts.ts` central — ídem, single-owner) | Append interface / Zod schema |
| `ts_object_entries_append` | `shared/ui/Icon.tsx:ICONS` (SOLO glifos genuinamente compartidos; el resto va en `frontend/icons.tsx` del plugin) | Append `key: ValueComponent,` dentro del objeto |
| `ts_function_body` | `scripts/plugins-sync.ts` (raro) | Modificar cuerpo de función específica |
| `app_provider_composition` | `app/providers/index.tsx` | Wrap `<NewProvider>` alrededor de `{children}` |
| `page_feature_mount` | `pages/<X>.tsx` (raro post-PR11) | Mount `<NewFeature />` en un anchor de la page |

### §3.3 CSS / YAML kinds

| Kind | Target files | Qué hace |
|---|---|---|
| `css_theme_block` | `src/index.css` | Append `--token-name: value;` dentro de `@theme {}` |
| `tailwind_token` (alias) | `src/index.css` | Mismo, semántico para tokens |
| `yaml_dict_keys_append` | `plugin.schema.yaml` | Append `new_property: { type: ... }` al `properties:` |

---

## §4. Schema de un `wiring_intent` en `task-result.yaml`

```yaml
wiring_intents:
  <spinal_file_path>:
    - kind: <one of the kinds above>
      # Campos según el kind:
      #   name: <identifier — class name, function name, token name, etc.>
      #   definition: |
      #     <SYNTACTICALLY VALID standalone block>
      #   requires_imports:
      #     - "from src.x import Y"
      #     - "import { Y } from \"@/x\";"
      #   order_hint: alphabetical_by_name | append | sorted_by_kind
      # (más campos específicos del kind)
```

### Ejemplos concretos

```yaml
# Worker que registra una tool nueva (en chats/workers/sales.py):
wiring_intents:
  hubara_agency/src/plugins/chats/workers/sales.py:
    - kind: register_tool_extension
      call: "ManageConversationTagTool(workspace_path=str(workspace_path))"
      requires_imports:
        - "from src.plugins.chats.agent.sales.tools.tag import ManageConversationTagTool"
      order_hint: alphabetical_by_call

# Plugin que agrega un Tailwind token nuevo:
wiring_intents:
  frontend_dashboard/src/index.css:
    - kind: tailwind_token
      name: "--color-warn"
      value: "#f59e0b"
      category: "color"
      order_hint: alphabetical_by_name

# Glifo GENUINAMENTE compartido promovido al SET BASE (raro post-F7 —
# el glifo de UN solo plugin va en su frontend/icons.tsx, SIN intent):
wiring_intents:
  frontend_dashboard/src/shared/ui/Icon.tsx:
    - kind: ts_object_entries_append
      name: "compass"
      definition: 'CompassIcon'
      requires_imports:
        - 'import { CompassIcon } from "lucide-react";'
      order_hint: alphabetical_by_name

# Plugin que agrega un DTO cross-plugin a platform/contracts.py:
wiring_intents:
  hubara_agency/src/platform/contracts.py:
    - kind: python_dataclass_module
      name: "MyNewDecision"
      definition: |
        @dataclass(frozen=True)
        class MyNewDecision:
            session_id: str
            payload: str
      requires_imports:
        - "from dataclasses import dataclass"
      order_hint: alphabetical_by_name
```

---

## §5. Reglas para emitir wiring_intents (desde el implementer)

1. **Emitir SIEMPRE para cada spinal file que tu task tocó.** El local
   edit es para que tus tests pasen. El intent es para que el merger
   consolide cuando otros plugins también editen el mismo file.
2. **NO emitir para `affects_new_files`.** Files nuevos no conflictan
   (cada plugin crea su propio path).
3. **`requires_imports` lista naively** (sin dedupe). El merger
   deduplica al consolidar.
4. **`definition` / `value` / `content` deben ser SYNTACTICALLY VALID
   standalone.** El merger los inserta verbatim.
5. **Un intent por adición atómica.** Tres `register_tool_extension`
   calls → tres intents.
6. **Si necesitás MODIFICAR (no append) un entry existente**, NO emitas
   intent. Marcá `status: blocked, blocked_reason: requires_planner_update`
   — el planner replantea la tarea.
7. **Si editaste un spinal file NO declarado en `affects_spinal_files`
   de tu task**, scope violation. `status: blocked, requires_planner_update`.

---

## §6. Cuándo invocar `hubara-merger-archon`

El merger se invoca **solo cuando ≥2 sub-pipelines paralelos emitieron
intents al mismo spinal file**. Si solo uno editó shared, no hay nada
que consolidar (git auto-merge funciona porque el otro plugin no tocó).

Decisión flow del orquestador:

```
                Multi-plugin HU corrió en paralelo (FASE 3)
                                │
                                ▼
                  Recolecta task-result.yaml de N plugins
                                │
                                ▼
                ¿Algún spinal file tiene intents de 2+ plugins?
                                │
              ┌─────────────────┴─────────────────┐
              │ no                                │ sí
              │                                   │
              ▼                                   ▼
       Saltar merger                   Invocar hubara-merger-archon
       (git auto-merge OK)             con $ARTIFACTS_DIR/batch-results/
              │                                   │
              └─────────────────┬─────────────────┘
                                ▼
                         FASE 4 — Validación final consolidada
```

---

## §7. Algoritmo del merger (resumen)

El `hubara-merger-archon` (ver `HUBARA_SKILL_BLUEPRINT.md §6.5`) hace:

1. **Carga task-result.yaml de cada plugin** desde `$ARTIFACTS_DIR/batch-results/`.
2. **Agrega intents por spinal_file** (dict `{file: [(F-id, intent), ...]}`).
3. **Por cada spinal file:**
   - Read main-state.
   - Deduplica `requires_imports` (set semantics), agrupa stdlib /
     third-party / local, ordena alfabético, inserta al top respetando PEP 8.
   - Aplica cada intent en orden (alphabetical_by primary id por default,
     o `order_hint` si se especifica).
   - Si dos intents tienen mismo `name` con `definition` distinta →
     **error, restore main-state**.
   - Si dos intents tienen mismo `name` con `definition` idéntica → skip
     (idempotent).
4. **Valida sintaxis:**
   - `.py` → `ast.parse(content)`.
   - `.ts/.tsx` → grep mínimo + `tsc` opcional.
   - `.md` → headings well-formed.
   - `.yaml` → `yaml.safe_load(content)`.
   - Si falla, **restore main-state + record error**.
5. **Emite `$ARTIFACTS_DIR/merge-report.yaml`** con `ok|partial|failed`.

---

## §8. Conflicts mecánicos (no van por merger)

Estos no son spinal pero **siempre causan conflict mecánico**:

| Archivo | Resolución |
|---|---|
| `hubara_agency/docker-compose.local.yml` | `git checkout --theirs docker-compose.local.yml && cd hubara_agency && uv run python scripts/render-compose.py && git add docker-compose.local.yml` |
| `hubara_agency/uv.lock` | `git checkout --theirs uv.lock && uv lock && git add uv.lock` |
| `frontend_dashboard/package-lock.json` | `git checkout --theirs package-lock.json && npm install && git add package-lock.json` |
| `PLUGIN_REFACTOR_LOG.md` | Append manual; tomar ambos lados del conflict |

El orquestador del pipeline hubara hace estas resoluciones automáticamente
en FASE 4 (después del fan-out).

---

## §9. Tests que enforzan el isolation

Cualquiera de estos tests rompe = bug en tu task o en el manifest del plugin:

| Test | Qué bloquea |
|---|---|
| `test_every_manifest_worker_declares_task_queue` | Worker sin `task_queue` en manifest |
| `test_task_queues_are_unique_across_workers` | Dos workers con misma queue |
| `test_every_worker_in_manifest_has_k8s_deployment` | Worker declarado sin K8s manifest |
| `test_every_k8s_worker_corresponds_to_a_manifest_worker` | K8s huérfano (worker borrado del manifest) |
| `test_docker_compose_local_is_up_to_date_with_manifests` | `docker-compose.local.yml` desincronizado |
| `test_plugin_id_regex_matches_between_schema_and_sync` | Regex de `id` divergente entre schema YAML y `plugins-sync.ts` |
| `test_existing_plugin_ids_match_the_pattern` | Plugin id no cumple el pattern |
| `plugins-no-cross-plugin` (dep-cruiser) | `@plugins/A/* → @plugins/B/*` |
| `agents-independent` (import-linter) | `src.plugins.A.agent → src.plugins.B.agent` |
| P-11 (`test_plugin_entity_ownership.arch.test.ts`) | `src/entities/` central con contenido (debe quedar VACÍO) |
| P-22 (mismo archivo) | Un plugin importando la entity de otro plugin |
| P-9 (`test_plugin_contract.py`) | Frontend con strings `/api/<otro-plugin>/` (estricto, sin xfail) |

Detalle en `sections/08-tests-and-gates.md`.

---

## §10. Próximo paso

| Si vas a… | Leé después |
|---|---|
| Implementar el `hubara-merger-archon` skill | El blueprint en `HUBARA_SKILL_BLUEPRINT.md §6.5` |
| Diagnosticar conflict mecánico | `sections/08-tests-and-gates.md` |
| Saber qué tests bloquean violaciones de shared files | `sections/08-tests-and-gates.md` |
| Entender el algoritmo del merger en detalle | el SKILL.md del merger (`.claude/skills/hubara-merger-archon/SKILL.md` cuando exista) |

---

**Fin sección 07.**
