# Consultar productos de Medusa v2 desde un backend Python

> Guía de integración para implementar un cliente Python (`HttpMedusaClient`) que consulta el catálogo del backend Medusa de Hubara y expone precios, imágenes y metadatos completos del producto.
>
> **Audiencia**: una AI / desarrollador que va a montar el feature desde cero.
> **Backend objetivo**: `hubara_backend/medusa-backend` corriendo Medusa **2.12.5** (ver `medusa-backend/package.json`).
> **No se necesita ningún endpoint custom**: todo se resuelve con la Admin API estándar.

---

## 0. TL;DR

1. **No crees endpoints custom**. Usa `GET /admin/products` y `GET /admin/products/:id` con el parámetro `fields=` expandido.
2. **Auth**: el camino correcto para un backend Python (server-to-server) es una **Secret API Key** enviada como `Authorization: Basic <token>`. JWT con email/password también funciona pero requiere relogin cada 24h.
3. **Precios**: en Medusa v2 los `amount` están en **unidad mayor** (ej. `49.99` significa $49.99, no 49 centavos). Esto rompe la intuición heredada de v1.
4. Implementa un único `HttpMedusaClient` (httpx async + Pydantic v2) y exponlo con dos métodos: `get_product(id, fields=...)` y `list_products(query=..., fields=..., limit=..., offset=...)`.

---

## 1. Versión y stack

| Componente | Versión / detalle |
|---|---|
| Medusa | **2.12.5** (`@medusajs/medusa@2.12.5` en `medusa-backend/package.json`) |
| File provider | `@medusajs/file-s3` apuntando a Cloudflare R2 (`medusa-config.ts` lo configura cuando `S3_ACCESS_KEY_ID` está presente). Las URLs de imágenes vendrán del `S3_PUBLIC_URL` configurado. |
| Auth admin | JWT (default 24h) o Secret API Key (long-lived) |
| CORS | `STORE_CORS`, `ADMIN_CORS`, `AUTH_CORS` configurables vía env (defaults `*`). Para server-to-server CORS no aplica. |

> ⚠️ El uploader Swift muestra "Medusa 2.13.4" en el header (`MainView.swift:78`) — es un texto cosmético desactualizado. La versión real es 2.12.5.

---

## 2. ¿Por qué no hace falta endpoint custom?

`GET /admin/products` ya devuelve **todo**: variantes, precios crudos, imágenes, tags, categorías, colecciones, canales de venta, opciones y `metadata`. El truco está en pedir explícitamente las relaciones con `fields=...,*relacion`. Si no pasas `fields`, Medusa devuelve un set por defecto y ningún backend externo querrá lidiar con ese contrato implícito.

Cuándo SÍ tendría sentido un endpoint custom (no es el caso ahora):
- Necesitas un shape de respuesta muy distinto (ej. agregaciones por categoría).
- Quieres cachear/transformar antes de llegar al cliente.
- Quieres exponer datos a un consumidor sin admin token.

Para "leer productos con todo su contexto" → endpoint estándar, suficiente.

---

## 3. Autenticación

Hay dos modos. **Para un backend Python usa la Opción A.**

### 3.1. Opción A — Secret API Key (recomendada server-to-server)

Es el equivalente v2 a un Personal Access Token: long-lived, revocable desde el panel admin, sin caducidad por defecto.

**Cómo crearla** (una vez, manualmente):
1. Login en el dashboard de Medusa (`https://<tu-medusa>/app`).
2. **Settings → Developer → Secret API Keys → Create**.
3. Copia el token mostrado (se muestra una sola vez).
4. Guárdalo en una env var `MEDUSA_ADMIN_TOKEN` del proyecto Python.

**Cómo se usa**:
```
Authorization: Basic <token-base64>
```
- El header es **Basic**, no Bearer.
- El "user" es el token; no hay password. Construcción: `base64(token + ":")`.
- Equivalente con `httpx.BasicAuth(token, "")` (passing empty password).

**Endpoint para crear/listar/revocar programáticamente** (si quisieras automatizarlo, opcional):
- `POST /admin/api-keys` con body `{ "title": "python-backend", "type": "secret" }` — requiere un admin autenticado por JWT.
- Documentación: <https://docs.medusajs.com/resources/commerce-modules/api-key>

