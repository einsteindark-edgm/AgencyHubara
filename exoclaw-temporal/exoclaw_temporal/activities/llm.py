"""LLM chat activity — calls the LLM provider and returns a serializable response."""

from __future__ import annotations

import asyncio
import os

import litellm
import structlog
from exoclaw_provider_litellm.provider import LiteLLMProvider
from temporalio import activity

from exoclaw_temporal.config import LLMChatInput, LLMConfig, LLMResponseData, ToolCallData

logger = structlog.get_logger()

def _litellm_cache_logger(kwargs, completion_response, start_time, end_time):
    """Callback para monitorear si DeepSeek/Gemini reportan caché."""
    try:
        usage = getattr(completion_response, "usage", None)
        if usage:
            details = getattr(usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) if details else 0
            if cached > 0:
                logger.info("🎯 CACHE HIT (Context Caching)!",
                            cached_tokens=cached,
                            total_prompt=usage.prompt_tokens,
                            model=kwargs.get("model"))
    except Exception as e:
        logger.warning("Error leyendo los detalles de caché", error=str(e))

litellm.success_callback = [_litellm_cache_logger]


def _cached_prompt_tokens(usage: object) -> int:
    """Tokens del prompt servidos desde el caché del proveedor (0 si no dice)."""
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    if not cached:
        cached = getattr(usage, "prompt_cache_hit_tokens", None)  # nativo de DeepSeek
    return int(cached) if isinstance(cached, int) and cached > 0 else 0


class _CacheAwareLiteLLMProvider(LiteLLMProvider):
    """El proveedor de exoclaw copia solo prompt/completion/total: suma
    `cached_tokens` al `usage` para que el costo por episodio cobre el input
    cacheado a su precio (DeepSeek: US$0,003/M vs US$0,15/M; verificado en
    prod 2026-10-06 que el proxy lo trae en `prompt_tokens_details`)."""

    def _parse_response(self, response: object):  # type: ignore[override]
        parsed = super()._parse_response(response)
        cached = _cached_prompt_tokens(getattr(response, "usage", None))
        if cached and isinstance(parsed.usage, dict):
            parsed.usage["cached_tokens"] = cached
        return parsed

def _attach_baggage(baggage: dict[str, str] | None):
    """Attachea ``baggage`` al contexto OTel actual; devuelve el token (o None).

    HU-003: corre DENTRO de la activity (la activity no se replaya → contextvars
    live OK). Así el span gen_ai que OpenLIT crea durante el LLM call recibe los
    IDs como atributos vía el ``BaggageSpanProcessor`` registrado en el provider.
    Guardado contra la ausencia de ``opentelemetry`` (exoclaw no lo exige).
    """
    if not baggage:
        return None
    try:
        from opentelemetry import baggage as _ob
        from opentelemetry import context as _oc
    except ImportError:
        return None
    ctx = None
    for key, value in baggage.items():
        ctx = _ob.set_baggage(key, str(value), context=ctx)
    return _oc.attach(ctx)


def _detach_baggage(token) -> None:
    if token is None:
        return
    try:
        from opentelemetry import context as _oc

        _oc.detach(token)
    except Exception:  # noqa: BLE001 — la observabilidad nunca rompe el turno
        pass


def _api_key(llm: LLMConfig) -> str | None:
    """La llave de la llamada, para el principal y el respaldo: la del payload
    si la trae (inputs agendados antes de que la llave saliera del payload), si
    no la de la variable ``api_key_env`` del entorno del worker."""
    if llm.api_key:
        return llm.api_key
    if not llm.api_key_env:
        return None
    key = os.environ.get(llm.api_key_env)
    if not key:
        # Sin llave, `litellm_proxy/` falla en el cliente si el proceso no tiene
        # `OPENAI_API_KEY`: el worker mal configurado se ve acá, con el nombre
        # de la variable.
        logger.warning("llm_api_key_missing", env=llm.api_key_env, model=llm.model)
    return key or None


