# HU-003 · Observabilidad OpenTelemetry GenAI + Evaluación RAGAS

> Documento de implementación. Self-contained: un humano (o agente single-shot) lee
> esto y puede empezar a commitear sin re-litigar decisiones.
>
> **Status**: Fase A (A1–A3) implementada, verificada y commiteada (`9e5046a`, branch
> `feat/otel-observability-poc`). A5 (integrar SigNoz self-hosted al docker-compose) + A4
> (validar prompt/completion) en curso. Backend: **SigNoz Community self-hosted** (Cloud
> descartado — mínimo $49/mes confirmado, no viable para bootstrapped).
> **Owner**: TBD.
> **Estimación**: Parte A (OTel) ~3 días · Parte B (RAGAS) ~2 días · ~18 commits total.
> **Relación**: Parte B reusa el dataset de [HU-001](../HU-001-conversation-eval-harness/PLAN.md).

---

## §0. TL;DR

Dos capas complementarias sobre los agentes DEHA:

- **Parte A — Observabilidad (OpenTelemetry).** Columna vertebral OTel para las 4 señales:
  **traces** (Temporal `TracingInterceptor` + LiteLLM callback `otel`), **metrics**
  (`gen_ai.client.token.usage`, `operation.duration` + métricas de negocio), **logs**
  (loguru existente + correlación `trace_id`/`span_id`, NO migración a OTel logs nativos),
  y **GenAI** (semantic conventions `gen_ai.*` con captura de prompt/completion). Todo sale
  por OTLP a **SigNoz Cloud** (backend OTel-native que unifica infra + LLM en un solo panel),
  opcionalmente vía un **Collector** local para PII scrubbing + fan-out a S3. Cero lock-in.

- **Parte B — Evaluación (RAGAS).** Calcula `faithfulness` (detector de alucinaciones),
  `answer_relevancy`, `context_precision`. Corre en **dos modos** con el mismo código:
  (1) **offline pre-deploy** dentro del harness HU-001 como un matcher más,
  (2) **offline post-deploy** leyendo traces reales de SigNoz y emitiendo los scores **como
  métricas OTel** (`hubara.eval.*`) + atributos de span (SigNoz no tiene `score()` nativo).

**Las 3 capas son ortogonales**: OTel = transporte/estándar, SigNoz = backend/dashboards,
RAGAS = scoring. OTel alimenta a SigNoz; RAGAS lee traces y emite métricas de evaluación de vuelta.

> **Backend elegido: SigNoz Cloud.** A diferencia de Langfuse (producto LLM que acepta OTLP),
> SigNoz es OTel-native de raíz (ClickHouse + OTLP, gRPC y HTTP). Unifica los traces de Temporal
> (infra) con los `gen_ai.*` (LLM) en un backend, trae dashboard pre-built de LiteLLM, pero **no
> tiene** objetos `score`, prompt-management ni session-replay LLM — cosas que este proyecto ya
> resuelve por otro lado (RAGAS propio, prompts en `.md`, vault JSONL + harness HU-001).

---

## §1. Modelo mental — por qué 3 capas y no 3 productos

| Capa | Qué es | Qué hace | Qué NO hace |
|---|---|---|---|
| **OpenTelemetry** | Estándar (SDK + Semantic Conventions + OTLP) | Emite y transporta señales | No almacena, no visualiza |
| **SigNoz** (backend elegido) | Plataforma OTel-native (ClickHouse) | Almacena, dashboards infra+LLM, histórico, alertas | No calcula scores de calidad |
| **RAGAS** | Librería de scoring | `faithfulness`, `relevancy`, etc. | No instrumenta, no almacena |

Regla: **OTel es a SigNoz lo que HTTP es a un navegador.** No compiten — uno transporta, el otro
renderiza. SigNoz consume OTLP nativo (gRPC `:443` o HTTP), así que una sola instrumentación OTel
alimenta sus dashboards de infra **y** de LLM en el mismo panel, sin SDK propietario.

```
[agentes DEHA]
   │ OTel SDK (gen_ai.* + Temporal interceptor)
   ▼
[OTel Collector]  ──┬──► SigNoz Cloud    (UN panel: traces infra + LLM, costo, tokens, métricas eval)
   PII filtering    │                     · gen_ai.* spans con prompt/completion
   sampling         │                     · gen_ai.client.token.usage / operation.duration
   fan-out          └──► S3 / parquet     (dataset offline para RAGAS batch)
                                  ▲
            [RAGAS evaluator] ── lee dataset / lee traces SigNoz ── emite métricas hubara.eval.*
```

