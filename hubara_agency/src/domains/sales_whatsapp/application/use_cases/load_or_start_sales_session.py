"""Use case: decide la ruta activa (Sales vs Remarketing) y se asegura de que
exista un workflow handle vivo, signalandole el mensaje del usuario.

Equivalente al legado `service._signal_temporal_and_poll`. La diferencia es
que aqui:

* `metadata.json` se accede via `MetadataStorePort` (no `Path.read_text`).
* El `BrainLoaderPort` y los `brain_dir` se inyectan (no se hardcodea
  `Path(__file__).parent / "shared_brain"`).
* La conexion al cluster Temporal entra como `client_factory` async
  (idealmente `get_temporal_client`).

Esto permite testear el use case con fakes sin levantar Temporal y sin tocar
filesystem real (NEW-2 cerrado).

Deuda consciente (R-DIP parcial): este use case importa `temporalio.client`
y los workflow classes directamente. El alcance de F9 acepta esto (la tarea
dice explicitamente "NO introduce activities nuevas; las llamadas a Temporal
siguen siendo client.start_workflow / handle.signal desde el handler HTTP").
Un futuro PR podria abstraer un `WorkflowDispatcherPort` con
`get_handle / start / signal` para invertir esta dependencia 100%.
"""
from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable

import structlog
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError

from src.core.constants import ROUTE_REMARKETING, ROUTE_VENTAS, SALES_QUEUE
from src.core.ports.brain_loader import BrainLoaderPort
from src.domains.remarketing_whatsapp.workflows.remarketing import (
    RemarketingSessionWorkflow,
)
from src.domains.sales_whatsapp.application.ports.metadata_store import (
    MetadataStorePort,
)
from src.domains.sales_whatsapp.contracts import SalesSessionInput
from src.domains.sales_whatsapp.workflows.sales_session import HubaraSalesSessionWorkflow

logger = structlog.get_logger()


ClientFactory = Callable[[], Awaitable[Client]]


class LoadOrStartSalesSession:
    """Resuelve la ruta del mensaje y asegura que el workflow correcto este corriendo.

    Reglas (heredadas del legado):

    * `metadata.active_route == "remarketing"`:  intentar reusar
      `remarketing-{session_id}`. Si existe y esta RUNNING, signalear; si esta
      muerto, fallback a Sales.
    * Caso contrario (default `ventas`): reusar `session-{session_id}` o
      `start_workflow(HubaraSalesSessionWorkflow.run, SalesSessionInput(...))`.
    * En ambos casos: `signal(workflow_class.send_message, [message, None, brain])`.
    """

    def __init__(
        self,
        client_factory: ClientFactory,
        metadata_store: MetadataStorePort,
        sales_brain_loader: BrainLoaderPort,
        remarketing_brain_loader: BrainLoaderPort,
        sales_brain_dir: Path,
        remarketing_brain_dir: Path,
    ) -> None:
        self._client_factory = client_factory
        self._metadata_store = metadata_store
        self._sales_brain_loader = sales_brain_loader
        self._remarketing_brain_loader = remarketing_brain_loader
        self._sales_brain_dir = sales_brain_dir
        self._remarketing_brain_dir = remarketing_brain_dir

    async def execute(
        self,
        session_id: str,
        message: str,
        phone_number_id: str | None,
    ) -> None:
        # 1. Resolver ruta y persistir phone_number_id (lectura + posible escritura).
        data = self._metadata_store.read(session_id)
        active_route = data.get("active_route", ROUTE_VENTAS)

        if phone_number_id:
            data["phone_number_id"] = phone_number_id
            self._metadata_store.write(session_id, data)

        # 2. Conectar al cluster.
        client = await self._client_factory()

        # 3. Si la ruta activa es remarketing, intentamos reusar; si murio, fallback.
        if active_route == ROUTE_REMARKETING:
            workflow_id = f"remarketing-{session_id}"
            logger.info(
                "Routing webhook to Remarketing Agent", workflow_id=workflow_id
            )
            workflow_class: type = RemarketingSessionWorkflow
            plugin_context = self._remarketing_brain_loader.load(
                self._remarketing_brain_dir
            )

            try:
                handle = client.get_workflow_handle(workflow_id)
                desc = await handle.describe()
                if desc.status != WorkflowExecutionStatus.RUNNING:
                    raise RuntimeError("Remarketing workflow is no longer running")
            except (RPCError, RuntimeError):
                logger.warning(
                    "Remarketing workflow not found or finished, falling back to Sales"
                )
                active_route = ROUTE_VENTAS

        # 4. Caso default (o fallback): rutear a Sales.
        if active_route != ROUTE_REMARKETING:
            workflow_id = f"session-{session_id}"
            logger.info("Routing webhook to Sales Agent", workflow_id=workflow_id)
            workflow_class = HubaraSalesSessionWorkflow
            plugin_context = self._sales_brain_loader.load(self._sales_brain_dir)

            try:
                handle = client.get_workflow_handle(workflow_id)
                desc = await handle.describe()
                if desc.status != WorkflowExecutionStatus.RUNNING:
                    raise RuntimeError("Workflow is no longer running")
            except (RPCError, RuntimeError):
                logger.info(
                    "Creando HubaraSalesSessionWorkflow", workflow_id=workflow_id
                )
                # F7: el caller ya no construye SessionInput. El workflow lo arma
                # via `bootstrap_sales_session_activity` como primera activity.
                handle = await client.start_workflow(
                    HubaraSalesSessionWorkflow.run,
                    SalesSessionInput(session_id=session_id),
                    id=workflow_id,
                    task_queue=SALES_QUEUE,
                )

        # 5. Signal del mensaje al workflow seleccionado.
        await handle.signal(
            workflow_class.send_message,  # type: ignore[attr-defined]
            args=[message, None, plugin_context],
        )
