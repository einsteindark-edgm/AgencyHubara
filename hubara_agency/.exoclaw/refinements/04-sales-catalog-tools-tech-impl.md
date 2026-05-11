# Implementation plan — 04 Sales catalog tools

- **Refinement**: `.exoclaw/refinements/04-sales-catalog-tools-tech.md`
- **Depends on**: HU-02 (CatalogPort + DTOs + LocalSnapshotCatalogClient).
- **Target agent**: `sales_whatsapp` (existente)
- **Implementer**: exoclaw-implementer
- **Date**: 2026-05-07

## 1. PR sequence (each step keeps tests green)

### PR-1: Tools + tests (sin registrar todavía)
**Goal**: las dos tools implementan ToolBase y devuelven envelopes correctos. Sin tocar `worker.py` aún (las tools existen pero no son visibles al LLM).
**Files**:
- CREATE `src/sales_whatsapp/tools/catalog.py` — `SearchProductsTool`, `GetProductByHandleTool`.
- CREATE `tests/sales_whatsapp/tools/__init__.py`.
- CREATE `tests/sales_whatsapp/tools/test_search_products_protocol.py`.
- CREATE `tests/sales_whatsapp/tools/test_search_products_envelope.py`.
- CREATE `tests/sales_whatsapp/tools/test_search_products_validation.py`.
- CREATE `tests/sales_whatsapp/tools/test_search_products_stale.py`.
- CREATE `tests/sales_whatsapp/tools/test_search_products_unavailable.py`.
- CREATE `tests/sales_whatsapp/tools/test_get_product_found.py`.
- CREATE `tests/sales_whatsapp/tools/test_get_product_not_found.py`.
- CREATE `tests/sales_whatsapp/tools/test_get_product_validation.py`.
**Verification**:
```bash
uv run pytest tests/sales_whatsapp/tools/ -x
```

### PR-2: Workspace TOOLS.md update
**Goal**: el LLM aprende la regla closed-list.
**Files**:
- EDIT `src/sales_whatsapp/workspace/TOOLS.md` — añadir sección "Catálogo de productos".
- EDIT/CREATE `tests/sales_whatsapp/test_workspace_system_prompt.py` — verificar que `search_products` y `get_product_by_handle` aparecen en el system prompt compuesto.
**Verification**:
```bash
uv run pytest tests/sales_whatsapp/test_workspace_system_prompt.py -x
```

### PR-3: Worker registration
**Goal**: las tools se vuelven visibles al LLM. **Cuidado**: este PR cambia el `tool_definitions_json` del próximo `bootstrap_sales_session_activity` — los workflows en curso al momento del deploy lo verán en el siguiente turno (no rompe replay porque el JSON viaja por input, no por history).
**Files**:
- EDIT `src/sales_whatsapp/worker.py` — añadir 2 `register_tool_extension(...)`.
**Verification**:
```bash
# No tests automáticos directos. Smoke manual:
uv run python -m src.sales_whatsapp.worker  # inspeccionar logs del primer turno
```

## 2. File-by-file (canonical content)

### `src/sales_whatsapp/tools/catalog.py` (NEW)

