# Sección 08 — Tests y gates (architecture + premortem + functional + E2E)

> **Cuándo leer esto:** estás corriendo tests / diagnosticando un gate
> roto / decidiendo qué tests escribir.
> **Pre-requisito:** `sections/01-general.md`.
> **Tamaño:** ~10 KB.
> **Reference complementario:** `references/deha-rules.md`,
> `references/fsd-rules.md`.

---

## §1. Tabla de gates (en orden de severidad)

| Gate | Comando | Bloquea PR? | Detalle |
|---|---|---|---|
| Architecture gate Python | `cd hubara_agency && uv run pytest -m architecture` | ✅ | DEHA + invariants |
| Import-linter (R-DIP) | `cd hubara_agency && uv run lint-imports` | ✅ | 4 contratos cross-plugin |
| Premortem invariants | `cd hubara_agency && uv run pytest tests/plugins/` | ✅ | 6 tests parity |
| Architecture gate frontend | `cd frontend_dashboard && npm run test:arch` | ✅ | FSD layering + 14 anti-patterns |
| dependency-cruiser | (parte del `test:arch`) | ✅ | Cross-plugin + deep imports |
| Functional tests Python | `cd hubara_agency && uv run pytest tests/functional/ -m functional` | ✅ | Evidencia E2E backend |
| Playwright E2E frontend | `cd frontend_dashboard && npx playwright test` | ✅ | Evidencia E2E frontend |
| Unit tests Python | `cd hubara_agency && uv run pytest` | ✅ | Toda la suite (293 actuales) |
| Unit tests frontend | `cd frontend_dashboard && npm test` | ✅ | Vitest (69 actuales) |
| Type check | `cd frontend_dashboard && npx tsc -b` | ✅ | Catches issues fuera de tests |
| Build | `cd frontend_dashboard && npm run build` | ✅ | Vite + Tauri |
| Render-compose drift | `cd hubara_agency && uv run python scripts/render-compose.py && git diff --exit-code docker-compose.local.yml` | ✅ | Premortem invariant |

---

## §2. Architecture gate Python — `pytest -m architecture`

Vive en `hubara_agency/tests/architecture/`. Carpetas:

```
hubara_agency/tests/architecture/
├── conftest.py                          # exemptions dicts + helpers
│   ├── R_JSON_FROZEN_EXEMPTIONS         # dataclasses sin frozen autorizados (~5 entries)
│   └── R_HEARTBEAT_EXEMPTIONS           # activities <10s autorizadas (~3 entries)
├── test_r_det.py                        # ❌ no implementado todavía (convention-only)
├── test_r_json.py                       # AST scan de @dataclass cruzando boundary
├── test_r_stateless.py                  # AST scan de _CACHE / _REGISTRY a nivel módulo
├── test_r_heartbeat.py                  # AST scan de @activity.defn sin @with_heartbeat
├── test_r_dip.py                        # llama import-linter via lint-imports
├── test_meta.py                         # META-GATE — flagea modificaciones a archivos protected
└── ... (otros invariantes estructurales)
```

### §2.1 ARCHITECTURE PROTECTED FILES (HARD STOP)

Estos archivos son **OUT OF SCOPE para CUALQUIER feature task**:

```
hubara_agency/tests/architecture/**
hubara_agency/.importlinter
hubara_agency/tests/architecture/conftest.py (incluye R_*_EXEMPTIONS dicts)
.archon/workflows/**
.claude/skills/hubara-*    (cuando exista; también exoclaw-* y frontend-*)
```

Si tu task necesita modificarlos para que pase el gate:
1. **STOP.** No los modifiques.
2. Marcá `status: blocked, blocked_reason: requires_planner_update`.
3. Notes: "feature requires architecture-rule change in <test_file>:<test_name>; needs ADR + separate PR before this task can land".
4. El operador autoriza ADR + PR de architecture-change.

