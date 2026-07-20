"""Push del snapshot catálogo a Meta Commerce Catalog (HU-002 Parte B).

Use case puro — NO conoce Temporal. Recibe el `products_json` ya pulleado
y persistido por `PullCatalogUseCase`, lo mapea a items Meta, calcula los
deltas (CREATE / UPDATE / DELETE) comparando hashes contra el último push
exitoso, y delega al `MetaCatalogPort` para el batch.

Reglas de negocio Hubara aplicadas:
  * `collection != None` ya se filtró en pull — no llega acá. Tracking opcional.
  * `image_url` ausente → skip + log (mapper devuelve None).
  * Soft-delete protection: si `len(current) < threshold_ratio * last_count`,
    se aborta sin borrar nada (regla B.8 del PLAN). Loguea warning.
  * Hash detection: SHA256 de `(title, description, price, availability,
    image_url, tags)`. Solo se manda UPDATE si el hash cambió. Productos
    nuevos van a CREATE.

Idempotencia: `retailer_id` = `CatalogProductDTO.id` — Meta hace upsert.
Re-correr el push con los mismos datos es seguro.

R-JSON: input/output son dataclasses frozen JSON-safe (ver `contracts.py`).
"""
from __future__ import annotations

import hashlib
import json
import time

from src.platform.catalog.dtos import CatalogProductDTO
from src.platform.meta_catalog.dtos import MetaBatchRequest
from src.platform.meta_catalog.mapper import map_products_batch
from src.platform.meta_catalog.port import MetaCatalogPort
from src.plugins.catalog.agent.contracts import (
    PushMetaCatalogInput,
    PushMetaCatalogResult,
)


class PushMetaCatalogUseCase:
    def __init__(self, meta_port: MetaCatalogPort) -> None:
        self._meta = meta_port

    async def execute(
        self, input: PushMetaCatalogInput
    ) -> PushMetaCatalogResult:
        started = time.time()

        # 1. Deserializar productos
        try:
            products_raw = json.loads(input.products_json)
        except (ValueError, TypeError) as e:
            return _failed(
                error=f"bad_products_json: {e}",
                duration=time.time() - started,
            )

        products = [_dto_from_dict(d) for d in products_raw]

        # 2. Map a MetaCatalogItem
        mapped, skipped_ids = map_products_batch(
            products,
            site_base_url=input.site_base_url,
            brand=input.brand,
        )

        # Separar skipped por razón (image vs price)
        skipped_image = 0
        skipped_price = 0
        for sid in skipped_ids:
            p = next((pp for pp in products if pp.id == sid), None)
            if not p:
                continue
            has_image = bool(p.thumbnail) or any(img.url for img in p.images)
            has_price = bool(p.variants and p.variants[0].prices)
            if not has_image:
                skipped_image += 1
            elif not has_price:
                skipped_price += 1

        # 3. Soft-delete protection (regla B.8)
        if input.last_meta_count > 0:
            current_count = len(products)
            threshold = int(input.last_meta_count * input.soft_delete_threshold_ratio)
            if current_count < threshold:
                return _failed(
                    error=(
                        f"aborted_threshold: current={current_count} < "
                        f"threshold={threshold} (last_meta_count="
                        f"{input.last_meta_count}, ratio="
                        f"{input.soft_delete_threshold_ratio})"
                    ),
                    duration=time.time() - started,
                    aborted_due_to_threshold=True,
                    skipped_image=skipped_image,
                    skipped_price=skipped_price,
                )

        # 4. Hash detection — qué crea / actualiza / borra
        try:
            previous_hashes: dict[str, str] = json.loads(input.previous_meta_hashes_json) or {}
        except (ValueError, TypeError):
            previous_hashes = {}

        current_hashes: dict[str, str] = {}
        for item in mapped:
            h = _hash_item(item)
            current_hashes[item.retailer_id] = h

        creates_list = []
        updates_list = []
        for item in mapped:
            prev = previous_hashes.get(item.retailer_id)
            current = current_hashes[item.retailer_id]
            if prev is None:
                creates_list.append(item)
            elif prev != current:
                updates_list.append(item)
            # si prev == current → no-op (skip)

        # Deletes: productos que estaban antes pero ya no están en pull
        current_ids = {item.retailer_id for item in mapped}
        deletes_list = [
            rid for rid in previous_hashes.keys() if rid not in current_ids
        ]

        # 5. Si no hay cambios, skip
        if not creates_list and not updates_list and not deletes_list:
            return PushMetaCatalogResult(
                ok=True,
                handle=None,
                creates=0,
                updates=0,
                deletes=0,
                skipped_image=skipped_image,
                skipped_price=skipped_price,
                skipped_collection=0,
                aborted_due_to_threshold=False,
                error=None,
                next_meta_hashes_json=json.dumps(current_hashes),
                duration_seconds=time.time() - started,
            )

        # 6. Push batch
        batch_req = MetaBatchRequest(
            catalog_id=input.catalog_id,
            access_token=input.system_user_token,
            creates=creates_list,
            updates=updates_list,
            deletes=deletes_list,
            allow_upsert=True,
        )
        batch_result = await self._meta.upsert_batch(batch_req)

        if not batch_result.ok:
            return PushMetaCatalogResult(
                ok=False,
                handle=batch_result.handle,
                creates=0,
                updates=0,
                deletes=0,
                skipped_image=skipped_image,
                skipped_price=skipped_price,
                skipped_collection=0,
                aborted_due_to_threshold=False,
                error=batch_result.error,
                next_meta_hashes_json=input.previous_meta_hashes_json,  # mantener anterior
                duration_seconds=time.time() - started,
            )

        return PushMetaCatalogResult(
            ok=True,
            handle=batch_result.handle,
            creates=len(creates_list),
            updates=len(updates_list),
            deletes=len(deletes_list),
            skipped_image=skipped_image,
            skipped_price=skipped_price,
            skipped_collection=0,
            aborted_due_to_threshold=False,
            error=None,
            next_meta_hashes_json=json.dumps(current_hashes),
            duration_seconds=time.time() - started,
        )


