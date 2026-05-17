# Hubara — project context (consumido por los skills del pipeline hubara)

> **Lectura obligatoria** para cualquier skill que invoque al implementer
> (refiner, planner, implementer, merger). El nodo `cargar-*` del workflow
> stagea este archivo en `$ARTIFACTS_DIR/project-context.md` antes de
> invocar el skill.

---

## §1. Layout del repo

- **Repo root:** `/Users/edgm/Documents/Projects/AgencyHubara` (cwd del operador cuando corre `archon run`).
- **Backend Python:** `hubara_agency/src/`
- **Frontend TS:** `frontend_dashboard/src/`
- **Manifests de plugins:** `frontend_dashboard/src/plugins/<id>/plugin.yaml`
  (asimetría: el manifest vive en el lado frontend; ambos loaders leen de ahí).
- **Plugins Python:** `hubara_agency/src/plugins/<id>/`
- **Plugins frontend:** `frontend_dashboard/src/plugins/<id>/frontend/`
- **Pipeline Archon:** `.archon/workflows/`
- **Skills Archon:** `.claude/skills/hubara-*-archon/`
- **Skill de arquitectura unificado:** `.claude/skills/hubara-architecture-guide/`
- **Convenciones pipeline:** `hubara_agency/.hubara/` (este archivo + `spinal-files.yaml`)

---

## §2. Comandos canónicos (con CWD)

### §2.1 Backend (todos desde repo root, prefijo `cd hubara_agency &&` obligatorio)

| Acción | Comando |
|---|---|
| Boot FastAPI | `cd hubara_agency && uv run python run_api.py` |
| Boot todos los workers | `cd hubara_agency && uv run python -m src.run_workers` |
| Filtrar por plugin | `cd hubara_agency && ENABLED_PLUGINS=chats uv run python run_api.py` |
| Worker individual | `cd hubara_agency && uv run python -m src.plugins.chats.workers.sales` |
| Test full | `cd hubara_agency && uv run pytest -q` |
| Architecture gate | `cd hubara_agency && uv run pytest -m architecture` |
| Premortem invariants | `cd hubara_agency && uv run pytest tests/plugins/` |
| Functional tests | `cd hubara_agency && uv run pytest tests/functional/ -m functional -v` |
| Import-linter (R-DIP) | `cd hubara_agency && uv run lint-imports` |
| Regenerar compose | `cd hubara_agency && uv run python scripts/render-compose.py` |
| Trigger catalog sync (debug) | `cd hubara_agency && uv run python scripts/trigger_catalog_sync.py` |
| Resolver task_queue | `cd hubara_agency && uv run python -c "from src.platform.plugin_manifest import get_task_queue; print(get_task_queue('chats', 'sales'))"` |

**Regla dura:** las commands de `uv run` SIEMPRE empiezan con
`cd hubara_agency &&`. Si en task.md §10 ves un comando sin ese prefijo,
agregalo (el planner debería haber incluido — sé defensivo).

### §2.2 Frontend (todos desde repo root, prefijo `cd frontend_dashboard &&` obligatorio)

| Acción | Comando |
|---|---|
| Dev server | `cd frontend_dashboard && npm run dev` |
| Sync plugins | `cd frontend_dashboard && npm run plugins:sync` |
| Tests (vitest) | `cd frontend_dashboard && npm test` |
| Architecture gate | `cd frontend_dashboard && npm run test:arch` |
| Type check (composite) | `cd frontend_dashboard && npx tsc -b` |
| Build | `cd frontend_dashboard && npm run build` |
| Playwright E2E | `cd frontend_dashboard && npx playwright test` |

### §2.3 Combined gates (para PR)

```bash
# render-compose drift check (debe exit 0)
cd hubara_agency && uv run python scripts/render-compose.py && \
  git diff --exit-code docker-compose.local.yml
```

---

## §3. Naming conventions