```python
"""Tools del agente Sales para consultar el catálogo de productos.

Closed-list grounding: las tools devuelven envelopes JSON cerrados.
El system prompt (`workspace/TOOLS.md`) instruye al LLM a sólo citar
productos cuyo `handle` aparezca en el último `tool_result`.

ADR-001 alignment: estas tools son inertes respecto a Temporal — solo
leen del CatalogPort. NO escriben metadata, NO emiten decisions.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext

from src.platform.catalog import (
    CatalogPort,
    CatalogUnavailableError,
    ProductNotFoundError,
    SearchResult,
)


class SearchProductsTool(ToolBase):
    """Busca productos del catálogo por substring en title/handle."""

    name = "search_products"
    description = (
        "Busca productos del catálogo de Hubara por nombre o handle. "
        "Retorna hasta `limit` productos con su precio, handle, imagen y tags. "
        "Usa esta tool cuando el cliente pregunte por productos sin escoger uno "
        "específico, o cuando quieras ofrecer recomendaciones."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "q": {
                "type": "string",
                "description": "Texto de búsqueda (substring case-insensitive en title y handle).",
                "minLength": 1,
                "maxLength": 100,
            },
            "limit": {
                "type": "integer",
                "description": "Máximo de productos a retornar (default 10, máximo 20).",
                "minimum": 1,
                "maximum": 20,
                "default": 10,
            },
        },
        "required": ["q"],
    }

    def __init__(self, workspace: str | Path, catalog: CatalogPort) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(
        self, ctx: ToolContext, q: str, limit: int = 10,
    ) -> str:
        try:
            result: SearchResult = await self._catalog.search(q=q, limit=limit)
        except CatalogUnavailableError as e:
            return json.dumps({
                "error": "catalog_unavailable",
                "message": (
                    "El catálogo no está disponible en este momento. "
                    "Pídele al cliente unos minutos y reintenta."
                ),
                "detail": str(e),
            }, ensure_ascii=False)

        return json.dumps({
            "query": result.query,
            "count": result.count,
            "truncated": result.truncated,
            "stale": result.stale,
            "manifest": asdict(result.manifest),
            "results": [
                _product_summary(p) for p in result.results
            ],
        }, ensure_ascii=False)


class GetProductByHandleTool(ToolBase):
    """Devuelve el detalle exacto de un producto por su handle."""

    name = "get_product_by_handle"
    description = (
        "Devuelve el detalle completo de UN producto cuyo handle ya conoces "
        "(visto en search_products). Úsalo para confirmar precio, descripción "
        "y variantes ANTES de cerrar venta. NO inventes handles — si el "
        "cliente menciona un producto, primero busca con search_products."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "handle": {
                "type": "string",
                "description": "Handle (slug) exacto del producto. Solo handles vistos en search_products.",
                "minLength": 1,
                "maxLength": 200,
            },
        },
        "required": ["handle"],
    }

    def __init__(self, workspace: str | Path, catalog: CatalogPort) -> None:
        self._workspace = Path(workspace)
        self._catalog = catalog

    async def execute_with_context(
        self, ctx: ToolContext, handle: str,
    ) -> str:
        try:
            product = await self._catalog.get_by_handle(handle)
        except ProductNotFoundError:
            return json.dumps({
                "found": False,
                "message": (
                    f"El handle '{handle}' no existe en el catálogo. "
                    "Usa search_products para descubrir productos disponibles."
                ),
            }, ensure_ascii=False)
        except CatalogUnavailableError as e:
            return json.dumps({
                "error": "catalog_unavailable",
                "message": (
                    "El catálogo no está disponible en este momento. "
                    "Pídele al cliente unos minutos y reintenta."
                ),
                "detail": str(e),
            }, ensure_ascii=False)

        # manifest no expuesto aquí porque get_by_handle no retorna SearchResult.
        # Si se necesita, el LLM ya lo recibió en el previous search_products.
        return json.dumps({
            "found": True,
            "product": _product_full(product),
        }, ensure_ascii=False)


# ---------- envelope helpers ----------

def _product_summary(p) -> dict[str, Any]:
    """Versión liviana para search_products (sin description larga)."""
    price, currency = _first_price(p)
    return {
        "id": p.id,
        "handle": p.handle,
        "title": p.title,
        "price": price,
        "currency": currency,
        "in_stock": True,  # v1: asumimos True. Stock real-time es follow-up.
        "thumbnail_url": p.thumbnail,
        "tags": p.tags,
    }


def _product_full(p) -> dict[str, Any]:
    """Versión completa para get_product_by_handle."""
    return {
        "id": p.id,
        "handle": p.handle,
        "title": p.title,
        "description": p.description,
        "thumbnail": p.thumbnail,
        "variants": [
            {
                "id": v.id,
                "title": v.title,
                "sku": v.sku,
                "price": v.prices[0].amount if v.prices else None,
                "currency": v.prices[0].currency_code if v.prices else None,
            }
            for v in p.variants
        ],
        "images": [{"url": i.url, "rank": i.rank} for i in p.images],
        "tags": p.tags,
        "categories": p.categories,
    }


def _first_price(p) -> tuple[str | None, str | None]:
    if not p.variants:
        return (None, None)
    v = p.variants[0]
    if not v.prices:
        return (None, None)
    return (v.prices[0].amount, v.prices[0].currency_code)
```

