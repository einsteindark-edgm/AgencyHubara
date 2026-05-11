# Implementation plan — 02 CatalogPort + LocalSnapshotCatalogClient

- **Refinement**: `.exoclaw/refinements/02-catalog-port-and-local-reader-tech.md`
- **Target agent**: `platform/catalog` (cross-agent infra) at `/Users/edgm/Documents/Projects/AgencyHubara/hubara_agency`
- **Implementer**: exoclaw-implementer
- **Date**: 2026-05-07

## 1. PR sequence (each step keeps tests green)

### PR-1: DTOs + errors + paths
**Goal**: contratos puros (frozen dataclasses) y errores tipados, sin lógica.
**Files**:
- CREATE `src/platform/catalog/__init__.py`.
- CREATE `src/platform/catalog/dtos.py` — los 6 DTOs del refinement §3.
- CREATE `src/platform/catalog/errors.py` — `ProductNotFoundError`, `CatalogUnavailableError`.
- CREATE `src/platform/catalog/paths.py` — `get_snapshot_dir()`.
- CREATE `tests/platform/catalog/__init__.py`, `tests/platform/catalog/test_dtos_serialization.py`, `tests/platform/catalog/test_paths.py`.
**Verification**:
```bash
uv run pytest tests/platform/catalog/test_dtos_serialization.py tests/platform/catalog/test_paths.py -x
```

### PR-2: CatalogPort Protocol
**Goal**: definir el Protocol que tools y activities consumen.
**Files**:
- CREATE `src/platform/catalog/port.py` — `CatalogPort` (`@runtime_checkable`).
- CREATE `tests/platform/catalog/test_port_protocol.py`.
**Verification**:
```bash
uv run pytest tests/platform/catalog/ -x
```

### PR-3: LocalSnapshotCatalogClient (lectura básica)
**Goal**: search + get_by_handle funcionando, sin staleness checks.
**Files**:
- CREATE `src/platform/catalog/local_snapshot.py` — `LocalSnapshotCatalogClient`.
- CREATE `tests/platform/catalog/test_local_snapshot_search.py`, `test_local_snapshot_get_by_handle.py`, `test_local_snapshot_failures.py`.
**Verification**:
```bash
uv run pytest tests/platform/catalog/ -x
```

### PR-4: mtime-aware cache + staleness
**Goal**: cache que detecta cambios sin restart + manifest age check.
**Files**:
- EDIT `src/platform/catalog/local_snapshot.py` — añadir `_ensure_loaded` con mtime, `stale` en `SearchResult`.
- CREATE `tests/platform/catalog/test_local_snapshot_mtime_reload.py`, `test_local_snapshot_stale.py`.
**Verification**:
```bash
uv run pytest tests/platform/catalog/ -x
```

### PR-5: Composition factory
**Goal**: `get_catalog_client()` lru_cache(1) listo para HU-04.
**Files**:
- CREATE `src/platform/catalog/composition.py`.
- (Sin test directo — el factory se valida implícito en HU-04 con `Fake CatalogPort`.)
**Verification**:
```bash
uv run pytest tests/platform/catalog/ -x
uv run ruff check src/platform/catalog
```

## 2. File-by-file (canonical content)

### `src/platform/catalog/__init__.py` (NEW)

```python
"""platform.catalog — port (Protocol) y adapter local (snapshot filesystem).

Cross-agent infrastructure. Consumed by:
  - src/sales_whatsapp/tools/catalog.py (HU-04) — lectura.
  - src/catalog_sync/use_cases/write_snapshot.py (HU-03) — escritura (no incluida aquí, solo lectura).

R-DIP: este paquete NO importa de ningún agente, ni de temporalio, ni de exoclaw.
Solo stdlib y los DTOs internos.
"""
from src.platform.catalog.composition import get_catalog_client
from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    SearchResult,
)
from src.platform.catalog.errors import (
    CatalogUnavailableError,
    ProductNotFoundError,
)
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.catalog.paths import get_snapshot_dir
from src.platform.catalog.port import CatalogPort

__all__ = [
    "CatalogImageDTO",
    "CatalogManifestDTO",
    "CatalogPort",
    "CatalogPriceDTO",
    "CatalogProductDTO",
    "CatalogUnavailableError",
    "CatalogVariantDTO",
    "LocalSnapshotCatalogClient",
    "ProductNotFoundError",
    "SearchResult",
    "get_catalog_client",
    "get_snapshot_dir",
]
```

