"""Lo que cuesta leer una foto queda en la conversación (como LLM, WA y Jev).

Las tres llamadas a Gemini por foto (describir, huella para buscar en el
catálogo y comparar contra las candidatas) devuelven lo que costaron: primero
el costo que calcula el proxy (`_hidden_params.response_cost`); si no viene,
tokens × la tabla de precios (`OPENLIT_PRICING_JSON`). El ingest lo suma al
episodio: `episodes[].vision_usage = {calls, cost_usd_micros}`.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.platform.vision import embeddings, litellm_adapter, photo_match
from src.platform.vision.costs import record_vision_cost
from src.platform.vision.embeddings import LiteLLMImageEmbeddingAdapter
from src.platform.vision.litellm_adapter import LiteLLMVisionAdapter
from src.platform.vision.photo_match import LiteLLMPhotoMatchAdapter

PRICING = Path(__file__).resolve().parents[2] / "deploy" / "openlit" / "pricing.json"
SID = "wa_573001234567"
_ANSWER = json.dumps({"tipo": "foto_producto", "descripcion": "vela rosada", "texto_visible": {}})


def _response(content: str, *, cost: float | None = None, usage: tuple[int, int] | None = None) -> Any:
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    if cost is not None:
        response._hidden_params = {"response_cost": cost}
    if usage is not None:
        response.usage = SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1])
    return response


def _completion(response: Any):
    async def fake(**kwargs: Any) -> Any:
        return response

    return fake


async def _describe(monkeypatch, response: Any):
    monkeypatch.setattr(litellm_adapter.litellm, "acompletion", _completion(response))
    adapter = LiteLLMVisionAdapter(model="litellm_proxy/gemini-multimodal", api_base="http://proxy", api_key="k")
    return await adapter.describe_image(b"\xff\xd8jpeg", "image/jpeg")


@pytest.mark.asyncio
async def test_describing_costs_what_the_proxy_says(monkeypatch) -> None:
    result = await _describe(monkeypatch, _response(_ANSWER, cost=0.00021))

    assert result.cost_usd_estimate == pytest.approx(0.00021)


@pytest.mark.asyncio
async def test_without_the_proxy_cost_it_is_tokens_times_the_price(monkeypatch) -> None:
    """gemini-2.5-flash-lite: US$0,10 / 1M de entrada y US$0,40 / 1M de salida."""
    monkeypatch.setenv("OPENLIT_PRICING_JSON", str(PRICING))

    result = await _describe(monkeypatch, _response(_ANSWER, usage=(600, 200)))

    assert result.cost_usd_estimate == pytest.approx(0.00014)


@pytest.mark.asyncio
async def test_comparing_against_the_candidates_reports_its_cost(monkeypatch) -> None:
    """gemini-3.5-flash-lite (alias gemini-photo-match): US$0,30 / US$2,50 por 1M."""
    monkeypatch.setenv("OPENLIT_PRICING_JSON", str(PRICING))
    adapter = LiteLLMPhotoMatchAdapter(api_base="http://proxy", api_key="k")

    monkeypatch.setattr(photo_match.litellm, "acompletion", _completion(_response('{"numero": 2}', cost=0.0009)))
    by_proxy = await adapter.pick_same_design(b"\xff\xd8", "image/jpeg", [[b"a"], [b"b"]])
    monkeypatch.setattr(photo_match.litellm, "acompletion", _completion(_response('{"numero": null}', usage=(2000, 100))))
    by_tokens = await adapter.pick_same_design(b"\xff\xd8", "image/jpeg", [[b"a"], [b"b"]])

    assert (by_proxy.number, by_proxy.cost_usd) == (2, pytest.approx(0.0009))
    assert by_tokens.cost_usd == pytest.approx(2000 * 0.30 / 1e6 + 100 * 2.50 / 1e6)


@pytest.mark.asyncio
async def test_the_photo_fingerprint_reports_its_cost(monkeypatch) -> None:
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 100, 100)).save(buf, format="JPEG")

    async def fake(**kwargs: Any) -> Any:
        response = SimpleNamespace(data=[{"embedding": [0.1] * 4}])
        response._hidden_params = {"response_cost": 0.00003}
        return response

    monkeypatch.setattr(embeddings.litellm, "aembedding", fake)
    adapter = LiteLLMImageEmbeddingAdapter(api_base="http://proxy", api_key="k", dimensions=4)

    vector, cost = await adapter.embed_measured(buf.getvalue(), "image/jpeg")

    assert vector == [0.1] * 4 and cost == pytest.approx(0.00003)


def _seed(vault: Path) -> Path:
    path = vault / SID / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"episodes": [{"episode_id": "ep_001", "closed_at_ms": None}]}), encoding="utf-8")
    return path


def test_the_cost_of_a_photo_goes_to_the_conversation(tmp_path: Path) -> None:
    path = _seed(tmp_path)

    assert record_vision_cost(SID, 0.00014, calls=1, vault_dir=tmp_path) is True
    assert record_vision_cost(SID, 0.0008, calls=2, vault_dir=tmp_path) is True

    episode = json.loads(path.read_text(encoding="utf-8"))["episodes"][0]
    assert episode["vision_usage"] == {"calls": 3, "cost_usd_micros": 940}


def test_a_read_without_a_known_price_still_counts(tmp_path: Path) -> None:
    """La huella (gemini-embedding-2) no tiene precio en la tabla: si el proxy
    no lo dice, la llamada se cuenta igual, sin inventar el costo."""
    path = _seed(tmp_path)

    record_vision_cost(SID, None, calls=1, vault_dir=tmp_path)

    episode = json.loads(path.read_text(encoding="utf-8"))["episodes"][0]
    assert episode["vision_usage"] == {"calls": 1, "cost_usd_micros": 0}
