"""`CustomerSummaryAdapter` es never-raises: qué ve el operador cuando el modelo
NO devuelve texto.

El adapter consume el alias ``gemini-backup`` del proxy (default de
``CUSTOMER_SUMMARY_MODEL``). Por contrato nunca levanta: ante cualquier problema
degrada al resumen determinístico y deja el motivo en ``error_detail``. Eso lo
hace cómodo para el endpoint y peligroso para nosotros — un modelo que deja de
contestar texto NO se ve como error (lección L-23).

El hueco que fija este archivo: una respuesta con ``content=None`` (el modelo
contestó solo con una tool call, un bloqueo de seguridad, o gastó el
``max_tokens=300`` sin emitir texto) pasaba por ``str(None)`` y el panel
"Historial cliente" mostraba el resumen literal ``None``, sin ``error_detail``.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.platform.customer_scoring import llm_summary
from src.platform.customer_scoring.llm_summary import CustomerSummaryAdapter
from src.platform.customer_scoring.port import CustomerScore

_SCORE = CustomerScore(
    tag="Recurrente",
    score_letter="A",
    score_value=87,
    score_reason="compró 3 veces en 90 días",
    monetary_cop=412000,
    rules_version=3,
)


def _reply(content: Any) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


async def _summarize(monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any]):
    async def fake_acompletion(**_kwargs: Any) -> dict[str, Any]:
        return reply

    monkeypatch.setattr(llm_summary.litellm, "acompletion", fake_acompletion)
    adapter = CustomerSummaryAdapter(
        model="litellm_proxy/cualquiera", api_base="http://proxy.invalid", api_key="k"
    )
    return await adapter.summarize(score=_SCORE, metadata={})


async def test_the_model_text_is_the_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await _summarize(monkeypatch, _reply("  Cliente fiel, recompra lavanda.  "))

    assert result.summary == "Cliente fiel, recompra lavanda."
    assert result.error_detail is None


@pytest.mark.parametrize("content", [None, "", "   \n"])
async def test_a_reply_without_text_degrades_to_the_deterministic_summary(
    monkeypatch: pytest.MonkeyPatch, content: Any
) -> None:
    result = await _summarize(monkeypatch, _reply(content))

    assert result.summary == (
        "Cliente recurrente (A). compró 3 veces en 90 días. "
        "Valor histórico: $412,000 COP."
    )
    assert result.error_detail == "empty_llm_response"


async def test_a_transport_failure_degrades_and_names_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_acompletion(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("404 models/x is not found for API version v1beta")

    monkeypatch.setattr(llm_summary.litellm, "acompletion", failing_acompletion)
    adapter = CustomerSummaryAdapter(
        model="litellm_proxy/cualquiera", api_base="http://proxy.invalid", api_key="k"
    )
    result = await adapter.summarize(score=_SCORE, metadata={})

    assert result.summary.startswith("Cliente recurrente (A).")
    assert result.error_detail is not None and "RuntimeError" in result.error_detail
