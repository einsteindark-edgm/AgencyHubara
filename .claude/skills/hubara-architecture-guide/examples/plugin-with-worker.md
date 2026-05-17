# Example — Plugin con worker Temporal sin LLM (template C)

> **Plugin real del repo:** `catalog`.
>
> **Use cuándo:** tu plugin necesita workflow long-running (sync de
> data externa, cron job, batch processing) pero NO necesita LLM
> tool-calling.

---

## §1. Archivos reales del plugin `catalog`

```
frontend_dashboard/src/plugins/catalog/
├── plugin.yaml                                # ver §2
└── frontend/
    ├── index.ts
    ├── CatalogSection.tsx                     # Page root (muestra jobs + dispara syncs)
    └── features/
        ├── upload-wizard/
        ├── upload-jobs/
        └── upload-inspector/

hubara_agency/src/plugins/catalog/
├── __init__.py
├── agent/
│   ├── __init__.py                            # docstring
│   ├── contracts.py                           # @dataclass frozen DTOs
│   ├── composition.py                         # factories
│   ├── workflows/
│   │   ├── __init__.py
│   │   └── sync.py                            # @workflow.defn CatalogSyncWorkflow
│   └── activities/
│       ├── __init__.py
│       ├── pull_medusa.py                     # @activity.defn
│       └── write_snapshot.py                  # @activity.defn
└── workers/
    ├── __init__.py
    └── sync.py                                # async def main() con Worker(...)

hubara_agency/k8s/aws-produccion/
└── worker-catalog-sync.yaml                   # 1 deployment K8s

hubara_agency/scripts/
└── trigger_catalog_sync.py                    # CLI dev: dispara workflow manual
```

---

## §2. Manifest (`plugin.yaml`)

```yaml
# frontend_dashboard/src/plugins/catalog/plugin.yaml (real)
id: catalog
version: 0.1.0
display_name: Catalog Sync
description: Sincronización del catálogo de productos (Medusa → snapshot filesystem). Worker Temporal disparado por schedule o manualmente; el frontend muestra jobs + permite disparar syncs.

depends_on: []

frontend:
  entry: ./frontend
  contributes:
    sections:
      - { key: upload, label: Catalog, order: 4, icon: pkg }
    sidebar:
      - { route: /catalog, label: Catalog, icon: pkg }

# Sin `api:` — el plugin no expone endpoints HTTP propios.
# El worker se dispara desde scripts/trigger_catalog_sync.py o desde otro plugin
# que dispare el workflow al final de su pipeline.

agent:
  python_module: src.plugins.catalog.agent
  workers:
    - name: sync
      module: src.plugins.catalog.workers.sync
      task_queue: queue-catalog-sync
      deployment:
        replicas: 1                       # CRITICAL: single writer (race en os.replace)
        strategy: Recreate                # evita dos pods escribiendo durante rollout
        cpu_request: 100m
        memory_request: 256Mi
        env_secrets:
          - { var: MEDUSA_BASE_URL,    secret: hubara-medusa-secret, key: MEDUSA_BASE_URL }
          - { var: MEDUSA_ADMIN_TOKEN, secret: hubara-medusa-secret, key: MEDUSA_ADMIN_TOKEN }
      compose:
        env:
          TEMPORAL_URL: temporal:7233
          WORKSPACE_VAULT_DIR: /app/hubara_vault
          CATALOG_SNAPSHOT_DIR: /app/hubara_vault/catalog
          CATALOG_MAX_AGE_MINUTES: "30"
          MEDUSA_BASE_URL: ${MEDUSA_BASE_URL}
          MEDUSA_ADMIN_TOKEN: ${MEDUSA_ADMIN_TOKEN}
        volumes:
          - hubara-vault-local:/app/hubara_vault
        depends_on:
          - temporal

wiring_intents:
  filesystem_volumes:
    - hubara-vault                       # subPath catalog/
  env_vars_required:
    - TEMPORAL_URL
    - CATALOG_SNAPSHOT_DIR
    - MEDUSA_BASE_URL
    - MEDUSA_ADMIN_TOKEN
```

**Decisiones clave:**

- `replicas: 1` + `strategy: Recreate` — el sync hace `os.replace` sobre
  el snapshot. 2+ pods escribiendo → race condition → snapshot corrupto.
  Recreate evita el overlap durante rollout.
