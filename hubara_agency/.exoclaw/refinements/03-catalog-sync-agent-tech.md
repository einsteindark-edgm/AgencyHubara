# Tech refinement — 03 catalog_sync agent (DEHA worker que descarga el catálogo de Medusa)

- **HU id**: catalog-03
- **Source**: discusión de diseño + dependencia de HU-01 (HttpMedusaClient) + HU-02 (CatalogPort/dtos)
- **Target agent**: `catalog_sync` (NUEVO agente DEHA hermano de `sales_whatsapp` y `remarketing_whatsapp`) en `hubara_agency/`
- **Refiner**: exoclaw-tech-refiner
- **Date**: 2026-05-07

## 1. Scope

**Summary**: Nuevo agente DEHA programático (sin LLM) cuyo único trabajo es **descargar periódicamente el catálogo desde Medusa Admin API y materializarlo como snapshot atómico en filesystem** para que el agente Sales (HU-04) lea sin ir a la red. Activado por una **Temporal Schedule** (cron-like nativo de Temporal). Sin tools, sin workspace, sin turnos de LLM.

**Acceptance criteria**:
- Given el worker `catalog_sync` corriendo y la Schedule activada cada 5 min, When pasa el intervalo, Then se ejecuta `CatalogSyncWorkflow.run(...)` que escribe `<snapshot_dir>/snapshot.json`, `<snapshot_dir>/by_handle/<handle>.json` por producto, y `<snapshot_dir>/manifest.json` con `fetched_at` actualizado.
- Given un sync exitoso, When `Sales` invoca `LocalSnapshotCatalogClient.search(...)` después, Then ve la nueva data por mtime (HU-02).
- Given un fallo transitorio en Medusa (timeout, 500), When la activity reintenta, Then después de 3 intentos exitosos completa; si los 3 fallan, el workflow falla y el manifest NO se actualiza (el snapshot anterior sigue válido).
- Given la activity de pull tarda >10s (catálogo grande), When ejecuta, Then envía heartbeats cada 10s vía `@with_heartbeat`.
- Given la escritura del snapshot, When ocurre, Then es **atómica**: escribe a `snapshot.json.tmp`, `os.replace(...)`. Sales nunca ve un archivo a medio escribir.
- Given el snapshot escrito, When inspecciono `manifest.json`, Then contiene `version` (uuid), `fetched_at` (ISO), `product_count` (int), `source_etag` (opt).
- Given que la Schedule corre y el último sync fue hace <30 min, When el manifest se inspecciona desde Sales, Then `stale=False` en `SearchResult`.

**Out of scope**:
- HTTP client (HU-01).
- Lectura del snapshot (HU-02).
- Tools del agente Sales (HU-04).
- Activación de la Schedule en producción / K8s manifests (HU-05).
- LLM, workspace, IDENTITY/SOUL/USER (este agente es programático puro).
- Stock en vivo / precios calculados (futuro).

## 2. Workflow mode

**Decision**: **turn_based** — `CatalogSyncWorkflow` con un único `@workflow.run`. Cada disparo de la Schedule es un workflow ID nuevo (`catalog-sync-<run_id>`). No hay state persistente entre runs porque el snapshot ES el state.

**Justificación**: La sincronización es one-shot ("pull → write → done"). No hay signals, queries ni continue-as-new. session_based sería sobre-ingeniería.

**File**: `src/catalog_sync/workflows/sync.py` (nuevo).

> **Nota Temporal**: la Schedule (cron de Temporal) la creamos como recurso del cluster en HU-05 vía CLI o API; el workflow definido aquí es el target de la Schedule. La Schedule arranca un nuevo workflow en cada tick. **Defer al `temporal:temporal-developer` skill** para los detalles exactos del Schedule API si surgen dudas en HU-05; aquí solo definimos el workflow.

## 3. Boundary DTOs (R-JSON)

**Ubicación**: `src/catalog_sync/contracts.py` (agent-specific — solo este agente los usa).

