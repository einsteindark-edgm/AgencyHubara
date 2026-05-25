"""HttpMedusaClient — async client for the Medusa v2 Admin API.

Source: features/catalogAgent/MEDUSA_PRODUCT_QUERY_FROM_PYTHON.md §5.3,
adaptado al layout `platform/`.

Auth modes (auto-selected):
  A) Secret API Key  → admin_token  → Authorization: Basic base64(token + ":")
  B) JWT email/pass  → admin_email + admin_password → Bearer <jwt>
     (auto-relogin on 401)

Reintentos: tenacity con backoff exponencial sobre `httpx.TransportError`
y `httpx.RemoteProtocolError`. Errores HTTP no-2xx levantan
`MedusaAPIError` sin reintentar (excepto el 401-relogin del path JWT).
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


# Default expansion: todo lo que tipicamente queremos al leer un producto
# end-to-end. Gotcha §4.1 de la guia: `*variants` NO incluye automaticamente
# `*variants.prices` — hay que pedirlos por separado.
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
                "Provide either admin_token (recommended) "
                "or admin_email + admin_password."
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

    # ---------- lifecycle ----------

    async def aclose(self) -> None:
        await self._http.aclose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator["HttpMedusaClient"]:
        try:
            yield self
        finally:
            await self.aclose()

    # ---------- public API ----------

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
            # Medusa v2 espera array notation: ?status[]=published
            params["status[]"] = [status] if isinstance(status, str) else status
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
            page = await self.list_products(
                limit=page_size, offset=offset, **filters
            )
            for p in page["products"]:
                yield p
            offset += page_size
            if offset >= page["count"]:
                break

    # ---------- customers (HU c4e3416f order registration) -------------

    async def list_customers(
        self,
        *,
        email: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """List customers, optionally filtered by email.

        Used to find an existing WhatsApp customer (synthesized email) before
        creating a new one (avoid duplicates on retry).
        """
        params: dict[str, Any] = {"limit": limit}
        if email:
            params["email"] = email
        return await self._request("GET", "/admin/customers", params=params)

    async def create_customer(
        self,
        *,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a customer in Medusa.

        Medusa v2 auto-creates a guest customer from the draft order email if
        none exists, but we create it explicitly to (a) tag with metadata
        like `session_key` for cross-reference and (b) reuse on retry to
        avoid duplicate guest customers.

        Returns the parsed `customer` object (NOT the envelope — the wrapping
        `{"customer": ...}` is unwrapped here for ergonomic use).
        """
        payload: dict[str, Any] = {"email": email}
        if first_name:
            payload["first_name"] = first_name
        if last_name:
            payload["last_name"] = last_name
        if phone:
            payload["phone"] = phone
        if metadata:
            payload["metadata"] = metadata
        data = await self._request("POST", "/admin/customers", json=payload)
        return data["customer"]

    # ---------- shipping options -------------------------------------

    async def list_shipping_options(
        self,
        *,
        region_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List configured shipping options.

        The adapter queries this lazily on first order registration to
        discover the operator's pre-created shipping option (the user said
        "ya tengo uno creado"). Filters by `region_id` if provided.
        """
        params: dict[str, Any] = {"limit": limit}
        if region_id:
            params["region_id"] = region_id
        return await self._request(
            "GET", "/admin/shipping-options", params=params
        )

    # ---------- order querying (dashboard) ---------------------------

    # Default expansion para list/get_order — incluye items + addresses +
    # customer + sales_channel + transactions. Equivalente al
    # DEFAULT_PRODUCT_FIELDS pero para orders. NOTA: NO incluye `*fulfillments`
    # ni `*payment_collections.*` por defecto — son arrays pesados que solo
    # pedimos en GET /admin/orders/{id} (detalle).
    DEFAULT_ORDER_LIST_FIELDS = ",".join(
        [
            "id", "display_id", "status", "payment_status",
            "fulfillment_status", "email", "currency_code",
            "created_at", "updated_at", "total", "subtotal",
            "shipping_total", "tax_total", "discount_total",
            "metadata", "region_id", "customer_id", "sales_channel_id",
            "*items",
            "*shipping_address",
            "*billing_address",
            "*customer",
            "*sales_channel",
        ]
    )
    DEFAULT_ORDER_DETAIL_FIELDS = DEFAULT_ORDER_LIST_FIELDS + "," + ",".join(
        [
            "*fulfillments",
            "*payment_collections",
            "*payment_collections.payments",
            "*shipping_methods",
            "*transactions",
        ]
    )

    async def list_orders(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order: str = "-created_at",
        status: list[str] | None = None,
        sales_channel_id: list[str] | None = None,
        fields: str | None = None,
    ) -> dict[str, Any]:
        """List orders. Used by the dashboard `/api/orders/orders` endpoint.

        Returns the Medusa response envelope `{"orders": [...], "count": N,
        "offset": M, "limit": L}` — unchanged shape so the adapter can do
        its own DTO mapping.
        """
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "order": order,
            "fields": fields or self.DEFAULT_ORDER_LIST_FIELDS,
        }
        if status:
            params["status[]"] = status
        if sales_channel_id:
            params["sales_channel_id[]"] = sales_channel_id
        return await self._request("GET", "/admin/orders", params=params)

    async def get_order(
        self,
        order_id: str,
        *,
        fields: str | None = None,
    ) -> dict[str, Any]:
        """Get a single order by ID. Unwraps the `{"order": ...}` envelope."""
        data = await self._request(
            "GET",
            f"/admin/orders/{order_id}",
            params={"fields": fields or self.DEFAULT_ORDER_DETAIL_FIELDS},
        )
        return data["order"]

    async def list_draft_orders(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order: str = "-created_at",
        fields: str | None = None,
    ) -> dict[str, Any]:
        """List draft orders. Draft orders are the ones our `register_order`
        tool creates on sale close — they live in a separate Medusa endpoint
        until the operator marks them complete (`is_draft_order=True`).

        The dashboard merges drafts + orders so the operator sees the full
        kanban including pedidos recién cerrados pero no completados.
        """
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "order": order,
            "fields": fields or self.DEFAULT_ORDER_LIST_FIELDS,
        }
        return await self._request("GET", "/admin/draft-orders", params=params)

    async def get_draft_order(
        self,
        draft_order_id: str,
        *,
        fields: str | None = None,
    ) -> dict[str, Any]:
        """Get a single draft order by ID."""
        data = await self._request(
            "GET",
            f"/admin/draft-orders/{draft_order_id}",
            params={"fields": fields or self.DEFAULT_ORDER_DETAIL_FIELDS},
        )
        return data["draft_order"]

    # ---------- draft orders -----------------------------------------

    async def create_draft_order(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a Medusa v2 Draft Order via POST /admin/draft-orders.

        Spec reference: https://docs.medusajs.com/api/admin#draft-orders_postdraftorders
        Required by the spec: `sales_channel_id`, `email`, `region_id`,
        `currency_code`, `shipping_methods`, `metadata`. In practice the JS
        SDK example proves Medusa accepts a more lenient payload — the
        adapter normalizes/synthesizes the strict ones.

        Returns the parsed `draft_order` object (envelope unwrapped). On
        non-2xx, raises `MedusaAPIError` like every other client method —
        the caller (adapter) catches and converts to
        `OrderRegistrationResult(success=False, ...)`.
        """
        data = await self._request("POST", "/admin/draft-orders", json=payload)
        return data["draft_order"]

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
                return await self._do_request(
                    method, path, params=params, json=json
                )
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