### 3.2. Opción B — JWT con email+password

Es lo que hace el uploader macOS hoy. Útil si tu backend Python actúa como gateway de un usuario humano cuyas credenciales conoces.

**Login**:
```http
POST /auth/user/emailpass
Content-Type: application/json

{ "email": "...", "password": "..." }
```
**Respuesta**: `{ "token": "<jwt>" }` (sin envoltura).

**Uso del JWT**:
```
Authorization: Bearer <jwt>
```

**TTL**: por defecto **1 día** (`projectConfig.http.jwtExpiresIn = "1d"`). Configurable en `medusa-config.ts`.

**Refresh**: `POST /auth/token/refresh` con el JWT actual (header Bearer) devuelve un nuevo token. No hay refresh-token rotativo separado. En la práctica: o relogeas o llamas a refresh proactivamente antes de expirar.

**Cuándo elegir B sobre A**: casi nunca. Solo si no puedes provisionar una Secret Key (ej. estás replicando exactamente el comportamiento del uploader macOS). Para Python server-to-server, **A es estrictamente mejor**.

---

## 4. El endpoint `GET /admin/products`

### 4.1. Sintaxis del parámetro `fields`

Es lista separada por comas. Soporta:

| Sintaxis | Significado |
|---|---|
| `title,handle` | Selecciona estos escalares (whitelist; lo demás se omite) |
| `+description` | Añade al set por defecto (sin reemplazarlo) |
| `-handle` | Quita un campo del set por defecto |
| `*variants` | Expande la relación `variants` con TODOS sus escalares |
| `*variants.prices` | Expansión anidada — el `*` aplica al último segmento |
| `variants.title` | Selecciona un escalar específico de la relación |

> **Gotcha crítico**: `*variants` **NO** incluye automáticamente `variants.prices`. Tienes que pedir ambos explícitamente: `fields=*variants,*variants.prices`.

### 4.2. Relaciones disponibles en `Product`

```
*variants
*variants.prices
*variants.options
*variants.options.option        # ascender de la relación valor → opción
*options
*options.values
*images
*tags
*categories
*collection
*sales_channels
*type
```

`metadata` y `thumbnail` son **columnas escalares** del producto (no relaciones), así que se piden sin `*`:
```
?fields=metadata,thumbnail
```

### 4.3. Filtros y paginación

| Param | Ejemplo | Notas |
|---|---|---|
| `q` | `q=lavanda` | Búsqueda full-text en title, handle, description |
| `id` | `id=prod_01ABC` | Soporta arrays: `id[]=prod_01&id[]=prod_02` |
| `title` | `title=Lavanda` | Match exacto |
| `handle` | `handle=mi-producto` | |
| `status` | `status=published` | `draft`, `proposed`, `published`, `rejected` |
| `category_id` | `category_id=pcat_01ABC` | Acepta array |
| `collection_id` | | Acepta array |
| `sales_channel_id` | | |
| `tags` | `tags[]=ptag_01ABC` | Por ID |
| `created_at` / `updated_at` | `created_at[$gte]=2026-01-01` | Operadores `$gt/$gte/$lt/$lte` |
| `limit` | `limit=50` | Default ~20, máx ~200 |
| `offset` | `offset=100` | |
| `order` | `order=-created_at` | Prefijo `-` para DESC |

### 4.4. Shape de respuesta

**Listado** `GET /admin/products?...`:
```json
{
  "products": [ /* ... */ ],
  "count": 142,
  "offset": 0,
  "limit": 20
}
```

**Detalle** `GET /admin/products/:id?...`:
```json
{
  "product": { /* ... */ }
}
```