| DTO | File | Fields | Notes |
|---|---|---|---|
| `CatalogSyncInput` | `src/catalog_sync/contracts.py` | `tenant_id: str = "default"`, `force_full_refresh: bool = True` (v1: siempre full; en futuro podría hacer delta) | Input del workflow. |
| `PullCatalogResult` | idem | `products_json: str` (JSON-string con la lista de `CatalogProductDTO` serializados — sortea el problema de transferir listas grandes con tipos complejos por boundary), `count: int`, `source_etag: str \| None`, `fetched_at: str` (ISO) | Output de la activity de pull. **`products_json: str`** es el truco JSON-string documentado para listas grandes (gotcha #6 del DEHA arch — mismo patrón que `tool_definitions_json`). |
| `WriteSnapshotInput` | idem | `products_json: str`, `count: int`, `source_etag: str \| None`, `fetched_at: str`, `snapshot_dir: str` | Input de la activity de escritura. `snapshot_dir` viaja como `str` (R-JSON). |
| `WriteSnapshotResult` | idem | `version: str`, `bytes_written: int`, `files_written: int` | Output de write activity. Útil para logging y métricas. |

Todos `@dataclass(frozen=True)`. Sin métodos. Sin `Path`, sin `datetime`, sin Pydantic (R-JSON).

**Reused from `exoclaw_temporal.config`**: ninguno (este agente no usa LLM, así que no necesita `LLMConfig`/`SessionInput`/`TurnInput`).

## 4. Activities

| Activity | File | Input → Output | Retry preset | Heartbeat | Use case invoked | Notes |
|---|---|---|---|---|---|---|
| `pull_medusa_catalog_activity` | `src/catalog_sync/activities/pull.py` | `CatalogSyncInput` → `PullCatalogResult` | `_TOOL_OPTIONS` (30s heartbeat, 10min total, 2 attempts) — el preset adecuado para I/O larga, ya disponible en `src/platform/temporal/retry_policies.py` | **Sí** — `@with_heartbeat(every=10)` (puede tardar >10s con catálogo de 1000+ productos paginado) | `PullCatalogUseCase.execute()` (ver §6) | Llama `HttpMedusaClient.iter_products(...)` (HU-01) y mapea cada `MedusaProduct` → `CatalogProductDTO` (HU-02). Devuelve `products_json: str` (json.dumps de la lista de DTOs). |
| `write_snapshot_activity` | `src/catalog_sync/activities/write.py` | `WriteSnapshotInput` → `WriteSnapshotResult` | `_CONV_OPTIONS` (5 attempts, 2 min) — escritura local rápida | **No** (escritura atómica de filesystem es <1s incluso con catálogos grandes) | `WriteSnapshotUseCase.execute()` (ver §6) | Escribe atómicamente: tmp → rename. Genera `version=uuid4().hex`, escribe `manifest.json`, `snapshot.json` y `by_handle/<h>.json` por producto. |

**R-STATELESS check**: ambas activities llaman `composition.py` factories en cada invocación. `pull_medusa_catalog_activity` usa `get_medusa_client()` (que sí es lru_cache(1) — esto es el patrón "un recurso de larga vida compartido" igual que `get_temporal_client`, no estado de negocio). `write_snapshot_activity` usa `get_snapshot_dir()` (función pura).

**No `execute_tool` override** — este agente no tiene tools.

## 5. Tools

Ninguna. Este es un agente programático sin LLM.

## 6. Use cases

Sí necesita 2 use cases — la lógica de mapeo Medusa→DTO y la escritura atómica son >15 LOC y vale la pena testearlas sin Temporal.

| Use case | File | Constructor deps | `execute(...)` shape | Why it earns its existence |
|---|---|---|---|---|
| `PullCatalogUseCase` | `src/catalog_sync/use_cases/pull_catalog.py` | `medusa_service: MedusaProductService` (HU-01) | `async def execute(input: CatalogSyncInput) -> PullCatalogResult` | Coordinación: paginar Medusa, mapear cada producto a `CatalogProductDTO`, capturar `etag`, formatear `fetched_at`. ~40 LOC. |
| `WriteSnapshotUseCase` | `src/catalog_sync/use_cases/write_snapshot.py` | `snapshot_dir: Path` (DI) | `async def execute(input: WriteSnapshotInput) -> WriteSnapshotResult` | Coordinación: escritura atómica, `by_handle/` directory, manifest.json, contadores de bytes. ~50 LOC. Tiene reglas de invariantes (manifest.json se escribe DESPUÉS de snapshot.json) que ameritan tests aislados. |

## 7. State adapters

| Adapter | File | Methods | Storage path |
|---|---|---|---|
| **(Reusa)** `LocalSnapshotCatalogClient` (HU-02) | — | — | — | (Lo importa en post-write validation opcional dentro de `WriteSnapshotUseCase` para sanity-check.) |

No hay nuevos adapters de state — la "persistencia" es escritura directa de filesystem que se quedó dentro del use case porque (a) hay 1 solo escritor en el repo y (b) no se reusa.

## 8. Prompts / workspace changes

Este agente **no tiene workspace** (no hay LLM). Específicamente:

- `src/catalog_sync/prompts.py` — **NO existe** (no se crea).
- `src/catalog_sync/workspace/` — **NO existe** (no hay `IDENTITY.md` etc.).
- `src/catalog_sync/config/env.py` — **NO existe** (no hay workspace path; el `snapshot_dir` lo resuelve `src/platform/catalog/paths.py` de HU-02).

**Justificación**: DEHA permite agentes "honest" (LLM-driven) y agentes "durable" (programáticos puros). Este es el segundo: un workflow puro de I/O. La estructura mínima del agente queda:

```
src/catalog_sync/
├── __init__.py
├── contracts.py
├── composition.py
├── worker.py
├── workflows/
│   ├── __init__.py
│   └── sync.py
├── activities/
│   ├── __init__.py
│   ├── pull.py
│   └── write.py
└── use_cases/
    ├── __init__.py
    ├── pull_catalog.py
    └── write_snapshot.py
```

Sin `tools/`, sin `state.py`, sin `prompts.py`, sin `workspace/`. Es legítimo dentro de DEHA — la lean layout no obliga a llenar carpetas que no se usan.

## 9. Composition wiring

| Factory en `composition.py` | Returns | Consumed by |
|---|---|---|
| `src/catalog_sync/composition.py` :: `get_pull_catalog_use_case()` (lru_cache(1)) | `PullCatalogUseCase` | `pull_medusa_catalog_activity` |
| `src/catalog_sync/composition.py` :: `get_write_snapshot_use_case()` (lru_cache(1)) | `WriteSnapshotUseCase` | `write_snapshot_activity` |
| Reuses `src/platform/medusa/composition.py:get_medusa_product_service()` (HU-01) | — | Inyectado en `PullCatalogUseCase` constructor. |
| Reuses `src/platform/catalog/paths.py:get_snapshot_dir()` (HU-02) | — | Inyectado en `WriteSnapshotUseCase` constructor. |

## 10. Worker registration (`worker.py`)

**Nuevo worker** `src/catalog_sync/worker.py`:

- Nueva constante `CATALOG_SYNC_QUEUE = "queue-catalog-sync"` añadida a `src/platform/constants.py`.
- `Worker(client, task_queue=CATALOG_SYNC_QUEUE, workflows=[CatalogSyncWorkflow], activities=[pull_medusa_catalog_activity, write_snapshot_activity])`.
- **NO `register_tool_extension(...)` calls** (este agente no usa tools).
- **NO `build_prompt`/`llm_chat`/`execute_tool`/`record_turn`** stock activities (este agente no es conversacional).

Reusa `get_temporal_client()` de `src/platform/temporal/client.py`.

## 11. Hard rules check

- **R-DET**: **applies — handled how**: el `CatalogSyncWorkflow.run` solo hace `await workflow.execute_activity(...)` 2 veces (pull → write) y un `workflow.now()` opcional para el `started_at` log. Cero `time.time()`, cero `datetime.now()`, cero `httpx.*`, cero `os.environ`. Verificable con grep en §5 de impl plan.
- **R-JSON**: **applies — handled how**: los 4 DTOs son `@dataclass(frozen=True)` con tipos primitivos. La lista de productos cruza como `products_json: str` (JSON-string trick) — el activity hace `json.dumps([asdict(p) for p in products])` de salida y el siguiente hace `json.loads(...)` de entrada.
- **R-STATELESS**: **applies — handled how**: ambas activities llaman factories de `composition.py`. No hay `_X = ` mutable module-level. El `MedusaClient` cachéado es `lru_cache(1)` — recurso compartido, no estado de negocio.
- **R-HEARTBEAT**: **applies — handled how**: `pull_medusa_catalog_activity` lleva `@with_heartbeat(every=10)` importado de `src/platform/temporal/heartbeat.py`. `write_snapshot_activity` no (es <1s).
- **R-DIP**: **applies — handled how**: el workflow importa solo `contracts`, retry policies, las funciones de activity (vía `imports_passed_through`). NO importa `httpx`, NO importa `MedusaClient`. Las activities sí pueden importar `httpx` indirectamente (vía `MedusaProductService`).

## 12. Tests

| Test file | Type | Asserts |
|---|---|---|
| `tests/catalog_sync/test_contracts.py` | Unit | Cada DTO se serializa con `json.dumps(asdict(...))` y se rehidrata. Sin métodos. |
| `tests/catalog_sync/test_pull_catalog_use_case.py` | Unit (Fake `MedusaProductService`) | Productos paginados → `PullCatalogResult.count` correcto. Mapeo `MedusaProduct.amount: Decimal` → `CatalogPriceDTO.amount: str`. |
| `tests/catalog_sync/test_write_snapshot_use_case.py` | Unit (`tmp_path`) | Escribe `snapshot.json`, `by_handle/<h>.json` por producto, `manifest.json`. Atomicidad: durante un fallo simulado en escritura del manifest, el `snapshot.json` viejo sigue íntegro. |
| `tests/catalog_sync/test_pull_activity.py` | `ActivityEnvironment` | `pull_medusa_catalog_activity` con `MedusaProductService` fake retorna `PullCatalogResult` esperado. |
| `tests/catalog_sync/test_write_activity.py` | `ActivityEnvironment` (`tmp_path`) | `write_snapshot_activity` con un `PullCatalogResult` fake produce los archivos esperados y un `WriteSnapshotResult` correcto. |
| `tests/catalog_sync/test_workflow.py` | `WorkflowEnvironment.start_time_skipping` | Reemplaza ambas activities con fakes; el workflow las llama en orden y pasa el `PullCatalogResult` como entrada al `WriteSnapshotInput`. |
| `tests/catalog_sync/test_replay.py` | Replay | Captura un workflow real exitoso, lo replaya. Fixture: `tests/catalog_sync/fixtures/catalog_sync_v1.json`. |

Replay: bump fixture a `_v2` cuando el `CatalogSyncInput` o `WriteSnapshotInput` cambien de shape.

## 13. Risks / open questions

- **R1**: El `products_json: str` puede crecer grande (1000 productos × 5KB = 5MB). Temporal soporta payloads hasta ~2-4MB por defecto. **Mitigación**: si el catálogo crece mucho, partir el sync en chunks paginados y hacer N writes (1 por página). Para v1 con catálogo <500 productos no es problema. **Acción HU-05**: medir tamaño real de payload en staging y subir `temporal.maxPayloadSize` o splitear si supera 1MB.
- **R2**: Schedule API exacta (`temporal schedule create ...` vs `client.create_schedule(...)`). **Defer to `temporal:temporal-developer`** en HU-05 cuando vayamos a activar.
- **R3**: El `source_etag` solo lo guardamos para visibilidad — no se usa para conditional GET por ahora. Habilitar conditional GET (`If-None-Match`) sería una optimización futura que evitaría el round-trip cuando no hay cambios. Out of scope HU-03.
- **R4**: Concurrent writers. Si dos workers `catalog_sync` corren en paralelo (escenario K8s con `replicas: 2`) podrían pisarse en `os.replace`. **Mitigación**: deployment con `replicas: 1` (single writer). HU-05 lo enforza.
- **R5**: ¿Borrar productos? Si Medusa elimina un producto, el snapshot completo sí lo refleja en cada sync (es un full refresh), pero el archivo `by_handle/<old-handle>.json` queda huérfano. **Mitigación**: `WriteSnapshotUseCase` limpia `by_handle/` antes de re-escribir. Test cubre.
- **R6**: ¿Failover si Medusa está down? Workflow falla, manifest no se actualiza, Sales sigue leyendo snapshot anterior con `stale=True` cuando se cumpla MAX_AGE. **Esto es feature, no bug** — degradación graceful. Documentar.
- **R7**: El task queue es independiente. **¿Dónde corre el worker?** Recomendación: container nuevo en K8s (decision HU-05). El binario es `python -m src.catalog_sync.worker`.
- **R8**: Pre-existing pattern check — el agente `dashboard/` que ya existe en el repo: ¿es similar (programático)? **Verificar**: `ls src/dashboard/`. Si tiene la misma forma (sin LLM), copiar su estructura. Si es algo distinto, este agente queda como referencia para futuros agentes programáticos.
- **Defer to `temporal:temporal-developer`**: la creación de la Schedule en HU-05.
- **Defer to `claude-api`**: ninguno.

## 14. Implementation order (suggested)

1. Constante `CATALOG_SYNC_QUEUE` en `src/platform/constants.py`.
2. `src/catalog_sync/__init__.py`, `contracts.py` (los 4 DTOs).
3. `src/catalog_sync/use_cases/pull_catalog.py` + tests.
4. `src/catalog_sync/use_cases/write_snapshot.py` + tests (puro; tmp_path).
5. `src/catalog_sync/activities/pull.py` + tests (`ActivityEnvironment`).
6. `src/catalog_sync/activities/write.py` + tests.
7. `src/catalog_sync/composition.py`.
8. `src/catalog_sync/workflows/sync.py` + tests (`WorkflowEnvironment`).
9. `src/catalog_sync/worker.py`.
10. Replay test + fixture.

(Esta HU depende de HU-01 y HU-02; correr ambas primero.)

---

**Next step**:

```
/exoclaw-implementer .exoclaw/refinements/03-catalog-sync-agent-tech.md
```