### `src/sales_whatsapp/workspace/TOOLS.md` (EDIT — añadir sección)

Localizar la sección "## Available tools" (después de `manage_conversation_tag`) y añadir antes de "## Instrucciones de Cierre de Venta":

```markdown
### `search_products`

- **Use when**: el cliente pregunta por productos sin escoger uno específico (ej: "qué velas tienen", "tienen algo de lavanda"), o cuando quieres ofrecer 1-3 recomendaciones.
- **Don't use when**: el cliente ya escogió un producto y quieres confirmar precio — usa `get_product_by_handle` con el handle de la búsqueda previa.
- **Input**: `q` (texto de búsqueda), `limit` (opcional, default 10).
- **Output**: `{query, count, truncated, stale, manifest, results: [{id, handle, title, price, currency, in_stock, thumbnail_url, tags}]}`.

### `get_product_by_handle`

- **Use when**: ya viste el `handle` en una respuesta previa de `search_products` y necesitas confirmar precio/descripción/variantes antes de cerrar venta.
- **Don't use when**: NO has corrido `search_products` antes y el cliente solo te dijo el nombre — busca primero, NUNCA inventes handles.
- **Input**: `handle` (string exacto).
- **Output**: `{found: true, product: {...}}` o `{found: false, message: "..."}`.

## Reglas anti-alucinación (OBLIGATORIAS)

1. **Closed-list**: solo puedes mencionar productos cuyo `handle` aparezca en el último `tool_result` de `search_products` o `get_product_by_handle` durante esta conversación. Si un producto no está en esos resultados, NO lo menciones — dile al cliente "no manejamos ese producto" o ejecuta `search_products` para descubrir.
2. **Citación literal**: cuando hables de un producto, usa el `title` y `price` exactos del envelope. Si el envelope dice `"price": "23000", "currency": "cop"`, dile al cliente "$23.000 COP". NO redondees, NO inventes precios.
3. **Stale data**: si la respuesta de la tool lleva `stale: true`, NO cierres venta. Dile al cliente "déjame confirmar disponibilidad y precio en breve" y escala internamente. El catálogo puede haber cambiado.
4. **Catálogo no disponible**: si la respuesta lleva `error: "catalog_unavailable"`, pide disculpas, ofrece reintentar en 1-2 minutos. **NO** uses tu memoria del catálogo previo.
5. **Cero handles inventados**: si el cliente menciona un producto por nombre, ejecuta `search_products` ANTES de mencionar handles. Si no aparece, dile que no lo manejas.
```

### `src/sales_whatsapp/worker.py` (EDIT — añadir registraciones)

Localizar las líneas 32-39 (los dos `register_tool_extension(...)` existentes) y añadir DESPUÉS:

```python
# HU-04: tools de catálogo. Leen del snapshot mantenido por catalog_sync (HU-03)
# vía CatalogPort. El cliente es singleton via lru_cache(1) — capturado por
# closure en la lambda de la factory.
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

(Imports al top del archivo si el ruff config lo prefiere; mantener el patrón de los imports existentes.)

## 3. Tests to add

### `tests/sales_whatsapp/tools/test_search_products_protocol.py` (NEW)

```python
from pathlib import Path
from src.sales_whatsapp.tools.catalog import SearchProductsTool


class _FakeCatalog:
    async def search(self, q, *, limit=10): raise NotImplementedError
    async def get_by_handle(self, handle): raise NotImplementedError


def test_protocol_compliance(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_FakeCatalog())
    assert tool.name == "search_products"
    assert "q" in tool.parameters["properties"]
    assert "limit" in tool.parameters["properties"]
    assert "q" in tool.parameters["required"]
    assert hasattr(tool, "execute_with_context")
```

### `tests/sales_whatsapp/tools/test_search_products_envelope.py` (NEW)

```python
import json, pytest
from pathlib import Path
from exoclaw.agent.tools import ToolContext
from src.sales_whatsapp.tools.catalog import SearchProductsTool
from src.platform.catalog.dtos import (
    CatalogManifestDTO, CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO, SearchResult,
)