**Cardinal sin:** editar test/import-linter/conftest para "silenciar" un
fallo. Eso ship bad architecture a main y rompe el trust contract.

### §2.2 META-GATE failures (NUNCA `status: passed`)

`test_meta.py` flagea modificaciones a protected files en el branch vs
`origin/main`. Si fire:

- Da igual si lo escribiste vos o si "venía preexistente en el branch"
  (Archon copia `.archon/` del repo principal — main sucio leaka al worktree).
- Da igual si todos los OTROS tests pasan.
- **`status: blocked`, `blocked_reason: requires_planner_update`,**
  nombrá los archivos en `notes`, STOP.
- **DO NOT set `ARCH_CHANGE_APPROVED=1`** en bash. Esa env var es para
  el operador en un PR explícito de architecture-change con ADR. Si la
  ponés vos, estás mintiendo al gate.
- **NO** reportar `status: passed` argumentando que el cambio "es
  preexistente" o "no es tuyo". El operador decide; vos no.

---

## §3. Import-linter (R-DIP) — los 4 contratos

Vive en `hubara_agency/.importlinter`. Los 4 contratos actuales:

```ini
[importlinter]
root_packages =
    src.platform
    src.plugins.chats
    src.plugins.catalog

[importlinter:contract:platform-no-agents]
type = forbidden
source_modules =
    src.platform
forbidden_modules =
    src.plugins.chats.agent
    src.plugins.catalog.agent

[importlinter:contract:agents-independent]
type = forbidden
source_modules =
    src.plugins.chats.agent
    src.plugins.catalog.agent
forbidden_modules =
    src.plugins.chats.agent   # other agents
    src.plugins.catalog.agent
# (cross-agent imports prohibited — except via src.platform)

[importlinter:contract:tools-no-temporal]
type = forbidden
source_modules =
    src.plugins.chats.agent.sales.tools
    src.plugins.chats.agent.remarketing.tools
forbidden_modules =
    temporalio.client
    temporalio.worker

[importlinter:contract:parsers-pure]
type = forbidden
source_modules =
    src.plugins.chats.agent.sales.parsers
forbidden_modules =
    httpx
    requests
    litellm
    temporalio
```

### §3.1 Correr import-linter

```bash
cd hubara_agency
uv run lint-imports
# Output:
# Analyzed 4 contracts.
# - platform-no-agents      ✓ kept
# - agents-independent      ✓ kept
# - tools-no-temporal       ✓ kept
# - parsers-pure            ✓ kept
# Contracts: 4 kept, 0 broken.
```

### §3.2 Cuándo agregar contrato nuevo

Solo en PR explícito de architecture-change. El feature task NO agrega
contratos — eso es ADR scope.

---

## §4. Premortem invariants — `tests/plugins/test_premortem_invariants.py`

Los 6 tests críticos (detalle en `references/manifest-schema.md`):

| Test | Bloquea | Mensaje típico de fallo |
|---|---|---|
| `test_every_worker_in_manifest_has_k8s_deployment` | Worker en manifest sin K8s yaml | `Workers declarados en manifests sin K8s deployment correspondiente: chats/sales` |
| `test_every_k8s_worker_corresponds_to_a_manifest_worker` | K8s yaml huérfano | `K8s deployments apuntan a workers no declarados: chats/sales` |
| `test_plugin_id_regex_matches_between_schema_and_sync` | Regex divergente | `Schema regex (...) y sync.ts regex (...) divergen` |
| `test_every_manifest_worker_declares_task_queue` | Worker sin queue | `Workers sin task_queue declarado: chats/sales` |
| `test_task_queues_are_unique_across_workers` | Queue duplicada | `queue 'queue-sales' declared by both chats/sales and chats/remarketing` |
| `test_docker_compose_local_is_up_to_date_with_manifests` | Drift compose | `docker-compose.local.yml is out of sync; run uv run python scripts/render-compose.py` |
| `test_existing_plugin_ids_match_the_pattern` | Plugin id mal | `chats: id='chat-bot' no matchea ^[a-z][a-z0-9_]*$` |