**Producto completo (con todas las expansiones)**:
```json
{
  "product": {
    "id": "prod_01HXYZ...",
    "title": "Vela Aroma Lavanda",
    "handle": "vela-aroma-lavanda",
    "description": "...",
    "status": "published",
    "thumbnail": "https://r2.example.com/abc.jpg",
    "weight": null, "length": null,
    "height": 12, "width": 8,
    "metadata": { /* objeto libre o null */ },
    "created_at": "2026-01-15T...",
    "updated_at": "2026-02-20T...",

    "variants": [
      {
        "id": "variant_01HXYZ...",
        "title": "Unico",
        "sku": null,
        "manage_inventory": false,
        "allow_backorder": false,
        "prices": [
          {
            "id": "price_01HXYZ...",
            "amount": 49.99,
            "currency_code": "usd",
            "min_quantity": null,
            "max_quantity": null,
            "price_list_id": null,
            "rules": {},
            "created_at": "...",
            "updated_at": "..."
          }
        ],
        "options": [
          { "id": "optval_01...", "value": "Unico" }
        ]
      }
    ],

    "options": [
      {
        "id": "opt_01...",
        "title": "Unico",
        "values": [ { "id": "optval_01...", "value": "Unico" } ]
      }
    ],

    "images": [
      { "id": "img_01...", "url": "https://r2.example.com/foto1.jpg", "rank": 0 },
      { "id": "img_02...", "url": "https://r2.example.com/foto2.jpg", "rank": 1 }
    ],

    "tags": [
      { "id": "ptag_01...", "value": "Aroma: Lavanda" },
      { "id": "ptag_02...", "value": "Color: Morado" }
    ],

    "categories": [
      { "id": "pcat_01...", "name": "Velas", "handle": "velas", "parent_category_id": null }
    ],

    "collection": { "id": "pcol_01...", "title": "Verano 2026", "handle": "verano-2026" },

    "sales_channels": [
      { "id": "sc_01...", "name": "WhatsApp" }
    ]
  }
}
```

### 4.5. Precios — la trampa importante

> **En Medusa v2 los precios se almacenan en unidad MAYOR.**
>
> Ejemplo: $49.99 → `amount: 49.99` (NO `4999`).
>
> En v1 era en centavos. Si vienes de v1, ajusta tu mental model. Documentación oficial: <https://docs.medusajs.com/learn/introduction/from-v1-to-v2>.

**En Python**: parsea `amount` como `decimal.Decimal`, **no** como `float`, para evitar errores de redondeo. JSON puede entregarlo como número o string según el campo; Pydantic v2 maneja ambos si declaras `Decimal`.

> 🔎 Para Hubara: el uploader Swift hoy hace `Int(product.precio)` al crear/actualizar (`RepositoryImpl.swift:115` y `:161`). Como el CSV trae COP (sin decimales), funciona — pero si algún día introducen monedas con decimales, **truncará**. Documentar este riesgo aparte; no es bloqueante para el feature de lectura.

`prices` es un array porque Medusa soporta múltiples monedas y price lists por variante. Para "el precio que se le muestra al cliente final ya con descuentos aplicados" necesitas `calculated_price`, que requiere contexto (region/sales_channel/customer) y normalmente se consulta vía Store API o vía Query. Para "leer lo que está cargado", `prices[]` cruda es lo correcto.

### 4.6. Imágenes

`images[]` viene **ordenado por `rank`**. La portada vive en el escalar `thumbnail` del producto (suele coincidir con `images[0].url` pero no está garantizado — usa `thumbnail` si lo necesitas explícito).

Las URLs apuntan al `S3_PUBLIC_URL` configurado en `medusa-config.ts` (Cloudflare R2 en producción). Son públicas; no requieren firmar.

### 4.7. Otros gotchas

- `fields` es **whitelisting**: si lo pasas, solo recibes lo que pides + `id`. Usa `+campo` para añadir al default sin reemplazar.
- Filtrar por atributos anidados de variante usa notación bracket: `variants[sku]=ABC`, `variants[q]=texto`.
- `metadata` puede no aparecer en la respuesta si no lo pides explícito en `fields` en algunos endpoints; **siempre pídelo** si lo vas a usar.
- Códigos de error comunes: `401` (token inválido o expirado), `404` (producto no existe), `400` (query mal formado, ej. `fields` con relación inexistente).

---

## 5. Implementación Python — `HttpMedusaClient`

### 5.1. Dependencias

