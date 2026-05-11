# Implementation plan — 01 HttpMedusaClient

- **Refinement**: `.exoclaw/refinements/01-http-medusa-client-tech.md`
- **Reference**: `features/catalogAgent/MEDUSA_PRODUCT_QUERY_FROM_PYTHON.md`
- **Target agent**: `platform/medusa` (cross-agent infra) at `/Users/edgm/Documents/Projects/AgencyHubara/hubara_agency`
- **Implementer**: exoclaw-implementer
- **Date**: 2026-05-07

## 1. PR sequence (each step keeps tests green)

### PR-1: deps + settings
**Goal**: declarar deps, primer módulo `MedusaSettings` con tests.
**Files**:
- EDIT `pyproject.toml` — añadir `httpx>=0.27,<1`, `pydantic>=2.6,<3`, `tenacity>=8.2,<10`, `pydantic-settings>=2,<3`.
- CREATE `src/platform/medusa/__init__.py` — vacío.
- CREATE `src/platform/medusa/settings.py` — `MedusaSettings(BaseSettings)`.
- CREATE `tests/platform/medusa/__init__.py` — vacío.
- CREATE `tests/platform/medusa/test_settings.py` — 3 tests (valid, missing_base_url, custom_token).
**Verification**:
```bash
uv sync
uv run pytest tests/platform/medusa/test_settings.py -x
```

### PR-2: Pydantic models
**Goal**: tipos completos con preservación de `Decimal`.
**Files**:
- CREATE `src/platform/medusa/models.py` — los 11 modelos del refinement §3.
- CREATE `tests/platform/medusa/test_models_decimal.py` — 2 tests (number→Decimal, str→Decimal).
- CREATE `tests/platform/medusa/test_models_extra_fields.py` — 1 test (campos extras ignorados).
**Verification**:
```bash
uv run pytest tests/platform/medusa/test_models_decimal.py tests/platform/medusa/test_models_extra_fields.py -x
```

### PR-3: HTTP client (auth + retries)
**Goal**: `HttpMedusaClient` operativo con ambas auth modes y reintentos.
**Files**:
- CREATE `src/platform/medusa/client.py` — `HttpMedusaClient`, `MedusaAPIError`, `DEFAULT_PRODUCT_FIELDS`.
- CREATE `tests/platform/medusa/test_client_auth.py` — 2 tests (basic header, bearer header).
- CREATE `tests/platform/medusa/test_client_jwt_relogin.py` — 2 tests (relogin on 401, no-relogin si Secret).
- CREATE `tests/platform/medusa/test_client_retries.py` — 1 test (TransportError x2 + ok).
- CREATE `tests/platform/medusa/test_client_pagination.py` — 1 test (iter_products 3 páginas).
- CREATE `tests/platform/medusa/test_default_fields.py` — 1 test (`*variants,*variants.prices` ambos en `DEFAULT_PRODUCT_FIELDS`).
**Verification**:
```bash
uv run pytest tests/platform/medusa/ -x
```

### PR-4: Service helper + composition
**Goal**: `MedusaProductService` tipado + factories `lru_cache(1)`.
**Files**:
- CREATE `src/platform/medusa/service.py` — `MedusaProductService`.
- CREATE `src/platform/medusa/composition.py` — `get_medusa_settings()`, `get_medusa_client()`, `get_medusa_product_service()`.
- CREATE `tests/platform/medusa/test_service.py` — 2 tests (get_product, list_products) con respx.
**Verification**:
```bash
uv run pytest tests/platform/medusa/ -x
uv run ruff check src/platform/medusa
```

## 2. File-by-file (canonical content)

### `pyproject.toml` (EDIT — añadir deps)

Localizar el bloque `[project] dependencies = [...]` (cualquier ubicación cercana al top). Añadir las 4 líneas si no existen:

```toml
"httpx>=0.27,<1",
"pydantic>=2.6,<3",
"tenacity>=8.2,<10",
"pydantic-settings>=2,<3",
```

> Si el repo usa un esquema diferente (e.g. `[tool.uv]` o `requirements.in`), aplicar la misma idea.

### `src/platform/medusa/__init__.py` (NEW)

