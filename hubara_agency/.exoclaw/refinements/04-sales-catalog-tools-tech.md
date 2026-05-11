# Tech refinement — 04 SearchProductsTool + GetProductByHandleTool (Sales agent)

- **HU id**: catalog-04
- **Source**: discusión de diseño + dependencia de HU-02 (CatalogPort + DTOs)
- **Target agent**: `sales_whatsapp` (existente) en `hubara_agency/`
- **Refiner**: exoclaw-tech-refiner
- **Date**: 2026-05-07

## 1. Scope

**Summary**: Dos tools nuevas para que el agente de Sales consulte el catálogo desde el snapshot local (HU-02). `SearchProductsTool` para descubrir productos por substring, `GetProductByHandleTool` para detalle exacto. **Closed-list grounding**: las tools devuelven envelopes JSON cerrados; el system prompt obliga al LLM a sólo citar productos por `handle` retornado por la última `tool_result`. Esto reemplaza progresivamente la skill `hubara_catalog/SKILL.md` hardcodeada con datos vivos de Medusa.

**Acceptance criteria**:
- Given Sales con la tool `search_products` registrada, When el LLM la invoca con `q="vela lavanda"`, Then recibe un envelope JSON con `{query, count, truncated, stale, manifest, results: [{id, handle, title, price, currency, in_stock, ...}]}`.
- Given el LLM invoca `get_product_by_handle("luz-serena")` y el handle existe, Then recibe `{product: {...}, found: true, manifest: {...}}`.
- Given el LLM invoca `get_product_by_handle("inventado")`, Then recibe `{found: false, message: "El handle 'inventado' no existe en el catálogo. Usa search_products para descubrir productos disponibles."}`. **No fuzzy fallback.**
- Given `SearchResult.stale=True`, When la tool ejecuta, Then el envelope retornado lleva `stale: true` y el system prompt instructions le dicen al LLM "si stale=true, no cierres venta, dile al cliente que confirmas y se lo dejas saber".
- Given un fallo del `LocalSnapshotCatalogClient` (snapshot no existe), When la tool ejecuta, Then retorna `{error: "catalog_unavailable", message: "El catálogo no está disponible en este momento."}` (la tool nunca crashea — es input al LLM).
- `workspace/TOOLS.md` documenta cuándo usar cada tool y la regla de closed-list (LLM solo puede mencionar productos cuyo `handle` venga del último `tool_result`).
- `workspace/skills/hubara_catalog/SKILL.md` (hardcoded) **NO se borra en esta HU** — eso es HU-05 (rollout gradual). Aquí las nuevas tools coexisten con la skill vieja.

**Out of scope**:
- HTTP client (HU-01).
- Snapshot reader / DTOs (HU-02).
- Sync agent (HU-03).
- Borrar la skill `hubara_catalog/SKILL.md` (HU-05).
- Wiring K8s (HU-05).
- Stock en vivo (futuro).

## 2. Workflow mode

**Decision**: **extending existing `HubaraSalesSessionWorkflow`** (`src/sales_whatsapp/workflows/sales_session.py:29`). Las nuevas tools se añaden vía `register_tool_extension(...)` en `src/sales_whatsapp/worker.py:32-39` siguiendo el patrón existente — el workflow no cambia su signature.

**Justificación**: Tools puras. Cero workflow code. Cero DTO changes. Patrón idéntico a `TransferToSalesAgentTool` y `ManageConversationTagTool`.

**File**: `src/sales_whatsapp/workflows/sales_session.py` — sin cambios.

## 3. Boundary DTOs (R-JSON)

Sin DTOs nuevos. La tool retorna un `str` (JSON) que el workflow loop ya maneja vía `run_agent_turn` y `tools_used` parsing.

**Reused**:
- `SearchResult`, `CatalogProductDTO`, `CatalogManifestDTO` de `src/platform/catalog/dtos.py` (HU-02) — vienen del `CatalogPort` y se serializan a JSON dentro del envelope retornado por la tool.
- `WorkspaceConfig` de `exoclaw_temporal.config` — usado en el constructor de las tools (igual que `TransferToSalesAgentTool`).

## 4. Activities

Sin activities nuevas. Las tools se ejecutan dentro del `execute_tool` activity ya overrideado (`src/platform/temporal/activities.py:23-47`). El `apply_tool_extensions(...)` recoge las factories registradas en `worker.py` automáticamente.

## 5. Tools

