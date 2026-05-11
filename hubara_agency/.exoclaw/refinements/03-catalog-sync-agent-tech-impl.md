# Implementation plan — 03 catalog_sync agent

- **Refinement**: `.exoclaw/refinements/03-catalog-sync-agent-tech.md`
- **Depends on**: HU-01 (HttpMedusaClient), HU-02 (CatalogPort + DTOs).
- **Target agent**: `catalog_sync` (NUEVO) at `/Users/edgm/Documents/Projects/AgencyHubara/hubara_agency`
- **Implementer**: exoclaw-implementer
- **Date**: 2026-05-07

## 1. PR sequence (each step keeps tests green)

### PR-1: scaffolding + contracts + queue constant
**Goal**: estructura del agente + DTOs R-JSON.
**Files**:
- EDIT `src/platform/constants.py` — añadir `CATALOG_SYNC_QUEUE = "queue-catalog-sync"`.
- CREATE `src/catalog_sync/__init__.py`, `src/catalog_sync/contracts.py`, `src/catalog_sync/workflows/__init__.py`, `src/catalog_sync/activities/__init__.py`, `src/catalog_sync/use_cases/__init__.py`.
- CREATE `tests/catalog_sync/__init__.py`, `tests/catalog_sync/test_contracts.py`.
**Verification**:
```bash
uv run pytest tests/catalog_sync/test_contracts.py -x
```

### PR-2: PullCatalogUseCase + tests
**Goal**: la lógica de mapeo Medusa→DTO testable sin Temporal.
**Files**:
- CREATE `src/catalog_sync/use_cases/pull_catalog.py`.
- CREATE `tests/catalog_sync/test_pull_catalog_use_case.py`.
**Verification**:
```bash
uv run pytest tests/catalog_sync/test_pull_catalog_use_case.py -x
```

### PR-3: WriteSnapshotUseCase + tests
**Goal**: escritura atómica + by_handle/ + manifest, tmp_path.
**Files**:
- CREATE `src/catalog_sync/use_cases/write_snapshot.py`.
- CREATE `tests/catalog_sync/test_write_snapshot_use_case.py`.
**Verification**:
```bash
uv run pytest tests/catalog_sync/test_write_snapshot_use_case.py -x
```

### PR-4: Activities (pull + write)
**Goal**: thin wrappers sobre los use cases con `@activity.defn` y heartbeat.
**Files**:
- CREATE `src/catalog_sync/activities/pull.py`, `src/catalog_sync/activities/write.py`.
- EDIT `src/catalog_sync/activities/__init__.py` — re-exports.
- CREATE `tests/catalog_sync/test_pull_activity.py`, `tests/catalog_sync/test_write_activity.py`.
**Verification**:
```bash
uv run pytest tests/catalog_sync/ -x
```

### PR-5: Composition + Workflow
**Goal**: factories + workflow que orquesta pull→write.
**Files**:
- CREATE `src/catalog_sync/composition.py`.
- CREATE `src/catalog_sync/workflows/sync.py`.
- EDIT `src/catalog_sync/workflows/__init__.py` — re-export.
- CREATE `tests/catalog_sync/test_workflow.py`.
**Verification**:
```bash
uv run pytest tests/catalog_sync/ -x
```

### PR-6: Worker
**Goal**: binario ejecutable `python -m src.catalog_sync.worker`.
**Files**:
- CREATE `src/catalog_sync/worker.py`.
**Verification**: smoke manual (correr el worker contra Temporal local; HU-05 cubre el smoke completo).

### PR-7: Replay test
**Goal**: capturar 1 history y validar replay.
**Files**:
- CREATE `tests/catalog_sync/fixtures/.gitkeep`.
- CREATE `tests/catalog_sync/test_replay.py` (skip-on-no-fixture inicialmente).
- Después de correr el worker, capturar history → `tests/catalog_sync/fixtures/catalog_sync_v1.json`.
**Verification**:
```bash
uv run pytest tests/catalog_sync/test_replay.py -x
```

## 2. File-by-file (canonical content)

### `src/platform/constants.py` (EDIT — añadir)

Localizar el bloque de queues (líneas 9-10 actuales) y añadir:

```python
CATALOG_SYNC_QUEUE = "queue-catalog-sync"
```

### `src/catalog_sync/__init__.py` (NEW)

