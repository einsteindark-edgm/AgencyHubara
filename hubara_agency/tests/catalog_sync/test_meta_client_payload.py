"""El cliente real (`_item_to_meta_data` + `upsert_batch`) debe hablar el
formato `/items_batch` de Meta: el retailer id va DENTRO de `data` como `id`, y
los campos usan nombres de feed (title/link/image_link/additional_image_link).

Regresión de prod (2026-06-30): el cliente mandaba name/url/image_url +
retailer_id a nivel del request → Meta respondía 200 con
``{"validation_status":[{"errors":[{"message":"Can not find required field id"}]}]}``
→ 0 productos creados, pero el cliente devolvía ok=True (rechazo silencioso).
El `_FakeMetaPort` de los otros tests bypassa el cliente HTTP, por eso no se
cazó (gotcha #1: verificar comportamiento, no schema). Estos tests fallan
contra ese código y pasan con el fix.
"""
from __future__ import annotations

import json

import pytest

from src.platform.meta_catalog import client as client_mod
from src.platform.meta_catalog.client import MetaCatalogClient, _item_to_meta_data
from src.platform.meta_catalog.dtos import MetaBatchRequest, MetaCatalogItem


def _item(**kw) -> MetaCatalogItem:
    base = dict(
        retailer_id="prod_123",
        name="Vela Lavanda",
        description="Aroma suave",
        url="https://hubara.com.co/products/vela-lavanda",
        image_url="https://img.example/x.jpg",
        price="23000 COP",
        availability="in stock",
        condition="new",
        brand="Hubara",
    )
    base.update(kw)
    return MetaCatalogItem(**base)


def test_item_data_uses_feed_field_names_with_id():
    data = _item_to_meta_data(_item(additional_image_urls=["https://img.example/y.jpg"]))
    # `id` es REQUIRED por /items_batch y va DENTRO de data (no retailer_id top-level).
    assert data["id"] == "prod_123"
    assert data["title"] == "Vela Lavanda"
    assert data["link"] == "https://hubara.com.co/products/vela-lavanda"
    assert data["image_link"] == "https://img.example/x.jpg"
    assert data["additional_image_link"] == "https://img.example/y.jpg"
    # Los nombres viejos NO deben aparecer — eran lo que Meta rechazaba.
    for legacy in ("name", "url", "image_url", "additional_image_urls"):
        assert legacy not in data, f"{legacy} provoca el rechazo de items_batch"


# --- Fake httpx para capturar el body y simular respuestas de Meta -----------


class _FakeResp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    last_body: dict | None = None
    resp: _FakeResp | None = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, params=None):  # noqa: A002
        _FakeAsyncClient.last_body = json
        return _FakeAsyncClient.resp


@pytest.mark.asyncio
async def test_create_and_delete_put_id_inside_data(monkeypatch):
    _FakeAsyncClient.resp = _FakeResp(200, {"handles": ["h1"]})
    monkeypatch.setattr(client_mod.httpx, "AsyncClient", _FakeAsyncClient)
    c = MetaCatalogClient()
    res = await c.upsert_batch(
        MetaBatchRequest(
            catalog_id="CAT",
            access_token="T",
            creates=[_item()],
            updates=[],
            deletes=["prod_del"],
        )
    )
    assert res.ok is True
    reqs = _FakeAsyncClient.last_body["requests"]
    create = next(r for r in reqs if r["method"] == "CREATE")
    delete = next(r for r in reqs if r["method"] == "DELETE")
    assert create["data"]["id"] == "prod_123"
    assert delete["data"]["id"] == "prod_del"  # DELETE también por data.id


@pytest.mark.asyncio
async def test_validation_status_is_failure_not_silent_ok(monkeypatch):
    _FakeAsyncClient.resp = _FakeResp(
        200,
        {"validation_status": [{"errors": [{"message": "Can not find required field id"}]}]},
    )
    monkeypatch.setattr(client_mod.httpx, "AsyncClient", _FakeAsyncClient)
    c = MetaCatalogClient()
    res = await c.upsert_batch(
        MetaBatchRequest(
            catalog_id="CAT", access_token="T", creates=[_item()], updates=[], deletes=[]
        )
    )
    # Un 200 con validation_status NO es éxito — antes devolvía ok=True (silencioso).
    assert res.ok is False
    assert "validation" in (res.error or "").lower()