| Tool class | File | LLM name | Parameters (JSON schema) | Side effects | Workspace TOOLS.md change |
|---|---|---|---|---|---|
| `SearchProductsTool` | `src/sales_whatsapp/tools/catalog.py` | `search_products` | `{type: "object", properties: {q: {type: "string", minLength: 1, maxLength: 100, description: "Búsqueda por substring en nombre/handle del producto."}, limit: {type: "integer", minimum: 1, maximum: 20, default: 10}}, required: ["q"]}` | Lee del `CatalogPort` (HU-02). NO escribe. | Bullet en TOOLS.md: cuándo usar (cliente pregunta por producto, ofrecer recomendaciones), cuándo NO (cliente ya escogió → usar `get_product_by_handle`). |
| `GetProductByHandleTool` | `src/sales_whatsapp/tools/catalog.py` | `get_product_by_handle` | `{type: "object", properties: {handle: {type: "string", minLength: 1, maxLength: 200, description: "Handle (slug) exacto del producto. Solo handles vistos en search_products."}}, required: ["handle"]}` | Lee del `CatalogPort`. NO escribe. | Bullet en TOOLS.md: usar para confirmar precio antes de cerrar, NO inventar handles. |

Signaturas concretas:

```python
async def execute_with_context(self, ctx: ToolContext, q: str, limit: int = 10) -> str: ...

async def execute_with_context(self, ctx: ToolContext, handle: str) -> str: ...
```

**Constructor**:

```python
class SearchProductsTool(ToolBase):
    def __init__(self, workspace: str | Path, catalog: CatalogPort) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

class GetProductByHandleTool(ToolBase):
    def __init__(self, workspace: str | Path, catalog: CatalogPort) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog
```

> **Inyección del `catalog`**: el `register_tool_extension(...)` en `worker.py` recibe una factory `(workspace) -> Tool`. Para inyectar el `CatalogPort` la factory **captura el cliente vía closure**:
>
> ```python
> from src.platform.catalog.composition import get_catalog_client
>
> _catalog = get_catalog_client()
> register_tool_extension(
>     "sales.search_products",
>     lambda ws: SearchProductsTool(workspace=str(ws), catalog=_catalog),
> )
> ```
>
> Esto preserva el contrato de la factory (`Callable[[Path], Tool]`) y mantiene a `_catalog` como singleton via `lru_cache(1)`.

**Envelope de retorno (CRITICAL — anti-alucinación)**:

`search_products`:
```json
{
  "query": "vela lavanda",
  "count": 3,
  "truncated": false,
  "stale": false,
  "manifest": {"version": "abc123", "fetched_at": "2026-05-07T12:34:56Z", "product_count": 142},
  "results": [
    {
      "id": "prod_01HXYZ",
      "handle": "vela-aroma-lavanda",
      "title": "Vela Aroma Lavanda",
      "price": "49000",
      "currency": "cop",
      "in_stock": true,
      "thumbnail_url": "https://r2.example.com/...jpg",
      "tags": ["Aroma: Lavanda"]
    }
  ]
}
```

`get_product_by_handle` (found):
```json
{
  "found": true,
  "stale": false,
  "manifest": {...},
  "product": {
    "id": "...", "handle": "...", "title": "...", "description": "...",
    "variants": [{"id": "...", "title": "...", "sku": null, "price": "49000", "currency": "cop"}],
    "images": [{"url": "...", "rank": 0}],
    "tags": [...], "categories": [...]
  }
}
```

`get_product_by_handle` (not found):
```json
{
  "found": false,
  "message": "El handle 'inventado' no existe en el catálogo. Usa search_products para descubrir productos disponibles."
}
```

Error envelopes (catalog unavailable):
```json
{"error": "catalog_unavailable", "message": "El catálogo no está disponible en este momento. Pide al cliente unos minutos y reintenta."}
```

## 6. Use cases

**No use case needed** — la lógica de cada tool es ~15 LOC (validar params → llamar `CatalogPort` → formatear envelope). Si en futuro las tools comparten lógica de formateo, extraer a un helper en `src/sales_whatsapp/tools/_envelopes.py`.

## 7. State adapters

Sin nuevos state adapters. Las tools NO escriben — solo leen vía `CatalogPort` (HU-02). **Importante**: a diferencia de `TransferToSalesAgentTool` y `ManageConversationTagTool` (que sí escriben `metadata.json`), estas tools son **read-only**. No hace falta `vault_dir`.

## 8. Prompts / workspace changes

- `src/sales_whatsapp/prompts.py` — sin cambios.
- `workspace/IDENTITY.md` — sin cambios.
- `workspace/SOUL.md` — sin cambios.
- `workspace/USER.md` — sin cambios.
- `workspace/TOOLS.md` — **AÑADIR** sección "Catálogo de productos" con:
  - Bullet `search_products` (cuándo / cuándo no / inputs / outputs).
  - Bullet `get_product_by_handle` (cuándo / inputs / outputs).
  - **Reglas anti-alucinación** (closed-list):
    - "Solo puedes mencionar productos cuyo `handle` aparezca en el último `tool_result` de `search_products` o `get_product_by_handle`."
    - "Cuando hables de un producto, cita siempre el `handle` y el `price` literal del envelope. Nunca inventes precios ni nombres."
    - "Si `stale: true`, NO cierres venta. Dile al cliente que confirmas disponibilidad y precio en breve."
    - "Si `error: catalog_unavailable`, pide disculpas y reintenta en un par de minutos. NO uses tu memoria del catálogo."