- `task_queue: queue-catalog-sync` — exclusive del plugin (single worker).
- `env_secrets` separados de `compose.env` — secretos van por K8s
  Secrets, no por compose env (compose env es para dev local).

---

## §3. Contracts (`agent/contracts.py`)

```python
# canonical — agent/contracts.py
from dataclasses import dataclass

@dataclass(frozen=True)
class CatalogSyncInput:
    """Input del workflow. Pocos campos — el sync es deterministic."""
    triggered_by: str           # "schedule" | "manual" | "post-deploy"
    force: bool = False         # ignora CATALOG_MAX_AGE_MINUTES si True

@dataclass(frozen=True)
class PullMedusaInput:
    medusa_base_url: str
    medusa_admin_token: str
    page_size: int = 100

@dataclass(frozen=True)
class PullMedusaOutput:
    products: list[dict]        # raw products del API
    fetched_at: str             # ISO timestamp

@dataclass(frozen=True)
class WriteSnapshotInput:
    snapshot_dir: str           # path absoluto
    products: list[dict]
    fetched_at: str

@dataclass(frozen=True)
class WriteSnapshotOutput:
    products_written: int
    snapshot_path: str
```

---

## §4. Workflow (`agent/workflows/sync.py`)

```python
# canonical — agent/workflows/sync.py
from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.plugins.catalog.agent.contracts import (
        CatalogSyncInput,
        PullMedusaInput,
        WriteSnapshotInput,
    )
    from src.platform.temporal.retry_policies import _CONV_OPTIONS

@workflow.defn(name="CatalogSyncWorkflow")
class CatalogSyncWorkflow:
    @workflow.run
    async def run(self, input: CatalogSyncInput) -> int:
        """Pull desde Medusa + write snapshot. Devolve products_written."""
        # Step 1: pull
        pulled = await workflow.execute_activity(
            "pull_medusa_catalog",                # name del @activity.defn
            PullMedusaInput(
                medusa_base_url=workflow.info().workflow_id,    # placeholder; en real viene de config
                medusa_admin_token="...",                       # idem
                page_size=100,
            ),
            **_CONV_OPTIONS,
        )

        # Step 2: write
        written = await workflow.execute_activity(
            "write_snapshot",
            WriteSnapshotInput(
                snapshot_dir="/app/hubara_vault/catalog",
                products=pulled.products,
                fetched_at=pulled.fetched_at,
            ),
            **_CONV_OPTIONS,
        )

        return written.products_written
```

**Notar:**

- Sin signals — el workflow es **one-shot** (pull + write, terminar).
- Sin `_pending` ni debounce — diferente al patrón conversacional.
- 2 activities en serie — simple y directo.

---

## §5. Activities

### §5.1 `pull_medusa.py`

```python
# canonical — agent/activities/pull_medusa.py
from temporalio import activity
import httpx

from src.platform.temporal.heartbeat import with_heartbeat
from src.plugins.catalog.agent.contracts import PullMedusaInput, PullMedusaOutput

@activity.defn(name="pull_medusa_catalog")
@with_heartbeat(every=15)              # Medusa puede tardar minutos en repos grandes
async def pull_medusa_catalog(input: PullMedusaInput) -> PullMedusaOutput:
    activity.logger.info("pulling Medusa catalog from %s", input.medusa_base_url)
    products = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        offset = 0
        while True:
            response = await client.get(
                f"{input.medusa_base_url}/admin/products",
                params={"limit": input.page_size, "offset": offset},
                headers={"Authorization": f"Bearer {input.medusa_admin_token}"},
            )
            response.raise_for_status()
            page = response.json()
            batch = page.get("products", [])
            if not batch:
                break
            products.extend(batch)
            offset += input.page_size
            activity.heartbeat()       # explicit, además del decorator

    return PullMedusaOutput(
        products=products,
        fetched_at=datetime.utcnow().isoformat(),
    )
```

### §5.2 `write_snapshot.py`