def _hash_item(item) -> str:
    """SHA256 de los campos que importan para detectar cambios reales.

    Excluye custom_data y url (que cambian solo si handle cambia, lo cual es
    raro y se trata como producto nuevo).
    """
    payload = json.dumps({
        "name": item.name,
        "description": item.description,
        "image_url": item.image_url,
        "additional_image_urls": item.additional_image_urls,
        "price": item.price,
        "availability": item.availability,
        "brand": item.brand,
        "gtin": item.gtin,
        "google_product_category": item.google_product_category,
        "custom_label_0": item.custom_label_0,
        "custom_label_1": item.custom_label_1,
        "custom_label_2": item.custom_label_2,
        "custom_label_3": item.custom_label_3,
        "custom_label_4": item.custom_label_4,
        "item_group_id": item.item_group_id,
        "color": item.color,
        "additional_variant_attribute": item.additional_variant_attribute,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dto_from_dict(d: dict) -> CatalogProductDTO:
    """Reconstruye CatalogProductDTO desde el dict serializado.

    Delega en el parser CANÓNICO (`product_dto_from_raw`) — este archivo
    tenía su propia copia que NO mapeaba `options` y el primer sync
    post-#178 publicó el Duo Zodiacal como un solo item (2026-07-16).
    """
    from src.platform.catalog.dtos import product_dto_from_raw

    return product_dto_from_raw(d)


def _failed(
    *,
    error: str,
    duration: float,
    aborted_due_to_threshold: bool = False,
    skipped_image: int = 0,
    skipped_price: int = 0,
) -> PushMetaCatalogResult:
    return PushMetaCatalogResult(
        ok=False,
        handle=None,
        creates=0,
        updates=0,
        deletes=0,
        skipped_image=skipped_image,
        skipped_price=skipped_price,
        skipped_collection=0,
        aborted_due_to_threshold=aborted_due_to_threshold,
        error=error,
        next_meta_hashes_json="{}",
        duration_seconds=duration,
    )