| Concepto | Pattern | Ejemplo |
|---|---|---|
| `plugin_id` | `^[a-z][a-z0-9_]*$` (snake_case) | `chats`, `agents_admin`, `order_tracking` |
| `worker.name` | lowercase, palabra única o snake_case | `sales`, `remarketing`, `catalog_sync` |
| `task_queue` | `^queue-[a-z][a-z0-9-]*$` | `queue-sales-agent`, `queue-catalog-sync` |
| K8s deployment file | `worker-<name>.yaml` | `worker-sales.yaml`, `worker-catalog-sync.yaml` |
| K8s metadata.name | `hubara-worker-<plugin>-<name>` | `hubara-worker-chats-sales` |
| Section `key` (frontend) | lowercase, sin guion medio | `chat`, `upload`, `agents` |
| HU id | `HU-<YYYYMMDD>-<HHMMSS>-<slug>` | `HU-20260517-143025-add-image-tool` |
| Branch | `hu/<HU_ID>` | `hu/HU-20260517-143025-add-image-tool` |
| Tool name (LLM) | snake_case | `search_products`, `manage_conversation_tag` |
| Activity `@activity.defn(name=...)` | snake_case | `name="send_whatsapp_message"` |
| Workflow class | `PascalCase + Workflow` | `HubaraSalesSessionWorkflow` |
| DTO frozen | `PascalCase` + suffix (`Decision`, `Input`, `Output`) | `TransferDecision`, `BootstrapSalesInput` |
| Composition factory | `get_<thing>` + `@lru_cache(maxsize=1)` | `get_manage_conversation_tag_tool` |

---

## §4. PYTHONPATH conventions

- `from src.platform...` resuelve desde `hubara_agency/` (PYTHONPATH base via uv workspace).
- `from src.plugins.<id>...` igual.
- **NO usar** `from hubara_agency.src...` desde código del repo. Solo
  algunos tests específicos lo usan vía import path absoluto.

---

## §5. Test paths convention

| Tipo | Path |
|---|---|
| Test general | `hubara_agency/tests/test_<modulo>.py` |
| Test de un plugin tool | `hubara_agency/tests/plugins/<plugin>/tools/test_<tool>.py` |
| Test de un plugin activity | `hubara_agency/tests/plugins/<plugin>/activities/test_<activity>.py` |
| Test de un plugin workflow | `hubara_agency/tests/plugins/<plugin>/workflows/test_<workflow>.py` |
| Functional test (E2E backend) | `hubara_agency/tests/functional/test_<feature>.py` con `@pytest.mark.functional` |
| Architecture test (PROTECTED) | `hubara_agency/tests/architecture/test_*.py` — NO modificar sin ADR |
| Premortem invariant | `hubara_agency/tests/plugins/test_premortem_invariants.py` — NO modificar |
| Frontend unit test | `frontend_dashboard/src/.../<file>.test.tsx` (junto al code) |
| Frontend architecture | `frontend_dashboard/src/test/architecture/*.test.ts` — PROTECTED |
| Frontend E2E | `frontend_dashboard/e2e/<feature>/<slice>.spec.ts` |

---

## §6. Vault paths (storage sub-namespaces)

```
$WORKSPACE_VAULT_DIR/                  ← default ./hubara_vault
├── wa_<phone>/                        ← runtime sessions (plugin chats)
│   ├── metadata.json                  ← active_route, tag, last_inbound_message_id
│   └── sessions/<session_id>.jsonl    ← message history
├── catalog/                           ← snapshot (escribe catalog, lee chats)
│   ├── manifest.json
│   └── products/<id>.json
└── <new_plugin>/                      ← agregá su top-level si tu plugin escribe
    └── ...
```

**Reglas duras del vault:**

1. Cada plugin escribiendo usa su propio sub-namespace top-level.
2. Tests NUNCA escriben al vault real — fixture autouse `_isolate_vault_dir`
   en `hubara_agency/tests/conftest.py` redirige a `tmp_path`.