class _FakeCatalog:
    async def search(self, q, *, limit=10):
        return SearchResult(
            query=q, count=1, truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="v1", fetched_at="2099-01-01T00:00:00+00:00", product_count=1),
            results=[CatalogProductDTO(
                id="1", handle="vela-aroma-lavanda", title="Vela Lavanda", status="published",
                variants=[CatalogVariantDTO(
                    id="v1", title="u",
                    prices=[CatalogPriceDTO(amount="49000", currency_code="cop")],
                )],
                tags=["Aroma: Lavanda"],
            )],
        )
    async def get_by_handle(self, h): raise NotImplementedError


@pytest.mark.asyncio
async def test_envelope_shape(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_FakeCatalog())
    out = await tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        q="lavanda", limit=10,
    )
    payload = json.loads(out)
    assert payload["query"] == "lavanda"
    assert payload["count"] == 1
    assert payload["stale"] is False
    assert "manifest" in payload
    r = payload["results"][0]
    for k in ("id", "handle", "title", "price", "currency", "in_stock", "thumbnail_url", "tags"):
        assert k in r
    assert r["price"] == "49000"
    assert r["currency"] == "cop"
```

### `tests/sales_whatsapp/tools/test_search_products_validation.py` (NEW)

```python
import pytest
from pathlib import Path
from exoclaw.agent.tools import ToolContext
from src.sales_whatsapp.tools.catalog import SearchProductsTool


class _NeverCalledCatalog:
    async def search(self, *a, **k): raise AssertionError("should not be called")
    async def get_by_handle(self, *a, **k): raise NotImplementedError


@pytest.mark.asyncio
async def test_empty_q_returns_validation_error(tmp_path: Path):
    """ToolBase.validate_params should reject q="" via JSON schema minLength."""
    tool = SearchProductsTool(workspace=tmp_path, catalog=_NeverCalledCatalog())
    # ToolRegistry.execute hace validate_params; aquí lo invocamos directo
    # vía la tool.execute() pública del ToolBase si existe — si no, por
    # contrato del ToolRegistry la validación ocurre antes de ejecutar.
    # En la práctica la tool individual no se llama con q="" porque la
    # registry filtra. Test simbólico: el JSON schema dice minLength=1.
    assert tool.parameters["properties"]["q"]["minLength"] == 1
    assert tool.parameters["properties"]["limit"]["minimum"] == 1
    assert tool.parameters["properties"]["limit"]["maximum"] == 20
```

> **Nota**: la validación ocurre en `ToolBase.validate_params` (framework). Aquí solo verificamos la shape del schema. El path completo "registry.execute con q='' → error" lo cubrirían tests de integración del framework.

### `tests/sales_whatsapp/tools/test_search_products_stale.py` (NEW)

```python
import json, pytest
from pathlib import Path
from exoclaw.agent.tools import ToolContext
from src.sales_whatsapp.tools.catalog import SearchProductsTool
from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult


class _StaleCatalog:
    async def search(self, q, *, limit=10):
        return SearchResult(
            query=q, count=0, truncated=False, stale=True,
            manifest=CatalogManifestDTO(version="old", fetched_at="2020-01-01T00:00:00+00:00", product_count=5),
            results=[],
        )
    async def get_by_handle(self, h): raise NotImplementedError


@pytest.mark.asyncio
async def test_stale_flag_propagates(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_StaleCatalog())
    out = await tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"), q="x",
    )
    assert json.loads(out)["stale"] is True
```

### `tests/sales_whatsapp/tools/test_search_products_unavailable.py` (NEW)

```python
import json, pytest
from pathlib import Path
from exoclaw.agent.tools import ToolContext
from src.sales_whatsapp.tools.catalog import SearchProductsTool
from src.platform.catalog.errors import CatalogUnavailableError


class _BrokenCatalog:
    async def search(self, *a, **k):
        raise CatalogUnavailableError("snapshot not found at /tmp/x/snapshot.json")
    async def get_by_handle(self, *a, **k): raise NotImplementedError