---

## §2. Goal & success criteria

### Goal

Que cualquier persona del equipo pueda responder, sin SSH a un pod:
1. "¿Qué hizo el agente en la sesión `wa_57...` el martes a las 3pm?" (trace navegable)
2. "¿Cuánto costó en tokens el agente de Sales esta semana?" (métrica agregada)
3. "¿En qué % de conversaciones el agente alucinó un precio/producto?" (RAGAS faithfulness)
4. "¿Subió la latencia del LLM tras el último deploy?" (métrica + alerta)

### Acceptance criteria — Parte A (OTel)

- [ ] Un turno real de Sales produce un trace en SigNoz con: workflow span → `build_prompt`
      → `llm_chat` (con `gen_ai.*` poblado: model, tokens, prompt, completion) → `execute_tool` anidados.
- [ ] El trace cruza `continue_as_new` sin romperse (span links validados).
- [ ] `gen_ai.client.token.usage` y `gen_ai.client.operation.duration` visibles como métricas.
- [ ] Logs de loguru incluyen `trace_id`/`span_id` y se correlacionan con el trace.
- [ ] PII (números WhatsApp, nombres) hasheada/filtrada antes de salir del Collector.
- [ ] Cambiar el backend (SigNoz → otro OTLP) = editar solo `otel-collector-config.yaml` (o env vars).
- [ ] Overhead de latencia por turno < 50ms p99 (instrumentación no bloquea el hot path).
- [ ] Kill-switch: `OTEL_SDK_DISABLED=true` apaga todo sin redeploy de código.

### Acceptance criteria — Parte B (RAGAS)

- [ ] Matcher `ragas:` opt-in en fixtures YAML del harness HU-001, con `min_score` que rompe build.
- [ ] `faithfulness` detecta un caso sintético de alucinación (fixture `011-faithfulness-precio-inventado`).
- [ ] Desglose de costo `agent_cost_usd` vs `judge_cost_usd` en `summary.json`.
- [ ] Pipeline offline: lee N traces de SigNoz del último día, calcula scores, emite métricas `hubara.eval.*`.
- [ ] Mismo `RagasEvaluator` corre en harness y en pipeline producción (DRY).

### Non-goals

- Migrar loguru → OTel logs nativos (experimental en Python 2026; correlación basta).
- RAGAS online en hot path (latencia + costo; siempre offline/batch).
- `context_recall` / `answer_correctness` (requieren ground truth, no lo tenemos).
- Auto-instrumentación zero-code (`opentelemetry-instrument`) — preferimos control explícito.
- Dashboards 100% custom (los pre-built de SigNoz para LiteLLM alcanzan para el MVP).

---

## §3. Arquitectura — Parte A (OTel)

### 3.1 Las 4 señales y dónde nacen

| Señal | Fuente | Mecanismo | Archivo que se toca |
|---|---|---|---|
| **Traces (workflow/activity)** | Temporal | `TracingInterceptor` en `Client.connect` | `src/platform/temporal/client.py` |
| **Traces (LLM)** | LiteLLM | `litellm.callbacks=["otel"]` | `exoclaw_temporal/activities/llm.py` |
| **Traces (tools)** | manual | span custom en `execute_tool` | `src/platform/temporal/activities.py` |
| **Metrics (GenAI)** | LiteLLM otel callback | automático (`gen_ai.client.*`) | (mismo que LLM traces) |
| **Metrics (negocio)** | manual | `meter.create_counter` | `src/platform/observability/metrics.py` (nuevo) |
| **Logs** | loguru | processor que inyecta trace ctx | `src/platform/logging.py` |
| **GenAI content** | LiteLLM | `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | (env var, no código) |

### 3.2 El bootstrap único de OTel

Un solo módulo `src/platform/observability/otel.py` (nuevo) con `init_otel()` que:
1. Configura `TracerProvider` + `MeterProvider` + `LoggerProvider` con un `Resource`
   común (`service.name`, `service.namespace`, `deployment.environment`).
2. Setea el `OTLPSpanExporter` / `OTLPMetricExporter` apuntando al Collector (env-driven).
3. Registra el `BatchSpanProcessor` (no bloquea el hot path — exporta en background).
4. Prende `litellm.callbacks=["otel"]`.
5. Es **idempotente** y **no-op si `OTEL_SDK_DISABLED=true`**.

Se llama una vez por proceso, al arranque de cada worker (junto a `setup_logging()`).

```python
# src/platform/observability/otel.py  (esqueleto)
from __future__ import annotations
import os
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