```python
"""catalog_sync — agente DEHA programático (sin LLM) que sincroniza el
catálogo desde Medusa Admin API y escribe un snapshot atómico que el
agente Sales lee vía src/platform/catalog/local_snapshot.py.
"""
```

### `src/catalog_sync/contracts.py` (NEW)

```python
"""Boundary DTOs del catalog_sync (R-JSON).

`products_json: str` aplica el JSON-string trick (gotcha #6 del DEHA arch)
para transferir listas grandes a través del workflow boundary sin tipos
complejos anidados.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogSyncInput:
    tenant_id: str = "default"
    force_full_refresh: bool = True


@dataclass(frozen=True)
class PullCatalogResult:
    products_json: str
    count: int
    fetched_at: str  # ISO 8601 UTC
    source_etag: str | None = None


@dataclass(frozen=True)
class WriteSnapshotInput:
    products_json: str
    count: int
    fetched_at: str
    snapshot_dir: str
    source_etag: str | None = None


@dataclass(frozen=True)
class WriteSnapshotResult:
    version: str
    bytes_written: int
    files_written: int
```

### `src/catalog_sync/use_cases/pull_catalog.py` (NEW)

```python
"""Pull del catálogo desde Medusa, mapeo a CatalogProductDTO, serialización a JSON.

Lo importante: este use case puro NO conoce Temporal. Se testea con un
fake de MedusaProductService.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

from src.catalog_sync.contracts import CatalogSyncInput, PullCatalogResult
from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.medusa.models import MedusaProduct
from src.platform.medusa.service import MedusaProductService


class PullCatalogUseCase:
    def __init__(self, medusa_service: MedusaProductService) -> None:
        self._medusa = medusa_service

    async def execute(self, input: CatalogSyncInput) -> PullCatalogResult:
        products: list[CatalogProductDTO] = []
        # Páginar el catálogo completo. status=published filtra borradores.
        async for raw in self._medusa.client.iter_products(
            page_size=100, status="published",
        ):
            mp = MedusaProduct.model_validate(raw)
            products.append(_to_dto(mp))

        payload = json.dumps([asdict(p) for p in products])
        return PullCatalogResult(
            products_json=payload,
            count=len(products),
            fetched_at=datetime.now(timezone.utc).isoformat(),
            source_etag=None,  # conditional GET es follow-up
        )


def _to_dto(mp: MedusaProduct) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=mp.id,
        handle=mp.handle,
        title=mp.title,
        status=mp.status,
        description=mp.description,
        thumbnail=mp.thumbnail,
        variants=[
            CatalogVariantDTO(
                id=v.id,
                title=v.title,
                sku=v.sku,
                prices=[
                    CatalogPriceDTO(
                        amount=str(p.amount),  # Decimal → str (R-JSON)
                        currency_code=p.currency_code,
                        min_quantity=p.min_quantity,
                        max_quantity=p.max_quantity,
                    )
                    for p in v.prices
                ],
            )
            for v in mp.variants
        ],
        images=[CatalogImageDTO(url=i.url, rank=i.rank) for i in mp.images],
        tags=[t.value for t in mp.tags],
        categories=[c.handle or c.name for c in mp.categories],
        metadata={k: json.dumps(v) if not isinstance(v, str) else v
                  for k, v in (mp.metadata or {}).items()} or None,
    )
```

### `src/catalog_sync/use_cases/write_snapshot.py` (NEW)

