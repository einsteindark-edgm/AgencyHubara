# Tech refinement — 02 CatalogPort + LocalSnapshotCatalogClient

- **HU id**: catalog-02
- **Source**: discusión de diseño (cache-aside / read-replica local) + HU-01 como dependencia futura
- **Target agent**: `platform` (cross-agent infra) en `hubara_agency/`
- **Refiner**: exoclaw-tech-refiner
- **Date**: 2026-05-07

## 1. Scope

**Summary**: Definir el `CatalogPort` (Protocol) que tanto Sales (HU-04) como `catalog_sync` (HU-03) consumen, sus DTOs cerrados, y el `LocalSnapshotCatalogClient` — el adapter de **lectura desde filesystem** con cache mtime-aware que Sales usará para responder en microsegundos. **No incluye el escritor** (eso es HU-03) ni las tools (HU-04).

**Acceptance criteria**:
- Given un `snapshot.json` válido en `<snapshot_dir>/snapshot.json`, When llamo `client.search(q="vela", limit=10)`, Then recibo un `SearchResult` con `results: list[CatalogProductDTO]` matcheando substring (case-insensitive) en `title` y `handle`.
- Given un `<snapshot_dir>/by_handle/<handle>.json` válido, When llamo `client.get_by_handle("luz-serena")`, Then recibo un `CatalogProductDTO` exacto.
- Given un handle inexistente, When llamo `client.get_by_handle("inventado")`, Then se levanta `ProductNotFoundError(handle="inventado")`.
- Given que el `manifest.json` reporta `fetched_at` más viejo que `MAX_SNAPSHOT_AGE_MINUTES`, When llamo `search(...)`, Then el `SearchResult.stale` es `True`.
- Given que el `snapshot.json` cambia en disco (mtime nuevo), When llamo `search(...)` por segunda vez, Then la nueva data se sirve sin reiniciar el proceso.
- Given que el `snapshot.json` no existe, When llamo `search(...)`, Then se levanta `CatalogUnavailableError`.
- Given que `snapshot.json` está corrupto (JSON inválido), When llamo `search(...)`, Then se levanta `CatalogUnavailableError` con mensaje claro (NO retorna `[]` silenciosamente).
- Todos los DTOs son `@dataclass(frozen=True)` y JSON-serializables (preparados para R-JSON si en algún momento cruzan boundary).

**Out of scope**:
- HTTP / `HttpMedusaClient` (HU-01).
- Escritor del snapshot (HU-03 escribe; esta HU solo lee).
- Tools del agente Sales (HU-04).
- Wiring en `worker.py` de Sales (HU-04 / HU-05).
- Búsqueda full-text con scoring sofisticado (Meilisearch). Sustring lower-case alcanza para v1.
- Stock en vivo / precios calculados por región (futuro).

## 2. Workflow mode

**Decision**: N/A — esta HU NO crea workflow. Es una librería de adapter de filesystem que será **consumida desde tools** (HU-04) y **opcionalmente desde activities** del `catalog_sync` (HU-03 en su validación post-write).

## 3. Boundary DTOs (R-JSON)

**Ubicación**: `src/platform/catalog/dtos.py`. Multi-agent: cualquier DTO que 2+ agentes serialicen cruzando `workflow.execute_activity` vive en `platform/`. Por ahora ninguno cruza, pero los hago R-JSON-compliant desde día 1 por si HU-03 los retorna desde una activity.