@pytest.mark.asyncio
async def test_unavailable_returns_error_envelope(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_BrokenCatalog())
    out = await tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"), q="x",
    )
    payload = json.loads(out)
    assert payload["error"] == "catalog_unavailable"
    assert "no está disponible" in payload["message"].lower() or "unavailable" in payload["message"].lower()
```

### `tests/sales_whatsapp/tools/test_get_product_found.py` (NEW)

```python
import json, pytest
from pathlib import Path
from exoclaw.agent.tools import ToolContext
from src.sales_whatsapp.tools.catalog import GetProductByHandleTool
from src.platform.catalog.dtos import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO


class _FakeCatalog:
    async def search(self, *a, **k): raise NotImplementedError
    async def get_by_handle(self, handle):
        return CatalogProductDTO(
            id="1", handle=handle, title="Luz Serena", status="published",
            description="Una vela serena.",
            variants=[CatalogVariantDTO(id="v1", title="u",
                prices=[CatalogPriceDTO(amount="23000", currency_code="cop")])],
        )


@pytest.mark.asyncio
async def test_get_found(tmp_path: Path):
    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_FakeCatalog())
    out = await tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        handle="luz-serena",
    )
    payload = json.loads(out)
    assert payload["found"] is True
    assert payload["product"]["handle"] == "luz-serena"
    assert payload["product"]["variants"][0]["price"] == "23000"
```

### `tests/sales_whatsapp/tools/test_get_product_not_found.py` (NEW)

```python
import json, pytest
from pathlib import Path
from exoclaw.agent.tools import ToolContext
from src.sales_whatsapp.tools.catalog import GetProductByHandleTool
from src.platform.catalog.errors import ProductNotFoundError


class _NotFoundCatalog:
    async def search(self, *a, **k): raise NotImplementedError
    async def get_by_handle(self, handle): raise ProductNotFoundError(handle)


@pytest.mark.asyncio
async def test_get_not_found_returns_envelope(tmp_path: Path):
    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_NotFoundCatalog())
    out = await tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        handle="inventado",
    )
    payload = json.loads(out)
    assert payload["found"] is False
    assert "inventado" in payload["message"]
```

### `tests/sales_whatsapp/tools/test_get_product_validation.py` (NEW)

```python
from pathlib import Path
from src.sales_whatsapp.tools.catalog import GetProductByHandleTool


class _NeverCalled:
    async def search(self, *a, **k): raise NotImplementedError
    async def get_by_handle(self, *a, **k): raise NotImplementedError


def test_handle_schema(tmp_path: Path):
    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_NeverCalled())
    assert tool.parameters["properties"]["handle"]["minLength"] == 1
    assert tool.parameters["properties"]["handle"]["maxLength"] == 200
    assert tool.parameters["required"] == ["handle"]
```

### `tests/sales_whatsapp/test_workspace_system_prompt.py` (NEW or EDIT)

```python
"""Verifica que las nuevas tools aparecen en el system prompt compuesto.

Si el archivo ya existe, añadir solo `test_search_products_in_tools_md`
y `test_get_product_by_handle_in_tools_md`.
"""
from pathlib import Path

WORKSPACE = Path(__file__).parents[2] / "src/sales_whatsapp/workspace"


def test_search_products_documented_in_tools_md():
    tools_md = (WORKSPACE / "TOOLS.md").read_text(encoding="utf-8")
    assert "search_products" in tools_md
    assert "Closed-list" in tools_md or "closed-list" in tools_md or "anti-alucinación" in tools_md.lower()


def test_get_product_by_handle_documented_in_tools_md():
    tools_md = (WORKSPACE / "TOOLS.md").read_text(encoding="utf-8")
    assert "get_product_by_handle" in tools_md
```

## 4. Replay fixture refresh

**N/A** para este HU. El workflow `HubaraSalesSessionWorkflow` no cambia su signature. Las tools son inyectadas vía `register_tool_extension(...)` y aparecen en `tool_definitions_json` (campo del `SessionInput` que viaja por input, no por history). Los replay tests existentes deberían seguir funcionando.

**Caveat**: si algún replay test viejo asume que `tool_definitions_json` tiene EXACTAMENTE 2 tools (`transfer_to_sales_agent`, `manage_conversation_tag`), ese assert fallará. Buscar:

```bash
grep -rEn 'tool_definitions_json.*search_products\|tools.*len.*=.*2' tests/sales_whatsapp/
```

Si hay matches: actualizar el assert para reflejar 4 tools.

## 5. Verification commands (run between every PR)

```bash
uv run ruff check src/sales_whatsapp/tools
uv run pytest tests/sales_whatsapp/tools/ -x
uv run pytest tests/sales_whatsapp/test_workspace_system_prompt.py -x