```toml
# pyproject.toml
[project]
dependencies = [
  "httpx>=0.27,<1",
  "pydantic>=2.6,<3",
  "tenacity>=8.2,<10",   # reintentos
]
```

> No existe SDK oficial de Medusa para Python. La práctica estándar es `httpx` (recomendado, async-first) o `requests` (sync).

### 5.2. Configuración (env vars)

```env
MEDUSA_BASE_URL=https://medusa.hubara.example.com
MEDUSA_ADMIN_TOKEN=sk_live_xxxxxxxxxxxxxxxx       # Secret API Key (Opción A)
# Alternativa Opción B (no recomendada):
# MEDUSA_ADMIN_EMAIL=admin@hubara.com
# MEDUSA_ADMIN_PASSWORD=...
MEDUSA_HTTP_TIMEOUT=30
```

### 5.3. Cliente HTTP completo

```python
# medusa/client.py
from __future__ import annotations

import base64
import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, AsyncIterator, Optional

import httpx
from pydantic import BaseModel, Field
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


class HttpMedusaClient:
    """
    Async HTTP client for the Medusa v2 Admin API.

    Auth modes (auto-selected):
      A) Secret API Key → admin_token  → Authorization: Basic <base64(token:)>
      B) JWT email/password → admin_email + admin_password → Authorization: Bearer <jwt>
         (auto-relogin on 401)
    """

    def __init__(
        self,
        base_url: str,
        *,
        admin_token: Optional[str] = None,
        admin_email: Optional[str] = None,
        admin_password: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        if not admin_token and not (admin_email and admin_password):
            raise ValueError(
                "Provide either admin_token (recommended) "
                "or admin_email + admin_password."
            )
        self.base_url = base_url.rstrip("/")
        self._admin_token = admin_token
        self._admin_email = admin_email
        self._admin_password = admin_password
        self._jwt: Optional[str] = None
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    # ---------- public API ----------

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
        """Returns the raw `product` dict from /admin/products/:id."""
        data = await self._request("GET", f"/admin/products/{product_id}", params={"fields": fields})
        return data["product"]

    async def list_products(
        self,
        *,
        q: Optional[str] = None,
        ids: Optional[list[str]] = None,
        title: Optional[str] = None,
        handle: Optional[str] = None,
        status: Optional[str] = None,
        category_id: Optional[str] = None,
        collection_id: Optional[str] = None,
        sales_channel_id: Optional[str] = None,
        tag_ids: Optional[list[str]] = None,
        limit: int = 50,
        offset: int = 0,
        order: str = "-created_at",
        fields: str = DEFAULT_PRODUCT_FIELDS,
    ) -> dict[str, Any]:
        """
        Returns the raw {products, count, offset, limit} dict from /admin/products.
        """
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "order": order,
            "fields": fields,
        }
        if q: params["q"] = q
        if ids: params["id[]"] = ids
        if title: params["title"] = title
        if handle: params["handle"] = handle
        if status: params["status"] = status
        if category_id: params["category_id"] = category_id
        if collection_id: params["collection_id"] = collection_id
        if sales_channel_id: params["sales_channel_id"] = sales_channel_id
        if tag_ids: params["tags[]"] = tag_ids
        return await self._request("GET", "/admin/products", params=params)

    async def iter_products(
        self,
        *,
        page_size: int = 100,
        **filters: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yields every product matching `filters`, paginating transparently."""
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
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
            retry=retry_if_exception_type((httpx.TransportError, httpx.RemoteProtocolError)),
            reraise=True,
        ):
            with attempt:
                return await self._do_request(method, path, params=params, json=json)
        raise RuntimeError("unreachable")  # for type checker

    async def _do_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        headers = await self._auth_headers()
        log.debug("Medusa %s %s params=%s", method, path, params)
        resp = await self._http.request(method, path, params=params, json=json, headers=headers)
        if resp.status_code == 401 and self._is_jwt_mode():
            # JWT expired — relogin once
            log.info("Medusa JWT expired, re-logging in")
            self._jwt = None
            headers = await self._auth_headers(force_login=True)
            resp = await self._http.request(method, path, params=params, json=json, headers=headers)
        if not resp.is_success:
            raise MedusaAPIError(resp.status_code, path, resp.text)
        return resp.json()

    def _is_jwt_mode(self) -> bool:
        return self._admin_token is None

    async def _auth_headers(self, *, force_login: bool = False) -> dict[str, str]:
        if self._admin_token:
            # HTTP Basic with token-as-username, empty password
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


# Default expansion: everything you typically want when reading a product end-to-end.
# Documented gotcha: *variants does NOT auto-include *variants.prices, both must be listed.
DEFAULT_PRODUCT_FIELDS = ",".join([
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
])
```