async def _chat(
    provider: LiteLLMProvider,
    input: LLMChatInput,
    *,
    model: str,
    reasoning_effort: str | None,
    deadline_s: float | None,
):
    """Una llamada al modelo; con ``deadline_s`` es un tope TOTAL.

    ``asyncio.wait_for`` cancela la llamada al vencer: el cliente HTTP cierra la
    conexión. El ``timeout`` de litellm, en cambio, es por lectura y un servidor
    que manda bytes de relleno (DeepSeek saturado: una línea vacía por minuto)
    lo reinicia para siempre.
    """
    tool_defs = input.tool_definitions()
    call = provider.chat(
        messages=input.messages,
        tools=tool_defs if tool_defs else None,
        model=model,
        temperature=input.llm.temperature,
        max_tokens=input.llm.max_tokens,
        reasoning_effort=reasoning_effort,
    )
    if deadline_s is None:
        return await call
    return await asyncio.wait_for(call, timeout=deadline_s)


async def _chat_with_fallback(provider: LiteLLMProvider, input: LLMChatInput):
    """La llamada con su tope; si vence o falla, la misma petición al respaldo.

    El respaldo va sin ``reasoning_effort`` (es del modelo principal) y con su
    propio tope: un respaldo colgado no repite el problema. Si también falla,
    el error sube y Temporal reintenta el intento. Un intento CANCELADO
    (``CancelledError``, que no es ``Exception``) se cancela: no cae al respaldo.
    """
    llm = input.llm
    try:
        return await _chat(
            provider,
            input,
            model=llm.model,
            reasoning_effort=llm.reasoning_effort,
            deadline_s=llm.call_deadline_s,
        )
    except Exception as exc:  # noqa: BLE001 — cualquier falla del principal va al respaldo
        if not llm.fallback_model:
            raise
        baggage = input.baggage or {}
        # Falla técnica = se recupera sola y avisa: nunca en silencio.
        logger.warning(
            "llm_fallback",
            cause="timeout" if isinstance(exc, (TimeoutError, litellm.Timeout)) else "error",
            primary_model=llm.model,
            fallback_model=llm.fallback_model,
            deadline_s=llm.call_deadline_s,
            session_id=baggage.get("session.id"),
            episode_id=baggage.get("episode.id"),
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
        return await _chat(
            provider,
            input,
            model=llm.fallback_model,
            reasoning_effort=None,
            deadline_s=llm.fallback_deadline_s,
        )


@activity.defn
async def llm_chat(input: LLMChatInput) -> LLMResponseData:
    """Call the LLM. Runs as a Temporal activity so it's retried on failure."""
    # HU-003: attach del baggage (session.id/episode.id/whatsapp.number) ANTES del
    # LLM call, para que el span gen_ai de OpenLIT lo reciba como atributos
    # (atribución de costo por conversación). Temporal NO propaga baggage
    # workflow→activity, por eso los IDs viajan por el input (LLMChatInput.baggage).
    _baggage_token = _attach_baggage(input.baggage)
    try:
        provider = _CacheAwareLiteLLMProvider(
            api_key=_api_key(input.llm),
            api_base=input.llm.api_base,
            default_model=input.llm.model,
            extra_headers=input.llm.extra_headers or None,
        )

        response = await _chat_with_fallback(provider, input)

        return LLMResponseData(
            content=response.content,
            finish_reason=response.finish_reason,
            has_tool_calls=response.has_tool_calls,
            tool_calls=[
                ToolCallData(id=tc.id, name=tc.name, arguments=tc.arguments)
                for tc in response.tool_calls
            ],
            reasoning_content=response.reasoning_content,
            thinking_blocks=response.thinking_blocks,
            # usage (prompt/completion/total tokens) para persistir el costo LLM
            # por episodio. El provider lo expone en LLMResponse.usage (dict).
            usage=dict(response.usage) if getattr(response, "usage", None) else None,
        )
    finally:
        _detach_baggage(_baggage_token)
