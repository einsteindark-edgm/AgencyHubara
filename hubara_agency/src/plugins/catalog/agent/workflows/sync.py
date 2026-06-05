"""CatalogSyncWorkflow — disparado ON-DEMAND por un caller externo.

Three-step: pull desde Medusa → write atomico del snapshot → push a Meta
Commerce Catalog. Sin signals, sin continue-as-new. Expone UNA query
read-only (`progress`) para que el dashboard pinte el step-by-step en
vivo de los 3 pasos reales — la query no muta state ni afecta el
determinismo (R-DET). El snapshot ES el state durable; Meta es una
proyección eventualmente consistente del snapshot.

────────────────────────────────────────────────────────────────────────
CÓMO SE DISPARA (v1)
────────────────────────────────────────────────────────────────────────
NO hay Temporal Schedule periódico. El workflow se dispara on-demand
después de cualquier mutación del catálogo en Medusa (create/update/
delete de producto). Quien debería disparar:

  - Ops manualmente:
      uv run python scripts/trigger_catalog_sync.py

  - El futuro `product_sync_agent` (cuando exista): cada vez que termine
    un workflow de update de productos, debe ejecutar (dentro de una
    `@activity.defn` — el cliente Temporal NO puede vivir en un workflow
    por R-DIP):

        from src.platform.temporal.client import get_temporal_client
        from src.plugins.catalog.agent.workflows import CatalogSyncWorkflow
        from src.plugins.catalog.agent.contracts import CatalogSyncInput
        from src.platform.catalog.paths import get_snapshot_dir
        from src.platform.plugin_manifest import get_task_queue

        client = await get_temporal_client()
        handle = await client.start_workflow(
            CatalogSyncWorkflow.run,
            CatalogSyncInput(
                tenant_id="default",
                force_full_refresh=True,
                snapshot_dir=str(get_snapshot_dir()),
            ),
            id=f"catalog-sync-on-demand-{cause}-{int(time.time())}",
            task_queue=get_task_queue("catalog", "sync"),
        )
        result = await handle.result()  # opcional: esperar version

  - Ver `scripts/trigger_catalog_sync.py` para el patron completo.

────────────────────────────────────────────────────────────────────────
META PUSH — semántica del paso 3
────────────────────────────────────────────────────────────────────────
El paso `push_meta_catalog_activity` lee las credenciales Meta de env
(`META_CATALOG_ID` + `META_SYSTEM_USER_TOKEN`). Si alguna falta, hace
**graceful skip**: `push.pushed=False, push.ok=True`. El sync no falla —
el snapshot local sigue válido y los agentes (sales, remarketing) leen
de ahí. Esto permite tener Meta apagado en dev local sin romper el
workflow.

R-JSON: el token NUNCA entra al input del workflow (Temporal lo
persistiría en el event history). La activity lo lee de env, R-DET lo
permite.

────────────────────────────────────────────────────────────────────────
RAZÓN: por qué NO usamos Schedule periódico
────────────────────────────────────────────────────────────────────────
Una Schedule cada N min introduce ventanas de staleness (entre que
algo cambia en Medusa y el siguiente tick). Trigger-by-event reduce
esa ventana a segundos y elimina sync innecesario cuando no hay
cambios. Si en el futuro hace falta detectar cambios externos a
nuestra API (ej. Medusa Admin Web), añadir un Schedule complementario
es trivial — el workflow no cambia.

────────────────────────────────────────────────────────────────────────
R-DET: cero `time.time()`, cero `datetime.now()`, cero `os.environ`. El
`snapshot_dir` cruza por input (lo resuelve el caller — script o
activity caller — o la activity como fallback si llega vacio). Las
credenciales Meta las lee la activity push, no el workflow.
"""
from __future__ import annotations

import json

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.plugins.catalog.agent.activities import (
        pull_medusa_catalog_activity,
        push_meta_catalog_activity,
        write_snapshot_activity,
    )
    from src.plugins.catalog.agent.contracts import (
        CatalogSyncInput,
        CatalogSyncResult,
        PushMetaActivityInput,
        WriteSnapshotInput,
    )
    from src.platform.temporal.retry_policies import (
        _CONV_OPTIONS,
        _TOOL_OPTIONS,
    )


