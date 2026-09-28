"""Contrato común del puerto de percepción (plan del laboratorio, PR 4).

Regla del ConnectorKit (docs/_sdk/07-connectorkit.md): ningún adapter sin
contract suite; la MISMA suite corre contra el fake y contra cada proveedor
real (acá con sus respuestas grabadas). El puerto hace preguntas tipadas sobre
una ráfaga (`noul`, `choice`, `score`) y devuelve respuestas con probabilidad.

Invariante que protege al cliente: el puerto NUNCA lanza. Timeout, error del
proveedor, llave ausente o respuesta con forma rara → `ok=False` con el
motivo, y el turno sale como hoy (fail-open).
"""
from __future__ import annotations

import asyncio
import json
import math

import httpx
import pytest

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.platform.perception.adapters.null import NullPerceptionAdapter
from src.platform.perception.adapters.openrouter_decisions import OpenRouterDecisionsAdapter
from src.platform.perception.ports import PerceptionResult
from tests.platform.perception.recorded import DECISIONS_OK, QUESTIONS

STATE = "[1] (10:02:05) me mandas el catálogo\n[2] (+7 s) y el envío a Bogotá cuánto sale"


def _decisions(handler, *, api_key: str | None = "sk-or-test", **kw) -> OpenRouterDecisionsAdapter:
    return OpenRouterDecisionsAdapter(
        model="typesafe/jev-1.13",
        api_key=api_key,
        transport=httpx.MockTransport(handler),
        **kw,
    )


def _json_handler(payload: dict, status: int = 200, sink: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if sink is not None:
            sink.append(request)
        return httpx.Response(status, json=payload)

    return handler


ADAPTERS = {
    "fake": lambda: FakePerceptionAdapter(),
    "openrouter_decisions": lambda: _decisions(_json_handler(DECISIONS_OK)),
}


# ── El contrato: lo que vale para TODOS los adaptadores ─────────────────────


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_answers_every_question_with_a_valid_typed_answer(name: str) -> None:
    result = await ADAPTERS[name]().ask(STATE, QUESTIONS, timeout_s=3)

    assert isinstance(result, PerceptionResult)
    assert result.ok, result.error
    assert result.provider and result.model
    assert result.latency_ms >= 0
    by_id = {a.id: a for a in result.answers}
    assert set(by_id) == {q.id for q in QUESTIONS}
    for q in QUESTIONS:
        a = by_id[q.id]
        assert a.kind == q.kind
        if q.kind == "noul":
            assert a.p is not None and 0.0 <= a.p <= 1.0
        elif q.kind == "choice":
            assert a.choice in q.criteria
            probs = dict(a.probs)
            assert set(probs) <= set(q.criteria)
            assert math.isclose(sum(probs.values()), 1.0, abs_tol=0.02)
        else:
            assert a.score is not None and 0.0 <= a.score <= len(q.criteria) - 1


async def test_null_adapter_answers_nothing_and_says_why() -> None:
    result = await NullPerceptionAdapter().ask(STATE, QUESTIONS, timeout_s=3)

    assert (result.ok, result.answers, result.error) == (False, (), "disabled")


# ── Jev por la Decisions API de OpenRouter ──────────────────────────────────


async def test_decisions_request_has_the_native_jev_shape() -> None:
    sent: list[httpx.Request] = []
    adapter = _decisions(_json_handler(DECISIONS_OK, sink=sent), provider_prefs={"data_collection": "deny"})

    await adapter.ask(STATE, QUESTIONS, timeout_s=3)

    [request] = sent
    assert request.url == "https://openrouter.ai/api/alpha/decisions"
    assert request.headers["authorization"] == "Bearer sk-or-test"
    body = json.loads(request.content)
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"] == STATE
    assert body["provider"] == {"data_collection": "deny"}
    assert body["questions"]["q0"] == {
        "type": "noul",
        "instructions": QUESTIONS[0].text,
        "criteria": dict(QUESTIONS[0].criteria),
    }
    assert body["questions"]["q1"]["type"] == "choice"
    assert body["questions"]["q2"] == {"type": "score", "instructions": QUESTIONS[2].text, "criteria": list(QUESTIONS[2].criteria)}


async def test_decisions_maps_answers_back_and_keeps_the_served_snapshot_and_cost() -> None:
    result = await _decisions(_json_handler(DECISIONS_OK)).ask(STATE, QUESTIONS, timeout_s=3)

    by_id = {a.id: a for a in result.answers}
    assert by_id["topic.catalogo"].p == 0.93
    assert (by_id["stage"].choice, by_id["stage"].confidence) == ("variantes", 0.7)
    assert by_id["urgencia"].score == 1.2
    assert result.model == "typesafe/jev-1.13-20260917"
    assert result.cost_usd == 0.00002
    assert result.input_tokens == 480


@pytest.mark.parametrize(
    ("status", "error"), [(402, "http_402"), (429, "http_429"), (502, "http_502"), (529, "http_529")]
)
async def test_decisions_provider_errors_fail_open(status: int, error: str) -> None:
    result = await _decisions(_json_handler({"error": {"code": status, "message": "x"}}, status)).ask(
        STATE, QUESTIONS, timeout_s=3
    )

    assert (result.ok, result.error, result.answers) == (False, error, ())


async def test_decisions_timeout_fails_open() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = await _decisions(handler).ask(STATE, QUESTIONS, timeout_s=3)

    assert (result.ok, result.error) == (False, "timeout")


async def test_decisions_slow_provider_is_cut_at_the_profile_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json=DECISIONS_OK)

    result = await _decisions(handler).ask(STATE, QUESTIONS, timeout_s=0.05)

    assert (result.ok, result.error) == (False, "timeout")


