"""Cliente HTTP a Meta Graph API /catalogs/{catalog_id}/items_batch.

Endpoints relevantes:

  * `POST /v23.0/{catalog_id}/items_batch` — submit batch
  * `GET /v23.0/{catalog_id}/check_batch_request_status?handle=...` — poll status
  * `GET /v23.0/{catalog_id}/products` — verificar consistencia

Rate limits:
  * ~200 calls/h business app inicialmente (escalable a 10k/h con uso).
  * Header `X-Business-Use-Case-Usage` con porcentaje usado.

Retry policy: lo hace el caller (Temporal activity). Acá solo devolvemos
errores estructurados — el caller decide qué reintentar.
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

from src.platform.meta_catalog.dtos import (
    MetaBatchItemResult,
    MetaBatchRequest,
    MetaBatchResult,
    MetaCatalogItem,
)

logger = structlog.get_logger()

GRAPH_API_VERSION = "v23.0"


class MetaCatalogClient:
    name = "meta_catalog_client"

    def __init__(
        self,
        api_version: str = GRAPH_API_VERSION,
        base_url: str = "https://graph.facebook.com",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version

    async def upsert_batch(
        self, request: MetaBatchRequest
    ) -> MetaBatchResult:
        """Submits CREATE + UPDATE + DELETE en un único batch.

        Idempotencia: `retailer_id` debe ser estable (Hubara usa
        `CatalogProductDTO.id`). Repetir el batch es safe — Meta upserta.
        """
        requests_payload: list[dict[str, Any]] = []

        for item in request.creates:
            requests_payload.append({
                "method": "CREATE",
                "retailer_id": item.retailer_id,
                "data": _item_to_meta_data(item),
            })
        for item in request.updates:
            requests_payload.append({
                "method": "UPDATE",
                "retailer_id": item.retailer_id,
                "data": _item_to_meta_data(item),
            })
        for retailer_id in request.deletes:
            requests_payload.append({
                "method": "DELETE",
                "retailer_id": retailer_id,
            })

        if not requests_payload:
            return MetaBatchResult(
                handle=None,
                ok=True,
                submitted=0,
            )

        url = f"{self._base_url}/{self._api_version}/{request.catalog_id}/items_batch"
        body = {
            "access_token": request.access_token,
            "requests": requests_payload,
            "allow_upsert": request.allow_upsert,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=body)
        except httpx.HTTPError as e:
            return MetaBatchResult(
                handle=None,
                ok=False,
                submitted=len(requests_payload),
                error=f"transport: {e}",
            )

        if resp.status_code >= 300:
            logger.warning(
                "meta_catalog.upsert_batch.bad_status",
                status=resp.status_code,
                body=resp.text[:600],
                submitted=len(requests_payload),
            )
            return MetaBatchResult(
                handle=None,
                ok=False,
                submitted=len(requests_payload),
                error=f"http_{resp.status_code}: {resp.text[:400]}",
            )

        try:
            data = resp.json()
        except ValueError:
            return MetaBatchResult(
                handle=None,
                ok=False,
                submitted=len(requests_payload),
                error="bad_json",
            )

        handle = data.get("handles", [None])[0] if "handles" in data else data.get("handle")
        logger.info(
            "meta_catalog.upsert_batch.ok",
            handle=handle,
            submitted=len(requests_payload),
            creates=len(request.creates),
            updates=len(request.updates),
            deletes=len(request.deletes),
        )
        return MetaBatchResult(
            handle=handle,
            ok=True,
            submitted=len(requests_payload),
        )

    async def check_batch_status(
        self,
        catalog_id: str,
        handle: str,
        access_token: str,
    ) -> MetaBatchResult:
        url = (
            f"{self._base_url}/{self._api_version}/{catalog_id}"
            f"/check_batch_request_status"
        )
        params = {"access_token": access_token, "handle": handle}

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url, params=params)
        except httpx.HTTPError as e:
            return MetaBatchResult(
                handle=handle,
                ok=False,
                submitted=0,
                error=f"transport: {e}",
            )

        if resp.status_code >= 300:
            return MetaBatchResult(
                handle=handle,
                ok=False,
                submitted=0,
                error=f"http_{resp.status_code}: {resp.text[:200]}",
            )

        try:
            data = resp.json()
        except ValueError:
            return MetaBatchResult(
                handle=handle,
                ok=False,
                submitted=0,
                error="bad_json",
            )

        status_data = data.get("data", [])
        if not status_data:
            return MetaBatchResult(
                handle=handle,
                ok=False,
                submitted=0,
                error="in_progress",
            )

        # Cada entrada tiene status: "completed" | "in_progress" | "failed"
        status_entry = status_data[0]
        overall_status = status_entry.get("status")
        if overall_status == "in_progress":
            return MetaBatchResult(
                handle=handle,
                ok=False,
                submitted=0,
                error="in_progress",
            )

        items_results: list[MetaBatchItemResult] = []
        errors = status_entry.get("errors", []) or []
        for err in errors:
            items_results.append(MetaBatchItemResult(
                retailer_id=str(err.get("retailer_id", "")),
                method=err.get("method", "CREATE"),
                ok=False,
                error_code=str(err.get("error_code")) if err.get("error_code") else None,
                error_message=err.get("error_message") or err.get("description"),
            ))

        return MetaBatchResult(
            handle=handle,
            ok=overall_status == "completed",
            submitted=status_entry.get("count", 0),
            error=None if overall_status == "completed" else overall_status,
            items_results=items_results,
        )


def _item_to_meta_data(item: MetaCatalogItem) -> dict[str, Any]:
    """Serializa MetaCatalogItem al shape JSON que /items_batch espera."""
    data: dict[str, Any] = {
        "name": item.name,
        "description": item.description,
        "url": item.url,
        "image_url": item.image_url,
        "price": item.price,
        "availability": item.availability,
        "condition": item.condition,
        "brand": item.brand,
    }
    if item.additional_image_urls:
        data["additional_image_urls"] = item.additional_image_urls
    if item.gtin:
        data["gtin"] = item.gtin
    if item.google_product_category:
        data["google_product_category"] = item.google_product_category
    for i, label in enumerate([
        item.custom_label_0,
        item.custom_label_1,
        item.custom_label_2,
        item.custom_label_3,
        item.custom_label_4,
    ]):
        if label:
            data[f"custom_label_{i}"] = label
    if item.sale_price:
        data["sale_price"] = item.sale_price
    if item.custom_data_tags:
        data["custom_data"] = {"tags": item.custom_data_tags}
    return data
