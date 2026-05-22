# hubara_agency — contexto backend Python

> Scoped a backend. Se carga ADEMÁS del `CLAUDE.md` raíz cuando trabajás aquí.
> Detalle canónico en `.claude/skills/hubara-architecture-guide/sections/`.

## Layering DEHA (hexagonal)

```
src/platform/       ← contracts, registries, composition compartido cross-plugin
src/plugins/<id>/   ← un plugin = una bounded context
  ├── agent/        ← workspace + tools LLM + composition (factories)
  ├── workers/      ← Temporal workers (workflows + activities)
  └── api/          ← endpoints FastAPI (opcional)
```

**Plugins actuales:** `agents_admin`, `catalog`, `chats`, `eta`, `orders`, `system_map`.

`src/sales_whatsapp/` y `src/remarketing_whatsapp/` son shells legacy del pre-PR11 — el código real vive en `src/plugins/chats/workers/{sales,remarketing}.py`.

## 5 R-rules DEHA (hard rules)

1. **R-DET** — workflows son determinísticos (no I/O directo, no `datetime.now()`, no `random`). Side effects → activity.
2. **R-JSON** — DTOs cruzando workflow ↔ activity boundary son `@dataclass(frozen=True)` JSON-serializable.
3. **R-STATELESS** — activities sin module-level cache. State vive en `composition.py` con `@lru_cache`.
4. **R-HEARTBEAT** — activities con worst-case > 10s usan `@with_heartbeat`.
5. **R-DIP** — `platform/` ❌→ plugins; plugins ❌→ plugins siblings; tools ❌→ `temporalio.client`. Cross-worker → declarative orchestration vía manifest transitions (ADR-2026-05-20).

Detalle en `.claude/skills/hubara-architecture-guide/references/deha-rules.md`.

## Comandos (todos desde el repo root con `cd hubara_agency &&`)

| Acción | Comando |
|---|---|
| Boot FastAPI | `cd hubara_agency && uv run python run_api.py` |
| Boot workers | `cd hubara_agency && uv run python -m src.run_workers` |
| Filtrar por plugin | `cd hubara_agency && ENABLED_PLUGINS=chats uv run python run_api.py` |
| Worker individual | `cd hubara_agency && uv run python -m src.plugins.chats.workers.sales` |
| Test full | `cd hubara_agency && uv run pytest -q` |
| Architecture gate | `cd hubara_agency && uv run pytest -m architecture` |
| Premortem invariants | `cd hubara_agency && uv run pytest tests/plugins/` |
| Functional E2E | `cd hubara_agency && uv run pytest tests/functional/ -m functional -v` |
| Import-linter (R-DIP) | `cd hubara_agency && uv run lint-imports` |
| Regenerar compose | `cd hubara_agency && uv run python scripts/render-compose.py` |

Tabla completa con vault, k8s, env vars: `hubara_agency/.hubara/project-context.md` §2.

## Paths PROTECTED (no editar sin ADR + `ARCH_CHANGE_APPROVED=1`)

- `tests/architecture/**` — gates de DEHA
- `tests/plugins/test_premortem_invariants.py` — invariants de plugin system
- `tests/architecture/conftest.py`
- `.importlinter`
- `src/platform/contracts.py`, `src/platform/registries.py`, `src/platform/tool_extensions.py`, `src/platform/constants.py` — spinal files

Cualquier modificación silenciosa rompe CI en `pytest -m architecture` + `lint-imports`.

## Naming conventions (extracto — full en project-context.md §3)

| Concepto | Pattern | Ejemplo |
|---|---|---|
| `plugin_id` | `^[a-z][a-z0-9_]*$` | `chats`, `agents_admin` |
| `task_queue` | `^queue-[a-z][a-z0-9-]*$` | `queue-sales-agent` |
| Tool name (LLM) | snake_case | `search_products` |
| Activity name | `@activity.defn(name="snake_case")` | `name="send_whatsapp_message"` |
| Workflow class | `PascalCase + Workflow` | `HubaraSalesSessionWorkflow` |
| DTO frozen | `PascalCase` + suffix | `TransferDecision` |
| Composition factory | `get_<thing>` + `@lru_cache(maxsize=1)` | `get_manage_conversation_tag_tool` |

## PYTHONPATH

- `from src.platform...` resuelve desde `hubara_agency/` (uv workspace).
- `from src.plugins.<id>...` igual.
- NO usar `from hubara_agency.src...` desde código del repo. Solo algunos tests vía import absoluto.

## Cuando agregar / modificar

- **Plugin nuevo:** leer `.claude/skills/hubara-architecture-guide/examples/plugin-with-worker.md` (o `plugin-frontend-only.md`).
- **Tool LLM:** sección `04-backend-agents.md` + `references/temporal-patterns.md`.
- **Activity nueva:** sección `04-backend-agents.md` § retry policies.
- **Cambio en `src/platform/`:** sección `02-backend-platform.md` + spinal-files check.

## Gotcha local

- Tests NUNCA escriben al vault real. Fixture autouse `_isolate_vault_dir` en `tests/conftest.py` redirige a `tmp_path`. Si tu test parece tocar `./hubara_vault/`, mirá esa fixture.
- `wa_*/metadata.json` en `hubara_vault/` son seed data committeados — NO borrar.