| DTO | File | Fields | Notas |
|---|---|---|---|
| `CatalogPriceDTO` | `src/platform/catalog/dtos.py` | `amount: str` (Decimal serializado), `currency_code: str`, `min_quantity: int \| None`, `max_quantity: int \| None` | `frozen=True`. **`amount: str`** para R-JSON (Decimal no es JSON-nativo en stdlib). |
| `CatalogVariantDTO` | idem | `id: str`, `title: str`, `sku: str \| None`, `prices: list[CatalogPriceDTO]` | |
| `CatalogImageDTO` | idem | `url: str`, `rank: int` | |
| `CatalogProductDTO` | idem | `id: str`, `handle: str`, `title: str`, `description: str \| None`, `status: str`, `thumbnail: str \| None`, `variants: list[CatalogVariantDTO]`, `images: list[CatalogImageDTO]`, `tags: list[str]`, `categories: list[str]`, `metadata: dict[str, str] \| None` | `metadata` flatten a `dict[str, str]` (Medusa lo usa para shipping codes, etc.). |
| `CatalogManifestDTO` | idem | `version: str`, `fetched_at: str` (ISO 8601), `product_count: int`, `source_etag: str \| None` | |
| `SearchResult` | idem | `query: str`, `count: int`, `truncated: bool`, `stale: bool`, `manifest: CatalogManifestDTO`, `results: list[CatalogProductDTO]` | El **closed-list grounding envelope** que el LLM verá al ejecutar la tool. |

**Reused from `exoclaw_temporal.config`**: ninguno.

> Excepción al "no `Path`" del DTO: aquí no hay paths que cruzan workflow boundaries — todos los DTOs son `str` puros.

## 4. Activities

Ninguna en esta HU.

## 5. Tools

Ninguna en esta HU.

## 6. Use cases

**No use case needed**. La lógica del `LocalSnapshotCatalogClient` es ~30 LOC de lectura+filtrado. Si en HU-04 las tools requieren coordinación más grande (ej: rerank con Meilisearch + fallback a substring), eso vivirá en HU-04 dentro de `sales_whatsapp/use_cases/`, no aquí.

## 7. State adapters

| Adapter | File | Methods | Storage path |
|---|---|---|---|
| `LocalSnapshotCatalogClient` (implementa `CatalogPort`) | `src/platform/catalog/local_snapshot.py` | `search(q: str, limit: int = 10) -> SearchResult`, `get_by_handle(handle: str) -> CatalogProductDTO` | `<snapshot_dir>/snapshot.json`, `<snapshot_dir>/by_handle/<handle>.json`, `<snapshot_dir>/manifest.json` |

**`<snapshot_dir>` resolución**: env var `CATALOG_SNAPSHOT_DIR`, default `<repo>/hubara_agency/catalog_workspace/`. Resuelto en `src/platform/catalog/paths.py:get_snapshot_dir()` (single place que lee env, igual que `<agent>/config/env.py` pero a nivel `platform/` porque es cross-agent — sigue el patrón de `src/platform/config.py`).

**Reglas de tolerancia**:
- `snapshot.json` no existe → `CatalogUnavailableError("snapshot not found at <path>")`.
- `snapshot.json` JSON inválido → `CatalogUnavailableError("snapshot corrupt: <json error>")`.
- `manifest.json` no existe pero `snapshot.json` sí → log warning, asume `stale=True`, `manifest.fetched_at = "1970-01-01T00:00:00Z"`.
- `by_handle/<handle>.json` no existe pero el handle SÍ aparece en `snapshot.json` → fallback a leer del snapshot in-memory.
- `by_handle/<handle>.json` y el handle no aparecen en snapshot → `ProductNotFoundError(handle)`.

**Cache mtime-aware** (es la pieza importante):

```python
# pseudo (no es production code)
class LocalSnapshotCatalogClient:
    def __init__(self, snapshot_dir: Path, max_age_minutes: int = 30) -> None:
        self._dir = snapshot_dir
        self._max_age = max_age_minutes
        self._cached_snapshot: dict | None = None
        self._cached_mtime: float = 0.0
        self._cached_manifest: CatalogManifestDTO | None = None

    def _ensure_loaded(self) -> None:
        snap_path = self._dir / "snapshot.json"
        if not snap_path.exists():
            raise CatalogUnavailableError(...)
        mtime = snap_path.stat().st_mtime
        if self._cached_snapshot is None or mtime > self._cached_mtime:
            self._cached_snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
            self._cached_mtime = mtime
            self._cached_manifest = self._load_manifest()
```

