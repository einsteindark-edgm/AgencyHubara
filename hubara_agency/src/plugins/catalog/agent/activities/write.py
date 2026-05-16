"""write_snapshot_activity — escritura atomica a filesystem.

Es la activity que LEGITIMAMENTE puede leer env (`get_snapshot_dir()`)
cuando el caller no resolvio el path (`snapshot_dir` vacio en input).
Workflow nunca lee env (R-DET).
"""
from __future__ import annotations

from dataclasses import replace

from temporalio import activity

from src.plugins.catalog.agent.composition import get_write_snapshot_use_case
from src.plugins.catalog.agent.contracts import WriteSnapshotInput, WriteSnapshotResult


@activity.defn(name="write_snapshot")
async def write_snapshot_activity(
    input: WriteSnapshotInput,
) -> WriteSnapshotResult:
    # Resolucion de env como fallback — el caller (Schedule / smoke script)
    # normalmente ya lo resuelve para auditabilidad.
    if not input.snapshot_dir:
        from src.platform.catalog.paths import get_snapshot_dir

        input = replace(input, snapshot_dir=str(get_snapshot_dir()))

    use_case = get_write_snapshot_use_case()
    activity.logger.info(
        "write_snapshot start dir=%s count=%d",
        input.snapshot_dir,
        input.count,
    )
    result = await use_case.execute(input)
    activity.logger.info(
        "write_snapshot done version=%s bytes=%d files=%d",
        result.version,
        result.bytes_written,
        result.files_written,
    )
    return result