```python
"""Escritura atómica del snapshot.

Reglas de invariantes:
  1. snapshot.json se escribe ANTES que manifest.json. Si la escritura
     del manifest falla, el snapshot anterior y el nuevo son ambos válidos
     (el reader igual sobrevive — `_load_manifest` tolera ausencia).
  2. El directorio `by_handle/` se LIMPIA y reescribe completo en cada
     sync para no dejar handles huérfanos cuando se borran productos.
  3. Cada archivo se escribe a `*.tmp` y luego `os.replace(...)` (atómico
     en POSIX).
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from src.catalog_sync.contracts import WriteSnapshotInput, WriteSnapshotResult


class WriteSnapshotUseCase:
    async def execute(self, input: WriteSnapshotInput) -> WriteSnapshotResult:
        snap_dir = Path(input.snapshot_dir)
        snap_dir.mkdir(parents=True, exist_ok=True)
        by_handle_dir = snap_dir / "by_handle"

        version = uuid4().hex
        bytes_written = 0
        files_written = 0

        # 1) snapshot.json (atómico)
        snap_path = snap_dir / "snapshot.json"
        snap_tmp = snap_dir / "snapshot.json.tmp"
        snap_tmp.write_text(input.products_json, encoding="utf-8")
        os.replace(snap_tmp, snap_path)
        bytes_written += snap_path.stat().st_size
        files_written += 1

        # 2) by_handle/*.json (limpiar y reescribir)
        if by_handle_dir.exists():
            shutil.rmtree(by_handle_dir)
        by_handle_dir.mkdir(parents=True, exist_ok=True)

        products = json.loads(input.products_json)
        for prod in products:
            handle = str(prod["handle"])
            file_path = by_handle_dir / f"{handle}.json"
            tmp_path = by_handle_dir / f"{handle}.json.tmp"
            tmp_path.write_text(json.dumps(prod), encoding="utf-8")
            os.replace(tmp_path, file_path)
            bytes_written += file_path.stat().st_size
            files_written += 1

        # 3) manifest.json (último — atómico)
        manifest = {
            "version": version,
            "fetched_at": input.fetched_at,
            "product_count": input.count,
            "source_etag": input.source_etag,
        }
        manifest_path = snap_dir / "manifest.json"
        manifest_tmp = snap_dir / "manifest.json.tmp"
        manifest_tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(manifest_tmp, manifest_path)
        bytes_written += manifest_path.stat().st_size
        files_written += 1

        return WriteSnapshotResult(
            version=version,
            bytes_written=bytes_written,
            files_written=files_written,
        )
```

### `src/catalog_sync/activities/pull.py` (NEW)

```python
from __future__ import annotations

from temporalio import activity

from src.catalog_sync.composition import get_pull_catalog_use_case
from src.catalog_sync.contracts import CatalogSyncInput, PullCatalogResult
from src.platform.temporal.heartbeat import with_heartbeat


@activity.defn(name="pull_medusa_catalog")
@with_heartbeat(every=10)
async def pull_medusa_catalog_activity(input: CatalogSyncInput) -> PullCatalogResult:
    use_case = get_pull_catalog_use_case()  # rebuilt-from-factory cada invoke (R-STATELESS)
    activity.logger.info("pull_medusa_catalog start tenant=%s", input.tenant_id)
    result = await use_case.execute(input)
    activity.logger.info("pull_medusa_catalog done count=%d", result.count)
    return result
```

### `src/catalog_sync/activities/write.py` (NEW)

```python
from __future__ import annotations

from temporalio import activity

from src.catalog_sync.composition import get_write_snapshot_use_case
from src.catalog_sync.contracts import WriteSnapshotInput, WriteSnapshotResult


@activity.defn(name="write_snapshot")
async def write_snapshot_activity(input: WriteSnapshotInput) -> WriteSnapshotResult:
    use_case = get_write_snapshot_use_case()
    activity.logger.info("write_snapshot start dir=%s count=%d",
                         input.snapshot_dir, input.count)
    result = await use_case.execute(input)
    activity.logger.info("write_snapshot done version=%s bytes=%d files=%d",
                         result.version, result.bytes_written, result.files_written)
    return result
```

### `src/catalog_sync/activities/__init__.py` (NEW)

```python
from src.catalog_sync.activities.pull import pull_medusa_catalog_activity
from src.catalog_sync.activities.write import write_snapshot_activity

__all__ = ["pull_medusa_catalog_activity", "write_snapshot_activity"]
```

### `src/catalog_sync/composition.py` (NEW)

```python
from __future__ import annotations

from functools import lru_cache

from src.catalog_sync.use_cases.pull_catalog import PullCatalogUseCase
from src.catalog_sync.use_cases.write_snapshot import WriteSnapshotUseCase
from src.platform.medusa.composition import get_medusa_product_service


@lru_cache(maxsize=1)
def get_pull_catalog_use_case() -> PullCatalogUseCase:
    return PullCatalogUseCase(medusa_service=get_medusa_product_service())


@lru_cache(maxsize=1)
def get_write_snapshot_use_case() -> WriteSnapshotUseCase:
    return WriteSnapshotUseCase()
```

### `src/catalog_sync/workflows/sync.py` (NEW)