`stat()` cuesta microsegundos; recargar un JSON de ~10MB cuesta milisegundos. Esto es lo que da la "actualización en tiempo real sin reiniciar" que pide el HU original. **Sin signals, sin watchdogs, sin restarts.**

Cuando HU-03 hace `os.replace(snapshot.json.tmp, snapshot.json)` (escritura atómica), el siguiente `search()` de Sales detecta el mtime nuevo y recarga. ✅

## 8. Prompts / workspace changes

Sin cambios. Los `TOOLS.md` updates van en HU-04.

## 9. Composition wiring

| Factory en `composition.py` | Returns | Consumed by |
|---|---|---|
| `src/platform/catalog/composition.py` :: `get_catalog_client()` (lru_cache(1)) | `CatalogPort` (devuelve un `LocalSnapshotCatalogClient` por default) | Tools de Sales (HU-04). Activities del `catalog_sync` para post-write validation (HU-03 opcional). |

> **Por qué `lru_cache(1)`**: el `LocalSnapshotCatalogClient` mantiene cache en memoria. Compartir UNA instancia por proceso es lo correcto. Cada activity rebuilds **su use case dependent** (R-STATELESS), pero la dependencia "cliente HTTP / cliente de cache" es un recurso de larga vida — mismo patrón que `get_temporal_client()` y el `HttpMedusaClient` de HU-01.

## 10. Worker registration

Sin cambios en esta HU. El worker de Sales no se toca hasta HU-04. El worker de `catalog_sync` se crea en HU-03.

## 11. Hard rules check

- **R-DET**: **N/A** — no hay workflow code.
- **R-JSON**: **applies — handled how**: todos los DTOs son `@dataclass(frozen=True)` con tipos primitivos (`str`, `int`, `bool`, `list`, `dict[str, str]`). `Decimal` se serializa como `str` para evitar la trampa de JSON serialization.
- **R-STATELESS**: **applies — handled how**: el `LocalSnapshotCatalogClient` tiene cache **a nivel de instancia**, no module-level. La instancia compartida vive en `composition.py:lru_cache(1)`, igual que `HttpMedusaClient`. **No** hay `_REGISTRY = ` ni `_CACHE = ` module-level. La cache mtime-aware es metadata de cache, no estado mutable de negocio (excepción documentada en `deha-architecture/anti-patterns.md`, idéntica a `_EXTENSIONS` en `tool_extensions.py`).
- **R-HEARTBEAT**: **N/A** — no hay activities. Las tools que llaman a `search()` no necesitan heartbeat (search es <100ms).
- **R-DIP**: **applies — handled how**: el adapter NO importa `temporalio.*`, NO importa `exoclaw.*`, NO importa de ningún agente. Solo stdlib (`pathlib`, `json`, `dataclasses`, `datetime`). Confirma con `grep -rEn "^from (temporalio|exoclaw|src\.(sales|remarketing|catalog_sync)_whatsapp)" src/platform/catalog/`.

## 12. Tests