_INITIALIZED = False

def init_otel(service_name: str) -> None:
    """Idempotente. No-op si OTEL_SDK_DISABLED=true. Llamar 1x por proceso."""
    global _INITIALIZED
    if _INITIALIZED or os.getenv("OTEL_SDK_DISABLED", "").lower() == "true":
        return

    resource = Resource.create({
        "service.name": service_name,                 # "sales-agent" | "remarketing-agent"
        "service.namespace": "hubara",
        "deployment.environment": os.getenv("ENVIRONMENT", "dev"),
    })

    # --- Traces ---
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # endpoint via OTEL_EXPORTER_OTLP_ENDPOINT
    trace.set_tracer_provider(tp)

    # --- Metrics ---
    mp = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
    )
    metrics.set_meter_provider(mp)

    # --- LLM callback (LiteLLM emite gen_ai.* spans + metrics) ---
    import litellm
    # NOTA: NO sobreescribir el callback de caché existente — appendear.
    existing = list(getattr(litellm, "callbacks", []) or [])
    if "otel" not in existing:
        litellm.callbacks = existing + ["otel"]

    _INITIALIZED = True
```

### 3.3 Por qué `BatchSpanProcessor` y no `SimpleSpanProcessor`

- **Simple** exporta sincrónico → suma latencia al hot path del turno. ❌
- **Batch** acumula spans y los manda en background cada N ms. El turno no espera al export. ✅
- Trade-off: si el worker crashea, se pierde el último batch sin exportar. Aceptable para
  observabilidad (no es source-of-truth — eso vive en Temporal history + vault JSONL).

### 3.4 El Collector y el fan-out

`infra/otel/otel-collector-config.yaml` (nuevo):

```yaml
receivers:
  otlp:
    protocols:
      grpc:        # SigNoz soporta gRPC (más eficiente) — los workers exportan acá
        endpoint: 0.0.0.0:4317
      http:        # fallback HTTP si algún emisor no habla gRPC
        endpoint: 0.0.0.0:4318

processors:
  batch: {}
  # PII scrubbing — hashea números de teléfono y nombres antes de exportar
  attributes/pii:
    actions:
      - key: gen_ai.prompt
        action: hash          # o regex-redact con transform processor
      - key: gen_ai.completion
        action: hash
      - key: session.from_number
        action: hash

exporters:
  otlp/signoz:
    endpoint: ${SIGNOZ_OTEL_ENDPOINT}      # ingest.<region>.signoz.cloud:443
    headers:
      signoz-ingestion-key: ${SIGNOZ_INGESTION_KEY}
    tls:
      insecure: false
  # opcionales (fan-out):
  # file/s3: { path: /data/traces.jsonl }   # dataset para RAGAS batch

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [attributes/pii, batch]
      exporters: [otlp/signoz]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp/signoz]
