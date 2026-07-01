"""push_meta_catalog_activity — propaga el snapshot a Meta Commerce Catalog.

Diseño:
  * Lee `META_CATALOG_ID` y `META_SYSTEM_USER_TOKEN` de env (R-DET permite a
    activities, no a workflows). Si alguno está vacío, **graceful skip**:
    devuelve `pushed=False, ok=True` (no es failure — solo no está
    configurado en este ambiente). El sync sigue OK; el snapshot local
    es la verdad para el agente sales.
  * Lee `<snapshot_dir>/.meta_state.json` para recuperar `previous_hashes`
    y `last_meta_count` del push anterior. Sin state previo, hace push
    full.
  * Invoca `PushMetaCatalogUseCase.execute(...)` — la lógica de delta +
    soft-delete protection vive ahí.
  * Si el push fue OK, persiste `.meta_state.json` para el próximo run.
  * Si falló, deja el state previo intacto (próximo run re-intenta los
    mismos cambios).

R-HEARTBEAT: usamos `@with_heartbeat(every=10)` porque el batch HTTP a
Graph API puede tardar >10s con catálogos de cientos de items + hashing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from temporalio import activity

from src.plugins.catalog.agent.composition import get_push_meta_catalog_use_case
from src.plugins.catalog.agent.contracts import (
    PushMetaActivityInput,
    PushMetaActivityResult,
    PushMetaCatalogInput,
)
from src.platform.temporal.heartbeat import with_heartbeat

# Nombre del archivo de state donde persistimos hashes + last_count.
# Vive junto al snapshot.json para co-locarlos (un único filesystem mount
# por tenant) y para mantener el state "atado" al snapshot que lo originó
# (si se hace rollback del snapshot, el state queda en sync).
_STATE_FILENAME = ".meta_state.json"


@activity.defn(name="push_meta_catalog")
@with_heartbeat(every=10)
async def push_meta_catalog_activity(
    input: PushMetaActivityInput,
) -> PushMetaActivityResult:
    catalog_id = (os.environ.get("META_CATALOG_ID") or "").strip()
    token = (os.environ.get("META_SYSTEM_USER_TOKEN") or "").strip()

    if not catalog_id or not token:
        activity.logger.warning(
            "push_meta_catalog skipped: META_CATALOG_ID=%s "
            "META_SYSTEM_USER_TOKEN=%s — graceful skip",
            "set" if catalog_id else "MISSING",
            f"set(len={len(token)})" if token else "MISSING",
        )
        return PushMetaActivityResult(
            ok=True,
            pushed=False,
            handle=None,
            creates=0,
            updates=0,
            deletes=0,
            skipped_image=0,
            skipped_price=0,
            aborted_due_to_threshold=False,
            error="meta_not_configured",
            duration_seconds=0.0,
        )

    # Fallback de `snapshot_dir` desde env (espejo de write_snapshot_activity).
    # Necesario cuando el caller workflow no resuelve el path (CatalogSyncInput
    # con snapshot_dir=""). R-DET permite env reads en activities.
    snapshot_dir = input.snapshot_dir
    if not snapshot_dir:
        from src.platform.catalog.paths import get_snapshot_dir

        snapshot_dir = str(get_snapshot_dir())

    state_path = Path(snapshot_dir) / _STATE_FILENAME
    previous_hashes_json = "{}"
    last_meta_count = 0
    if state_path.exists():
        try:
            state_raw = json.loads(state_path.read_text(encoding="utf-8"))
            previous_hashes = state_raw.get("previous_hashes") or {}
            if not isinstance(previous_hashes, dict):
                previous_hashes = {}
            previous_hashes_json = json.dumps(previous_hashes)
            last_meta_count = int(state_raw.get("last_meta_count") or 0)
        except (json.JSONDecodeError, ValueError, TypeError, OSError) as e:
            activity.logger.warning(
                "push_meta_catalog: corrupt state file at %s: %s — "
                "proceeding with full push (previous_hashes={}, last_count=0)",
                state_path,
                e,
            )

    # force_full_refresh: ignorar los hashes previos → todos los items van como
    # CREATE (re-push completo). Recupera fetches de imagen que Meta falló sin
    # que cambien los datos (el delta por hash los saltearía). Mantenemos
    # `last_meta_count` para que el guard soft-delete (regla B.8) siga activo.
    if input.force_full_refresh:
        previous_hashes_json = "{}"

    activity.logger.info(
        "push_meta_catalog start tenant=%s catalog_id=%s token_len=%d "
        "snapshot_dir=%s prev_hashes=%d last_count=%d",
        input.tenant_id,
        catalog_id,
        len(token),
        snapshot_dir,
        len(json.loads(previous_hashes_json)),
        last_meta_count,
    )

    use_case = get_push_meta_catalog_use_case()
    result = await use_case.execute(
        PushMetaCatalogInput(
            tenant_id=input.tenant_id,
            catalog_id=catalog_id,
            system_user_token=token,
            products_json=input.products_json,
            previous_meta_hashes_json=previous_hashes_json,
            site_base_url=input.site_base_url,
            brand=input.brand,
            last_meta_count=last_meta_count,
        )
    )

    activity.logger.info(
        "push_meta_catalog done ok=%s handle=%s creates=%d updates=%d "
        "deletes=%d skipped_image=%d skipped_price=%d aborted_threshold=%s "
        "error=%s duration=%.2fs",
        result.ok,
        result.handle,
        result.creates,
        result.updates,
        result.deletes,
        result.skipped_image,
        result.skipped_price,
        result.aborted_due_to_threshold,
        result.error,
        result.duration_seconds,
    )

    # Persistir el state nuevo SOLO si el push fue OK. Si falló, dejar el
    # state previo intacto — el próximo run re-intenta los mismos deltas
    # (idempotente por retailer_id).
    if result.ok and not result.aborted_due_to_threshold:
        try:
            current_products = json.loads(input.products_json)
            new_state = {
                "previous_hashes": json.loads(result.next_meta_hashes_json),
                "last_meta_count": len(current_products),
                "last_handle": result.handle,
            }
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(new_state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            activity.logger.info(
                "push_meta_catalog: state persisted at %s (hashes=%d, "
                "last_meta_count=%d)",
                state_path,
                len(new_state["previous_hashes"]),
                new_state["last_meta_count"],
            )
        except (OSError, ValueError, TypeError) as e:
            activity.logger.warning(
                "push_meta_catalog: failed to persist state at %s: %s — "
                "próximo run hará push full (no es bloqueante)",
                state_path,
                e,
            )

    return PushMetaActivityResult(
        ok=result.ok,
        pushed=True,
        handle=result.handle,
        creates=result.creates,
        updates=result.updates,
        deletes=result.deletes,
        skipped_image=result.skipped_image,
        skipped_price=result.skipped_price,
        aborted_due_to_threshold=result.aborted_due_to_threshold,
        error=result.error,
        duration_seconds=result.duration_seconds,
    )
