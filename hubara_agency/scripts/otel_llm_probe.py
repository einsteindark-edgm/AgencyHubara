"""Probe A4 (HU-003 §4) — genera un gen_ai span REAL con prompt/completion → SigNoz.

A diferencia de otel_smoke.py (workflow trivial, sin LLM), este probe hace UN
chat completion real vía LiteLLM con el callback `otel` activo. El objetivo es el
gate del §4: confirmar que SigNoz muestra el CONTENIDO del prompt/completion en el
span gen_ai.*, no solo los tokens.

Uso (apuntando al proxy LiteLLM local que ya tiene las keys):
    cd hubara_agency && PYTHONPATH=. \
      OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true \
      uv run python scripts/otel_llm_probe.py
"""

from __future__ import annotations


def main() -> None:
    from src.platform.observability import init_otel

    init_otel("otel-llm-probe")  # activa callback otel + exporter SigNoz

    import litellm

    try:
        resp = litellm.completion(
            model="litellm_proxy/deepseek-v4-flash",
            messages=[
                {"role": "user", "content": "Respondé solo con la palabra: hola"}
            ],
            api_base="http://localhost:4000",
            api_key="sk-probe-local",
            max_tokens=16,
        )
        content = resp.choices[0].message.content
        print(f"LLM_RESPONSE: {content!r}")
    except Exception as e:  # noqa: BLE001 — probe diagnóstico
        print(f"LLM_ERROR: {type(e).__name__}: {e}")

    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()
    print("PROBE_DONE")


if __name__ == "__main__":
    main()
