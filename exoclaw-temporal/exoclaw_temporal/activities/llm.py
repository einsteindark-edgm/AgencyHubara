"""LLM chat activity — calls the LLM provider and returns a serializable response."""

from __future__ import annotations

from temporalio import activity
import litellm
import structlog
from exoclaw_provider_litellm.provider import LiteLLMProvider
from exoclaw_temporal.config import LLMChatInput, LLMResponseData, ToolCallData

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

@activity.defn
async def llm_chat(input: LLMChatInput) -> LLMResponseData:
    """Call the LLM. Runs as a Temporal activity so it's retried on failure."""
    provider = LiteLLMProvider(
        api_key=input.llm.api_key,
        api_base=input.llm.api_base,
        default_model=input.llm.model,
        extra_headers=input.llm.extra_headers or None,
    )

    tool_defs = input.tool_definitions()
    response = await provider.chat(
        messages=input.messages,
        tools=tool_defs if tool_defs else None,
        model=input.llm.model,
        temperature=input.llm.temperature,
        max_tokens=input.llm.max_tokens,
        reasoning_effort=input.llm.reasoning_effort,
    )

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
    )