@workflow.defn(name="CatalogSyncWorkflow")
class CatalogSyncWorkflow:
    """Sync Medusa → copia local → Meta, con progreso observable.

    El estado de progreso (`self._steps` + `self._phase`) se inicializa en
    `__init__` (determinístico, sin args) y SOLO se muta dentro de `run`
    antes/después de cada activity — nunca desde la query. La query
    `progress` lo serializa para que el dashboard pinte el step-by-step real.
    """

    # Etiquetas estables de los 3 pasos reales (las consume el frontend).
    _STEP_LABELS = (
        ("pull", "Traer productos de Medusa"),
        ("write", "Escribir copia local (snapshot)"),
        ("push", "Propagar a Meta Commerce Catalog"),
    )

    def __init__(self) -> None:
        self._phase: str = "starting"
        self._product_count: int = 0
        self._version: str = ""
        self._error: str | None = None
        self._steps: list[dict] = [
            {"key": key, "label": label, "status": "pending", "detail": ""}
            for key, label in self._STEP_LABELS
        ]

    def _set_step(self, key: str, status: str, detail: str = "") -> None:
        for step in self._steps:
            if step["key"] == key:
                step["status"] = status
                if detail:
                    step["detail"] = detail
                break

    @workflow.query(name="progress")
    def progress(self) -> str:
        """JSON-string del progreso (R-JSON: evita dataclasses anidados en el
        cliente). Lo consume `GET /api/catalog/sync/{id}`."""
        return json.dumps(
            {
                "phase": self._phase,
                "product_count": self._product_count,
                "version": self._version,
                "error": self._error,
                "steps": self._steps,
            }
        )

    @workflow.run
    async def run(self, input: CatalogSyncInput) -> CatalogSyncResult:
        # 1) Pull (activity con heartbeat).
        self._phase = "pulling"
        self._set_step("pull", "running")
        pull_result = await workflow.execute_activity(
            pull_medusa_catalog_activity,
            input,
            **_TOOL_OPTIONS,
        )
        self._product_count = pull_result.count
        self._set_step("pull", "done", f"{pull_result.count} productos")

        # 2) Write atomico del snapshot.
        self._phase = "writing"
        self._set_step("write", "running")
        write_input = WriteSnapshotInput(
            products_json=pull_result.products_json,
            count=pull_result.count,
            fetched_at=pull_result.fetched_at,
            snapshot_dir=input.snapshot_dir,  # caller-provided o "" → activity fallback
            source_etag=pull_result.source_etag,
        )
        write_result = await workflow.execute_activity(
            write_snapshot_activity,
            write_input,
            **_CONV_OPTIONS,
        )
        self._version = write_result.version
        self._set_step(
            "write",
            "done",
            f"{write_result.files_written} archivos · v{write_result.version[:8]}",
        )

        # 3) Push a Meta Commerce Catalog. Graceful skip si env no configurado.
        # Reusamos `products_json` de pull (no re-leemos snapshot — ya está en
        # memoria del workflow). La activity lee/escribe `.meta_state.json`
        # en `snapshot_dir` para hashes incrementales y last_meta_count.
        # Usamos el `snapshot_dir` ya resuelto en write_input para que coincida
        # con el del snapshot escrito (no el `input.snapshot_dir` que podría
        # estar vacío y haber sido resuelto por fallback en write_snapshot).
        self._phase = "pushing"
        self._set_step("push", "running")
        push_input = PushMetaActivityInput(
            tenant_id=input.tenant_id,
            products_json=pull_result.products_json,
            snapshot_dir=write_input.snapshot_dir,
        )
        push_result = await workflow.execute_activity(
            push_meta_catalog_activity,
            push_input,
            **_TOOL_OPTIONS,
        )
        if push_result.pushed:
            self._set_step(
                "push",
                "done",
                f"{push_result.creates} nuevos · {push_result.updates} actualizados",
            )
        else:
            reason = (
                "Meta no configurado"
                if push_result.error == "meta_not_configured"
                else (push_result.error or "sin cambios")
            )
            self._set_step("push", "skipped", f"omitido — {reason}")

        self._phase = "completed"
        return CatalogSyncResult(write=write_result, push=push_result)