### 5.4. Modelos Pydantic v2

Capa de tipado opcional pero muy recomendable. Permite validación + autocompletado en el resto del backend.

```python
# medusa/models.py
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MedusaPrice(_Base):
    id: str
    amount: Decimal                     # MAJOR units — see §4.5
    currency_code: str
    min_quantity: Optional[int] = None
    max_quantity: Optional[int] = None
    price_list_id: Optional[str] = None
    rules: dict[str, Any] = Field(default_factory=dict)


class MedusaOptionValue(_Base):
    id: str
    value: str


class MedusaVariant(_Base):
    id: str
    title: str
    sku: Optional[str] = None
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
    handle: Optional[str] = None
    parent_category_id: Optional[str] = None


class MedusaCollection(_Base):
    id: str
    title: str
    handle: Optional[str] = None


class MedusaSalesChannel(_Base):
    id: str
    name: str


class MedusaProduct(_Base):
    id: str
    title: str
    handle: str
    description: Optional[str] = None
    status: str
    thumbnail: Optional[str] = None
    height: Optional[float] = None
    width: Optional[float] = None
    length: Optional[float] = None
    weight: Optional[float] = None
    metadata: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    variants: list[MedusaVariant] = Field(default_factory=list)
    options: list[MedusaOption] = Field(default_factory=list)
    images: list[MedusaImage] = Field(default_factory=list)
    tags: list[MedusaTag] = Field(default_factory=list)
    categories: list[MedusaCategory] = Field(default_factory=list)
    collection: Optional[MedusaCollection] = None
    sales_channels: list[MedusaSalesChannel] = Field(default_factory=list)


class MedusaProductPage(_Base):
    products: list[MedusaProduct]
    count: int
    offset: int
    limit: int
```

Helpers tipados sobre el cliente raw:

```python
# medusa/service.py
from .client import HttpMedusaClient
from .models import MedusaProduct, MedusaProductPage

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

### 5.5. Uso

```python
# main.py (FastAPI o lo que sea)
import os
from medusa.client import HttpMedusaClient
from medusa.service import MedusaProductService

async def example():
    client = HttpMedusaClient(
        base_url=os.environ["MEDUSA_BASE_URL"],
        admin_token=os.environ["MEDUSA_ADMIN_TOKEN"],
    )
    async with client.session():
        svc = MedusaProductService(client)

        # Detalle
        product = await svc.get("prod_01HXYZ...")
        print(product.title, product.variants[0].prices[0].amount)

        # Listado paginado
        page = await svc.list(status="published", limit=20)
        print(f"{page.count} productos totales, mostrando {len(page.products)}")

        # Iteración total (paginado transparente, devuelve dicts crudos)
        async for raw in client.iter_products(status="published", page_size=100):
            print(raw["title"])
```

---

## 6. Ejemplos `curl` para validar antes de codear

Sustituye `$BASE` y `$TOKEN`. Sirve para confirmar conectividad y permisos antes de empezar a escribir Python.

```bash
# 1) Healthcheck — debería responder 200
curl -i "$BASE/health"

# 2) Listado mínimo (devolverá poco si no pides fields)
curl -s -u "$TOKEN:" "$BASE/admin/products?limit=2" | jq '.products[0] | keys'

# 3) Listado completo (un solo producto, todos los campos esperados)
curl -s -u "$TOKEN:" \
  "$BASE/admin/products?limit=1&fields=id,title,handle,status,thumbnail,*variants,*variants.prices,*images,*tags,*categories,*collection,*sales_channels" \
  | jq '.products[0]'

