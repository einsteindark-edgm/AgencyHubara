# Sección 09 — Convenciones (naming + secrets + env vars + comandos + vault)

> **Cuándo leer esto:** vas a tomar decisiones operacionales (naming de
> queue, dónde poner un secret, qué env vars declarar) o a correr un
> comando que no recordás.
> **Pre-requisito:** `sections/01-general.md`.
> **Tamaño:** ~8 KB.

---

## §1. Naming conventions

| Concepto | Pattern / formato | Ejemplo válido | Ejemplo inválido |
|---|---|---|---|
| `plugin_id` (dirname + manifest `id`) | `^[a-z][a-z0-9_]*$` (snake_case) | `chats`, `agents_admin`, `order_tracking` | `chat-bot` (dash), `2chat` (digit first) |
| `worker.name` (dentro del plugin) | lowercase, palabra única o snake_case | `sales`, `remarketing`, `catalog_sync` | `CatalogSync`, `sync-catalog` |
| `task_queue` | `^queue-[a-z][a-z0-9-]*$` (kebab-case, prefijo `queue-`) | `queue-sales-agent`, `queue-catalog-sync` | `sales`, `SalesQueue` |
| K8s deployment file | `worker-<name>.yaml` (matchea el `name` del manifest, con dashes) | `worker-sales.yaml`, `worker-catalog-sync.yaml` | `worker_sales.yaml`, `chats-sales.yaml` |
| K8s deployment metadata.name | `hubara-worker-<plugin>-<name>` | `hubara-worker-chats-sales` | `worker-sales` (falta prefijo) |
| Section `key` (frontend) | lowercase, sin guión medio | `chat`, `upload`, `agents` | `chat-bot` (dash), `Chat` |
| HU id (Archon pipeline) | `HU-<YYYYMMDD>-<HHMMSS>-<slug>` | `HU-20260517-143025-add-image-tool` | `HU-1`, `HU-2026-05-17` (missing time) |
| Branch (Archon pipeline) | `hu/<HU_ID>` | `hu/HU-20260517-143025-add-image-tool` | `feature/...`, `add-image-tool` |
| Tool name (LLM-facing) | snake_case | `search_products`, `manage_conversation_tag` | `SearchProducts`, `search-products` |
| Activity name | `name="snake_case"` en `@activity.defn` | `name="send_whatsapp_message"` | `name="SendWhatsAppMessage"` |
| Workflow class | `PascalCase` + suffix `Workflow` | `HubaraSalesSessionWorkflow`, `RemarketingSessionWorkflow` | `salesWorkflow`, `Sales` |
| DTO frozen | `PascalCase` + suffix descriptivo (`Decision`, `Input`, `Output`, `Spec`) | `TransferDecision`, `BootstrapSalesInput` | `transfer_decision`, `bootstrap_input` |
| Composition factory | `get_<thing>` snake_case con `@lru_cache(maxsize=1)` | `get_manage_conversation_tag_tool` | `make_tag_tool`, `tagToolFactory` |

---

## §2. Secrets de K8s (manifest → cluster)

```yaml
# en plugin.yaml — agent.workers[].deployment.env_secrets:
deployment:
  env_secrets:
    - { var: DEEPSEEK_API_KEY,        secret: hubara-llm-secret,      key: DEEPSEEK_API_KEY }
    - { var: WHATSAPP_PHONE_NUMBER_ID, secret: hubara-whatsapp-secret, key: WHATSAPP_PHONE_NUMBER_ID }
    - { var: WHATSAPP_ACCESS_TOKEN,   secret: hubara-whatsapp-secret, key: WHATSAPP_ACCESS_TOKEN }
    - { var: WHATSAPP_VERIFY_TOKEN,   secret: hubara-whatsapp-secret, key: WHATSAPP_VERIFY_TOKEN }
```

| Campo | Significado |
|---|---|
| `var` | Env var name dentro del container (lo que tu código lee con `os.environ.get("DEEPSEEK_API_KEY")`) |
| `secret` | Nombre del K8s `Secret` resource (ya creado en el cluster por el operador) |
| `key` | Key dentro del Secret data block |

### §2.1 K8s Secrets actuales en el cluster (no agregar sin ADR)

- `hubara-llm-secret`: DEEPSEEK_API_KEY, OPENAI_API_KEY (futuro)
- `hubara-whatsapp-secret`: WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_ACCESS_TOKEN, WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET (futuro)
- `hubara-medusa-secret`: MEDUSA_BASE_URL, MEDUSA_ADMIN_TOKEN

### §2.2 Si necesitás secret nuevo

1. ADR documentando el secret + por qué.
2. Operador lo crea con `kubectl create secret generic ...`.
3. Declarar en `deployment.env_secrets` del manifest.
4. NO commitear el valor del secret. NUNCA.

---

## §3. Env vars no-secret (declarativas en manifest)