### §4.1 Bypass intencional del compose check

Solo si estás en medio de un refactor del `render-compose.py` mismo:

```bash
RENDER_COMPOSE_SKIP=1 uv run pytest tests/plugins/
```

NUNCA en CI ni en feature tasks normales.

---

## §5. Architecture gate frontend — `npm run test:arch`

Vive en `frontend_dashboard/src/test/architecture/`. Incluye:

| Test | Qué verifica |
|---|---|
| `test_dep_cruiser_rules` | Las 4 import rules FSD + cross-plugin |
| `test_zod_at_boundary` | Cada `apiClient.get<unknown>(...)` se sigue de `schema.parse(...)` |
| `test_tailwind_token_naming` | NO hay `--color-text-*`; usá `--color-fg`, `--color-fg-muted` |
| `test_barrel_only_public_api` | NO hay deep imports (`@plugins/X/ui/Y` directo) |
| `test_env_centralization` | NO hay `process.env.X` fuera de `shared/config/env.ts` |
| `test_no_hardcoded_urls` | NO hay URLs hardcodeadas en componentes |
| `test_jsx_uses_tsx_ext` | JSX en `.tsx`, NO en `.ts` |
| `test_meta` | META-GATE — flagea modificaciones a archivos protected |

### §5.1 PROTECTED FILES (frontend)

```
frontend_dashboard/src/test/architecture/**
frontend_dashboard/.dependency-cruiser.cjs
frontend_dashboard/tsconfig.arch.json
*_ALLOWLIST / CSS_FILE_ALLOWLIST / ARCHITECTURE_PROTECTED_PREFIXES exports en helpers.ts
.archon/workflows/**
.claude/skills/hubara-*
```

Mismas reglas que en backend (§2.1, §2.2).

---

## §6. Functional tests Python — `tests/functional/`

**Mandatory** para cada feature task que NO sea puramente refactor
interno. Patrón:

```python
# canonical — tests/functional/test_<feature>.py
import pytest

@pytest.mark.functional
async def test_<short_outcome>(tmp_path, mock_llm):
    # Setup: instanciar tool / endpoint / workflow
    # Act: ejecutar el path
    # Assert: el outcome observable
    assert ...
```

### §6.1 Cuatro patrones de functional test

| Patrón | Cuándo usar | Fixtures clave |
|---|---|---|
| **Tool test** | Feature es un `ToolBase` nuevo | `tmp_path`, instancia tool, `await tool.execute_with_context(ctx, **params)`, assert JSON envelope |
| **FastAPI endpoint** | Feature es un endpoint nuevo | `api_client` (httpx ASGI, sin port real), `await api_client.post("/...", json={...})`, assert status + body |
| **Workflow test** | Feature es un workflow Temporal | `workflow_env` (TimeSkipping), Worker con activities mocked, `await env.client.start_workflow(...)`, assert result |
| **Agent E2E** | "User → LLM → tool → reply" path completo | Igual workflow + `mock_llm` que devuelve tool-call envelope; assert mensaje final |

### §6.2 LLM strategy

**SIEMPRE** usar `mock_llm` fixture. Es la default. Skipea con mensaje
claro si `LIVE_LLM=1` está set (modo debug solo).

**NUNCA** pin a LLM real en checked-in functional test — burns API credits + flaky.

### §6.3 Test name convention

- `test_<short_outcome>` (una observación por test).
- NO `test_all_features` ni `test_everything`.
- Output verbose útil — el captured pytest -v se incrusta en el PR
  comment como evidencia.

---

## §7. Playwright E2E frontend — `e2e/`

**Mandatory** para cada feature task que toca UI.

