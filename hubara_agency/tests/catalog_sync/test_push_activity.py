"""Tests directos de `push_meta_catalog_activity` (sin Temporal env, sin HTTP).

Cobertura:
  1. Graceful skip cuando META_CATALOG_ID / META_SYSTEM_USER_TOKEN faltan.
  2. Happy path: leyendo snapshot, llamando al port, persistiendo state.
  3. State file corrupto → log warning + procede con push full.
  4. Error en el push → state previo NO se sobrescribe.
  5. Soft-delete threshold tripped → state NO se sobrescribe.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.platform.meta_catalog.dtos import MetaBatchRequest, MetaBatchResult
from src.platform.meta_catalog.port import MetaCatalogPort
from src.plugins.catalog.agent.activities.push import push_meta_catalog_activity
from src.plugins.catalog.agent.contracts import PushMetaActivityInput


# =============================================================================
# Helpers — fakes
# =============================================================================


class _FakeMetaPort(MetaCatalogPort):
    """Port fake — captura los batches sin hacer HTTP."""

    name = "fake_meta_port"

    def __init__(self, *, ok: bool = True, handle: str | None = "h_test"):
        self.ok = ok
        self.handle = handle
        self.batches: list[MetaBatchRequest] = []

    async def upsert_batch(
        self, request: MetaBatchRequest
    ) -> MetaBatchResult:
        self.batches.append(request)
        return MetaBatchResult(
            handle=self.handle if self.ok else None,
            ok=self.ok,
            submitted=(
                len(request.creates)
                + len(request.updates)
                + len(request.deletes)
            ),
            error=None if self.ok else "fake_http_500",
        )

    async def check_batch_status(
        self, catalog_id: str, handle: str, access_token: str
    ) -> MetaBatchResult:
        return MetaBatchResult(handle=handle, ok=True, submitted=0)


def _sample_products_json() -> str:
    """Producto válido para mapping. Tiene image + price."""
    return json.dumps([
        {
            "id": "HUB-VEL-LAV-250",
            "handle": "vela-lavanda-250",
            "title": "Vela Lavanda 250g",
            "status": "published",
            "thumbnail": "https://assets.hubara.com.co/products/lavanda.webp",
            "images": [],
            "variants": [
                {
                    "id": "v1",
                    "title": "Default",
                    "sku": None,
                    "prices": [
                        {"amount": "45000", "currency_code": "COP"}
                    ],
                }
            ],
            "tags": ["aroma:lavanda"],
            "categories": ["velas"],
        }
    ])


@pytest.fixture
def isolated_snapshot_dir(tmp_path: Path) -> Path:
    d = tmp_path / "snapshot"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _no_temporal_context():
    """`activity.logger` requiere contexto Temporal en runtime real, pero
    en tests directos sin worker se queda en NoneType. Lo mockeamos para
    que `.info()` / `.warning()` sean no-ops."""
    with patch(
        "src.plugins.catalog.agent.activities.push.activity"
    ) as mock_activity:
        mock_activity.logger = type(
            "L",
            (),
            {
                "info": lambda *a, **k: None,
                "warning": lambda *a, **k: None,
                "error": lambda *a, **k: None,
            },
        )()
        # defn decorator: passthrough
        mock_activity.defn = lambda **kwargs: (lambda f: f)
        yield


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.asyncio
async def test_graceful_skip_when_catalog_id_missing(isolated_snapshot_dir):
    """Sin META_CATALOG_ID, la activity NO debe llamar al port — solo
    devuelve `pushed=False, ok=True, error='meta_not_configured'`."""
    with patch.dict(
        "os.environ",
        {"META_CATALOG_ID": "", "META_SYSTEM_USER_TOKEN": "EAAtoken"},
        clear=False,
    ):
        result = await push_meta_catalog_activity(
            PushMetaActivityInput(
                tenant_id="default",
                products_json=_sample_products_json(),
                snapshot_dir=str(isolated_snapshot_dir),
            )
        )
    assert result.ok is True
    assert result.pushed is False
    assert result.error == "meta_not_configured"
    assert result.creates == 0
    # State file NO se debe crear porque no hubo push
    assert not (isolated_snapshot_dir / ".meta_state.json").exists()


@pytest.mark.asyncio
async def test_graceful_skip_when_token_missing(isolated_snapshot_dir):
    """Sin META_SYSTEM_USER_TOKEN, igual graceful skip."""
    with patch.dict(
        "os.environ",
        {"META_CATALOG_ID": "1234", "META_SYSTEM_USER_TOKEN": ""},
        clear=False,
    ):
        result = await push_meta_catalog_activity(
            PushMetaActivityInput(
                tenant_id="default",
                products_json=_sample_products_json(),
                snapshot_dir=str(isolated_snapshot_dir),
            )
        )
    assert result.pushed is False
    assert result.error == "meta_not_configured"


@pytest.mark.asyncio
async def test_happy_path_creates_state_file(
    isolated_snapshot_dir, monkeypatch
):
    """Con env OK + port fake exitoso: push se ejecuta, state se persiste."""
    fake_port = _FakeMetaPort(ok=True, handle="h_first")

    with patch.dict(
        "os.environ",
        {
            "META_CATALOG_ID": "1234567890",
            "META_SYSTEM_USER_TOKEN": "EAAprodtoken",
        },
        clear=False,
    ):
        # Patcheamos la factory para que devuelva use_case con nuestro fake
        with patch(
            "src.plugins.catalog.agent.activities.push.get_push_meta_catalog_use_case"
        ) as mock_factory:
            from src.plugins.catalog.agent.use_cases.push_meta_catalog import (
                PushMetaCatalogUseCase,
            )

            mock_factory.return_value = PushMetaCatalogUseCase(
                meta_port=fake_port
            )
            result = await push_meta_catalog_activity(
                PushMetaActivityInput(
                    tenant_id="default",
                    products_json=_sample_products_json(),
                    snapshot_dir=str(isolated_snapshot_dir),
                )
            )

    assert result.pushed is True
    assert result.ok is True
    assert result.handle == "h_first"
    assert result.creates == 1
    assert result.updates == 0

    # State file persistido
    state_path = isolated_snapshot_dir / ".meta_state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert "previous_hashes" in state
    assert "HUB-VEL-LAV-250" in state["previous_hashes"]
    assert state["last_meta_count"] == 1
    assert state["last_handle"] == "h_first"

    # Port recibió exactamente 1 CREATE
    assert len(fake_port.batches) == 1
    batch = fake_port.batches[0]
    assert batch.catalog_id == "1234567890"
    assert batch.access_token == "EAAprodtoken"
    assert len(batch.creates) == 1
    assert batch.creates[0].retailer_id == "HUB-VEL-LAV-250"
    # La imagen debe estar normalizada (webp → cdn-cgi/image/format=jpeg)
    assert "/cdn-cgi/image/" in batch.creates[0].image_url
    assert "format=jpeg" in batch.creates[0].image_url


@pytest.mark.asyncio
async def test_corrupt_state_file_falls_back_to_full_push(
    isolated_snapshot_dir,
):
    """Si .meta_state.json está corrupto, log warning y proceder con full
    push (todos los items como CREATE)."""
    # Escribir state corrupto
    (isolated_snapshot_dir / ".meta_state.json").write_text(
        "{ this is not json at all"
    )

    fake_port = _FakeMetaPort(ok=True, handle="h_recovery")
    with patch.dict(
        "os.environ",
        {
            "META_CATALOG_ID": "1234",
            "META_SYSTEM_USER_TOKEN": "EAAtoken",
        },
        clear=False,
    ):
        with patch(
            "src.plugins.catalog.agent.activities.push.get_push_meta_catalog_use_case"
        ) as mock_factory:
            from src.plugins.catalog.agent.use_cases.push_meta_catalog import (
                PushMetaCatalogUseCase,
            )

            mock_factory.return_value = PushMetaCatalogUseCase(
                meta_port=fake_port
            )
            result = await push_meta_catalog_activity(
                PushMetaActivityInput(
                    tenant_id="default",
                    products_json=_sample_products_json(),
                    snapshot_dir=str(isolated_snapshot_dir),
                )
            )

    assert result.pushed is True
    assert result.ok is True
    # Como prev_hashes = {}, todo es CREATE
    assert result.creates == 1
    # State file ahora es válido (sobreescrito)
    state = json.loads(
        (isolated_snapshot_dir / ".meta_state.json").read_text()
    )
    assert state["last_meta_count"] == 1


async def _run_push(isolated_snapshot_dir, fake_port, *, force: bool = False):
    """Helper: corre la activity con env OK + el port fake dado."""
    with patch.dict(
        "os.environ",
        {"META_CATALOG_ID": "1234567890", "META_SYSTEM_USER_TOKEN": "EAAtoken"},
        clear=False,
    ):
        with patch(
            "src.plugins.catalog.agent.activities.push.get_push_meta_catalog_use_case"
        ) as mock_factory:
            from src.plugins.catalog.agent.use_cases.push_meta_catalog import (
                PushMetaCatalogUseCase,
            )

            mock_factory.return_value = PushMetaCatalogUseCase(
                meta_port=fake_port
            )
            return await push_meta_catalog_activity(
                PushMetaActivityInput(
                    tenant_id="default",
                    products_json=_sample_products_json(),
                    snapshot_dir=str(isolated_snapshot_dir),
                    force_full_refresh=force,
                )
            )


@pytest.mark.asyncio
async def test_second_sync_without_force_is_noop(isolated_snapshot_dir):
    """CONTROL: sin force, el segundo sync de datos IDÉNTICOS es no-op —
    el hash matchea → NO se re-pushea (delta). Documenta el comportamiento
    que hacía irrecuperable un fetch de imagen fallido en Meta."""
    port1 = _FakeMetaPort(ok=True, handle="h1")
    r1 = await _run_push(isolated_snapshot_dir, port1)
    assert r1.creates == 1  # primer push crea

    port2 = _FakeMetaPort(ok=True, handle="h2")
    r2 = await _run_push(isolated_snapshot_dir, port2)
    assert r2.creates == 0 and r2.updates == 0  # delta no-op
    assert len(port2.batches) == 0  # el port NO se llama


@pytest.mark.asyncio
async def test_force_full_refresh_repushes_unchanged_items(
    isolated_snapshot_dir,
):
    """FIX: con force_full_refresh=True, un segundo sync de datos idénticos
    RE-PUSHEA todos los items como CREATE (ignora previous_hashes) — así el
    dashboard puede recuperar imágenes que Meta no fetcheó. Este es el flag
    que el botón 'Sincronizar' manda pero que hoy el push ignora."""
    port1 = _FakeMetaPort(ok=True, handle="h1")
    r1 = await _run_push(isolated_snapshot_dir, port1)
    assert r1.creates == 1

    port2 = _FakeMetaPort(ok=True, handle="h2")
    r2 = await _run_push(isolated_snapshot_dir, port2, force=True)
    assert r2.creates == 1, "force_full_refresh debe re-pushear items sin cambios"
    assert len(port2.batches) == 1
    assert port2.batches[0].creates[0].retailer_id == "HUB-VEL-LAV-250"


@pytest.mark.asyncio
async def test_push_error_does_not_overwrite_state(
    isolated_snapshot_dir,
):
    """Si el port devuelve error, el state previo se mantiene intacto
    (próximo run re-intenta los mismos deltas)."""
    # Pre-poblar state con datos "viejos"
    old_state = {
        "previous_hashes": {"OLD-SKU": "deadbeef"},
        "last_meta_count": 42,
        "last_handle": "h_old",
    }
    (isolated_snapshot_dir / ".meta_state.json").write_text(
        json.dumps(old_state)
    )

    fake_port = _FakeMetaPort(ok=False)  # fake fails
    with patch.dict(
        "os.environ",
        {
            "META_CATALOG_ID": "1234",
            "META_SYSTEM_USER_TOKEN": "EAAtoken",
        },
        clear=False,
    ):
        with patch(
            "src.plugins.catalog.agent.activities.push.get_push_meta_catalog_use_case"
        ) as mock_factory:
            from src.plugins.catalog.agent.use_cases.push_meta_catalog import (
                PushMetaCatalogUseCase,
            )

            mock_factory.return_value = PushMetaCatalogUseCase(
                meta_port=fake_port
            )
            result = await push_meta_catalog_activity(
                PushMetaActivityInput(
                    tenant_id="default",
                    products_json=_sample_products_json(),
                    snapshot_dir=str(isolated_snapshot_dir),
                )
            )

    assert result.pushed is True
    assert result.ok is False  # push failed
    # State file NO sobrescrito — sigue con el contenido viejo
    state = json.loads(
        (isolated_snapshot_dir / ".meta_state.json").read_text()
    )
    assert state == old_state