| Test file | Type | Asserts |
|---|---|---|
| `tests/platform/catalog/test_dtos_serialization.py` | Unit | `CatalogProductDTO` → `dataclasses.asdict(...)` → `json.dumps(...)` roundtrip funciona. `Decimal` se preserva como str. |
| `tests/platform/catalog/test_local_snapshot_search.py` | Unit (`tmp_path`) | Snapshot con 3 productos. `search(q="luz")` matchea 2 (case-insensitive title/handle). `limit=1` aplica. `truncated=True` si el match supera limit. |
| `tests/platform/catalog/test_local_snapshot_get_by_handle.py` | Unit (`tmp_path`) | Handle existente con `by_handle/<h>.json` → DTO. Handle existente sin `by_handle/` (fallback al snapshot) → DTO. Handle inexistente → `ProductNotFoundError`. |
| `tests/platform/catalog/test_local_snapshot_mtime_reload.py` | Unit (`tmp_path`) | Llamada 1 → carga snapshot. Modificar snapshot en disco con mtime nuevo. Llamada 2 → recarga (verifica con un producto añadido). |
| `tests/platform/catalog/test_local_snapshot_stale.py` | Unit (`tmp_path`) | Manifest con `fetched_at` viejo (>30 min) → `SearchResult.stale=True`. Manifest reciente → `stale=False`. |
| `tests/platform/catalog/test_local_snapshot_failures.py` | Unit (`tmp_path`) | Sin snapshot → `CatalogUnavailableError`. JSON inválido → `CatalogUnavailableError`. Manifest ausente pero snapshot ok → log warning, `stale=True`. |
| `tests/platform/catalog/test_paths.py` | Unit (monkeypatch env) | `CATALOG_SNAPSHOT_DIR` env override aplica. Default cuando no está. `~` se expande. |
| `tests/platform/catalog/test_port_protocol.py` | Unit | `LocalSnapshotCatalogClient` satisface `CatalogPort` (isinstance via runtime_checkable Protocol O comprobación estructural con `mypy`/`ty`). |

Replay: N/A.

## 13. Risks / open questions

- **R1**: `CatalogPort` como `typing.Protocol` con `@runtime_checkable` decorator. Recomiendo **runtime_checkable** para poder hacer asserts en tests; el costo es mínimo. Otra alternativa: ABC. Voto Protocol porque DEHA prefiere structural typing.
- **R2**: `MAX_SNAPSHOT_AGE_MINUTES` default. Recomiendo **30 min** (snapshot agent en HU-03 corre cada 5 min; 30 min = 6 ciclos perdidos antes de marcar stale). Configurable via env `CATALOG_MAX_AGE_MINUTES`.
- **R3**: Búsqueda substring case-insensitive contra `title` y `handle`. ¿Incluir `description`? Recomiendo **NO** por ahora (matches falsos altos). Habilitable con flag `search_in_description=False` en la signature.
- **R4**: `metadata` como `dict[str, str]` aplana — Medusa permite valores anidados. **Acción**: HU-03 al construir el snapshot debe `json.dumps` cualquier valor no-string. Documentado en HU-03.
- **R5**: ¿Soportamos múltiples catálogos / tenants? **No por ahora**. Single-tenant. Si llega multi-tenant, `CatalogPort` recibirá un `tenant_id: str` extra y `<snapshot_dir>/<tenant>/`.
- **R6**: `from datetime import datetime; datetime.fromisoformat(manifest.fetched_at)` para chequear staleness. Esto es OK fuera de un workflow (workflows tienen R-DET). Tools no son workflows; `datetime.utcnow()` aquí es legal.
- **R7**: `by_handle/<handle>.json` por producto añade I/O por consulta exacta. Alternativa: cargar todo el snapshot al cache y matchear in-memory. **Recomendado**: implementar AMBAS rutas — `get_by_handle` primero intenta `by_handle/<h>.json` (fast path), si no existe cae al snapshot in-memory.
- **Defer to `temporal:temporal-developer`**: ninguno.
- **Defer to `claude-api`**: ninguno.

## 14. Implementation order (suggested)

1. `src/platform/catalog/__init__.py` con re-exports.
2. `src/platform/catalog/dtos.py` (datacalsses + serialization helpers + tests de roundtrip).
3. `src/platform/catalog/port.py` (Protocol + runtime_checkable + ProductNotFoundError + CatalogUnavailableError).
4. `src/platform/catalog/paths.py` (lectura de env + default).
5. `src/platform/catalog/local_snapshot.py` (lector con cache mtime).
6. `src/platform/catalog/composition.py` (factory `get_catalog_client()`).
7. Tests por capa (cada paso verifica con `pytest tests/platform/catalog/ -x`).

(Esta HU es independiente de HU-01. Pueden correr en paralelo.)

---

**Next step**:

```
/exoclaw-implementer .exoclaw/refinements/02-catalog-port-and-local-reader-tech.md
```