```python
from __future__ import annotations

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.catalog_sync.activities import (
        pull_medusa_catalog_activity,
        write_snapshot_activity,
    )
    from src.catalog_sync.contracts import (
        CatalogSyncInput,
        WriteSnapshotInput,
        WriteSnapshotResult,
    )
    from src.platform.catalog.paths import get_snapshot_dir
    from src.platform.temporal.retry_policies import _CONV_OPTIONS, _TOOL_OPTIONS


@workflow.defn(name="CatalogSyncWorkflow")
class CatalogSyncWorkflow:
    """Single-shot workflow: pull catálogo desde Medusa, escribe snapshot.

    Dispared por una Temporal Schedule (HU-05). Cada disparo es un workflow
    ID nuevo (`catalog-sync-<scheduled-time>`). No hay state entre runs —
    el snapshot es el state.
    """

    @workflow.run
    async def run(self, input: CatalogSyncInput) -> WriteSnapshotResult:
        # 1) Pull (puede tardar; activity con heartbeat).
        pull_result = await workflow.execute_activity(
            pull_medusa_catalog_activity,
            input,
            **_TOOL_OPTIONS,
        )

        # 2) Write atómico.
        # NOTE: get_snapshot_dir() lee env vía `os.environ.get`. R-DET prohíbe
        # hacerlo desde @workflow.run. Por eso la resolución del path se delega
        # a la activity write_snapshot_activity vía un default (None) que la
        # activity rellena. Aquí pasamos un placeholder.
        write_input = WriteSnapshotInput(
            products_json=pull_result.products_json,
            count=pull_result.count,
            fetched_at=pull_result.fetched_at,
            snapshot_dir=str(_resolve_snapshot_dir_for_workflow()),
            source_etag=pull_result.source_etag,
        )
        return await workflow.execute_activity(
            write_snapshot_activity,
            write_input,
            **_CONV_OPTIONS,
        )


def _resolve_snapshot_dir_for_workflow() -> str:
    """Llamado desde dentro del workflow context. Pero `get_snapshot_dir()`
    lee env — eso violaría R-DET si se llamara dentro de @workflow.run.

    Workaround: el path se resuelve en el módulo (al importar el workflow)
    y se "captura" como una constante de proceso. Funciona porque el
    workspace path no cambia en runtime. Si en futuro queremos cambiarlo
    sin restart, hay que hacerlo via activity.
    """
    return str(get_snapshot_dir())
```

> **R-DET note (importante)**: leer `os.environ` está prohibido dentro de `@workflow.run`. Pero `get_snapshot_dir()` se llama **al importar el módulo** (vía `_resolve_snapshot_dir_for_workflow`), no en runtime del workflow. Esto es legal: la lectura ocurre una sola vez, en el worker startup, antes de ejecutar el workflow. **Más limpio**: pasarlo a través del input. Si el implementer prefiere esa ruta, modificar `CatalogSyncInput` para incluir `snapshot_dir: str` y resolverlo desde el caller (la Schedule lo pasa). **Decision para PR**: ir con la versión limpia (input field), no la "constante de módulo".
>
> **Replanteo PR-1**: cambiar el `CatalogSyncInput` a:
>
> ```python
> @dataclass(frozen=True)
> class CatalogSyncInput:
>     tenant_id: str = "default"
>     force_full_refresh: bool = True
>     snapshot_dir: str = ""  # resuelto por el caller (Schedule); fallback en activity
> ```
>
> Y en `pull_medusa_catalog_activity` (y workflow):
>
> ```python
> # En el workflow:
> snapshot_dir = input.snapshot_dir  # confianza al caller
>
> # Si está vacío (caller no lo seteó), la activity lo resuelve desde env:
> # En activities/write.py:
> if not input.snapshot_dir:
>     from src.platform.catalog.paths import get_snapshot_dir
>     input = replace(input, snapshot_dir=str(get_snapshot_dir()))
> ```
>
> Esto mantiene R-DET 100% y deja la activity como única responsable del env. **Recomendación implementer**: aplicar este replanteo en PR-1 y eliminar `_resolve_snapshot_dir_for_workflow` arriba.

### `src/catalog_sync/workflows/__init__.py` (NEW)

```python
from src.catalog_sync.workflows.sync import CatalogSyncWorkflow

__all__ = ["CatalogSyncWorkflow"]
```

### `src/catalog_sync/worker.py` (NEW)