### `src/platform/catalog/dtos.py` (NEW)

```python
"""DTOs JSON-safe del catálogo. R-JSON-ready desde día 1.

Decimal → str para evitar la trampa de JSON serialization. Las activities
de HU-03 pueden retornar estos DTOs cruzando workflow boundary sin
violar R-JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogPriceDTO:
    amount: str  # Decimal serialized (R-JSON-safe)
    currency_code: str
    min_quantity: int | None = None
    max_quantity: int | None = None


@dataclass(frozen=True)
class CatalogVariantDTO:
    id: str
    title: str
    sku: str | None = None
    prices: list[CatalogPriceDTO] = field(default_factory=list)


@dataclass(frozen=True)
class CatalogImageDTO:
    url: str
    rank: int = 0


@dataclass(frozen=True)
class CatalogProductDTO:
    id: str
    handle: str
    title: str
    status: str
    description: str | None = None
    thumbnail: str | None = None
    variants: list[CatalogVariantDTO] = field(default_factory=list)
    images: list[CatalogImageDTO] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class CatalogManifestDTO:
    version: str
    fetched_at: str  # ISO 8601 UTC
    product_count: int
    source_etag: str | None = None


@dataclass(frozen=True)
class SearchResult:
    """Closed-list grounding envelope retornado por CatalogPort.search()."""
    query: str
    count: int
    truncated: bool
    stale: bool
    manifest: CatalogManifestDTO
    results: list[CatalogProductDTO] = field(default_factory=list)
```

### `src/platform/catalog/errors.py` (NEW)

```python
"""Errores tipados del CatalogPort."""
from __future__ import annotations


class CatalogError(Exception):
    """Base para errores del catalog port."""


class ProductNotFoundError(CatalogError):
    def __init__(self, handle: str) -> None:
        self.handle = handle
        super().__init__(f"Product handle not found: {handle!r}")


class CatalogUnavailableError(CatalogError):
    """El snapshot no está disponible o está corrupto."""
```

### `src/platform/catalog/paths.py` (NEW)

```python
"""Resolución del snapshot directory.

Único lugar en `platform/catalog/` que lee `os.environ`. Sigue el patrón
de `src/platform/config.py:9-25`.
"""
from __future__ import annotations

import os
from pathlib import Path

# Default = <repo>/hubara_agency/catalog_workspace/. Override en prod via env.
_DEFAULT_SNAPSHOT_DIR = (
    Path(__file__).resolve().parents[3] / "catalog_workspace"
).resolve()


def get_snapshot_dir() -> Path:
    raw = os.environ.get("CATALOG_SNAPSHOT_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_SNAPSHOT_DIR


def get_max_age_minutes() -> int:
    raw = os.environ.get("CATALOG_MAX_AGE_MINUTES", "30")
    try:
        return int(raw)
    except ValueError:
        return 30
```

### `src/platform/catalog/port.py` (NEW)

```python
"""CatalogPort — Protocol que tools y activities consumen.

Implementaciones existentes:
  - LocalSnapshotCatalogClient (default, lee del snapshot del filesystem).

Futuro:
  - HttpLiveCatalogClient (consultas en vivo a Medusa para stock real-time).
  - MeilisearchCatalogClient (búsqueda full-text).

R-DIP: el Protocol vive aquí; los consumers (tools, activities) reciben
una instancia vía constructor injection. No conocen la implementación.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.platform.catalog.dtos import CatalogProductDTO, SearchResult


@runtime_checkable
class CatalogPort(Protocol):
    async def search(self, q: str, *, limit: int = 10) -> SearchResult: ...
    async def get_by_handle(self, handle: str) -> CatalogProductDTO: ...
```

### `src/platform/catalog/local_snapshot.py` (NEW)