- `workspace/AGENTS.md` — sin cambios.
- `workspace/skills/hubara_catalog/SKILL.md` — **sin cambios en esta HU**. Coexiste con las tools nuevas. HU-05 lo deprecará una vez verificado que las tools cubren todo.

> **Frontmatter rule**: si por algún caso necesitamos crear una skill nueva (ej. `catalog_query/SKILL.md`) con `metadata: {"exoclaw": {"always": false, "tools": "search_products, get_product_by_handle"}}`, **DEBE** ser single-line inline JSON. No usar block scalar.

## 9. Composition wiring

Sin `composition.py` change en Sales — las tools usan el `get_catalog_client()` cross-agent de HU-02 vía import directo en `worker.py`.

| Factory consumed | Returns | Where injected |
|---|---|---|
| `src/platform/catalog/composition.py:get_catalog_client()` | `CatalogPort` (LocalSnapshotCatalogClient) | Capturado por closure en `register_tool_extension(...)` lambdas. |

## 10. Worker registration (`worker.py`)

`src/sales_whatsapp/worker.py` — añadir 2 nuevas registraciones siguiendo el patrón existente (`worker.py:32-39`):

```python
# Después de la registración de manage_conversation_tag (worker.py:36-39):
from src.platform.catalog.composition import get_catalog_client
from src.sales_whatsapp.tools.catalog import (
    GetProductByHandleTool,
    SearchProductsTool,
)

_catalog = get_catalog_client()

register_tool_extension(
    "sales.search_products",
    lambda workspace: SearchProductsTool(workspace=str(workspace), catalog=_catalog),
)
register_tool_extension(
    "sales.get_product_by_handle",
    lambda workspace: GetProductByHandleTool(workspace=str(workspace), catalog=_catalog),
)
```

- Add to `workflows=[...]`: nada nuevo (workflow no cambia).
- Add to `activities=[...]`: nada nuevo (la tool usa el `execute_tool` ya wireado).
- `register_tool_extension(...)`: 2 nuevas (above).

## 11. Hard rules check

- **R-DET**: **N/A** — tools no son workflows.
- **R-JSON**: **applies — handled how**: la tool retorna `str` (JSON envelope). El `tool_result` ya cruza boundary como string. Reusa DTOs JSON-safe de HU-02 vía `dataclasses.asdict(...)` para serializar.
- **R-STATELESS**: **applies — handled how**: las tools son instanciadas por el `apply_tool_extensions(...)` en cada `execute_tool` invocation. El `_catalog` capturado vía closure es un singleton por proceso (`lru_cache(1)` en HU-02 composition) — recurso compartido, no estado de negocio mutable.
- **R-HEARTBEAT**: **N/A** — `execute_tool` ya tiene `@with_heartbeat(every=10)` en `src/platform/temporal/activities.py:24`. Las tools `search`/`get_by_handle` son <100ms (lectura de filesystem in-memory).
- **R-DIP**: **applies — handled how**: las tools NO importan `temporalio.client` ni `temporalio.worker`. Importan solo `exoclaw.agent.tools` (ToolBase, ToolContext) y `src.platform.catalog.*` (Port + DTOs + errors). Confirma con `grep -rEn "^from temporalio\.(client|worker)" src/sales_whatsapp/tools/`.

## 12. Tests

| Test file | Type | Asserts |
|---|---|---|
| `tests/sales_whatsapp/tools/test_search_products_protocol.py` | Unit | `SearchProductsTool` cumple Protocol: `name="search_products"`, `description` no vacío, `parameters["properties"]` tiene `q` y `limit`. |
| `tests/sales_whatsapp/tools/test_search_products_envelope.py` | Unit (Fake `CatalogPort`) | Search retorna envelope con `query`, `count`, `truncated`, `stale`, `manifest`, `results`. Result fields: `id`, `handle`, `title`, `price`, `currency`, `in_stock`, `thumbnail_url`, `tags`. JSON parseable. |
| `tests/sales_whatsapp/tools/test_search_products_validation.py` | Unit | `q=""` → tool returns error envelope from `ToolBase.validate_params`. `limit=0` → idem. `limit=21` → idem. |
| `tests/sales_whatsapp/tools/test_search_products_stale.py` | Unit (Fake `CatalogPort` con `stale=True`) | Envelope lleva `stale: true`. |
| `tests/sales_whatsapp/tools/test_search_products_unavailable.py` | Unit (Fake que levanta `CatalogUnavailableError`) | Envelope `{error: "catalog_unavailable", ...}`. NO crashea. |
| `tests/sales_whatsapp/tools/test_get_product_found.py` | Unit | Handle existente → `{found: true, stale: false, manifest, product}`. |
| `tests/sales_whatsapp/tools/test_get_product_not_found.py` | Unit (Fake levanta `ProductNotFoundError`) | Handle inexistente → `{found: false, message: ...}`. NO crashea. NO fuzzy fallback. |
| `tests/sales_whatsapp/tools/test_get_product_validation.py` | Unit | `handle=""` → error envelope. `handle` con 200+ chars → error. |
| `tests/sales_whatsapp/test_workspace_system_prompt.py` | Workspace | (Reutiliza el patrón que ya debería existir.) Después de `TOOLS.md` update, asserts que `"search_products"` y `"get_product_by_handle"` aparecen en el system prompt compuesto. |

