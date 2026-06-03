"""OTel bootstrap — HU-003 (Parte A, commit A1+A3).

Inicializa OpenTelemetry para los workers de los agentes DEHA:
  - Traces  (TracerProvider + BatchSpanProcessor)
  - Metrics (MeterProvider + PeriodicExportingMetricReader)
  - GenAI   (appendea el callback `otel` de LiteLLM — emite spans gen_ai.*)

Reglas de diseño:
  - **Idempotente**: llamar `init_otel()` N veces inicializa una sola vez.
  - **Kill-switch**: `OTEL_SDK_DISABLED=true` → no-op total, sin redeploy.
  - **Sin endpoint → consola**: si no hay `OTEL_EXPORTER_OTLP_ENDPOINT`, exporta a
    stdout (`ConsoleSpanExporter`). Útil para el PoC local — cero infra.
  - **Con endpoint → OTLP gRPC**: SigNoz Cloud
    (`OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.<region>.signoz.cloud:443`,
    `OTEL_EXPORTER_OTLP_HEADERS=signoz-ingestion-key=<key>`).

NO se llama desde dentro de un workflow (R-DET) — solo desde el `main()` del worker,
fuera del sandbox determinista. Esto vive en `platform/observability/`, no en una activity.
"""

from __future__ import annotations

import logging
import os

import structlog

logger = structlog.get_logger()

_INITIALIZED = False


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() == "true"


def _has_otlp_endpoint() -> bool:
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())