```yaml
# en plugin.yaml — agent.workers[].compose.env:
compose:
  env:
    TEMPORAL_URL: temporal:7233
    API_BASE_LLMLITE: http://litellm:4000
    WORKSPACE_VAULT_DIR: /app/hubara_vault
    CATALOG_SNAPSHOT_DIR: /app/hubara_vault/catalog
    CATALOG_MAX_AGE_MINUTES: "30"
    DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}   # ← interpolated del host (.env del operador)
```

| Tipo de valor | Cuándo usar |
|---|---|
| Literal string | Configuración fija (URLs internas, paths, defaults) |
| `${HOST_VAR}` | Pass-through al `.env` del operador (compose-only; en K8s usa env_secrets) |

### §3.1 Wiring intents para env vars (documentación cross-plugin)

```yaml
# en plugin.yaml — al fondo:
wiring_intents:
  env_vars_required:
    - DEEPSEEK_API_KEY
    - WHATSAPP_PHONE_NUMBER_ID
    - WHATSAPP_ACCESS_TOKEN
    - WHATSAPP_VERIFY_TOKEN
    - TEMPORAL_URL
    - WORKSPACE_VAULT_DIR
```

Esto es **declarativo** (no se aplica auto). Lo usan tests + docs + el
operador al setupear un tenant nuevo.

### §3.2 Env vars cross-plugin globales (en `src/platform/config.py`)

Solo van acá si las leen 2+ plugins:

```python
# src/platform/config.py
import os
WORKSPACE_VAULT_DIR = os.environ.get("WORKSPACE_VAULT_DIR", "./hubara_vault")
TEMPORAL_URL = os.environ.get("TEMPORAL_URL", "localhost:7233")
# ... pocas más
```

**NO agregar plugin-specific config acá.** Eso va en el worker module
del plugin o en `composition.py`.

---

## §4. Vault paths (sub-namespaces por plugin)

```
$WORKSPACE_VAULT_DIR/                     ← env var, default ./hubara_vault
├── wa_<phone>/                           ← runtime sessions (plugin chats)
│   ├── metadata.json                     ← active_route, tag, status_history, last_inbound_message_id
│   └── sessions/<session_id>.jsonl       ← message history (sales + remarketing)
├── catalog/                              ← snapshot (escribe catalog, lee chats)
│   ├── manifest.json                     ← productos + variantes
│   └── products/<id>.json                ← detalle por producto
└── <new_plugin>/                         ← si tu plugin escribe, agregá su top-level
    └── ...
```

### §4.1 Reglas críticas del vault

1. **Cada plugin que escribe usa su propio sub-namespace top-level.**
   Si tu plugin no respeta esto, otro plugin podría sobreescribir tu data.
2. **Ningún test puede escribir al vault real.** Defensa en 3 capas:
   - Fixture autouse `_isolate_vault_dir` en `tests/conftest.py` redirige
     `WORKSPACE_VAULT_DIR` a `tmp_path` por test.
   - Tools que aceptan `vault_dir=` reciben `tmp_path` en constructor.
   - `monkeypatch.setattr` puntual cuando un módulo capturó el global
     por import.
3. **Los `wa_*/metadata.json` están commiteados como seed data** para que
   el frontend dev local muestre UI realista. NO borrar.

### §4.2 K8s mount

```yaml
# en k8s/aws-produccion/efs-pvc.yaml + worker manifests:
volumes:
  - name: vault
    persistentVolumeClaim: { claimName: hubara-vault-efs }
volumeMounts:
  - name: vault
    mountPath: /app/hubara_vault
```

EFS permite multi-writer (sales + remarketing + catalog mountean el mismo
PVC). Pero respetá los sub-namespaces para no pisarte con otro worker.

---

## §5. `render-compose.py` — cuándo correr + qué genera

```bash
cd hubara_agency
uv run python scripts/render-compose.py
# Output: [render-compose] wrote hubara_agency/docker-compose.local.yml (~5500 bytes)
```

### §5.1 Cuándo correr

- Después de editar `agent.workers[].compose` en cualquier manifest.
- Después de agregar/eliminar un worker en cualquier manifest.
- **Antes de cada commit que toque manifests.** Si olvidás, el test
  `test_docker_compose_local_is_up_to_date_with_manifests` falla en CI.

### §5.2 Qué genera

`docker-compose.local.yml` con:

- Servicios fijos de `docker-compose.base.yml`: db, temporal, temporal-ui,
  litellm, hubara-api, hubara-frontend.
- Servicios auto-generados desde cada `agent.workers[].compose`:
  - `hubara-worker-<plugin>-<name>` (e.g. `hubara-worker-chats-sales`).
  - Image: `hubara-agency-prod:latest` (la misma para todos los workers).
  - Command: `python -m hubara_agency.src.plugins.<plugin>.workers.<name>`.
  - Env, volumes, depends_on del manifest.

### §5.3 Naming override

Si querés un nombre custom (raro), declarar en el manifest:

```yaml
agent:
  workers:
    - name: sales
      module: src.plugins.chats.workers.sales
      compose:
        service_name: my-custom-name    # override del default hubara-worker-chats-sales
```

---

## §6. `plugins-sync.ts` — cuándo correr + qué genera

