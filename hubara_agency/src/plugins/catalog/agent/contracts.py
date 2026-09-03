"""Boundary DTOs del catalog_sync (R-JSON).

`products_json: str` aplica el JSON-string trick (gotcha #6 del DEHA arch)
para transferir listas grandes a traves del workflow boundary sin tipos
complejos anidados.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogSyncInput:
    """Input del CatalogSyncWorkflow.

    `snapshot_dir`: resuelto por el caller (Schedule script) o por la
    activity de fallback. Cruzar como `str` cumple R-JSON. Si llega vacio,
    `write_snapshot_activity` lo resuelve desde env via
    `src/platform/catalog/paths.py:get_snapshot_dir()` (la activity es el
    unico legitimado para leer env — R-DET prohibe hacerlo desde
    `@workflow.run`).
    """

    tenant_id: str = "default"
    force_full_refresh: bool = True
    snapshot_dir: str = ""


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


# ---------- Meta Catalog sync (HU-002 Parte B) ----------


@dataclass(frozen=True)
class PushMetaCatalogInput:
    """Input para `PushMetaCatalogUseCase`.

    `products_json`: viene del `PullCatalogResult` que ya tenés en memoria
    en el workflow. Reusar evita un segundo pull contra Medusa.

    `catalog_id` + `system_user_token`: por tenant. Vivos en agents_admin
    plugin; el workflow los inyecta como string en el input (R-JSON).

    `previous_meta_hashes_json`: dict[retailer_id, sha256_hash] del último
    push exitoso, persistido en el snapshot. Se usa para detectar updates
    incrementales (solo se manda CREATE/UPDATE si el hash cambió). Si está
    vacío, se hace push full.

    `site_base_url`: para construir el `url` de cada item en Meta.
    """

    tenant_id: str
    catalog_id: str
    system_user_token: str
    products_json: str
    previous_meta_hashes_json: str = "{}"
    site_base_url: str = "https://hubara.com.co"
    brand: str = "Hubara"
    soft_delete_threshold_ratio: float = 0.5  # ver B.8 — no borrar si pull < 50% último count
    last_meta_count: int = 0  # si > 0, validar threshold


@dataclass(frozen=True)
class PushMetaCatalogResult:
    """Resultado del push.

    `handle`: el id del batch en Meta (para poll posterior).
    `next_meta_hashes_json`: el dict actualizado de hashes — persiste en
    snapshot para el próximo sync incremental.
    """

    ok: bool
    handle: str | None
    creates: int
    updates: int
    deletes: int
    skipped_image: int
    skipped_price: int
    skipped_collection: int  # ya filtrados por pull (allowlist); tracking solo
    aborted_due_to_threshold: bool
    error: str | None
    next_meta_hashes_json: str
    duration_seconds: float


# ---------- Activity boundary (Meta push) ----------


@dataclass(frozen=True)
class PushMetaActivityInput:
    """Input al `push_meta_catalog_activity`.

    NO incluye `catalog_id` ni `system_user_token` — esos son secretos y
    no deben vivir en el workflow event history (Temporal los persiste).
    La activity los lee de env (META_CATALOG_ID / META_SYSTEM_USER_TOKEN)
    como hace `write_snapshot_activity` con `CATALOG_SNAPSHOT_DIR`. R-DET:
    activities pueden leer env, workflows no.

    `snapshot_dir`: para leer `.meta_state.json` (previous_hashes,
    last_meta_count) y persistir el state nuevo después del push exitoso.

    `force_full_refresh`: si True, ignora los `previous_hashes` del state file
    → todos los items se re-pushean como CREATE (Meta re-encola el fetch de
    imágenes). Recuperación cuando Meta falla el fetch async de una imagen sin
    que cambien los datos del producto (el delta por hash lo saltearía como
    "sin cambios"). El botón "Sincronizar" del dashboard lo manda en True.
    """

    tenant_id: str
    products_json: str
    snapshot_dir: str
    site_base_url: str = "https://hubara.com.co"
    brand: str = "Hubara"
    force_full_refresh: bool = False


@dataclass(frozen=True)
class PushMetaActivityResult:
    """Resultado de la activity. Subset de `PushMetaCatalogResult` + flag
    `pushed` para distinguir "config missing" de "config OK pero no había
    cambios".
    """

    ok: bool
    pushed: bool  # False si META_CATALOG_ID / token vacíos → graceful skip
    handle: str | None
    creates: int
    updates: int
    deletes: int
    skipped_image: int
    skipped_price: int
    aborted_due_to_threshold: bool
    error: str | None
    duration_seconds: float
    # Cuántos catálogos recibieron el batch (primario + META_EXTRA_CATALOG_IDS).
    # 0 en graceful skip. Los contadores creates/updates/deletes están SUMADOS
    # sobre todos los catálogos (1 item x N catálogos = N creates).
    catalogs_pushed: int = 1
    # Desglose por catálogo (R-JSON): {catalog_id: {ok, handle, creates,
    # updates, deletes, error}} en el orden primario → réplicas. Los
    # contadores de arriba están sumados; acá está QUIÉN recibió qué.
    per_catalog_json: str = "{}"


# ---------- Workflow result ----------


@dataclass(frozen=True)
class CatalogSyncResult:
    """Resultado completo del `CatalogSyncWorkflow`.

    Incluye ambos pasos (snapshot write + Meta push) para que el caller
    (script ops o el futuro `product_sync_agent`) tenga visibilidad total
    del run. Si Meta no está configurado, `push.pushed=False` y el resto
    de `push.*` son zero — el snapshot sigue válido.
    """

    write: WriteSnapshotResult
    push: PushMetaActivityResult