```

**Decisión de arranque**: se puede empezar **sin** Collector — exportar directo de la app a SigNoz
Cloud seteando `OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.<region>.signoz.cloud:443` y
`OTEL_EXPORTER_OTLP_HEADERS=signoz-ingestion-key=<key>`. El Collector se agrega cuando necesitás
(a) PII scrubbing serio, (b) fan-out a S3. Para el PoC, directo. Para prod, Collector.

---

## §4. El gotcha crítico — versión de semantic conventions

### 4.1 El problema — el formato de prompt/completion es inestable

OTel GenAI semantic conventions sigue **experimental** y movió el lugar de prompts/completions
entre versiones. Hoy conviven tres formas, y qué dashboard las muestra depende de la versión:

| Versión semconv | Dónde van prompt/completion |
|---|---|
| **≤ v1.36** | span attributes `gen_ai.content.prompt` / `gen_ai.content.completion` |
| **v1.37 (atributos)** | span attributes `gen_ai.input.messages` / `gen_ai.output.messages` |
| **v1.37+ (eventos)** | span events `gen_ai.client.inference.operation.details` |

SigNoz lee el contenido de **span attributes** (`gen_ai.input.messages`, `gen_ai.output.messages`,
`gen_ai.system_instructions`) y se compromete a seguir el estándar conforme madura. Pero como es
experimental, **la versión de la instrumentación LiteLLM/OTel que emitís y la versión que el
dashboard de SigNoz espera tienen que coincidir** — si no, ves el trace con tokens/costo pero
**sin el texto** del prompt/completion. Y el texto es justo lo que necesitás para detectar alucinaciones.

(El caso análogo en Langfuse fue público — [issue #12657](https://github.com/langfuse/langfuse/issues/12657):
no leía el formato de eventos v1.37+. Mismo riesgo, distinto vendor → por eso se **verifica en el PoC**.)

### 4.2 La mitigación

1. **Pinear la versión de la instrumentación LiteLLM/OTel** a una que emita el formato atributo,
   o configurar el opt-in para forzar compatibilidad.
2. Setear explícito: `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` y **verificar en el
   PoC** que SigNoz muestra el prompt/completion poblado (no solo tokens) antes de seguir.
3. **Test de regresión** (`tests/observability/test_genai_span_shape.py`): genera un `llm_chat`
   con un fake provider y assertea que el span lleva `gen_ai.input.messages`/`gen_ai.output.messages`
   (el formato que SigNoz lee), no solo tokens — atrapa el día que un upgrade cambie el shape.
4. Documentar en `project-context.md` que **upgrades de `litellm` u `opentelemetry-*` requieren
   re-verificar este mapeo** (gotcha load-bearing).

**Por eso el PoC (Fase 1) es no-negociable antes de instrumentar todo**: si el formato no mapea,
toda la inversión en spans no se ve en el dashboard.

---

## §5. Puntos de instrumentación exactos

> Paths confirmados contra el código vivo. Nota: los workers **reales** post-PR11 viven en
> `src/plugins/chats/workers/{sales,remarketing}.py` — `src/sales_whatsapp/worker.py` es shell legacy.

### 5.1 Traces de workflow/activity — `client.py`

```python
# src/platform/temporal/client.py  — añadir interceptor
from temporalio.contrib.opentelemetry import TracingInterceptor

async def get_temporal_client() -> Client:
    ...
    return await Client.connect(
        TEMPORAL_URL,
        namespace=TEMPORAL_NAMESPACE,
        tls=tls_config,
        interceptors=[TracingInterceptor()],   # ← NUEVO. Spans automáticos workflow+activity
    )