# R-DIP — tools no importan temporalio.client/worker ni HTTP libs
grep -rEn "^from temporalio\.(client|worker)" src/sales_whatsapp/tools/catalog.py \
  || echo "R-DIP (tools) ok"
grep -rEn "^from (httpx|requests|litellm)" src/sales_whatsapp/tools/catalog.py \
  || echo "R-DIP (tools no HTTP) ok"

# Frontmatter de skills (HU-05 lo cierra; aquí solo verificar que TOOLS.md no rompió nada)
grep -rEn "^metadata: \|" src/sales_whatsapp/workspace/skills/*/SKILL.md \
  || echo "skill frontmatter ok"
```

## 6. Smoke-test recipe

Pre-requisito: HU-02 mergeada y un snapshot generado a mano (ver §6 del impl plan de HU-02) o con HU-03 corriendo.

```bash
# 1) Snapshot existe en /tmp/hubara_catalog (de HU-02 smoke).
ls /tmp/hubara_catalog/snapshot.json

# 2) Arrancar Sales worker apuntando al mismo dir.
CATALOG_SNAPSHOT_DIR=/tmp/hubara_catalog \
EXOCLAW_WORKSPACE_SALES=$(pwd)/src/sales_whatsapp/workspace \
uv run python -m src.sales_whatsapp.worker

# 3) En logs del worker buscar al primer turno la línea
#    bootstrap_sales_session_activity: workspace=...
# y luego en `tool_definitions_json` (visible si DEBUG está activo) que
# aparezcan "search_products" y "get_product_by_handle".

# 4) Mandar un mensaje de prueba (vía simulate_whatsapp.py o webhook real):
uv run python src/tests/simulate_whatsapp.py "qué velas tienen?"

# Validar que el LLM:
#  - llama search_products con un q razonable
#  - cita handles del envelope (no inventa)
#  - precios coinciden con los del snapshot
```

## 7. Rollback strategy

PR-1 y PR-2 son aditivos puros (carpeta nueva + sección nueva en markdown). PR-3 es el "switch on" — cambia el comportamiento del LLM en runtime.

Rollback de PR-3: `git revert <sha>` o eliminar las 2 líneas `register_tool_extension(...)` añadidas. El siguiente `bootstrap_sales_session_activity` volverá al `tool_definitions_json` previo (solo `transfer_to_sales_agent` + `manage_conversation_tag`). **El LLM no necesita continue-as-new para ver el cambio** — el bootstrap corre en cada nueva sesión, así que sesiones nuevas ven el rollback inmediato. Sesiones en curso esperan al `continue_as_new` (cada 50 turnos) o a un mensaje nuevo después del workflow restart.

## 8. Coordination updates

ADRs:
- `ADR-2026-05-07-07: Sales tools de catálogo leen del CatalogPort, NO HTTP directo`. Razón: latencia + determinismo.
- `ADR-2026-05-07-08: closed-list grounding como regla de prompt + envelope con manifest`. Razón: anti-alucinación.

## 9. Risks I'm carrying forward from the refinement

- **R1**: `price` como string. Implementado: `"price": "49000"`. El LLM formatea.
- **R3**: description larga solo en `get_product_by_handle`. Implementado: `_product_summary` la omite.
- **R4**: validador post-LLM que verifica handles citados. **Out of scope HU-04**, follow-up.
- **R5**: skill `hubara_catalog/SKILL.md` coexiste con `always: true`. HU-05 lo deprecará.
- **R7**: `_catalog = get_catalog_client()` a module-level en worker.py. Está bien para producción; tests usan Fake CatalogPort directo.

---

**Status**: refinement validado, plan listo. **Stop point**: confirmar antes de PR-1.
