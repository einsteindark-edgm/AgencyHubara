"""pull_medusa_catalog_activity — paginar Medusa, mapear a DTOs.

Heartbeat cada 10s (R-HEARTBEAT) porque la paginacion puede tardar >10s
en catalogos grandes.
"""
from __future__ import annotations

from temporalio import activity

from src.plugins.catalog.agent.composition import get_pull_catalog_use_case
from src.plugins.catalog.agent.contracts import CatalogSyncInput, PullCatalogResult
from src.platform.temporal.heartbeat import with_heartbeat


@activity.defn(name="pull_medusa_catalog")
@with_heartbeat(every=10)
async def pull_medusa_catalog_activity(
    input: CatalogSyncInput,
) -> PullCatalogResult:
    use_case = get_pull_catalog_use_case()  # R-STATELESS via factory
    activity.logger.info(
        "pull_medusa_catalog start tenant=%s", input.tenant_id
    )
    result = await use_case.execute(input)
    activity.logger.info(
        "pull_medusa_catalog done count=%d", result.count
    )
    return result