```python
"""LocalSnapshotCatalogClient — lee del filesystem con cache mtime-aware.

Acepta updates en vivo: al detectar mtime nuevo en `snapshot.json`, recarga.
Sin signals, sin watchdogs, sin restart. La escritura atómica del catalog_sync
agent (HU-03, `os.replace(...)`) garantiza que el reader nunca ve un archivo
a medio escribir.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    SearchResult,
)
from src.platform.catalog.errors import (
    CatalogUnavailableError,
    ProductNotFoundError,
)

log = logging.getLogger(__name__)

_DEFAULT_MANIFEST = CatalogManifestDTO(
    version="unknown",
    fetched_at="1970-01-01T00:00:00+00:00",
    product_count=0,
)


class LocalSnapshotCatalogClient:
    def __init__(self, snapshot_dir: Path, *, max_age_minutes: int = 30) -> None:
        self._dir = Path(snapshot_dir)
        self._max_age = max_age_minutes
        self._cached_products: list[CatalogProductDTO] | None = None
        self._cached_by_handle: dict[str, CatalogProductDTO] = {}
        self._cached_mtime: float = 0.0
        self._cached_manifest: CatalogManifestDTO = _DEFAULT_MANIFEST

    # ---------- public ----------

    async def search(self, q: str, *, limit: int = 10) -> SearchResult:
        self._ensure_loaded()
        assert self._cached_products is not None  # ensured by _ensure_loaded
        q_lower = q.lower().strip()
        matches = [
            p for p in self._cached_products
            if q_lower in p.title.lower() or q_lower in p.handle.lower()
        ]
        truncated = len(matches) > limit
        return SearchResult(
            query=q,
            count=len(matches),
            truncated=truncated,
            stale=self._is_stale(),
            manifest=self._cached_manifest,
            results=matches[:limit],
        )

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        # Fast path: by_handle/<h>.json directo.
        per_handle_path = self._dir / "by_handle" / f"{handle}.json"
        if per_handle_path.exists():
            try:
                raw = json.loads(per_handle_path.read_text(encoding="utf-8"))
                return _product_from_raw(raw)
            except json.JSONDecodeError as e:
                raise CatalogUnavailableError(
                    f"by_handle/{handle}.json corrupt: {e}"
                ) from e

        # Slow path: cargar snapshot completo y buscar.
        self._ensure_loaded()
        if handle in self._cached_by_handle:
            return self._cached_by_handle[handle]
        raise ProductNotFoundError(handle)

    # ---------- internals ----------

    def _ensure_loaded(self) -> None:
        snap_path = self._dir / "snapshot.json"
        if not snap_path.exists():
            raise CatalogUnavailableError(
                f"snapshot not found at {snap_path}"
            )
        try:
            mtime = snap_path.stat().st_mtime
        except OSError as e:
            raise CatalogUnavailableError(f"snapshot stat failed: {e}") from e

        if self._cached_products is None or mtime > self._cached_mtime:
            try:
                payload = json.loads(snap_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise CatalogUnavailableError(f"snapshot corrupt: {e}") from e
            self._cached_products = [_product_from_raw(r) for r in payload]
            self._cached_by_handle = {p.handle: p for p in self._cached_products}
            self._cached_mtime = mtime
            self._cached_manifest = self._load_manifest()
            log.info(
                "catalog snapshot reloaded mtime=%.0f products=%d",
                mtime, len(self._cached_products),
            )

    def _load_manifest(self) -> CatalogManifestDTO:
        path = self._dir / "manifest.json"
        if not path.exists():
            log.warning("catalog manifest missing at %s — assuming stale", path)
            return _DEFAULT_MANIFEST
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            log.warning("catalog manifest corrupt: %s — assuming stale", e)
            return _DEFAULT_MANIFEST
        return CatalogManifestDTO(
            version=str(payload.get("version", "unknown")),
            fetched_at=str(payload.get("fetched_at", _DEFAULT_MANIFEST.fetched_at)),
            product_count=int(payload.get("product_count", 0)),
            source_etag=payload.get("source_etag"),
        )

    def _is_stale(self) -> bool:
        try:
            fetched = datetime.fromisoformat(self._cached_manifest.fetched_at)
        except ValueError:
            return True
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - fetched) > timedelta(minutes=self._max_age)


# ---------- raw → DTO ----------

def _product_from_raw(raw: dict[str, Any]) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=str(raw["id"]),
        handle=str(raw["handle"]),
        title=str(raw["title"]),
        status=str(raw.get("status", "published")),
        description=raw.get("description"),
        thumbnail=raw.get("thumbnail"),
        variants=[_variant_from_raw(v) for v in raw.get("variants", [])],
        images=[CatalogImageDTO(url=i["url"], rank=int(i.get("rank", 0)))
                for i in raw.get("images", [])],
        tags=list(raw.get("tags", [])),
        categories=list(raw.get("categories", [])),
        metadata={k: str(v) for k, v in (raw.get("metadata") or {}).items()},
    )


def _variant_from_raw(raw: dict[str, Any]) -> CatalogVariantDTO:
    return CatalogVariantDTO(
        id=str(raw["id"]),
        title=str(raw["title"]),
        sku=raw.get("sku"),
        prices=[
            CatalogPriceDTO(
                amount=str(p["amount"]),
                currency_code=str(p["currency_code"]),
                min_quantity=p.get("min_quantity"),
                max_quantity=p.get("max_quantity"),
            )
            for p in raw.get("prices", [])
        ],
    )
```