```python
"""platform.medusa — adapter HTTP a Medusa Admin API v2.

Cross-agent infrastructure: este paquete expone `HttpMedusaClient` y
`MedusaProductService` para que cualquier activity (de hoy o futura) los
consuma vía `composition.get_medusa_client()`.

R-DIP: este paquete NO importa de ningún agente. Sus consumers son
`src/catalog_sync/...` (HU-03) y, opcionalmente en futuro, tools del Sales
agent que necesiten datos en vivo (stock real-time, pricing por región).
"""
from src.platform.medusa.client import (
    DEFAULT_PRODUCT_FIELDS,
    HttpMedusaClient,
    MedusaAPIError,
)
from src.platform.medusa.service import MedusaProductService
from src.platform.medusa.settings import MedusaSettings

__all__ = [
    "DEFAULT_PRODUCT_FIELDS",
    "HttpMedusaClient",
    "MedusaAPIError",
    "MedusaProductService",
    "MedusaSettings",
]
```

### `src/platform/medusa/settings.py` (NEW)

```python
"""MedusaSettings — env vars (Pydantic Settings v2)."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MedusaSettings(BaseSettings):
    """Lee env vars con prefijo `MEDUSA_`.

    Required: `MEDUSA_BASE_URL`. Auth: o bien `MEDUSA_ADMIN_TOKEN` (Opción A,
    recomendada), o bien `MEDUSA_ADMIN_EMAIL` + `MEDUSA_ADMIN_PASSWORD` (Opción
    B). El `HttpMedusaClient` valida que al menos una pareja esté presente.
    """
    model_config = SettingsConfigDict(env_prefix="MEDUSA_", extra="ignore")

    base_url: str = Field(..., description="https://medusa.hubara.example.com")
    admin_token: str | None = Field(default=None, description="Secret API Key (Opción A)")
    admin_email: str | None = Field(default=None)
    admin_password: str | None = Field(default=None)
    http_timeout: float = Field(default=30.0)
```

### `src/platform/medusa/models.py` (NEW)

```python
"""Pydantic v2 models que reflejan la respuesta de Medusa Admin API.

Estos modelos NO cruzan workflow boundaries (R-JSON los prohíbe). Quien
los consume desde una activity los convierte a los `@dataclass` de
`src/platform/catalog/dtos.py` (HU-02) antes de retornar.

Campos extras (Medusa puede añadir nuevos) se ignoran silenciosamente
gracias a `model_config = ConfigDict(extra="ignore")`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MedusaPrice(_Base):
    id: str
    amount: Decimal  # MAJOR units — gotcha §4.5 de la guía Medusa
    currency_code: str
    min_quantity: int | None = None
    max_quantity: int | None = None
    price_list_id: str | None = None
    rules: dict[str, Any] = Field(default_factory=dict)


class MedusaOptionValue(_Base):
    id: str
    value: str


class MedusaVariant(_Base):
    id: str
    title: str
    sku: str | None = None
    manage_inventory: bool = False
    allow_backorder: bool = False
    prices: list[MedusaPrice] = Field(default_factory=list)
    options: list[MedusaOptionValue] = Field(default_factory=list)


class MedusaOption(_Base):
    id: str
    title: str
    values: list[MedusaOptionValue] = Field(default_factory=list)


class MedusaImage(_Base):
    id: str
    url: str
    rank: int = 0


class MedusaTag(_Base):
    id: str
    value: str


class MedusaCategory(_Base):
    id: str
    name: str
    handle: str | None = None
    parent_category_id: str | None = None


class MedusaCollection(_Base):
    id: str
    title: str
    handle: str | None = None


class MedusaSalesChannel(_Base):
    id: str
    name: str


class MedusaProduct(_Base):
    id: str
    title: str
    handle: str
    description: str | None = None
    status: str
    thumbnail: str | None = None
    height: float | None = None
    width: float | None = None
    length: float | None = None
    weight: float | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    variants: list[MedusaVariant] = Field(default_factory=list)
    options: list[MedusaOption] = Field(default_factory=list)
    images: list[MedusaImage] = Field(default_factory=list)
    tags: list[MedusaTag] = Field(default_factory=list)
    categories: list[MedusaCategory] = Field(default_factory=list)
    collection: MedusaCollection | None = None
    sales_channels: list[MedusaSalesChannel] = Field(default_factory=list)


class MedusaProductPage(_Base):
    products: list[MedusaProduct]
    count: int
    offset: int
    limit: int
```

### `src/platform/medusa/client.py` (NEW)