```

Esto solo crea spans para client calls. Para que el **worker** también los cree (activities),
el `TracingInterceptor` se pasa además al `Worker(...)` en cada `workers/*.py`. Un helper
`get_worker_interceptors()` centraliza esto para no repetir.

### 5.2 Traces + metrics del LLM — `llm.py`

```python
# exoclaw_temporal/activities/llm.py  — el callback de caché YA existe, NO romperlo
# init_otel() (llamado al boot del worker) appendea "otel" a litellm.callbacks.
# Resultado: cada provider.chat() emite un span gen_ai.* con model/tokens/prompt/completion
# + alimenta los histogramas gen_ai.client.token.usage y operation.duration.
```

⚠️ El callback de caché (`_litellm_cache_logger` en `litellm.success_callback`) y el callback
`otel` (en `litellm.callbacks`) son listas **distintas** en LiteLLM — coexisten sin pisarse.
`init_otel()` debe **appendear**, nunca asignar `=`.

### 5.3 Spans custom de tools — `activities.py`

```python
# src/platform/temporal/activities.py  — execute_tool
from opentelemetry import trace
_tracer = trace.get_tracer("hubara.tools")

@activity.defn(name="execute_tool")
@with_heartbeat(every=10)
async def execute_tool(input: ExecuteToolInput) -> str:
    with _tracer.start_as_current_span(f"tool.{input.name}") as span:
        span.set_attribute("gen_ai.tool.name", input.name)
        span.set_attribute("gen_ai.tool.call.arguments", json.dumps(input.params)[:2000])
        result = await registry.execute(input.name, input.params, ctx)
        span.set_attribute("gen_ai.tool.call.result", result[:2000])  # truncado
        return result
```

⚠️ **Heartbeats NO como spans.** Cada `activity.heartbeat()` cada 10s NO debe crear un span
(explota la cardinalidad). Si querés trazarlos, son **span events** dentro del span de la activity.

### 5.4 Logs correlacionados — `logging.py`

```python
# src/platform/logging.py  — añadir trace context a cada log line
from opentelemetry import trace

def _inject_trace_context(record):
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx and ctx.is_valid:
        record["extra"]["trace_id"] = format(ctx.trace_id, "032x")
        record["extra"]["span_id"] = format(ctx.span_id, "016x")

# loguru: logger.configure(patcher=_inject_trace_context)
```

Esto NO migra a OTel logs nativos (experimental). Mantiene loguru + agrega `trace_id` para
poder saltar de un log a su trace en SigNoz. Las 3 señales quedan correlacionadas por `trace_id`
aunque los logs viajen por su canal actual. (SigNoz también ingesta logs OTLP si más adelante
querés unificarlos — pero no es bloqueante para el MVP.)

### 5.5 Métricas de negocio — `metrics.py` (nuevo)

```python
# src/platform/observability/metrics.py
from opentelemetry import metrics
_meter = metrics.get_meter("hubara.business")

turns_total = _meter.create_counter("hubara.agent.turns", unit="1")
escalations_total = _meter.create_counter("hubara.agent.escalations", unit="1")
transfers_total = _meter.create_counter("hubara.agent.transfers", unit="1")
tool_errors = _meter.create_counter("hubara.agent.tool_errors", unit="1")
```

Se incrementan desde las activities (no desde el workflow — R-DET). Esto te da los KPIs de
negocio (cuántas escalaciones/día por agente) en el mismo backend que la telemetría técnica.

---

## §6. Arquitectura — Parte B (RAGAS)

### 6.1 Dos modos, un evaluador

```
┌─ MODO 1: PRE-DEPLOY (harness HU-001) ────────────────────────┐
│  fixture YAML con bloque `ragas:` opt-in                     │
│   → runner captura (user, assistant, tool_results, workspace)│
│   → RagasEvaluator.evaluate_turn(...)                        │
│   → score como AssertionResult → HTML report + min_score gate│
└──────────────────────────────────────────────────────────────┘
┌─ MODO 2: POST-DEPLOY (producción) ───────────────────────────┐
│  Temporal Scheduled Workflow (nocturno)                      │
│   → lee N traces del último día desde SigNoz Query API       │
│   → reconstruye (question, answer, contexts) de los spans    │
│   → RagasEvaluator.evaluate_turn(...)   ← MISMO código       │
│   → emite métrica OTel gauge hubara.eval.faithfulness=0.87   │
│     con labels {agent, session_id, trace_id} → dashboard     │
└──────────────────────────────────────────────────────────────┘
```

El `RagasEvaluator` es agnóstico de la fuente — recibe un `EvalSample(question, answer, contexts)`
y devuelve `dict[metric, score]`. Lo comparten ambos modos.

### 6.1.b Cómo viven los scores en SigNoz (no hay `score()` nativo)

SigNoz no tiene un objeto "score" de primera clase como Langfuse. Los RAGAS scores se modelan
con primitivas OTel estándar — dos representaciones complementarias:

```python
# src/platform/observability/eval_metrics.py
from opentelemetry import metrics
_meter = metrics.get_meter("hubara.eval")

faithfulness_g = _meter.create_gauge("hubara.eval.faithfulness")        # tendencia agregada
relevancy_g    = _meter.create_gauge("hubara.eval.answer_relevancy")
precision_g    = _meter.create_gauge("hubara.eval.context_precision")

# 1) Como MÉTRICA (gauge) con labels → dashboards de tendencia "faithfulness por agente/día"
faithfulness_g.set(0.87, {"agent": "sales", "session_id": sid})
# 2) Como ATRIBUTO de span (en el span del turno, vía el batch pipeline) → drill-down:
#    abrís un trace puntual y ves su score pegado. Útil para "mostrame las conversaciones <0.5".
```

**Trade-off vs Langfuse**: perdés la UI de "lista de scores filtrable" out-of-the-box, pero ganás
que la métrica de calidad vive en el **mismo dashboard** que latencia/costo/tokens — podés cruzar
"¿la faithfulness cae cuando sube la latencia del LLM?" en un solo panel. Para "ver las peores
conversaciones", un dashboard de SigNoz con `WHERE hubara.eval.faithfulness < 0.5` lo cubre.

### 6.2 El problema del `context` (lo no obvio)

RAGAS espera `contexts: list[str]`. En los agentes hay **tres** fuentes y hay que unirlas bien:

```python
def build_contexts(turn) -> list[str]:
    contexts = []
    # 1. Tool results = "retrieved context" RAG-style (catálogo, checkout...)
    for tc in turn.tool_calls:
        contexts.append(f"[tool:{tc.name}] {tc.result}")
    # 2. Workspace .md de hechos (NO la persona) = grounding
    for md in ("IDENTITY.md", "TOOLS.md"):   # SOUL.md es persona, NO va
        contexts.append(read_workspace_md(md))
    # 3. Catalog snapshot resumido si la pregunta es de productos
    if any(tc.name in ("search_products", "get_product_by_handle") for tc in turn.tool_calls):
        contexts.append(catalog_summary())
    return contexts
```

**Por qué importa**: solo tool_results → RAGAS marca "no faithful" cuando el agente cita su
identidad. Solo .md → no detecta precio inventado fuera del catálogo. **Van ambos.**

### 6.3 Métricas aplicables

| Métrica | Aplica | Detecta | Costo/turno |
|---|---|---|---|
| `faithfulness` | ✅ **la clave** | Alucinación: precio/producto que no está en contexts | ~1 LLM call |
| `answer_relevancy` | ✅ | Respuesta desconectada de la pregunta | ~1 LLM call |
| `context_precision` | ✅ | Tool devolvió ruido / el agente no usó lo recuperado | ~1 LLM call |
| `aspect_critic` (custom) | ⚠️ casos especiales | "¿respeta horario comercial con tono natural?" | configurable |
| `context_recall` | ❌ | — (necesita ground truth) | — |
| `answer_correctness` | ❌ | — (necesita ground truth) | — |

`aspect_critic` cierra gaps que los hard matchers de HU-001 no pueden (ej. fixture `006-fuera-de-horario`:
validar tono, no solo presencia de keywords).

### 6.4 Contratos (extiende HU-001 §6)

```python
@dataclass(frozen=True)
class RagasExpect:
    faithfulness: dict[str, float] | None = None        # {"min_score": 0.8}
    answer_relevancy: dict[str, float] | None = None
    context_precision: dict[str, float] | None = None

# TurnExpect gana: ragas: RagasExpect | None = None
# TurnResult gana:  ragas_scores: dict[str, float] = field(default_factory=dict)
#                   judge_tokens_used: int = 0
# FixtureResult.llm_cost_usd se desglosa: agent_cost_usd + judge_cost_usd

@dataclass(frozen=True)
class EvalSample:                # el contrato que comparten ambos modos
    question: str
    answer: str
    contexts: list[str]
    session_id: str | None = None
    trace_id: str | None = None  # label de la métrica hubara.eval.* para correlacionar con el trace
```

---

## §7. Fases de implementación

### Parte A — OTel (≈3 días)

| # | Commit | Crea / Edita | Verify |
|---|---|---|---|
| A1 | `feat(obs): bootstrap otel idempotente` | `observability/otel.py`, deps en `pyproject.toml` | `init_otel()` no-op con `OTEL_SDK_DISABLED=true`; import limpio |
| A2 | `feat(obs): TracingInterceptor en client + workers` | `temporal/client.py`, helper en `workers/*` | un workflow local genera spans en consola (`ConsoleSpanExporter`) |
| A3 | `feat(obs): LiteLLM otel callback (append-safe)` | `otel.py` (append a `litellm.callbacks`) | `llm_chat` emite span `gen_ai.*`; callback de caché sigue vivo |
| A4 | **`feat(obs): PoC SigNoz — VERIFICAR prompt/completion visible`** | env vars SigNoz | **un turno real aparece en SigNoz con prompt/completion != null** (§4) |
| A5 | `feat(obs): spans custom de tools + business metrics` | `temporal/activities.py`, `observability/metrics.py` | trace muestra `tool.*` anidado; counters incrementan |
| A6 | `feat(obs): correlación trace_id en loguru` | `platform/logging.py` | log line incluye `trace_id` que matchea el trace |
| A7 | `feat(obs): OTel Collector + PII scrubbing + fan-out` | `infra/otel/otel-collector-config.yaml`, compose | PII hasheada; `OTEL_SDK_DISABLED` apaga todo |
| A8 | `test(obs): regresión shape gen_ai span + smoke` | `tests/observability/` | CI valida que el span lleva el formato que SigNoz lee |

### Parte B — RAGAS (≈2 días)

| # | Commit | Crea / Edita | Verify |
|---|---|---|---|
| B1 | `feat(eval): EvalSample + context_builder + RagasEvaluator` | `conversation_eval/ragas/` | unit test: builder arma contexts; evaluator con LLM stub |
| B2 | `feat(eval): ragas matcher en harness (opt-in YAML)` | extiende `contracts.py`, `fixture_loader.py`, `runner.py` | fixture con `ragas:` corre y reporta score |
| B3 | `feat(eval): ragas en HTML report + cost split` | `reporting/*` | snapshot HTML con barras de score; `judge_cost_usd` separado |
| B4 | `feat(eval): fixture 011 faithfulness (alucinación sintética)` | `fixtures/sales/011_*.yaml` | `faithfulness < 0.5` en respuesta con precio inventado |
| B5 | `feat(eval): pipeline producción (Scheduled Workflow + SigNoz read → métricas eval)` | `observability/ragas_batch/` | corre contra N traces, emite `hubara.eval.*` gauges |

**Orden recomendado**: A1→A4 (PoC) **antes** que nada más. Si A4 no mapea (§4), parar y resolver
el formato semconv antes de seguir. B1–B4 pueden ir en paralelo a A5–A8 (no dependen de prod OTel).
B5 depende de Parte A completa (necesita traces reales en SigNoz + la SigNoz Query API).

---

## §8. Qué ganamos / qué perdemos — tabla honesta

### Ganamos

- **Un trace end-to-end por turno**: webhook → workflow → build_prompt → llm_chat → tool → respuesta.
  Hoy: nada (solo loguru disperso).
- **Costo y tokens por agente/día** sin instrumentar a mano (LiteLLM otel callback lo da gratis).
- **Detección de alucinaciones medible** (RAGAS faithfulness) — el pedido original.
- **Portabilidad**: cambiar SigNoz por Datadog/Grafana/Phoenix = config, no código.
- **Correlación logs↔traces** por `trace_id`.
- **Base para alertas** (latencia LLM subió, tasa de tool_errors, etc.).

### Perdemos / cuesta

- **Mantenimiento del gotcha semconv** (§4): upgrades requieren re-verificar que SigNoz muestra el texto.
- **PII en spans** si capturás contenido → obliga a scrubbing en Collector (más infra).
- **OTel logs nativos NO** (experimental) → logs correlacionados pero no "unificados" 100%.
- **Sin features LLM-dev de Langfuse** (session-replay, evaluator templates, prompt-mgmt): SigNoz es
  observabilidad, no dev-platform. Ya las cubrís por otro lado (vault + harness + RAGAS propio + `.md`).
- **RAGAS sin UI de scores nativa**: se modela como métricas/atributos (§6.1.b); "peores convos" = query.
- **Cardinalidad/storage**: hay que tunear sampling; el plan de SigNoz Cloud factura por volumen ingerido.
- **Costo RAGAS**: ~1 LLM call extra por turno evaluado (mitigado: offline + sampling + cache).

### Lo que NO cambia (sigue siendo source of truth)

- Temporal history = verdad de la orquestación (durabilidad).
- Vault JSONL = verdad de la conversación.
- OTel es **observabilidad derivada** — si se cae, no se pierde negocio, solo visibilidad.

---

## §9. Decisiones abiertas (cerrar antes de implementar)

| # | Decisión | Opciones | Recomendación |
|---|---|---|---|
| D1 | Backend observabilidad | ✅ **RESUELTO: SigNoz Cloud** | OTel-native; unifica infra+LLM; dashboard LiteLLM pre-built |
| D2 | Collector desde día 1 | Sí · No (export directo en PoC) | **No en PoC, sí en prod** (PII + fan-out) |
| D3 | Captura de contenido de prompts | Full · Hash · Off | **Full en dev, hash/redact en prod** (Collector) |
| D4 | LLM judge de RAGAS | DeepSeek (mismo) · Claude Haiku | **DeepSeek MVP**, evaluar Haiku si hay sesgo |
| D5 | Scope inicial de señales | ✅ **RESUELTO: traces+genai primero** (A1–A4) | resto incremental |
| D6 | Sampling en prod | 100% · head-sampling % · tail (errores) | **100% en PoC**, tail-sampling cuando escale |

---

## §10. Costo y overhead

| Concepto | Estimación |
|---|---|
| Overhead latencia/turno (BatchSpanProcessor) | < 50ms p99 (export en background) |
| Volumen SigNoz Cloud | ~1–5 KB/span; ~10 spans/turno → ~50KB/turno (factura por GB ingerido) |
| RAGAS judge | ~1 LLM call/métrica/turno evaluado; sampling al 10% en prod |
| Costo judge (DeepSeek) | ~$0.003/turno × 10% sampling × volumen |
| Infra Collector | 1 container ligero (256MB RAM típico) |

Kill-switches: `OTEL_SDK_DISABLED=true` (apaga OTel), sampling rate (baja volumen),
RAGAS sampling % (baja costo de eval).

---

## §11. Gotchas específicos al stack DEHA

1. **R-DET + sandbox (CONFIRMADO por `scripts/otel_smoke.py`)**: el `TracingInterceptor` NO es
   "determinista by design" — crea el span de ejecución DENTRO del workflow sandbox y OTel genera
   los span IDs con `random.getrandbits`, que el sandbox bloquea (`RestrictedWorkflowAccessError`).
   **Fix obligatorio**: cada `Worker(...)` usa `workflow_runner=otel_workflow_runner()` (passthrough
   de `opentelemetry`). Sin esto, TODO workflow real rompe al validar. Corolarios: (a) el módulo que
   contiene un `@workflow.defn` NO debe importar `opentelemetry` a nivel módulo (el sandbox lo
   re-importa al validar); (b) nunca crear spans/métricas manuales desde el workflow code — solo
   desde activities. Ya aplicado en `sales.py` + `remarketing.py`.
2. **`continue_as_new` cada 50 turnos**: cada continuación es un trace nuevo; el `TracingInterceptor`
   debe linkear spans. Pinear versión de `temporalio` y verificar en A2 (hay fixes recientes en el
   [forum de Temporal](https://community.temporal.io/t/fix-workflow-level-otlp-tracing-with-python-workers/17255)).
3. **LiteLLM doble callback list**: `success_callback` (caché) y `callbacks` (otel) son distintas.
   `init_otel` **appendea**, no asigna. Romper esto mata el cache logger existente.
4. **Heartbeats cada 10s**: span events, NO spans. Si no, cardinalidad explota.
5. **Workers reales en `plugins/chats/workers/`**: instrumentar ahí, no en los shells legacy
   `sales_whatsapp/worker.py`.
6. **SigNoz Cloud = endpoint `:443` con header `signoz-ingestion-key`** (no Authorization Basic).
   Soporta gRPC y HTTP; usar gRPC desde los workers por eficiencia.
7. **PII**: números y nombres en prompts. Sin scrubbing, se filtran al backend. Self-host NO exime
   (backups). Hash en Collector.
8. **`ruff --fix` borra imports de OTel entre edits** si quedan "sin usar" en un archivo a medio
   instrumentar — ya pasó con otros imports (ver MEMORY dispatcher). Commitear instrumentación completa.

---

## §12. Quick-start para el implementador

1. Leé §1 (modelo mental) y §4 (gotcha semconv) — son lo no obvio.
2. Empezá por A1→A4 con `ConsoleSpanExporter` antes de SigNoz (ves los spans en stdout, cero infra).
3. **A4 es el gate**: si SigNoz no muestra prompt/completion, resolvé §4 antes de seguir.
4. Parte B reusa todo el dataset de HU-001 — no reinventes captura, extendé el runner.
5. Cada commit cierra con su `verify` verde.

Dudas no resueltas acá → §13 "Open during impl".

---

## §13. Open during impl

_Vacío al cierre de la spec. El implementador anota acá las decisiones tomadas en runtime._