### `src/platform/catalog/composition.py` (NEW)

```python
"""DI factory para el CatalogPort.

Devuelve por default un LocalSnapshotCatalogClient apuntando al
`get_snapshot_dir()`. Singleton por proceso (lru_cache(1)) — la cache
mtime-aware vive en la instancia, así que compartir es correcto.
"""
from __future__ import annotations

from functools import lru_cache

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.catalog.paths import get_max_age_minutes, get_snapshot_dir
from src.platform.catalog.port import CatalogPort


@lru_cache(maxsize=1)
def get_catalog_client() -> CatalogPort:
    return LocalSnapshotCatalogClient(
        snapshot_dir=get_snapshot_dir(),
        max_age_minutes=get_max_age_minutes(),
    )
```

## 3. Tests to add

### `tests/platform/catalog/test_dtos_serialization.py` (NEW)

```python
import json
from dataclasses import asdict
from src.platform.catalog.dtos import (
    CatalogProductDTO, CatalogVariantDTO, CatalogPriceDTO, CatalogImageDTO
)


def test_product_dto_roundtrips_through_json():
    p = CatalogProductDTO(
        id="prod_1", handle="x", title="Foo", status="published",
        variants=[CatalogVariantDTO(
            id="v1", title="u",
            prices=[CatalogPriceDTO(amount="49.99", currency_code="usd")],
        )],
        images=[CatalogImageDTO(url="http://x", rank=0)],
        tags=["A", "B"],
        metadata={"key": "value"},
    )
    s = json.dumps(asdict(p))
    back = json.loads(s)
    assert back["handle"] == "x"
    assert back["variants"][0]["prices"][0]["amount"] == "49.99"
```

### `tests/platform/catalog/test_local_snapshot_search.py` (NEW)

```python
import json, pytest
from pathlib import Path
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient


@pytest.fixture
def snap_dir(tmp_path: Path):
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps([
        {"id": "1", "handle": "luz-serena", "title": "Luz Serena", "status": "published",
         "variants": [{"id": "v1", "title": "u", "prices": [{"amount": "23000", "currency_code": "cop"}]}]},
        {"id": "2", "handle": "vela-cruz", "title": "Cruz de Vida", "status": "published",
         "variants": [{"id": "v2", "title": "u", "prices": [{"amount": "17000", "currency_code": "cop"}]}]},
        {"id": "3", "handle": "luz-belen", "title": "Luz de Belén", "status": "published",
         "variants": [{"id": "v3", "title": "u", "prices": [{"amount": "20000", "currency_code": "cop"}]}]},
    ]))
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": "v1", "fetched_at": "2099-01-01T00:00:00+00:00", "product_count": 3,
    }))
    return tmp_path


@pytest.mark.asyncio
async def test_search_substring_case_insensitive(snap_dir):
    client = LocalSnapshotCatalogClient(snap_dir)
    res = await client.search(q="LUZ", limit=10)
    assert res.count == 2
    handles = {p.handle for p in res.results}
    assert handles == {"luz-serena", "luz-belen"}


@pytest.mark.asyncio
async def test_search_truncates_when_over_limit(snap_dir):
    client = LocalSnapshotCatalogClient(snap_dir)
    res = await client.search(q="luz", limit=1)
    assert res.count == 2
    assert len(res.results) == 1
    assert res.truncated is True
```

### `tests/platform/catalog/test_local_snapshot_get_by_handle.py` (NEW)