Adaptado del §5.3 de `MEDUSA_PRODUCT_QUERY_FROM_PYTHON.md` (cita textual con ajustes mínimos para nuestra estructura). Mantener el bloque `DEFAULT_PRODUCT_FIELDS` literal para no romper el invariante de `*variants,*variants.prices`.

```python
"""HttpMedusaClient — async client for Medusa v2 Admin API.

Source: features/catalogAgent/MEDUSA_PRODUCT_QUERY_FROM_PYTHON.md §5.3.

Auth modes (auto-selected):
  A) Secret API Key  → admin_token  → Authorization: Basic base64(token + ":")
  B) JWT email/pass  → admin_email + admin_password → Bearer <jwt>
"""
from __future__ import annotations

import base64
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)


class MedusaAPIError(Exception):
    """Raised when the Medusa Admin API returns a non-2xx response."""

    def __init__(self, status_code: int, path: str, body: str) -> None:
        self.status_code = status_code
        self.path = path
        self.body = body
        super().__init__(f"HTTP {status_code} on {path}: {body[:300]}")


# Default expansion: everything you typically want when reading a product end-to-end.
# Documented gotcha: *variants does NOT auto-include *variants.prices; both must be listed.
DEFAULT_PRODUCT_FIELDS = ",".join(
    [
        "id", "title", "handle", "description", "status",
        "thumbnail", "height", "width", "length", "weight",
        "metadata", "created_at", "updated_at",
        "*variants",
        "*variants.prices",
        "*variants.options",
        "*options",
        "*options.values",
        "*images",
        "*tags",
        "*categories",
        "*collection",
        "*sales_channels",
    ]
)


class HttpMedusaClient:
    def __init__(
        self,
        base_url: str,
        *,
        admin_token: str | None = None,
        admin_email: str | None = None,
        admin_password: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not admin_token and not (admin_email and admin_password):
            raise ValueError(
                "Provide either admin_token (recommended) or admin_email + admin_password."
            )
        self.base_url = base_url.rstrip("/")
        self._admin_token = admin_token
        self._admin_email = admin_email
        self._admin_password = admin_password
        self._jwt: str | None = None
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator["HttpMedusaClient"]:
        try:
            yield self
        finally:
            await self.aclose()

    async def get_product(
        self,
        product_id: str,
        *,
        fields: str = DEFAULT_PRODUCT_FIELDS,
    ) -> dict[str, Any]:
        data = await self._request(
            "GET", f"/admin/products/{product_id}", params={"fields": fields}
        )
        return data["product"]

    async def list_products(
        self,
        *,
        q: str | None = None,
        ids: list[str] | None = None,
        title: str | None = None,
        handle: str | None = None,
        status: str | None = None,
        category_id: str | None = None,
        collection_id: str | None = None,
        sales_channel_id: str | None = None,
        tag_ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str = "-created_at",
        fields: str = DEFAULT_PRODUCT_FIELDS,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "order": order,
            "fields": fields,
        }
        if q:
            params["q"] = q
        if ids:
            params["id[]"] = ids
        if title:
            params["title"] = title
        if handle:
            params["handle"] = handle
        if status:
            params["status"] = status
        if category_id:
            params["category_id"] = category_id
        if collection_id:
            params["collection_id"] = collection_id
        if sales_channel_id:
            params["sales_channel_id"] = sales_channel_id
        if tag_ids:
            params["tags[]"] = tag_ids
        return await self._request("GET", "/admin/products", params=params)

    async def iter_products(
        self,
        *,
        page_size: int = 100,
        **filters: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        offset = 0
        while True:
            page = await self.list_products(limit=page_size, offset=offset, **filters)
            for p in page["products"]:
                yield p
            offset += page_size
            if offset >= page["count"]:
                break

    # ---------- internals ----------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
            retry=retry_if_exception_type(
                (httpx.TransportError, httpx.RemoteProtocolError)
            ),
            reraise=True,
        ):
            with attempt:
                return await self._do_request(method, path, params=params, json=json)
        raise RuntimeError("unreachable")  # type-checker hint

    async def _do_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = await self._auth_headers()
        log.debug("Medusa %s %s params=%s", method, path, params)
        resp = await self._http.request(
            method, path, params=params, json=json, headers=headers
        )
        if resp.status_code == 401 and self._is_jwt_mode():
            log.info("Medusa JWT expired, re-logging in")
            self._jwt = None
            headers = await self._auth_headers(force_login=True)
            resp = await self._http.request(
                method, path, params=params, json=json, headers=headers
            )
        if not resp.is_success:
            raise MedusaAPIError(resp.status_code, path, resp.text)
        return resp.json()

    def _is_jwt_mode(self) -> bool:
        return self._admin_token is None

    async def _auth_headers(self, *, force_login: bool = False) -> dict[str, str]:
        if self._admin_token:
            raw = f"{self._admin_token}:".encode()
            encoded = base64.b64encode(raw).decode()
            return {"Authorization": f"Basic {encoded}"}
        if self._jwt is None or force_login:
            self._jwt = await self._login()
        return {"Authorization": f"Bearer {self._jwt}"}

    async def _login(self) -> str:
        resp = await self._http.post(
            "/auth/user/emailpass",
            json={"email": self._admin_email, "password": self._admin_password},
            headers={"Content-Type": "application/json"},
        )
        if not resp.is_success:
            raise MedusaAPIError(resp.status_code, "/auth/user/emailpass", resp.text)
        return resp.json()["token"]
```