def init_otel(service_name: str) -> None:
    """Inicializa traces + metrics + LiteLLM GenAI callback. Idempotente.

    Args:
        service_name: identifica el worker en el backend ("sales-agent",
            "remarketing-agent", ...). Va en el Resource como `service.name`.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    if _flag("OTEL_SDK_DISABLED"):
        logger.info("otel.disabled", reason="OTEL_SDK_DISABLED=true")
        _INITIALIZED = True
        return

    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "hubara",
            "deployment.environment": os.getenv("ENVIRONMENT", "dev"),
        }
    )

    use_otlp = _has_otlp_endpoint()

    # --- Traces ---
    if use_otlp:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        span_exporter = OTLPSpanExporter()
    else:
        span_exporter = ConsoleSpanExporter()

    tracer_provider = TracerProvider(resource=resource)
    # BatchSpanProcessor: exporta en background — NO suma latencia al hot path del turno.
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    # BaggageSpanProcessor (HU-003 A7): copia session.id / episode.id /
    # whatsapp.number (seteados como baggage en run_agent_turn y auto-propagados
    # workflow→activity por el TracingInterceptor) a atributos de CADA span del
    # turno — incluido el span gen_ai de OpenLIT. Habilita la atribución de costo
    # por conversación/número en SigNoz (GROUP BY session.id, episode.id).
    from src.platform.observability.baggage import BaggageSpanProcessor

    tracer_provider.add_span_processor(BaggageSpanProcessor())
    trace.set_tracer_provider(tracer_provider)

    # --- Metrics ---
    if use_otlp:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )

        metric_exporter = OTLPMetricExporter()
    else:
        metric_exporter = ConsoleMetricExporter()

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(metric_exporter)],
    )
    metrics.set_meter_provider(meter_provider)

    # --- Logs (OTel-logs aún experimental en Python; lo configuramos para que los
    # events/logs de OpenLIT tengan LoggerProvider — sin esto: "Failed to export
    # logs batch" al shutdown). 3a señal correlacionada por trace_id. ---
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    if use_otlp:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
            OTLPLogExporter,
        )

        log_exporter = OTLPLogExporter()
    else:
        from opentelemetry.sdk._logs.export import ConsoleLogExporter

        log_exporter = ConsoleLogExporter()

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    set_logger_provider(logger_provider)

    # Puente stdlib logging → OTel (Tier 4 "logs enriquecidos"): cada log que pasa
    # por el `logging` estándar (uvicorn HTTP, httpx, temporalio, litellm, urllib3,
    # asyncio…) se exporta como OTel log con su severity y el trace_id/span_id del
    # span activo → navegación trace↔logs en SigNoz. Coexiste con el InterceptHandler
    # de setup_logging (stdlib→loguru→stdout): el record va a AMBOS, sin loop. (Los
    # logs emitidos por loguru/structlog DIRECTO no pasan por stdlib → follow-up si
    # se quiere ese nivel; lo operacionalmente crítico —HTTP/Temporal/LLM/errores—
    # viaja por stdlib y queda cubierto.)
    from opentelemetry.sdk._logs import LoggingHandler

    _root_logger = logging.getLogger()
    if not any(isinstance(h, LoggingHandler) for h in _root_logger.handlers):
        _root_logger.addHandler(
            LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
        )

    # --- GenAI (OpenLIT → gen_ai.* estándar + métricas tokens/cost/latencia) ---
    _instrument_genai(service_name)

    _INITIALIZED = True
    logger.info(
        "otel.initialized",
        service=service_name,
        exporter="otlp-grpc" if use_otlp else "console",
    )


def instrument_fastapi_app(app: object) -> None:
    """Instrumenta una app FastAPI con OTel — un span SERVER por request HTTP.

    Llamar desde el entrypoint HTTP (``src/main.py``) DESPUÉS de ``init_otel()``.
    Hace que el proceso API sea el servicio ``api-gateway`` en SigNoz: el webhook
    de WhatsApp y los endpoints del dashboard aparecen como spans raíz, y —vía
    ``add_traced_background_task``— ese span se propaga a Temporal, encadenando
    webhook → workflow → activity → LLM → tool en UN solo trace distribuido.

    El paquete ``opentelemetry-instrumentation-fastapi`` ya viene instalado
    (dependencia transitiva de OpenLIT). ``instrument_app`` es idempotente. httpx
    NO se toca acá: OpenLIT ya lo instrumenta (evita spans duplicados).

    Degrada sin romper (``OTEL_SDK_DISABLED`` o falta del paquete) — nunca tumba
    el arranque del proceso HTTP por un tema de observabilidad.
    """
    if _flag("OTEL_SDK_DISABLED"):
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("otel.fastapi_instrumented")
    except Exception as exc:  # noqa: BLE001 — degradar, no romper el proceso HTTP
        logger.warning("otel.fastapi_failed", error=str(exc))


def _instrument_genai(service_name: str) -> None:
    """Instrumenta LiteLLM con OpenLIT → spans ``gen_ai.*`` estándar + métricas.

    Reemplaza el callback nativo de LiteLLM (que emitía el namespace propietario
    ``llm.litellm_proxy.*`` — no portable, no lo entiende RAGAS ni otro backend
    sin mapeo custom). OpenLIT auto-instrumenta LiteLLM y emite:
      - traces con ``gen_ai.*`` semantic conventions (prompt/completion estándar),
      - métricas de valores: token usage (prompt/completion), costo USD, latencia.

    Reusa el TracerProvider GLOBAL ya configurado por init_otel (no crea
    exporters propios) → los spans del LLM cuelgan del trace de Temporal
    (correlación workflow→activity→LLM) y van al mismo SigNoz. El cache logger
    en ``litellm.success_callback`` queda intacto (OpenLIT no toca esa lista).

    Si OpenLIT no está o falla, cae al callback nativo de LiteLLM
    (``llm.litellm_proxy.*``) — degradado pero sin perder observabilidad.
    """
    try:
        import openlit

        # SIN otlp_endpoint ni tracer: OpenLIT reusa el TracerProvider/MeterProvider
        # GLOBAL ya configurado por init_otel → los spans del LLM cuelgan del trace de
        # Temporal (correlación) y van al mismo SigNoz. Defaults relevantes:
        # capture_message_content=True (prompt/completion), disable_metrics=False (valores).
        openlit.init(
            application_name=service_name,
            environment=os.getenv("ENVIRONMENT", "dev"),
            # Pricing del costo (HU-003 A7): OpenLIT calcula gen_ai.usage.cost =
            # tokens × tarifa de SU tabla (NO reusa el cost de litellm). Los
            # modelos deepseek-v4-flash/gemini-backup son nuevos/custom y NO están
            # en la tabla pública remota de OpenLIT → sin esto cost=0 (los tokens
            # SÍ se capturan igual). `OPENLIT_PRICING_JSON` apunta a un archivo
            # local con las tarifas (deploy/openlit/pricing.json). Si no está
            # seteado → None → OpenLIT usa su tabla remota (default, requiere red).
            pricing_json=os.getenv("OPENLIT_PRICING_JSON") or None,
            # evals_logs_export usa OTel-logs (experimental, sin LoggerProvider en
            # nuestro setup) → causaba "Failed to export logs batch" al shutdown.
            # Lo desactivamos; traces (gen_ai.*) + métricas (tokens/cost/latencia)
            # NO se ven afectados. Para prod: agregar LoggerProvider y reactivar.
            evals_logs_export=False,
        )
        logger.info("otel.openlit_instrumented", service=service_name)
        return
    except Exception as exc:  # noqa: BLE001 — degradar, no romper el worker
        logger.warning("otel.openlit_failed", error=str(exc), fallback="litellm_native")

    _enable_litellm_native_callback()


def _enable_litellm_native_callback() -> None:
    """Fallback: appendea ``"otel"`` a ``litellm.callbacks`` (NO toca ``success_callback``).

    GOTCHA (HU-003 §11.3): LiteLLM tiene DOS listas — ``success_callback`` (cache
    logger en activities/llm.py, NO tocar) y ``callbacks`` (handler ``"otel"``).
    Appendea idempotentemente. Emite el formato propietario ``llm.litellm_proxy.*``.
    """
    try:
        import litellm
    except ImportError:
        logger.warning("otel.litellm_missing", detail="litellm no importable; skip GenAI")
        return

    existing = list(getattr(litellm, "callbacks", None) or [])
    if "otel" not in existing:
        litellm.callbacks = existing + ["otel"]
        logger.info("otel.litellm_callback_enabled", callbacks=litellm.callbacks)


def otel_workflow_runner():
    """SandboxedWorkflowRunner con ``opentelemetry`` como passthrough module.

    GOTCHA R-DET (HU-003 §11 — descubierto por scripts/otel_smoke.py): el
    TracingInterceptor crea el span de ejecución DENTRO del workflow sandbox;
    OTel genera los span IDs con ``random.getrandbits``, que el sandbox
    determinista de Temporal bloquea (RestrictedWorkflowAccessError). Marcar
    ``opentelemetry`` como passthrough lo resuelve sin abrir el sandbox a otro
    no-determinismo. Sin esto, CUALQUIER workflow real falla al crear su span
    una vez que el interceptor está activo en el client.

    Inofensivo con OTEL_SDK_DISABLED=true (el provider no-op no genera IDs reales).
    Usar en cada Worker:  ``workflow_runner=otel_workflow_runner()``.
    """
    from temporalio.worker.workflow_sandbox import (
        SandboxedWorkflowRunner,
        SandboxRestrictions,
    )

    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules(
            "opentelemetry"
        )
    )
