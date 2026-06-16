/**
 * OTel web (Tier 2) — distributed tracing del dashboard React.
 *
 * Instrumenta `fetch` y `XMLHttpRequest` del browser: cada llamada al backend genera
 * un span y le inyecta el header `traceparent` (W3C). El backend FastAPI (servicio
 * `api-gateway`, instrumentado en el Tier 1) lo continúa → el trace arranca en el
 * navegador del admin y sigue al backend en SigNoz: dashboard → api-gateway → … en
 * un solo trace distribuido. Sirve para acciones del admin (ver ads, órdenes, etc.);
 * el camino del cliente de WhatsApp arranca en el webhook, no acá.
 *
 * Requisitos de infra (ya configurados en el collector):
 *  - CORS habilitado en el receiver OTLP-HTTP (sino el browser bloquea el POST).
 *  - el browser exporta a `env.otelExporterUrl` (default collector local :4318).
 *
 * FSD: vive en `app/` (composition root, se llama una vez desde `main.tsx`) e importa
 * de `shared/config`. Idempotente. Degrada solo: cualquier fallo de init NO debe
 * tumbar el render del dashboard.
 */

import { ZoneContextManager } from "@opentelemetry/context-zone";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { FetchInstrumentation } from "@opentelemetry/instrumentation-fetch";
import { XMLHttpRequestInstrumentation } from "@opentelemetry/instrumentation-xml-http-request";
import { resourceFromAttributes } from "@opentelemetry/resources";
import {
  BatchSpanProcessor,
  ParentBasedSampler,
  TraceIdRatioBasedSampler,
  WebTracerProvider,
} from "@opentelemetry/sdk-trace-web";
import { ATTR_SERVICE_NAME } from "@opentelemetry/semantic-conventions";

import { env } from "@/shared/config";

let initialized = false;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Inicializa el tracing del browser. Llamar UNA vez al arranque (main.tsx), antes
 * del render. Re-invocaciones son no-op.
 */
export function initWebTracing(): void {
  if (initialized) return;
  initialized = true;

  // F6.6 (auditoría 2026-06-10): opt-in en dev / on en prod (VITE_OTEL_ENABLED
  // fuerza). Sin esto, cada fetch generaba spans y el exporter posteaba a
  // /v1/traces cada ~5s aunque NO hubiera collector — el "traces llamándose
  // cada rato" que se veía en la Network tab.
  if (!env.otelEnabled) return;

  try {
    // El header `traceparent` se propaga SOLO a llamadas al backend — a terceros
    // rompería su CORS. Same-origin se propaga siempre por default.
    const propagateTraceHeaderCorsUrls = [new RegExp(escapeRegExp(env.apiUrl))];

    const provider = new WebTracerProvider({
      resource: resourceFromAttributes({
        [ATTR_SERVICE_NAME]: "dashboard-frontend",
      }),
      // Muestreo por trace-id (respeta la decisión del padre): con
      // VITE_OTEL_SAMPLE_RATE=0.1 solo el 10% de las interacciones exporta.
      sampler: new ParentBasedSampler({
        root: new TraceIdRatioBasedSampler(
          Number.isFinite(env.otelSampleRate) ? env.otelSampleRate : 1,
        ),
      }),
      spanProcessors: [
        new BatchSpanProcessor(
          new OTLPTraceExporter({ url: env.otelExporterUrl }),
        ),
      ],
    });

    // ZoneContextManager mantiene el contexto del span activo a través de los
    // callbacks async del browser (fetch/timers). register() setea el propagador
    // W3C `traceparent` por default.
    provider.register({ contextManager: new ZoneContextManager() });

    registerInstrumentations({
      instrumentations: [
        new FetchInstrumentation({ propagateTraceHeaderCorsUrls }),
        new XMLHttpRequestInstrumentation({ propagateTraceHeaderCorsUrls }),
      ],
    });
  } catch (err) {
    // Observabilidad nunca tumba la app.
    console.warn("[otel] initWebTracing failed; tracing disabled", err);
  }
}