### `src/platform/medusa/service.py` (NEW)

```python
"""MedusaProductService — typed wrapper sobre HttpMedusaClient."""
from __future__ import annotations

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.models import MedusaProduct, MedusaProductPage


class MedusaProductService:
    def __init__(self, client: HttpMedusaClient) -> None:
        self.client = client

    async def get(self, product_id: str) -> MedusaProduct:
        raw = await self.client.get_product(product_id)
        return MedusaProduct.model_validate(raw)

    async def list(self, **kwargs) -> MedusaProductPage:
        raw = await self.client.list_products(**kwargs)
        return MedusaProductPage.model_validate(raw)
```

### `src/platform/medusa/composition.py` (NEW)

```python
"""DI factories para platform/medusa.

Patrón: lru_cache(1) para que el HttpMedusaClient (recurso de larga vida)
sea singleton por proceso. Mismo patrón que `src/platform/temporal/client.py`.
"""
from __future__ import annotations

from functools import lru_cache

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.service import MedusaProductService
from src.platform.medusa.settings import MedusaSettings


@lru_cache(maxsize=1)
def get_medusa_settings() -> MedusaSettings:
    return MedusaSettings()  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_medusa_client() -> HttpMedusaClient:
    s = get_medusa_settings()
    return HttpMedusaClient(
        base_url=s.base_url,
        admin_token=s.admin_token,
        admin_email=s.admin_email,
        admin_password=s.admin_password,
        timeout=s.http_timeout,
    )


@lru_cache(maxsize=1)
def get_medusa_product_service() -> MedusaProductService:
    return MedusaProductService(get_medusa_client())
```

## 3. Tests to add

Ver §1 (las rutas y nombres exactos están listadas). Pattern para los tests más críticos:

```python
# tests/platform/medusa/test_client_auth.py
import base64
import respx, httpx, pytest
from src.platform.medusa.client import HttpMedusaClient


@pytest.mark.asyncio
async def test_basic_auth_header_uses_token_then_colon():
    c = HttpMedusaClient(base_url="https://m.test", admin_token="sk_xyz")
    expected = "Basic " + base64.b64encode(b"sk_xyz:").decode()
    with respx.mock(base_url="https://m.test") as r:
        route = r.get("/admin/products").mock(
            return_value=httpx.Response(200, json={"products": [], "count": 0, "offset": 0, "limit": 50})
        )
        await c.list_products(limit=50)
        assert route.calls[0].request.headers["Authorization"] == expected
    await c.aclose()


# tests/platform/medusa/test_models_decimal.py
from decimal import Decimal
from src.platform.medusa.models import MedusaProduct


def test_amount_as_number_becomes_decimal():
    payload = {
        "id": "p1", "title": "X", "handle": "x", "status": "published",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "variants": [{"id": "v1", "title": "u", "prices": [
            {"id": "pr1", "amount": 49.99, "currency_code": "usd"}
        ]}],
    }
    p = MedusaProduct.model_validate(payload)
    assert p.variants[0].prices[0].amount == Decimal("49.99")


def test_amount_as_string_becomes_decimal():
    payload = {
        "id": "p1", "title": "X", "handle": "x", "status": "published",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "variants": [{"id": "v1", "title": "u", "prices": [
            {"id": "pr1", "amount": "49.99", "currency_code": "usd"}
        ]}],
    }
    p = MedusaProduct.model_validate(payload)
    assert p.variants[0].prices[0].amount == Decimal("49.99")


# tests/platform/medusa/test_default_fields.py
from src.platform.medusa.client import DEFAULT_PRODUCT_FIELDS


def test_default_fields_contains_both_variants_and_variants_prices():
    fields = DEFAULT_PRODUCT_FIELDS.split(",")
    assert "*variants" in fields
    assert "*variants.prices" in fields  # gotcha §4.1 de la guía Medusa
```

