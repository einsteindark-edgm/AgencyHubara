"""El alias `openrouter-perception` llega a OpenRouter con logprobs y las preferencias.

Brazo C del laboratorio (plan §1.3). El proxy corre con `drop_params: True`:
si litellm creyera que el proveedor no admite `logprobs`, los DESCARTARÍA en
silencio y el clasificador respondería sin probabilidades (la comparación con
Jev dejaría de ser justa). Tampoco sirve de nada `require_parameters` si el
bloque `extra_body.provider` no llega. Mismo método que
`test_llm_thinking_disabled.py`: el model_list REAL del proxy en un Router de
litellm contra un upstream falso, y se asierta el BODY que sale.
"""
from __future__ import annotations

from tests.platform.test_llm_thinking_disabled import _body_sent_upstream


async def test_logprobs_and_provider_preferences_reach_openrouter() -> None:
    body = await _body_sent_upstream(
        "openrouter-perception", logprobs=True, top_logprobs=20, temperature=0, max_tokens=64
    )

    assert body["model"] == "openai/gpt-4o-mini-2024-07-18"
    assert (body["logprobs"], body["top_logprobs"], body["temperature"]) == (True, 20, 0)
    assert body["provider"] == {
        "order": ["openai"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
    }