# 4) Detalle por ID
curl -s -u "$TOKEN:" \
  "$BASE/admin/products/prod_01HXYZ?fields=*variants,*variants.prices,*images,*tags" \
  | jq '.product'

# 5) Búsqueda
curl -s -u "$TOKEN:" \
  "$BASE/admin/products?q=lavanda&limit=5&fields=id,title,*variants.prices" \
  | jq '.products[] | {id, title, prices: [.variants[].prices[]?.amount]}'
```

> Nota: `curl -u "$TOKEN:"` envía `Authorization: Basic <base64(TOKEN:)>` automáticamente. Equivalente a lo que hace el cliente Python.

Variante con JWT (Opción B):
```bash
JWT=$(curl -s -X POST "$BASE/auth/user/emailpass" \
        -H 'Content-Type: application/json' \
        -d '{"email":"...","password":"..."}' | jq -r .token)
curl -s -H "Authorization: Bearer $JWT" "$BASE/admin/products?limit=1" | jq
```

---

## 7. Estructura sugerida del módulo en el backend Python

```
your_backend/
├── medusa/
│   ├── __init__.py
│   ├── client.py          # HttpMedusaClient + MedusaAPIError + DEFAULT_PRODUCT_FIELDS
│   ├── models.py          # Pydantic models (MedusaProduct, MedusaVariant, ...)
│   ├── service.py         # MedusaProductService con métodos tipados
│   └── settings.py        # Pydantic BaseSettings que lee MEDUSA_* env vars
├── api/
│   └── products.py        # Tus endpoints HTTP que delegan en MedusaProductService
└── tests/
    └── medusa/
        ├── test_client.py        # con respx (mock httpx)
        └── test_service.py
```

`settings.py` recomendado:

```python
# medusa/settings.py
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class MedusaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEDUSA_", extra="ignore")
    base_url: str
    admin_token: str | None = None
    admin_email: str | None = None
    admin_password: str | None = None
    http_timeout: float = 30.0
```

Inyección (FastAPI):
```python
from fastapi import Depends
from .medusa.settings import MedusaSettings
from .medusa.client import HttpMedusaClient
from .medusa.service import MedusaProductService

async def medusa_service() -> MedusaProductService:
    s = MedusaSettings()
    client = HttpMedusaClient(
        base_url=s.base_url,
        admin_token=s.admin_token,
        admin_email=s.admin_email,
        admin_password=s.admin_password,
        timeout=s.http_timeout,
    )
    try:
        yield MedusaProductService(client)
    finally:
        await client.aclose()
```

---

## 8. Manejo de errores

| Caso | Excepción / acción |
|---|---|
| `401 Unauthorized` (JWT mode) | Relogin automático una vez (ya implementado). |
| `401 Unauthorized` (Secret token) | Token revocado o mal copiado → `MedusaAPIError`, no reintentar. |
| `404 Not Found` en `/admin/products/:id` | Producto no existe → mapear a tu propio `ProductNotFound` en `service.py`. |
| `400 Bad Request` con `fields=` | Pediste una relación inexistente. Loggea `resp.body` y revisa la lista en §4.2. |
| Timeouts / `httpx.TransportError` | Reintento exponencial (3 intentos) ya implementado vía tenacity. |
| `429 Too Many Requests` | Medusa OSS no rate-limita por defecto, pero un proxy delante (Cloudflare/Railway) sí podría. Si aparece, añade backoff y un `circuit-breaker`. |

---

## 9. Testing

Usa `respx` para mockear httpx sin tocar red:

```python
# tests/medusa/test_service.py
import respx, httpx, pytest
from medusa.client import HttpMedusaClient
from medusa.service import MedusaProductService

@pytest.mark.asyncio
async def test_get_product_parses_prices_as_decimal():
    client = HttpMedusaClient(base_url="https://m.test", admin_token="sk_x")
    svc = MedusaProductService(client)
    fixture = {
        "product": {
            "id": "prod_1", "title": "X", "handle": "x", "status": "published",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "variants": [{"id":"v1","title":"u","prices":[
                {"id":"p1","amount":49.99,"currency_code":"usd"}
            ]}],
        }
    }
    with respx.mock(base_url="https://m.test") as r:
        r.get("/admin/products/prod_1").mock(return_value=httpx.Response(200, json=fixture))
        product = await svc.get("prod_1")
        assert str(product.variants[0].prices[0].amount) == "49.99"
    await client.aclose()