3. Los `wa_*/metadata.json` están committeados como seed data — NO borrar.

---

## §7. Secrets K8s actuales

| Secret name | Keys |
|---|---|
| `hubara-llm-secret` | `DEEPSEEK_API_KEY` (y `OPENAI_API_KEY` futuro) |
| `hubara-whatsapp-secret` | `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_VERIFY_TOKEN` |
| `hubara-medusa-secret` | `MEDUSA_BASE_URL`, `MEDUSA_ADMIN_TOKEN` |

**Crear secret nuevo:** requiere ADR + `kubectl create secret generic ...`
manual del operador. NO commitear valores.

---

## §8. Env vars cross-plugin globales

Viven en `hubara_agency/src/platform/config.py`. Solo agregar acá si 2+
plugins las leen:

- `WORKSPACE_VAULT_DIR` (default `./hubara_vault`)
- `TEMPORAL_URL` (default `localhost:7233`)
- `API_BASE_LLMLITE` (default `http://localhost:4000`)
- `WHATSAPP_*` (cuando el caller lo necesita)

**No agregar plugin-specific config acá.** Eso va en el worker module
del plugin o en `composition.py`.

---

## §9. Reglas duras del implementer (recordatorio)

**5 R-rules DEHA + FSD + plugin manifest:**

1. **R-DET**: workflows determinísticos (no I/O directo, no `datetime.now()`, no `random`).
2. **R-JSON**: DTOs cruzando boundary son `@dataclass(frozen=True)` JSON-serializable.
3. **R-STATELESS**: activities sin module-level cache.
4. **R-HEARTBEAT**: activities >10s usan `@with_heartbeat`.
5. **R-DIP**: `platform/` ❌→ plugins, plugins ❌→ plugins siblings, tools ❌→ `temporalio.client`.
6. **FSD layering**: `shared → entities → features → pages → app`.
7. **Manifest = SSoT**: si no está expresable en `plugin.yaml`, es bug del schema.

Detalle de cada regla en `.claude/skills/hubara-architecture-guide/references/`.

---

## §10. Comportamiento ante meta-gate failure (CRITICAL)

Si el meta-gate flagea modificación a archivos protected (listados en
`spinal-files.yaml` con `protected: true`):

- **No importa** si lo escribiste vos o si "venía preexistente en el branch".
- **No importa** si los otros tests pasan.
- **Status: blocked**, blocked_reason: requires_planner_update.
- **NO** set `ARCH_CHANGE_APPROVED=1` por tu cuenta — es bypass del operador con ADR.
- **NO** reportar `status: passed` argumentando que el cambio "no es tuyo".

---

## §11. Tooling extra disponible

| Tool | Cuándo usar |
|---|---|
| `uv` | Python deps + run (todo lo Python pasa por uv) |
| `npm` / `node` | Frontend (todo lo TS) |
| `bun` | Solo para Archon `script:` nodes (runtime más rápido que tsx) |
| `jq` | Parsing JSON en bash scripts del pipeline |
| `gh` | GitHub Issues + Projects + PRs |
| `curl` | HTTP local testing |
| `python3` | Solo para utilidades del pipeline (e.g. random port) — el código del proyecto usa `uv run python` |

---

## §12. Estado actual del repo (snapshot al momento de PR12)

| Dimensión | Valor |
|---|---|
| Plugins frontend | 5 (chats, catalog, orders, eta, agents_admin) |
| Plugins agentic | 2 (chats con 2 workers, catalog con 1 worker) |
| Workers Temporal | 3 (chats/sales, chats/remarketing, catalog/sync) |
| Routers FastAPI | 3 (todos de chats) |
| Tests Python | ~293 |
| Tests frontend | ~69 |

---

**Fin project-context.md.** Este archivo se carga al comienzo de cada
invocación de skill del pipeline. Si una sección no aplica a tu task,
ignorala — pero LEELA toda primero.