```python
import json, pytest
from pathlib import Path
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.catalog.errors import ProductNotFoundError


@pytest.fixture
def snap_dir(tmp_path: Path):
    (tmp_path / "snapshot.json").write_text(json.dumps([
        {"id": "1", "handle": "luz-serena", "title": "Luz Serena", "status": "published"},
    ]))
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": "v1", "fetched_at": "2099-01-01T00:00:00+00:00", "product_count": 1,
    }))
    return tmp_path


@pytest.mark.asyncio
async def test_get_by_handle_fast_path(tmp_path):
    by_handle = tmp_path / "by_handle"
    by_handle.mkdir()
    (by_handle / "luz-serena.json").write_text(json.dumps({
        "id": "1", "handle": "luz-serena", "title": "Luz Serena", "status": "published",
    }))
    client = LocalSnapshotCatalogClient(tmp_path)
    p = await client.get_by_handle("luz-serena")
    assert p.handle == "luz-serena"


@pytest.mark.asyncio
async def test_get_by_handle_slow_path_falls_back_to_snapshot(snap_dir):
    client = LocalSnapshotCatalogClient(snap_dir)
    p = await client.get_by_handle("luz-serena")
    assert p.title == "Luz Serena"


@pytest.mark.asyncio
async def test_get_by_handle_unknown_raises(snap_dir):
    client = LocalSnapshotCatalogClient(snap_dir)
    with pytest.raises(ProductNotFoundError):
        await client.get_by_handle("inventado")
```

### `tests/platform/catalog/test_local_snapshot_mtime_reload.py` (NEW)

```python
import json, os, pytest, time
from pathlib import Path
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient


@pytest.mark.asyncio
async def test_mtime_change_triggers_reload(tmp_path: Path):
    (tmp_path / "snapshot.json").write_text(json.dumps([
        {"id": "1", "handle": "h1", "title": "T1", "status": "published"},
    ]))
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": "v1", "fetched_at": "2099-01-01T00:00:00+00:00", "product_count": 1,
    }))
    client = LocalSnapshotCatalogClient(tmp_path)

    res1 = await client.search(q="h1")
    assert res1.count == 1

    # bump mtime via atomic write
    new_payload = json.dumps([
        {"id": "1", "handle": "h1", "title": "T1", "status": "published"},
        {"id": "2", "handle": "h2", "title": "T2", "status": "published"},
    ])
    tmp = tmp_path / "snapshot.json.new"
    tmp.write_text(new_payload)
    # garantizar mtime distinto en sistemas con stat granular
    os.utime(tmp, (time.time() + 5, time.time() + 5))
    os.replace(tmp, tmp_path / "snapshot.json")

    res2 = await client.search(q="h")
    assert res2.count == 2
```

### `tests/platform/catalog/test_local_snapshot_stale.py` (NEW)

```python
import json, pytest
from pathlib import Path
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient


@pytest.mark.asyncio
async def test_stale_when_manifest_old(tmp_path: Path):
    (tmp_path / "snapshot.json").write_text(json.dumps([
        {"id": "1", "handle": "h", "title": "T", "status": "published"},
    ]))
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": "v", "fetched_at": "2020-01-01T00:00:00+00:00", "product_count": 1,
    }))
    client = LocalSnapshotCatalogClient(tmp_path, max_age_minutes=30)
    res = await client.search(q="h")
    assert res.stale is True


@pytest.mark.asyncio
async def test_fresh_when_manifest_recent(tmp_path: Path):
    from datetime import datetime, timezone
    (tmp_path / "snapshot.json").write_text(json.dumps([
        {"id": "1", "handle": "h", "title": "T", "status": "published"},
    ]))
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": "v",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "product_count": 1,
    }))
    client = LocalSnapshotCatalogClient(tmp_path, max_age_minutes=30)
    res = await client.search(q="h")
    assert res.stale is False
```

### `tests/platform/catalog/test_local_snapshot_failures.py` (NEW)

```python
import pytest
from pathlib import Path
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.catalog.errors import CatalogUnavailableError


@pytest.mark.asyncio
async def test_no_snapshot_raises(tmp_path: Path):
    client = LocalSnapshotCatalogClient(tmp_path)
    with pytest.raises(CatalogUnavailableError):
        await client.search(q="x")


@pytest.mark.asyncio
async def test_corrupt_snapshot_raises(tmp_path: Path):
    (tmp_path / "snapshot.json").write_text("not valid json {{{")
    client = LocalSnapshotCatalogClient(tmp_path)
    with pytest.raises(CatalogUnavailableError):
        await client.search(q="x")
```

### `tests/platform/catalog/test_paths.py` (NEW)