```typescript
// canonical — e2e/<feature>/<slice>.spec.ts
import { expect, test } from "@playwright/test";

test.describe("<feature>", () => {
  test("<user-observable outcome>", async ({ page }) => {
    await page.goto("/<route>");
    await page.getByRole("button", { name: "..." }).click();
    await expect(page.getByText("<expected visible content>")).toBeVisible();
  });
});
```

### §7.1 Backend strategy

- FastAPI en background en puerto **random** (asignado por pipeline).
- `VITE_API_URL` apunta al puerto random — el Vite dev server lo lee.
- Si necesitás estado backend específico, fixture HTTP call antes del
  user-interaction assert.

### §7.2 Reglas

- NO `page.waitForTimeout(...)` — flaky. Usá `expect(...).toBeVisible({ timeout: ... })`.
- Auto-waiting selectors: `getByRole`, `getByText`, `getByLabel`.
- Screenshots on failure automáticas (configurado en `playwright.config.ts`).

### §7.3 Skip documentado

Si el task es PURE INTERNAL REFACTOR sin UI surface change, documentá el
skip en `task-result.yaml notes`. El DoD acepta skip documentado, NO
silencio.

---

## §8. Cómo correr toda la suite localmente

```bash
# Backend full
cd hubara_agency
uv sync
uv run pytest -q
uv run pytest -m architecture
uv run pytest tests/plugins/
uv run pytest tests/functional/ -m functional -v
uv run lint-imports

# Render-compose check (no drift)
uv run python scripts/render-compose.py
git diff --exit-code docker-compose.local.yml

# Frontend full
cd ../frontend_dashboard
npm test
npm run test:arch
npx tsc -b
npm run build

# Playwright E2E (necesita FastAPI corriendo en background)
# En terminal 1:
cd ../hubara_agency && UVICORN_PORT=8000 uv run python run_api.py &
# En terminal 2:
cd frontend_dashboard
VITE_API_URL="http://127.0.0.1:8000" npx playwright test
```

---

## §9. Diagnose checklist (cuando un gate rompe)

### §9.1 `pytest -m architecture` falla

1. ¿Es R-JSON? — un `@dataclass` sin `frozen=True` cruzando boundary.
   Fix: `@dataclass(frozen=True)`. NO agregar a `R_JSON_FROZEN_EXEMPTIONS`.
2. ¿Es R-HEARTBEAT? — activity sin `@with_heartbeat`. Fix: decorar.
3. ¿Es R-DIP via import-linter? — ver §9.2.
4. ¿Es META-GATE? — tocaste protected file. `status: blocked`.

### §9.2 `lint-imports` falla

Lee el output, identifica el contrato roto, y el import específico que
viola. Fix moviendo el import a un lugar válido o reformulando el
patrón (DI invertida via `tool_extensions`, etc.).

### §9.3 `npm run test:arch` falla

1. dep-cruiser → cross-plugin import o deep import. Fix.
2. Zod at boundary → falta `schema.parse(...)`. Agregar.
3. Tailwind token naming → `--color-text-*`. Renombrar.
4. META-GATE → tocaste protected. `status: blocked`.

### §9.4 `test_docker_compose_local_is_up_to_date` falla

`cd hubara_agency && uv run python scripts/render-compose.py && git add docker-compose.local.yml && git commit`.

### §9.5 Playwright E2E falla

- Backend no arranca → revisar logs uvicorn.
- Selector no encuentra → asegurate de usar `getByRole`/`getByText`, no `getByTestId` salvo si está documentado.
- Race condition → agregar `expect(...).toBeVisible({ timeout: 10000 })`.

---

## §10. Próximo paso

| Si vas a… | Leé después |
|---|---|
| Entender las R-rules en detalle | `references/deha-rules.md` |
| Entender las FSD rules + 14 anti-patterns | `references/fsd-rules.md` |
| Saber el schema del manifest (premortem invariants) | `references/manifest-schema.md` |
| Diagnosticar problema específico de Temporal | `references/temporal-patterns.md` |

---

**Fin sección 08.**