```python
from __future__ import annotations

import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.catalog_sync.activities import (
    pull_medusa_catalog_activity,
    write_snapshot_activity,
)
from src.catalog_sync.workflows import CatalogSyncWorkflow
from src.platform.constants import CATALOG_SYNC_QUEUE
from src.platform.logging import setup_logging
from src.platform.temporal.client import get_temporal_client

setup_logging()


async def main() -> None:
    logger.info("Conectando catalog_sync al clúster Temporal...")
    client = await get_temporal_client()

    worker = Worker(
        client,
        task_queue=CATALOG_SYNC_QUEUE,
        workflows=[CatalogSyncWorkflow],
        activities=[
            pull_medusa_catalog_activity,
            write_snapshot_activity,
        ],
    )

    logger.info("📦 catalog_sync worker arriba. Cola: '{}'", CATALOG_SYNC_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

## 3. Tests to add

### `tests/catalog_sync/test_contracts.py` (NEW)

```python
import json
from dataclasses import asdict
from src.catalog_sync.contracts import (
    CatalogSyncInput, PullCatalogResult, WriteSnapshotInput, WriteSnapshotResult,
)


def test_all_dtos_json_roundtrip():
    for dto in [
        CatalogSyncInput(),
        PullCatalogResult(products_json="[]", count=0, fetched_at="2026-01-01T00:00:00Z"),
        WriteSnapshotInput(products_json="[]", count=0, fetched_at="2026-01-01T00:00:00Z", snapshot_dir="/tmp/x"),
        WriteSnapshotResult(version="abc", bytes_written=10, files_written=1),
    ]:
        s = json.dumps(asdict(dto))
        assert isinstance(s, str)
```

### `tests/catalog_sync/test_pull_catalog_use_case.py` (NEW)

```python
import pytest
from src.catalog_sync.contracts import CatalogSyncInput
from src.catalog_sync.use_cases.pull_catalog import PullCatalogUseCase


class _FakeClient:
    async def iter_products(self, **kwargs):
        yield {
            "id": "p1", "title": "Vela 1", "handle": "vela-1", "status": "published",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "variants": [{"id": "v1", "title": "u", "prices": [{"id": "pr", "amount": 49.99, "currency_code": "usd"}]}],
            "images": [], "tags": [], "categories": [],
        }


class _FakeService:
    def __init__(self):
        self.client = _FakeClient()

    async def get(self, _id): raise NotImplementedError
    async def list(self, **k): raise NotImplementedError


@pytest.mark.asyncio
async def test_pull_returns_dto_with_decimal_as_str():
    use_case = PullCatalogUseCase(medusa_service=_FakeService())
    result = await use_case.execute(CatalogSyncInput())
    assert result.count == 1
    import json
    payload = json.loads(result.products_json)
    assert payload[0]["handle"] == "vela-1"
    assert payload[0]["variants"][0]["prices"][0]["amount"] == "49.99"  # str, not number
```

### `tests/catalog_sync/test_write_snapshot_use_case.py` (NEW)

```python
import json, os, pytest
from pathlib import Path
from src.catalog_sync.contracts import WriteSnapshotInput
from src.catalog_sync.use_cases.write_snapshot import WriteSnapshotUseCase


@pytest.mark.asyncio
async def test_write_creates_snapshot_by_handle_and_manifest(tmp_path: Path):
    products = [
        {"id": "1", "handle": "luz-serena", "title": "Luz Serena", "status": "published"},
        {"id": "2", "handle": "vela-cruz", "title": "Cruz", "status": "published"},
    ]
    use_case = WriteSnapshotUseCase()
    result = await use_case.execute(WriteSnapshotInput(
        products_json=json.dumps(products),
        count=2,
        fetched_at="2026-05-07T12:00:00+00:00",
        snapshot_dir=str(tmp_path),
    ))

    assert (tmp_path / "snapshot.json").exists()
    assert (tmp_path / "by_handle" / "luz-serena.json").exists()
    assert (tmp_path / "by_handle" / "vela-cruz.json").exists()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["product_count"] == 2
    assert manifest["fetched_at"] == "2026-05-07T12:00:00+00:00"
    assert result.files_written >= 4  # 1 snapshot + 2 by_handle + 1 manifest


@pytest.mark.asyncio
async def test_write_cleans_orphan_handles(tmp_path: Path):
    by_handle = tmp_path / "by_handle"
    by_handle.mkdir()
    (by_handle / "old-product.json").write_text("{}")

    use_case = WriteSnapshotUseCase()
    await use_case.execute(WriteSnapshotInput(
        products_json=json.dumps([{"id": "1", "handle": "new-product", "title": "X", "status": "published"}]),
        count=1, fetched_at="2026-01-01T00:00:00Z", snapshot_dir=str(tmp_path),
    ))

    assert not (by_handle / "old-product.json").exists()  # cleaned
    assert (by_handle / "new-product.json").exists()