Replay: **N/A** en este PR. El workflow no cambia signature (las tools se inyectan via `tool_definitions_json` que ya viaja en `SessionInput.tool_definitions_json`). Sin embargo, si después del PR un replay test viejo con histories anteriores asume tools fijas, el `bootstrap_sales_session_activity` añadirá las nuevas — eso **no rompe replay** porque las tools no se serializan al history; solo viajan dentro del `tool_definitions_json` del input.

## 13. Risks / open questions

- **R1**: `price` en el envelope — ¿retornar como `string` (Decimal-as-str) o `int` (centavos / unidad menor)? **Recomendado**: **`string`**. Razones: (a) ya viene como string del DTO de HU-02, (b) preservar Decimal sin precision loss, (c) el LLM lee strings literales mejor (no se confunde con división). Ejemplo: `"price": "49000"`. El LLM ve "49000 cop" y formatea "49.000 COP" en el chat.
- **R2**: ¿Convertir COP a formato local "$49.000 COP" en la tool, o dejar al LLM? **Recomendado**: dejar al LLM. La tool retorna número raw + currency code; el LLM aplica formato según `IDENTITY.md` (Hubara Colombia usa "$X.XXX COP"). Razón: separar I/O de UX.
- **R3**: ¿Incluir `description` completa del producto? Medusa puede tener descripciones largas con mucho ruido. **Recomendado**: en `search_products` NO (solo title+tags); en `get_product_by_handle` SÍ (el cliente ya lo escogió, vale el costo de tokens). Configurable con flag.
- **R4**: ¿Qué pasa si el LLM ignora la regla closed-list y cita un producto inventado? **Mitigación opcional v2**: validador post-LLM que hace regex de "$NN.NNN" en el `final_content` y verifica contra el último `tool_result`. Si no matchea, re-prompt. **Out of scope HU-04**; documentar como follow-up.
- **R5**: La skill `hubara_catalog/SKILL.md` (hardcoded) sigue activa con `always: true` durante esta HU. **Esto es por diseño** — coexisten para que Sales pueda fallback al catálogo viejo si las tools fallan. HU-05 borra la skill cuando confirmemos cobertura.
- **R6**: Test `test_workspace_system_prompt.py` — verificar si ya existe en el repo o hay que crearlo. (`grep -r "test_workspace" hubara_agency/tests/`).
- **R7**: La factory pattern `_catalog = get_catalog_client()` a module-level en `worker.py` se ejecuta UNA vez al importar el worker. Eso está bien para el worker process pero hay que verificar que el `lru_cache` no se invalide entre tests con `monkeypatch`. **Acción**: tests usan `Fake CatalogPort` directo, no via composition.
- **Defer to `temporal:temporal-developer`**: ninguno.
- **Defer to `claude-api`**: ninguno.

## 14. Implementation order (suggested)

1. Crear `src/sales_whatsapp/tools/catalog.py` con `SearchProductsTool` + `GetProductByHandleTool`. Constructor + JSON schemas + `execute_with_context`.
2. Tests unitarios con Fake `CatalogPort` (cubrir todos los casos del §12).
3. Update `workspace/TOOLS.md` con las dos secciones nuevas + reglas closed-list.
4. Test de workspace system prompt (asserts tools mencionadas).
5. Update `src/sales_whatsapp/worker.py` con los `register_tool_extension(...)`.
6. Smoke manual: arrancar Sales worker y observar que el `tool_definitions_json` del primer turno incluya `search_products` y `get_product_by_handle`.

(Esta HU depende de HU-02. NO de HU-01 ni HU-03 — las tools leen del snapshot ya escrito; durante el desarrollo se puede crear un snapshot de prueba a mano.)

---

**Next step**:

```
/exoclaw-implementer .exoclaw/refinements/04-sales-catalog-tools-tech.md
```
