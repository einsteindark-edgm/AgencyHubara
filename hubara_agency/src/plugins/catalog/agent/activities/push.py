"""push_meta_catalog_activity — propaga el snapshot a Meta Commerce Catalog.

Diseño:
  * Lee `META_CATALOG_ID` y `META_SYSTEM_USER_TOKEN` de env (R-DET permite a
    activities, no a workflows). Si alguno está vacío, **graceful skip**:
    devuelve `pushed=False, ok=True` (no es failure — solo no está
    configurado en este ambiente). El sync sigue OK; el snapshot local
    es la verdad para el agente sales.
  * `META_EXTRA_CATALOG_IDS` (opcional, CSV): catálogos RÉPLICA que reciben
    exactamente el mismo batch que el primario. Caso real: el número en
    coexistencia (+57 322 9041190) tiene su catálogo atado a la app del
    teléfono (`2507823826404263`) y Meta NO permite conectarle el catálogo
    primario (`(#10) SMB business type`) — la única forma de mantenerlo igual
    es pushear a los dos. `META_CATALOG_ID` sigue siendo UNO porque el worker
    de ventas lo usa para las tarjetas `product_list` de WhatsApp.
  * Lee `<snapshot_dir>/.meta_state.json` para recuperar `previous_hashes`
    y `last_meta_count` del push anterior. Sin state previo, hace push
    full. Cada réplica tiene su propio state
    (`.meta_state.<catalog_id>.json`): el delta es POR catálogo, así una
    réplica recién agregada recibe push full sin tocar el primario.
  * Invoca `PushMetaCatalogUseCase.execute(...)` — la lógica de delta +
    soft-delete protection vive ahí.
  * Si el push fue OK, persiste el state de ESE catálogo para el próximo run.
  * Si falló, deja el state previo intacto (próximo run re-intenta los
    mismos cambios). Una réplica que falla NO bloquea al primario ni a las
    otras: se reporta `ok=False` + `error` con el catálogo que falló.

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
    PushMetaCatalogResult,
)
from src.platform.temporal.heartbeat import with_heartbeat

# Nombre del archivo de state donde persistimos hashes + last_count.
# Vive junto al snapshot.json para co-locarlos (un único filesystem mount
# por tenant) y para mantener el state "atado" al snapshot que lo originó
# (si se hace rollback del snapshot, el state queda en sync).
_STATE_FILENAME = ".meta_state.json"


def resolve_catalog_ids(env: dict[str, str] | os._Environ[str]) -> list[str]:
    """Primario + réplicas, en orden, sin vacíos ni duplicados.

    Vacío si `META_CATALOG_ID` no está (las réplicas solas no alcanzan: sin
    primario no hay catálogo para el bot de ventas).
    """
    primary = (env.get("META_CATALOG_ID") or "").strip()
    if not primary:
        return []
    ids = [primary]
    for raw in (env.get("META_EXTRA_CATALOG_IDS") or "").split(","):
        cid = raw.strip()
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def state_path_for(snapshot_dir: str, catalog_id: str, *, primary: bool) -> Path:
    """`.meta_state.json` para el primario (compat con el state ya existente
    en prod); `.meta_state.<catalog_id>.json` para cada réplica."""
    if primary:
        return Path(snapshot_dir) / _STATE_FILENAME
    return Path(snapshot_dir) / f".meta_state.{catalog_id}.json"


def _read_state(state_path: Path) -> tuple[str, int]:
    """(previous_hashes_json, last_meta_count). State ausente o corrupto →
    push full ({} / 0) con warning."""
    if not state_path.exists():
        return "{}", 0
    try:
        state_raw = json.loads(state_path.read_text(encoding="utf-8"))
        previous_hashes = state_raw.get("previous_hashes") or {}
        if not isinstance(previous_hashes, dict):
            previous_hashes = {}
        return json.dumps(previous_hashes), int(state_raw.get("last_meta_count") or 0)
    except (json.JSONDecodeError, ValueError, TypeError, OSError) as e:
        activity.logger.warning(
            "push_meta_catalog: corrupt state file at %s: %s — "
            "proceeding with full push (previous_hashes={}, last_count=0)",
            state_path,
            e,
        )
        return "{}", 0


def _persist_state(
    state_path: Path, result: PushMetaCatalogResult, products_json: str
) -> None:
    """Persistir el state nuevo SOLO si el push fue OK. Si falló, dejar el
    state previo intacto — el próximo run re-intenta los mismos deltas
    (idempotente por retailer_id)."""
    if not (result.ok and not result.aborted_due_to_threshold):
        return
    try:
        current_products = json.loads(products_json)
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


async def _push_one_catalog(
    input: PushMetaActivityInput,
    *,
    catalog_id: str,
    token: str,
    snapshot_dir: str,
    primary: bool,
) -> PushMetaCatalogResult:
    state_path = state_path_for(snapshot_dir, catalog_id, primary=primary)
    previous_hashes_json, last_meta_count = _read_state(state_path)

    # force_full_refresh: ignorar los hashes previos → todos los items van como
    # CREATE (re-push completo). Recupera fetches de imagen que Meta falló sin
    # que cambien los datos (el delta por hash los saltearía). Mantenemos
    # `last_meta_count` para que el guard soft-delete (regla B.8) siga activo.
    if input.force_full_refresh:
        previous_hashes_json = "{}"

    activity.logger.info(
        "push_meta_catalog start tenant=%s catalog_id=%s primary=%s token_len=%d "
        "snapshot_dir=%s prev_hashes=%d last_count=%d",
        input.tenant_id,
        catalog_id,
        primary,
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
        "push_meta_catalog done catalog_id=%s ok=%s handle=%s creates=%d "
        "updates=%d deletes=%d skipped_image=%d skipped_price=%d "
        "aborted_threshold=%s error=%s duration=%.2fs",
        catalog_id,
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

    _persist_state(state_path, result, input.products_json)
    return result


@activity.defn(name="push_meta_catalog")
@with_heartbeat(every=10)
async def push_meta_catalog_activity(
    input: PushMetaActivityInput,
) -> PushMetaActivityResult:
    catalog_ids = resolve_catalog_ids(os.environ)
    token = (os.environ.get("META_SYSTEM_USER_TOKEN") or "").strip()

    if not catalog_ids or not token:
        activity.logger.warning(
            "push_meta_catalog skipped: META_CATALOG_ID=%s "
            "META_SYSTEM_USER_TOKEN=%s — graceful skip",
            "set" if catalog_ids else "MISSING",
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
            catalogs_pushed=0,
        )

    # Fallback de `snapshot_dir` desde env (espejo de write_snapshot_activity).
    # Necesario cuando el caller workflow no resuelve el path (CatalogSyncInput
    # con snapshot_dir=""). R-DET permite env reads en activities.
    snapshot_dir = input.snapshot_dir
    if not snapshot_dir:
        from src.platform.catalog.paths import get_snapshot_dir

        snapshot_dir = str(get_snapshot_dir())

    results: list[tuple[str, PushMetaCatalogResult]] = []
    for idx, catalog_id in enumerate(catalog_ids):
        result = await _push_one_catalog(
            input,
            catalog_id=catalog_id,
            token=token,
            snapshot_dir=snapshot_dir,
            primary=(idx == 0),
        )
        results.append((catalog_id, result))

    # Agregado: contadores sumados (1 item x N catálogos = N creates), `ok`
    # solo si TODOS los catálogos OK, `error` nombra el/los catálogos que
    # fallaron, `handle` = el del primario (el que mira el dashboard).
    errors = [
        f"{cid}: {r.error}" for cid, r in results if not r.ok and r.error
    ]
    primary_result = results[0][1]
    return PushMetaActivityResult(
        ok=all(r.ok for _, r in results),
        pushed=True,
        handle=primary_result.handle
        or next((r.handle for _, r in results if r.handle), None),
        creates=sum(r.creates for _, r in results),
        updates=sum(r.updates for _, r in results),
        deletes=sum(r.deletes for _, r in results),
        skipped_image=primary_result.skipped_image,
        skipped_price=primary_result.skipped_price,
        aborted_due_to_threshold=any(r.aborted_due_to_threshold for _, r in results),
        error="; ".join(errors) if errors else None,
        duration_seconds=sum(r.duration_seconds for _, r in results),
        catalogs_pushed=len(results),
    )