```

### `tests/catalog_sync/test_pull_activity.py` (NEW)

```python
import pytest
from temporalio.testing import ActivityEnvironment

from src.catalog_sync.activities.pull import pull_medusa_catalog_activity
from src.catalog_sync.contracts import CatalogSyncInput
from src.catalog_sync.use_cases.pull_catalog import PullCatalogUseCase


@pytest.mark.asyncio
async def test_activity_calls_use_case(monkeypatch):
    class _FakeUseCase:
        async def execute(self, input):
            from src.catalog_sync.contracts import PullCatalogResult
            return PullCatalogResult(
                products_json="[]", count=0, fetched_at="2026-01-01T00:00:00Z",
            )

    monkeypatch.setattr(
        "src.catalog_sync.activities.pull.get_pull_catalog_use_case",
        lambda: _FakeUseCase(),
    )

    env = ActivityEnvironment()
    result = await env.run(pull_medusa_catalog_activity, CatalogSyncInput())
    assert result.count == 0
```

### `tests/catalog_sync/test_write_activity.py` (NEW)

```python
import json, pytest
from pathlib import Path
from temporalio.testing import ActivityEnvironment

from src.catalog_sync.activities.write import write_snapshot_activity
from src.catalog_sync.contracts import WriteSnapshotInput


@pytest.mark.asyncio
async def test_write_activity_writes_files(tmp_path: Path):
    env = ActivityEnvironment()
    result = await env.run(write_snapshot_activity, WriteSnapshotInput(
        products_json=json.dumps([{"id": "1", "handle": "h", "title": "T", "status": "published"}]),
        count=1,
        fetched_at="2026-01-01T00:00:00Z",
        snapshot_dir=str(tmp_path),
    ))
    assert result.files_written >= 3
    assert (tmp_path / "snapshot.json").exists()
```

### `tests/catalog_sync/test_workflow.py` (NEW)

```python
import pytest
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.catalog_sync.contracts import (
    CatalogSyncInput, PullCatalogResult, WriteSnapshotInput, WriteSnapshotResult,
)
from src.catalog_sync.workflows import CatalogSyncWorkflow


@activity.defn(name="pull_medusa_catalog")
async def fake_pull(input: CatalogSyncInput) -> PullCatalogResult:
    return PullCatalogResult(
        products_json='[{"id":"1","handle":"h","title":"T","status":"published"}]',
        count=1, fetched_at="2026-05-07T12:00:00+00:00",
    )


@activity.defn(name="write_snapshot")
async def fake_write(input: WriteSnapshotInput) -> WriteSnapshotResult:
    assert input.count == 1
    assert "h" in input.products_json
    return WriteSnapshotResult(version="abc", bytes_written=100, files_written=3)


@pytest.mark.asyncio
async def test_workflow_calls_pull_then_write():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-catalog-sync",
            workflows=[CatalogSyncWorkflow],
            activities=[fake_pull, fake_write],
        ):
            result = await env.client.execute_workflow(
                CatalogSyncWorkflow.run,
                CatalogSyncInput(snapshot_dir="/tmp/test"),
                id="test-1",
                task_queue="test-catalog-sync",
            )
            assert result.version == "abc"
```

### `tests/catalog_sync/test_replay.py` (NEW)

```python
import json
from pathlib import Path
import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.catalog_sync.workflows import CatalogSyncWorkflow

FIXTURE = Path(__file__).parent / "fixtures" / "catalog_sync_v1.json"


@pytest.mark.asyncio
@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not captured yet")
async def test_workflow_replays():
    history = WorkflowHistory.from_json(FIXTURE.read_text())
    replayer = Replayer(workflows=[CatalogSyncWorkflow])
    await replayer.replay_workflow(history)
```

## 4. Replay fixture refresh

Después de PR-6 + un sync exitoso real:

```bash
WORKFLOW_ID=$(temporal workflow list --query 'WorkflowType="CatalogSyncWorkflow"' --output json | jq -r '.[0].execution.workflowId')
temporal workflow show --workflow-id "$WORKFLOW_ID" --output json > tests/catalog_sync/fixtures/catalog_sync_v1.json
```

Bumpear a `_v2.json` cuando `CatalogSyncInput` o `WriteSnapshotInput` cambien shape.

## 5. Verification commands (run between every PR)

```bash
# Type + lint
uv run ruff check src/catalog_sync
uv run ty check src/catalog_sync

