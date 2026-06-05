"""Modelo juez (LLM-as-judge) para DeepEval, ruteado por nuestro proxy litellm.

Wrapper custom de `DeepEvalBaseLLM` (NO el `LiteLLMModel` nativo) — deliberado:

  * El `LiteLLMModel` nativo pide `logprobs`/`top_logprobs` para el scoring fino de
    G-Eval. Gemini (`gemini-flash-lite`) NO soporta `top_logprobs`, y el proxy lo
    rechaza con 400 (`UnsupportedParamsError`). Como NO implementamos
    `a_generate_raw_response`, DeepEval G-Eval cae a su path schema (sin logprobs)
    → funciona con CUALQUIER modelo (gemini, deepseek, ...) a través del proxy,
    SIN tener que tocar la config del proxy (`drop_params`).
  * Apunta al MISMO gateway litellm que el resto del backend (`API_BASE_LLMLITE` +
    `LITELLM_API_KEY`). Privacidad: la conversación (PII) no sale de la infra propia.

**El juez es un alias de config, no código** — cambiarlo es trivial via
`EVAL_JUDGE_MODEL` (default `litellm_proxy/gemini-pro-judge` = Gemini Pro, frontier e
independiente del `deepseek-v4-flash` del agente → evita self-preference). Es el MISMO
juez que el golden del GitHub Action: el eval online y el de CI tienen el mismo criterio.

`deepeval` se importa con guard al top (→ `object` si ausente) para que el módulo
sea importable sin el extra `evals` (gate de arquitectura). `litellm` es dep
default (siempre disponible).
"""
from __future__ import annotations

import os
from typing import Any

try:  # guard: importable sin el extra `evals`
    from deepeval.models import DeepEvalBaseLLM as _Base
except Exception:  # noqa: BLE001
    _Base = object  # type: ignore[assignment,misc]


# El MISMO juez para el eval online (conversaciones reales -> SigNoz) y el golden
# del GitHub Action (CI). Sin esto, el online caía al default `gemini-backup` (más
# débil, y además es el fallback del propio agente DeepSeek -> riesgo de
# self-preference) mientras el golden usaba `gemini-pro-judge`: la MISMA conversación
# habría tenido criterio distinto según el path. Override puntual con EVAL_JUDGE_MODEL.
_DEFAULT_JUDGE_MODEL = "litellm_proxy/gemini-pro-judge"


def get_judge_model_name() -> str:
    """Alias del modelo juez. Env `EVAL_JUDGE_MODEL`, default `gemini-pro-judge`.

    PARIDAD del criterio: mismo modelo (Gemini Pro, frontier e independiente del
    DeepSeek del agente), misma temperatura (0) y mismas métricas/rúbricas/umbrales
    (`all_sales_metrics`) que el golden del CI — online y GitHub Action juzgan igual.
    """
    return os.getenv("EVAL_JUDGE_MODEL", _DEFAULT_JUDGE_MODEL)


class LiteLLMJudge(_Base):  # type: ignore[misc,valid-type]
    """Juez LLM-as-judge vía proxy litellm, SIN logprobs (compatible con cualquier modelo).

    Implementa el contrato mínimo de `DeepEvalBaseLLM`: `load_model`,
    `get_model_name`, `generate`, `a_generate`. NO implementa
    `(a_)generate_raw_response` → G-Eval usa el path schema (sin logprobs). El base
    provee `(a_)generate_with_schema` que delega a `(a_)generate(prompt, schema=...)`;
    devolvemos el texto del LLM y DeepEval extrae el JSON (`trimAndLoadJson`).
    """

    def __init__(self, model: str | None = None, temperature: float = 0.0) -> None:
        from src.platform.config import API_BASE_LLMLITE, LITELLM_API_KEY

        self._model = model or get_judge_model_name()
        self._temperature = temperature
        self._api_base = API_BASE_LLMLITE
        self._api_key = LITELLM_API_KEY

    def load_model(self, *args: Any, **kwargs: Any) -> "LiteLLMJudge":
        return self

    def get_model_name(self, *args: Any, **kwargs: Any) -> str:
        return self._model

    @staticmethod
    def _messages(prompt: Any) -> list[dict[str, str]]:
        content = prompt if isinstance(prompt, str) else str(prompt)
        return [{"role": "user", "content": content}]

    def generate(self, prompt: Any, schema: Any = None, *args: Any, **kwargs: Any) -> str:
        import litellm

        resp = litellm.completion(
            model=self._model,
            api_base=self._api_base,
            api_key=self._api_key,
            temperature=self._temperature,
            messages=self._messages(prompt),
        )
        return resp.choices[0].message.content or ""

    async def a_generate(self, prompt: Any, schema: Any = None, *args: Any, **kwargs: Any) -> str:
        import litellm

        resp = await litellm.acompletion(
            model=self._model,
            api_base=self._api_base,
            api_key=self._api_key,
            temperature=self._temperature,
            messages=self._messages(prompt),
        )
        return resp.choices[0].message.content or ""


def build_judge(*, model: str | None = None, temperature: float = 0.0) -> Any:
    """Construye el juez custom apuntando al proxy litellm (sin logprobs)."""
    return LiteLLMJudge(model=model, temperature=temperature)