```python
# canonical — agent/activities/write_snapshot.py
import json
import os
from pathlib import Path
from temporalio import activity

from src.plugins.catalog.agent.contracts import WriteSnapshotInput, WriteSnapshotOutput

@activity.defn(name="write_snapshot")
async def write_snapshot(input: WriteSnapshotInput) -> WriteSnapshotOutput:
    snapshot_dir = Path(input.snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest.json (atomic via tmp + os.replace)
    manifest = {
        "fetched_at": input.fetched_at,
        "product_count": len(input.products),
    }
    tmp = snapshot_dir / "manifest.json.tmp"
    final = snapshot_dir / "manifest.json"
    tmp.write_text(json.dumps(manifest, indent=2))
    os.replace(tmp, final)              # ATOMIC — esto es por qué replicas: 1

    # Write per-product files
    products_dir = snapshot_dir / "products"
    products_dir.mkdir(exist_ok=True)
    for product in input.products:
        product_id = product["id"]
        tmp = products_dir / f"{product_id}.json.tmp"
        final = products_dir / f"{product_id}.json"
        tmp.write_text(json.dumps(product))
        os.replace(tmp, final)

    return WriteSnapshotOutput(
        products_written=len(input.products),
        snapshot_path=str(snapshot_dir),
    )
```

---

## §6. Worker (`workers/sync.py`)

```python
# canonical — workers/sync.py
import asyncio

from loguru import logger
from temporalio.worker import Worker

from src.platform.plugin_manifest import get_task_queue
from src.platform.logging import setup_logging
from src.platform.temporal.client import get_temporal_client

# Plugin imports
from src.plugins.catalog.agent.activities.pull_medusa import pull_medusa_catalog
from src.plugins.catalog.agent.activities.write_snapshot import write_snapshot
from src.plugins.catalog.agent.workflows.sync import CatalogSyncWorkflow

setup_logging()


async def main() -> None:
    logger.info("Conectando worker catalog-sync a Temporal...")
    client = await get_temporal_client()
    task_queue = get_task_queue("catalog", "sync")          # post-PR11
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[CatalogSyncWorkflow],
        activities=[pull_medusa_catalog, write_snapshot],
    )
    logger.info("catalog-sync worker up. Queue: '{}'", task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## §7. K8s deployment (`k8s/aws-produccion/worker-catalog-sync.yaml`)

```yaml
# canonical — worker-catalog-sync.yaml (resumido)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hubara-worker-catalog-sync
  labels: { app: hubara, role: worker, plugin: catalog, worker: sync }
spec:
  replicas: 1                                # SINGLE writer
  strategy: { type: Recreate }               # evita overlap durante rollout
  selector:
    matchLabels: { app: hubara, role: worker, plugin: catalog, worker: sync }
  template:
    metadata:
      labels: { app: hubara, role: worker, plugin: catalog, worker: sync }
    spec:
      containers:
        - name: worker
          image: hubara-agency-prod:latest
          command: ["python", "-m", "hubara_agency.src.plugins.catalog.workers.sync"]
          env:
            - name: WORKSPACE_VAULT_DIR
              value: /app/hubara_vault
            - name: CATALOG_SNAPSHOT_DIR
              value: /app/hubara_vault/catalog
            - name: TEMPORAL_URL
              value: temporal.svc:7233
            - name: MEDUSA_BASE_URL
              valueFrom: { secretKeyRef: { name: hubara-medusa-secret, key: MEDUSA_BASE_URL } }
            - name: MEDUSA_ADMIN_TOKEN
              valueFrom: { secretKeyRef: { name: hubara-medusa-secret, key: MEDUSA_ADMIN_TOKEN } }
          volumeMounts:
            - name: vault
              mountPath: /app/hubara_vault
          resources:
            requests: { cpu: 100m, memory: 256Mi }
            limits:   { cpu: 500m, memory: 512Mi }
      volumes:
        - name: vault
          persistentVolumeClaim: { claimName: hubara-vault-efs }
```

**Notar:**

- `command` apunta exactamente al módulo del worker.
- `valueFrom.secretKeyRef` mapea desde el K8s Secret (consistente con
  `deployment.env_secrets` del manifest).
- PVC compartido con otros workers (sub-namespace `catalog/` aislado por path).

---

## §8. Tests

### §8.1 Test del workflow (replay-safe)

```python
# canonical — tests/plugins/catalog/workflows/test_sync_replay.py
import json
from pathlib import Path
import pytest
from temporalio.client import WorkflowFailureError
from temporalio.worker import Worker
from temporalio.testing import WorkflowEnvironment

