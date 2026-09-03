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


# =============================================================================
# Multi-catálogo: META_EXTRA_CATALOG_IDS (réplicas del catálogo primario)
# =============================================================================


async def _run_push_env(
    isolated_snapshot_dir, fake_port, env: dict[str, str], *, force: bool = False
):
    """Como `_run_push` pero con env explícito (multi-catálogo)."""
    with patch.dict("os.environ", env, clear=False):
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


_MULTI_ENV = {
    "META_CATALOG_ID": "CAT-PRIMARY-001",
    # Espacios + vacíos + duplicado del primario: todos se toleran/ignoran.
    "META_EXTRA_CATALOG_IDS": " CAT-REPLICA-002 , ,CAT-PRIMARY-001, 111 ",
    "META_SYSTEM_USER_TOKEN": "EAAtoken",
}


@pytest.mark.asyncio
async def test_extra_catalog_ids_push_same_items_to_each_catalog(
    isolated_snapshot_dir,
):
    """Con META_EXTRA_CATALOG_IDS, el MISMO batch (mismos retailer_ids, con
    item_group_id/color de Medusa) se manda a cada catálogo — primario primero,
    réplicas después — y cada catálogo tiene su propio state de delta."""
    port = _FakeMetaPort(ok=True, handle="h_multi")
    result = await _run_push_env(isolated_snapshot_dir, port, _MULTI_ENV)

    assert [b.catalog_id for b in port.batches] == [
        "CAT-PRIMARY-001",
        "CAT-REPLICA-002",
        "111",
    ], "un batch por catálogo, en orden, sin duplicados ni vacíos"
    assert all(
        [c.retailer_id for c in b.creates] == ["HUB-VEL-LAV-250"]
        for b in port.batches
    ), "cada catálogo recibe exactamente los mismos items"

    assert result.ok is True and result.pushed is True
    assert result.catalogs_pushed == 3
    assert result.creates == 3  # 1 item x 3 catálogos

    # State del primario sigue en `.meta_state.json` (compat con el state ya
    # existente en prod); réplicas en `.meta_state.<catalog_id>.json`.
    assert (isolated_snapshot_dir / ".meta_state.json").exists()
    assert (isolated_snapshot_dir / ".meta_state.CAT-REPLICA-002.json").exists()
    assert (isolated_snapshot_dir / ".meta_state.111.json").exists()


@pytest.mark.asyncio
async def test_replica_added_later_gets_full_push_while_primary_stays_delta(
    isolated_snapshot_dir,
):
    """El delta es POR catálogo: agregar una réplica después de que el
    primario ya sincronizó → el primario es no-op (hash igual) y la réplica
    recibe push FULL. Es el caso real de prod: `CAT-PRIMARY-001` ya tiene
    `.meta_state.json`; agregamos `CAT-REPLICA-002` por env."""
    only_primary = {**_MULTI_ENV, "META_EXTRA_CATALOG_IDS": ""}
    port1 = _FakeMetaPort(ok=True, handle="h1")
    r1 = await _run_push_env(isolated_snapshot_dir, port1, only_primary)
    assert r1.creates == 1 and r1.catalogs_pushed == 1

    with_replica = {**_MULTI_ENV, "META_EXTRA_CATALOG_IDS": "CAT-REPLICA-002"}
    port2 = _FakeMetaPort(ok=True, handle="h2")
    r2 = await _run_push_env(isolated_snapshot_dir, port2, with_replica)

    assert [b.catalog_id for b in port2.batches] == ["CAT-REPLICA-002"], (
        "solo la réplica nueva va al port; el primario es delta no-op"
    )
    assert r2.creates == 1 and r2.updates == 0
    assert r2.catalogs_pushed == 2
    assert r2.ok is True


class _FailFor(_FakeMetaPort):
    """Port fake que falla SOLO para un catálogo (las réplicas son
    independientes: un 500 en una no debe tumbar a las demás)."""

    def __init__(self, failing_catalog_id: str):
        super().__init__(ok=True, handle="h_ok")
        self._failing = failing_catalog_id

    async def upsert_batch(self, request: MetaBatchRequest) -> MetaBatchResult:
        self.batches.append(request)
        if request.catalog_id == self._failing:
            return MetaBatchResult(
                handle=None, ok=False, submitted=1, error="fake_http_500"
            )
        return MetaBatchResult(handle="h_ok", ok=True, submitted=1)


@pytest.mark.asyncio
async def test_replica_failure_is_reported_but_does_not_block_primary(
    isolated_snapshot_dir,
):
    """Si una réplica falla: `ok=False` con el catálogo culpable en `error`,
    el primario igual pushea y persiste su state, y la réplica NO persiste
    state (el próximo run la re-intenta full)."""
    port = _FailFor("CAT-REPLICA-002")
    env = {**_MULTI_ENV, "META_EXTRA_CATALOG_IDS": "CAT-REPLICA-002"}
    result = await _run_push_env(isolated_snapshot_dir, port, env)

    assert [b.catalog_id for b in port.batches] == [
        "CAT-PRIMARY-001",
        "CAT-REPLICA-002",
    ]
    assert result.ok is False
    assert result.pushed is True
    assert result.catalogs_pushed == 2
    assert "CAT-REPLICA-002" in (result.error or "")
    assert "fake_http_500" in (result.error or "")
    assert result.handle == "h_ok", "el handle reportado es el del primario"

    assert (isolated_snapshot_dir / ".meta_state.json").exists()
    assert not (isolated_snapshot_dir / ".meta_state.CAT-REPLICA-002.json").exists()


@pytest.mark.asyncio
async def test_result_carries_per_catalog_breakdown(isolated_snapshot_dir):
    """PM-05: los contadores agregados no dicen QUÉ catálogo recibió qué.
    `per_catalog_json` (R-JSON) trae el desglose por catálogo — ok, handle,
    creates/updates/deletes y error — para el dashboard y el script de ops."""
    port = _FailFor("CAT-REPLICA-002")
    env = {**_MULTI_ENV, "META_EXTRA_CATALOG_IDS": "CAT-REPLICA-002"}
    result = await _run_push_env(isolated_snapshot_dir, port, env)

    per = json.loads(result.per_catalog_json)
    assert list(per) == ["CAT-PRIMARY-001", "CAT-REPLICA-002"]
    assert per["CAT-PRIMARY-001"] == {
        "ok": True,
        "handle": "h_ok",
        "creates": 1,
        "updates": 0,
        "deletes": 0,
        "error": None,
    }
    assert per["CAT-REPLICA-002"]["ok"] is False
    assert per["CAT-REPLICA-002"]["handle"] is None
    assert per["CAT-REPLICA-002"]["creates"] == 0
    assert per["CAT-REPLICA-002"]["error"] == "fake_http_500"