@pytest.mark.parametrize(
    "broken",
    [
        {**DECISIONS_OK, "answers": {k: v for k, v in DECISIONS_OK["answers"].items() if k != "q1"}},
        {**DECISIONS_OK, "answers": {**DECISIONS_OK["answers"], "q1": {"type": "choice", "choice": "otra"}}},
        {**DECISIONS_OK, "answers": {**DECISIONS_OK["answers"], "q0": {"type": "noul", "noul": 1.7}}},
        {**DECISIONS_OK, "answers": {**DECISIONS_OK["answers"], "q0": {"type": "choice", "choice": "true"}}},
        {"model": "x"},
    ],
    ids=["missing", "choice-out-of-criteria", "p-out-of-range", "wrong-type", "no-answers"],
)
async def test_decisions_unexpected_shape_fails_open(broken: dict) -> None:
    """La API está en alpha: si la forma cambia, el turno sale como hoy."""
    result = await _decisions(_json_handler(broken)).ask(STATE, QUESTIONS, timeout_s=3)

    assert (result.ok, result.error) == (False, "bad_shape")


async def test_decisions_without_key_never_calls_the_provider() -> None:
    sent: list = []

    result = await _decisions(_json_handler(DECISIONS_OK, sink=sent), api_key=None).ask(STATE, QUESTIONS, timeout_s=3)

    assert (result.ok, result.error, sent) == (False, "no_api_key", [])


async def test_decisions_anonymizes_the_state_when_the_profile_asks() -> None:
    sent: list[httpx.Request] = []
    adapter = _decisions(_json_handler(DECISIONS_OK, sink=sent), anonymize=True)
    state = "Soy Carolina, mi número es 3001234567 y el correo caro@example.com. Calle 45 # 12-30"

    await adapter.ask(state, QUESTIONS, timeout_s=3, redact=("Carolina",))

    sent_state = json.loads(sent[0].content)["state"]
    for secret in ("Carolina", "3001234567", "caro@example.com", "45 # 12-30"):
        assert secret not in sent_state