from src.plugins.catalog.agent.workflows.sync import CatalogSyncWorkflow
from src.plugins.catalog.agent.contracts import (
    CatalogSyncInput, PullMedusaInput, PullMedusaOutput,
    WriteSnapshotInput, WriteSnapshotOutput,
)

@pytest.mark.functional
async def test_catalog_sync_happy_path(tmp_path):
    # Fakes para las activities
    async def fake_pull(input: PullMedusaInput) -> PullMedusaOutput:
        return PullMedusaOutput(
            products=[{"id": "p1", "name": "Producto 1"}],
            fetched_at="2026-05-17T00:00:00Z",
        )

    async def fake_write(input: WriteSnapshotInput) -> WriteSnapshotOutput:
        return WriteSnapshotOutput(
            products_written=len(input.products),
            snapshot_path=str(tmp_path),
        )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-catalog",
            workflows=[CatalogSyncWorkflow],
            activities=[fake_pull, fake_write],
        ):
            result = await env.client.execute_workflow(
                CatalogSyncWorkflow.run,
                CatalogSyncInput(triggered_by="test"),
                id="test-catalog-1",
                task_queue="test-catalog",
            )
            assert result == 1
```

### §8.2 Test del activity `write_snapshot`

```python
# canonical — tests/plugins/catalog/activities/test_write_snapshot.py
import json
from temporalio.testing import ActivityEnvironment
from src.plugins.catalog.agent.activities.write_snapshot import write_snapshot
from src.plugins.catalog.agent.contracts import WriteSnapshotInput

async def test_write_snapshot_atomic(tmp_path):
    env = ActivityEnvironment()
    result = await env.run(
        write_snapshot,
        WriteSnapshotInput(
            snapshot_dir=str(tmp_path),
            products=[{"id": "p1", "name": "Test"}],
            fetched_at="2026-05-17T00:00:00Z",
        ),
    )
    assert result.products_written == 1
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["product_count"] == 1
    product = json.loads((tmp_path / "products" / "p1.json").read_text())
    assert product["name"] == "Test"
```

---

## §9. Script de trigger manual (`scripts/trigger_catalog_sync.py`)

```python
# canonical — scripts/trigger_catalog_sync.py (CLI dev)
import asyncio
import argparse

from src.platform.plugin_manifest import get_task_queue
from src.platform.temporal.client import get_temporal_client
from src.plugins.catalog.agent.contracts import CatalogSyncInput
from src.plugins.catalog.agent.workflows.sync import CatalogSyncWorkflow


async def main(no_wait: bool, force: bool) -> None:
    client = await get_temporal_client()
    handle = await client.start_workflow(
        CatalogSyncWorkflow.run,
        CatalogSyncInput(triggered_by="manual", force=force),
        id=f"catalog-sync-manual-{int(asyncio.get_event_loop().time())}",
        task_queue=get_task_queue("catalog", "sync"),
    )
    print(f"started workflow id={handle.id}")
    if not no_wait:
        result = await handle.result()
        print(f"products written: {result}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.no_wait, args.force))
```

---

## §10. Verificación

```bash
# Backend
cd hubara_agency
uv sync

# Smoke
uv run python -c "import src.plugins.catalog.workers.sync"
uv run python -c "from src.platform.plugin_manifest import get_task_queue; print(get_task_queue('catalog', 'sync'))"
# → queue-catalog-sync

# Tests
uv run pytest tests/plugins/catalog/ -v
uv run pytest -m architecture
uv run pytest tests/plugins/ -v          # premortem invariants

# Boot del worker
ENABLED_PLUGINS=catalog uv run python -m src.plugins.catalog.workers.sync

# Trigger manual (en otra terminal)
uv run python scripts/trigger_catalog_sync.py

# Frontend
cd ../frontend_dashboard
npm run plugins:sync     # registry incluye catalog
npm run dev              # visible en /catalog tab
```

---

## §11. Pros y limitaciones del template C

| Pro | Limitación |
|---|---|
| Workflows long-running con retry sofisticado | Sin endpoint propio HTTP (trigger via CLI o otro plugin) |
| Heartbeat para activities long | Configuración K8s más compleja (secrets, single-writer) |
| Idempotente atomic writes via os.replace | replicas: 1 → no horizontal scaling |
| Tests con WorkflowEnvironment | Requiere Temporal + Worker para tests E2E |

---

**Fin example.**