```bash
cd frontend_dashboard
npm run plugins:sync
# Output: [plugins-sync] generated registry with 5 plugins
```

### §6.1 Cuándo correr

- Después de editar cualquier `plugin.yaml` (sección `frontend:`).
- Después de agregar/eliminar un plugin.
- **Automáticamente en `predev` y `prebuild`** (corre antes de `npm run
  dev` y `npm run build`). En la mayoría de los casos no necesitás
  invocarlo a mano.

### §6.2 Qué genera

`src/app/plugin-registry.generated.ts` (gitignored) con el `PLUGINS`
array que `Dashboard.tsx` consume.

---

## §7. Comandos canónicos del día a día

### §7.1 Backend dev local

```bash
cd hubara_agency
uv sync                                     # instalar deps Python
uv run python run_api.py                    # FastAPI :8000
uv run python -m src.run_workers            # arranca chats.sales + chats.remarketing + catalog.sync

# Filtrado por plugin:
ENABLED_PLUGINS=chats uv run python run_api.py
ENABLED_PLUGINS=chats,catalog uv run python -m src.run_workers

# Un worker individual:
uv run python -m src.plugins.chats.workers.sales

# Resolver queue desde código:
uv run python -c "from src.platform.plugin_manifest import get_task_queue; print(get_task_queue('chats', 'sales'))"
# → queue-sales-agent

# Trigger Temporal workflow manual (debug):
uv run python scripts/trigger_catalog_sync.py
uv run python scripts/trigger_catalog_sync.py --no-wait
```

### §7.2 Frontend dev local

```bash
cd frontend_dashboard
npm install                                 # primera vez o cuando cambia package.json
npm run dev                                 # corre `predev` (plugins:sync) + Vite
npm run plugins:sync                        # regenera registry manualmente
npm run build                               # build tauri (requires Rust toolchain)
```

### §7.3 Stack completo dockerizado

```bash
docker compose -f hubara_agency/docker-compose.local.yml up -d
# levanta: db + temporal + temporal-ui + litellm + hubara-api +
#          hubara-worker-chats-sales + hubara-worker-chats-remarketing +
#          hubara-worker-catalog-sync + hubara-frontend
```

### §7.4 Tests + gates (orden recomendado)

```bash
# Backend
cd hubara_agency
uv run pytest -q                            # full suite (293+ tests)
uv run pytest -m architecture               # solo arquitectura
uv run pytest tests/plugins/                # premortem invariants
uv run pytest tests/functional/ -m functional -v  # functional evidence
uv run lint-imports                         # import-linter (R-DIP)

# Frontend
cd ../frontend_dashboard
npm test                                    # vitest
npm run test:arch                           # dependency-cruiser + arch tests
npx tsc -b                                  # type check (modo composite)
npm run build                               # vite build

# E2E (con FastAPI corriendo)
# Terminal 1: cd hubara_agency && uv run python run_api.py
# Terminal 2:
cd frontend_dashboard && npx playwright test
```

### §7.5 Render-compose check (antes de commit)

```bash
cd hubara_agency
uv run python scripts/render-compose.py
git diff --exit-code docker-compose.local.yml   # exit 0 → no drift
# Si exit 1, commitear el regenerated:
git add docker-compose.local.yml
```

---

## §8. PR / commit conventions

### §8.1 Commit message

```
<short description in present tense>

<longer paragraph explaining why if non-obvious>

<reference issue: Closes #N>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### §8.2 PR title

Patrones canónicos según contexto:
- `feat(plugins): <ID> — <descripción>` para features
- `fix(plugins): <ID> — <descripción>` para bugfixes
- `chore(<scope>): <descripción>` para cleanups
- `docs(<scope>): <descripción>` para docs

Ejemplos reales de la historia del refactor:
- `feat(plugins): PR9+PR10 — auditoría + premortem (26 fixes + 26 tests nuevos)`
- `feat(plugins): PR11 — manifest = single source of truth (paralelismo Archon real)`

### §8.3 Branch strategy (Archon pipeline)

- Branch base: `main`
- Branch de feature: `hu/<HU_ID>` (e.g. `hu/HU-20260517-143025-add-image-tool`)
- Squash-merge al final (1 commit por HU en main)

---

## §9. Setup inicial (1 vez por dev / por máquina)

```bash
# uv (Python)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --directory hubara_agency

# Node + npm
brew install node bun jq         # bun para scripts, jq para parsing en bash

# gh CLI (para Archon pipeline)
brew install gh
gh auth login
gh auth refresh -s project,read:project   # para GitHub Projects v2

# Tauri toolchain (solo si vas a buildear desktop)
brew install rust
cargo install create-tauri-app
```

---

## §10. Próximo paso

| Si vas a… | Leé después |
|---|---|
| Ver patrones completos de "agregar X" | `sections/10-cookbook.md` |
| Saber el schema YAML completo | `references/manifest-schema.md` |
| Ver ejemplo trabajado de plugin específico | `examples/plugin-<template>.md` |

---

**Fin sección 09.**