## 4. Replay fixture refresh

N/A. No tocamos workflows.

## 5. Verification commands (run between every PR)

```bash
# Type check + lint
uv run ruff check src/platform/medusa
uv run ty check src/platform/medusa  # si el repo usa ty; si no, mypy

# Unit
uv run pytest tests/platform/medusa/ -x

# Hard rules grep (este HU NO toca workflows ni tools, así que solo R-DIP aplica)
grep -rEn "^from (temporalio|exoclaw|src\.(sales|remarketing|catalog_sync)_whatsapp)" src/platform/medusa/ \
  || echo "R-DIP (platform/medusa no depende de agentes ni temporalio) ok"

# Confirma que platform/ no se renombró a core/shared/common
find src/ -maxdepth 1 -type d \( -name core -o -name shared -o -name common -o -name domains \) \
  | (! grep -q .) && echo "platform naming ok"
```

## 6. Smoke-test recipe

Cuando la PR-4 pase, validar contra un Medusa real (puede ser staging o local):

```bash
# 1) Health check de Medusa
curl -i "$MEDUSA_BASE_URL/health"

# 2) Listado mínimo (con curl, sin Python — confirma token + URL antes de codear contra él)
curl -s -u "$MEDUSA_ADMIN_TOKEN:" \
  "$MEDUSA_BASE_URL/admin/products?limit=1&fields=id,title,handle,*variants,*variants.prices" \
  | jq '.products[0] | {id, title, handle, prices: [.variants[].prices[]?.amount]}'

# 3) Equivalente en Python (requiere las env vars exportadas)
uv run python -c "
import asyncio
from src.platform.medusa.composition import get_medusa_product_service

async def main():
    svc = get_medusa_product_service()
    page = await svc.list(status='published', limit=2)
    for p in page.products:
        prices = [v.prices[0].amount for v in p.variants if v.prices]
        print(p.handle, '|', p.title, '|', prices)

asyncio.run(main())
"
```

Si el Python script imprime al menos 1 producto con un `Decimal(...)` en `prices`, esta HU está completa.

## 7. Rollback strategy

Cada PR es revertible independiente:
- PR-1 revert: removes deps + settings module. PR-2..4 quedarían rotas — revert también esos commits.
- PR-2..4: cada uno aporta un módulo nuevo en una carpeta nueva (`platform/medusa/`). `git revert <sha>` quita el módulo. Ninguno modifica código existente, así que no rompe agentes existentes.

## 8. Coordination updates

Si existe `agent_coordination/active_work.md`:
- Append: `| 2026-05-07T... | exoclaw-implementer | catalog-01 PR-1 deps + settings | done |`.
- Por cada PR mergeado, marcar `done`.

ADRs en `decisions.md` (5 líneas):
- `ADR-2026-05-07-01: HttpMedusaClient en src/platform/medusa/`. Razón: cross-agent. Patrón idéntico a `src/platform/temporal/client.py`.
- `ADR-2026-05-07-02: Pydantic v2 (NO en boundary, solo dentro del proceso)`. Razón: tipado fuerte de la respuesta de Medusa sin violar R-JSON.

## 9. Risks I'm carrying forward from the refinement

- **R1**: `MEDUSA_BASE_URL` por entorno. Mitigación: HU-05 cierra el wiring de env vars / K8s secret.
- **R2**: Confirmar Medusa versión 2.12.5 contra `hubara_backend/medusa-backend/package.json`. Mitigación: HU-05 valida.
- **R6**: Pydantic Decimal-from-JSON-number. Mitigación: tests cubren ambos paths (number / string).

---

**Status**: refinement validado, plan listo. **Stop point**: confirmar con el user antes de aplicar PR-1 (`uv` add + creación de archivos). Output esperado por usuario después del go-ahead: 4 PRs verdes secuenciales.