```

Tests recomendados:
1. Auth basic header se construye correctamente (`base64(token + ":")`).
2. JWT mode: 401 → relogin → reintento (verifica que `/auth/user/emailpass` se llama 1 vez).
3. `fields` se serializa como string en el query (no como lista).
4. `iter_products` paginar transparente cuando `count > limit`.
5. Pydantic acepta `amount` como número y como string y siempre lo expone como `Decimal`.
6. `MedusaAPIError` se levanta con `status_code`, `path` y `body`.

---

## 10. Checklist final para la AI implementadora

Antes de dar el feature por hecho, confirma TODOS estos puntos:

- [ ] Has añadido `httpx`, `pydantic`, `tenacity` y `pydantic-settings` al `pyproject.toml`.
- [ ] El backend lee `MEDUSA_BASE_URL` + `MEDUSA_ADMIN_TOKEN` desde env (no hardcoded).
- [ ] Validaste con `curl` que `GET $BASE/admin/products?limit=1` con tu token responde 200.
- [ ] El cliente envía `Authorization: Basic` cuando hay `admin_token` y `Bearer` cuando hay credenciales.
- [ ] El cliente reloguea automáticamente en JWT mode al recibir 401.
- [ ] `DEFAULT_PRODUCT_FIELDS` incluye `*variants,*variants.prices` (NO solo `*variants`).
- [ ] Los `Decimal` se preservan a través del JSON (probado con un producto que tenga `amount=49.99`).
- [ ] Hay test unitario con `respx` cubriendo al menos: detalle exitoso, 404, 401-reauth, paginación.
- [ ] Logging: cada request loggea método+path+status (sin loggear el token).
- [ ] El servicio expone únicamente lectura por ahora — no añadas write methods sin discutirlo.
- [ ] El doc de tu endpoint Python cita esta integración: "Cliente Medusa Admin v2 según `MEDUSA_PRODUCT_QUERY_FROM_PYTHON.md`".

---

## Apéndice A — Referencias oficiales

- Admin API Reference: <https://docs.medusajs.com/api/admin>
- Auth routes: <https://docs.medusajs.com/resources/commerce-modules/auth/authentication-route>
- API Key module (Secret keys): <https://docs.medusajs.com/resources/commerce-modules/api-key>
- Secret API Keys (panel): <https://docs.medusajs.com/user-guide/settings/developer/secret-api-keys>
- v1 → v2 changes (precios en unidad mayor): <https://docs.medusajs.com/learn/introduction/from-v1-to-v2>
- Application config (`jwtExpiresIn`): <https://docs.medusajs.com/learn/configurations/medusa-config>
- Sintaxis de `fields` con ejemplos: <https://docs.medusajs.com/resources/commerce-modules/product/guides/price>
- JS SDK auth (espejo de cómo el JS-SDK oficial hace lo mismo): <https://docs.medusajs.com/resources/js-sdk/auth/overview>

## Apéndice B — Diferencias clave con lo que hace el uploader Swift hoy

El uploader macOS (`HubaraMedusaUploader/Sources/Data/MedusaClient.swift`) usa **Opción B** (JWT) y solo llama endpoints de **escritura**. Para el backend Python NO copies su patrón de auth: usa Secret Key. Ambos clientes pueden coexistir contra el mismo Medusa sin conflicto.

| Aspecto | Uploader Swift | Backend Python (esta guía) |
|---|---|---|
| Auth | JWT email/pass, JWT en Keychain | Secret API Key en env var |
| Reintentos | Ninguno | Tenacity 3x exponencial |
| Tipos | Codable estructuras mínimas | Pydantic v2 con todas las relaciones |
| Operaciones | CRUD + uploads | Solo lectura (por ahora) |
| Concurrencia | `URLSession` + async/await | `httpx.AsyncClient` |