# Tests
uv run pytest tests/catalog_sync/ -x

# Hard rules grep
grep -rEn "(time\.time|datetime\.now|uuid\.uuid4|random\.|^\s*open\(|requests\.|httpx\.)" src/catalog_sync/workflows/ \
  || echo "R-DET ok (workflow puro)"
grep -rEn "^[A-Z_][A-Z0-9_]+\s*=\s*[\[\{]" src/catalog_sync/activities/*.py \
  || echo "R-STATELESS ok (activities sin module-level mutable)"
grep -rEn "^from temporalio\.(client|worker)" src/catalog_sync/use_cases/ \
  || echo "R-DIP (use_cases) ok"
grep -rEn "^from (litellm|httpx|requests|exoclaw_conversation)" src/catalog_sync/workflows/ \
  || echo "R-DIP (workflows) ok"

# Lean compliance
find src/catalog_sync/ -maxdepth 2 -type d \( -name domain -o -name application -o -name infrastructure -o -name interfaces \) \
  | (! grep -q .) && echo "lean layout ok"
```

## 6. Smoke-test recipe

```bash
# Terminal 1 — Temporal dev server (si no hay uno corriendo en mTLS)
temporal server start-dev

# Terminal 2 — catalog_sync worker
MEDUSA_BASE_URL=$MEDUSA_BASE_URL \
MEDUSA_ADMIN_TOKEN=$MEDUSA_ADMIN_TOKEN \
CATALOG_SNAPSHOT_DIR=/tmp/hubara_catalog \
uv run python -m src.catalog_sync.worker

# Terminal 3 — disparar UN sync manual (sin Schedule todavía; eso es HU-05)
uv run python -c "
import asyncio
from temporalio.client import Client
from src.catalog_sync.contracts import CatalogSyncInput
from src.catalog_sync.workflows import CatalogSyncWorkflow
from src.platform.constants import CATALOG_SYNC_QUEUE

async def main():
    client = await Client.connect('localhost:7233')
    result = await client.execute_workflow(
        CatalogSyncWorkflow.run,
        CatalogSyncInput(snapshot_dir='/tmp/hubara_catalog'),
        id='manual-sync-1',
        task_queue=CATALOG_SYNC_QUEUE,
    )
    print('OK', result)

asyncio.run(main())
"

# Validar
ls -la /tmp/hubara_catalog/
cat /tmp/hubara_catalog/manifest.json
ls /tmp/hubara_catalog/by_handle/ | head
```

## 7. Rollback strategy

Cada PR es revertible. La PR-1 introduce nueva carpeta `src/catalog_sync/` y constante en `src/platform/constants.py` — `git revert` quita ambos. PR-2..6 son archivos en la nueva carpeta. PR-7 es solo tests.

Una vez en prod (HU-05): si hay regresión, el rollback es:
1. `kubectl scale deploy catalog-sync-worker --replicas=0` (worker muerto, sync deja de correr).
2. Sales sigue leyendo el último snapshot bueno hasta que envejezca a `stale=True`.
3. Investigar, fix, redeploy.

## 8. Coordination updates

ADRs:
- `ADR-2026-05-07-05: catalog_sync agente DEHA programático sin LLM`. Razón: workflow puro de I/O; no necesita workspace/IDENTITY.
- `ADR-2026-05-07-06: snapshot_dir cruza como str en CatalogSyncInput, no via env desde workflow`. Razón: R-DET; el caller (Schedule) o la activity son los únicos que leen env.

## 9. Risks I'm carrying forward from the refinement

- **R1**: payload `products_json` puede crecer >1MB. Mitigación: HU-05 mide y splittea si supera.
- **R4**: concurrent writers. Mitigación: K8s deploy con `replicas: 1`.
- **R5**: handles huérfanos. Mitigación: implementada — `WriteSnapshotUseCase` borra `by_handle/` antes de re-escribir.
- **R8**: revisar agente `dashboard/`. Acción pre-PR-1: `ls src/dashboard/`.

---

**Status**: refinement validado, plan listo. **Stop point**: confirmar antes de PR-1.