```python
from pathlib import Path
from src.platform.catalog.paths import get_max_age_minutes, get_snapshot_dir


def test_default_dir_when_no_env(monkeypatch):
    monkeypatch.delenv("CATALOG_SNAPSHOT_DIR", raising=False)
    p = get_snapshot_dir()
    assert isinstance(p, Path)
    assert p.is_absolute()


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CATALOG_SNAPSHOT_DIR", str(tmp_path))
    assert get_snapshot_dir() == tmp_path.resolve()


def test_max_age_default(monkeypatch):
    monkeypatch.delenv("CATALOG_MAX_AGE_MINUTES", raising=False)
    assert get_max_age_minutes() == 30


def test_max_age_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("CATALOG_MAX_AGE_MINUTES", "abc")
    assert get_max_age_minutes() == 30
```

### `tests/platform/catalog/test_port_protocol.py` (NEW)

```python
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.catalog.port import CatalogPort


def test_local_client_satisfies_port_structurally(tmp_path):
    client = LocalSnapshotCatalogClient(tmp_path)
    # runtime_checkable Protocol
    assert isinstance(client, CatalogPort)
```

## 4. Replay fixture refresh

N/A. No tocamos workflows.

## 5. Verification commands (run between every PR)

```bash
uv run ruff check src/platform/catalog
uv run pytest tests/platform/catalog/ -x

# R-DIP
grep -rEn "^from (temporalio|exoclaw|src\.(sales|remarketing|catalog_sync)_whatsapp)" src/platform/catalog/ \
  || echo "R-DIP ok"

# R-STATELESS — solo aceptamos cache de instancia, NO module-level
grep -rEn "^[A-Z_][A-Z0-9_]+\s*=\s*[\[\{]" src/platform/catalog/local_snapshot.py | grep -v "_DEFAULT_MANIFEST" \
  || echo "R-STATELESS ok (solo _DEFAULT_MANIFEST inmutable)"
```

## 6. Smoke-test recipe

Genera un snapshot de prueba a mano para validar end-to-end del reader sin esperar HU-03:

```bash
mkdir -p /tmp/catalog_test/by_handle
cat > /tmp/catalog_test/snapshot.json <<'EOF'
[
  {"id":"1","handle":"luz-serena","title":"Luz Serena","status":"published",
   "variants":[{"id":"v1","title":"u","prices":[{"amount":"23000","currency_code":"cop"}]}]},
  {"id":"2","handle":"vela-cruz","title":"Cruz de Vida","status":"published",
   "variants":[{"id":"v2","title":"u","prices":[{"amount":"17000","currency_code":"cop"}]}]}
]
EOF
cat > /tmp/catalog_test/manifest.json <<EOF
{"version":"smoke-1","fetched_at":"$(date -u +%Y-%m-%dT%H:%M:%S%z | sed 's/\(..\)$/:\1/')","product_count":2}
EOF

CATALOG_SNAPSHOT_DIR=/tmp/catalog_test uv run python -c "
import asyncio
from src.platform.catalog.composition import get_catalog_client

async def main():
    c = get_catalog_client()
    print(await c.search(q='luz'))
    print(await c.get_by_handle('vela-cruz'))

asyncio.run(main())
"
```

Esperado: `SearchResult(count=1, ...)` y `CatalogProductDTO(handle='vela-cruz', ...)`.

## 7. Rollback strategy

Cada PR es revertible independiente. Cinco archivos nuevos en una carpeta nueva (`src/platform/catalog/`); ninguno modifica código existente. `git revert <sha>` quita la carpeta sin daños colaterales.

## 8. Coordination updates

ADRs (5 líneas):
- `ADR-2026-05-07-03: CatalogPort como Protocol en src/platform/catalog/`. Razón: cross-agent + multi-implementación futura (local, http, search engine).
- `ADR-2026-05-07-04: mtime-aware in-memory cache en LocalSnapshotCatalogClient`. Razón: live reload sin restarts; cero overhead en hot path.

## 9. Risks I'm carrying forward from the refinement

- **R3**: Búsqueda solo en title/handle. Doc'ed; expandible cuando llegue Meilisearch.
- **R4**: `metadata` aplanado a `dict[str, str]`. Coordinado con HU-03 para que el writer haga `json.dumps` de valores nested.
- **R7**: `by_handle/<h>.json` vs slow-path. Implementado: fast path primero, fallback al snapshot.

---

**Status**: refinement validado, plan listo. **Stop point**: confirmar antes de PR-1.
