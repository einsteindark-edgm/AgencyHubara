# Plan — Harness de evaluación de calidad del LLM (sales agent) sobre OTel + SigNoz + DeepEval

> Investigación + diseño. NO es código aún. Pensado para alimentar el pipeline Archon
> (`archon workflow run hu-hubara-pipeline`) o para ejecutar por fases manualmente.
> Fecha: 2026-06-03. Autor: agente (research) + operador (decisiones).

---

## 0. TL;DR

La técnica que leíste es el **ciclo de evaluación de LLMOps** ("eval-driven development"):
no se evalúa el LLM una sola vez, se cierra un **loop continuo** con **tres superficies**:

1. **Unit tests de eval (pre-prod, offline, con referencia)** — corren en CI contra un
   **golden dataset** curado. Bloquean el deploy si baja el % de casos que pasan. → "test
   unitarios antes de salir a producción con deepeval".
2. **Evals online/asíncronas (prod, muestreadas, sin referencia)** — un job programado
   muestrea conversaciones **reales** desde las trazas (SigNoz), las puntúa con un LLM-juez,
   y **escribe los scores de vuelta a SigNoz** → tablero de calidad + alertas de drift.
   → "en ciertos momentos del día se va al tablero y hace evals con público real".
3. **Curación de goldens (cierra 2→1)** — las conversaciones que puntúan bajo / son
   interesantes se promueven a **golden cases** (con la respuesta corregida) y se suman al
   dataset de regresión. → "convertir esos casos en golden cases".

**¿Se puede con lo que tenemos? Sí — ~80% de la infra ya está.** Lo construido en HU-003
(OTel→SigNoz, spans `gen_ai.*` con prompt/completion, `session.id`/`episode.id` en cada span,
`MeterProvider`/`LoggerProvider`, Temporal Schedule probado, litellm+DeepSeek, OpenLIT) es
justamente el cimiento. Falta: el paquete `deepeval`, definir el **modelo juez**, una capa de
lectura de conversaciones, el **harness DEHA** (workflow + activities en un Schedule), las
**métricas de calidad específicas de sales**, el **golden store** + curación, y el **gate de CI**.

---

## 1. La técnica, investigada

### 1.1 El stack de herramientas (qué hace cada una)

| Herramienta | Qué es | Rol en nuestro caso |
|---|---|---|
| **DeepEval** (OSS, Confident AI) | Framework de eval estilo pytest. `LLMTestCase` / `ConversationalTestCase`, métricas (G-Eval, ConversationalGEval, AnswerRelevancy, Faithfulness, RoleAdherence, KnowledgeRetention, ConversationCompleteness), `EvaluationDataset` de `Golden`s, `evaluate()` programático + `deepeval test run` para CI, juez LLM **custom** (LiteLLM/DeepSeek/`DeepEvalBaseLLM`). | **Motor de eval** en las 3 superficies. |
| **Confident AI** (cloud, paga) | Plataforma SaaS sobre DeepEval: online evals sobre trazas vivas, **curación automática de dataset desde producción**, dashboards, alertas. Free tier mínimo (5 test runs/semana, 1 GB spans); self-host solo Enterprise. | ❌ **No la usamos** — manda PII de WhatsApp (clientes en Colombia) a un SaaS. Replicamos su loop sobre **nuestro SigNoz self-hosted**. |
| **OpenLIT** (OSS, ya instalado) | OTel-native. Auto-instrumenta litellm (`gen_ai.*`). Además trae **11 evals LLM-as-judge** (hallucination, bias, toxicity, relevance, faithfulness…) que **emiten como métricas OTel** al mismo backend (SigNoz) con `collect_metrics=True`. | Vía **inline/rápida** alternativa para evals por-llamada. Complementa a DeepEval (que es per-conversación + golden + CI). |
| **SigNoz** (self-hosted, ya corriendo) | ClickHouse + OTel. Trazas con `gen_ai.prompt/completion`, métricas de tokens/costo/latencia, query por ClickHouse SQL + Trace API REST. | **Hub**: fuente de muestreo + destino de scores + dashboards + alertas. |
| **Temporal** (ya en stack) | Durable execution + **Schedules** (cron nativo, OSS). Patrón ya probado en `orders/workers/reconcile.py`. | **Scheduler** de la eval asíncrona ("en ciertos momentos del día"). |

**Hallazgo clave**: la parte "mágica" de la técnica (online evals sobre trazas + auto-curación
de dataset) es **feature cloud de Confident AI**. Por privacidad la replicamos nosotros: el loop
es ~150 líneas de glue sobre infra que ya tenemos. No dependemos de su SaaS.

### 1.2 Por qué multi-turn (no single-turn)

El sales agent es **conversacional** (WhatsApp, episodios multi-turno). DeepEval distingue:

- `LLMTestCase` (single-turn: input → actual_output) → sirve para evals de **una tool/respuesta**.
- `ConversationalTestCase` (lista de `Turn(role, content)`) → evalúa **la conversación entera**:
  ¿el agente mantuvo el rol?, ¿retuvo el contexto entre turnos?, ¿avanzó hacia la conversión?,
  ¿escaló a humano cuando debía? Esto es lo que importa para sales.

Métricas multi-turno relevantes: `ConversationalGEval` (criterio en lenguaje natural, LLM-juez),
`RoleAdherenceMetric`, `KnowledgeRetentionMetric`, `ConversationCompletenessMetric`.

---

## 2. Las 3 superficies mapeadas a tu pedido

### Superficie 1 — Unit tests de eval (pre-prod, con referencia)
**"poder tener unit test de eval … antes de salir a producción con deepeval"**

- Corre en CI (o `pytest -m eval` local) **contra el golden dataset** versionado en el repo.
- Usa goldens (`ConversationalGolden`: `scenario`, `expected_outcome`, `turns`) → DeepEval arma
  el `ConversationalTestCase` y puntúa con `ConversationalGEval` (criterio = "responde según el
  playbook de sales", "no inventa precios", "usa tuteo no voseo", …) + `assert_test`.
- Gate: si baja el % de casos que pasan vs baseline → **bloquea el merge** (igual que
  `pytest -m architecture`).
- Comando: `deepeval test run tests/evals/test_sales_goldens.py` (maneja async/retries/repeats).

```python
# tests/evals/test_sales_goldens.py  (ilustrativo)
import pytest
from deepeval import assert_test
from deepeval.dataset import EvaluationDataset
from deepeval.test_case import ConversationalTestCase, Turn
from src.plugins.chats.agent.sales.evals.metrics import sales_quality_metrics

dataset = EvaluationDataset()
dataset.add_goldens_from_json_file("tests/evals/goldens/sales/curated.json")  # ConversationalGoldens

@pytest.mark.eval
@pytest.mark.parametrize("golden", dataset.goldens)
def test_sales_conversation(golden):
    tc = ConversationalTestCase(turns=golden.turns, scenario=golden.scenario,
                                expected_outcome=golden.expected_outcome,
                                chatbot_role="Asesor de ventas Hubara")
    assert_test(test_case=tc, metrics=sales_quality_metrics(threshold=0.7))
```

### Superficie 2 — Evals online/asíncronas (prod, sin referencia, sobre SigNoz)
**"trackear las conversaciones y en ciertos momentos del día ir al tablero y hacer evals con público real"**

- Un **Temporal Schedule** dispara, p.ej. 08:00 / 14:00 / 20:00 (`ScheduleSpec`), el
  `SalesEvalWorkflow`.
- El workflow (DET) elige la **ventana** (últimas N horas) y orquesta; las activities
  (no-DET, donde viven los LLM-calls) hacen el trabajo:
  1. **Seleccionar** qué conversaciones evaluar (muestreo) — consultando SigNoz por
     `session.id`/`episode.id` (las que ya etiquetamos en HU-003): las más caras, las más
     largas, las que escalaron a humano, + una muestra aleatoria del resto.
  2. **Reconstruir** cada conversación como `ConversationalTestCase` (ver §4.3 — fuente).
  3. **Puntuar** con métricas **sin referencia** (no hay ground-truth en prod):
     ConversationalGEval de calidad de sales, RoleAdherence, KnowledgeRetention, relevancia,
     + chequeos de dominio (voseo, precios inventados, handoff correcto).
  4. **Emitir** los scores **de vuelta a SigNoz** como métricas OTel
     (`gen_ai.eval.score` con atributos `metric.name`, `session.id`, `episode.id`, `verdict`)
     + un span/log por eval para drill-down.
- Resultado: un tablero **"Calidad del LLM"** en SigNoz (score por métrica en el tiempo, por
  segmento), alertas de **drift** (si el score promedio cae), y una lista de
  **candidatos a golden** (los que puntúan bajo).

### Superficie 3 — Loop de curación de goldens (cierra 2→1)
**"convertir esos casos en golden cases para luego hacer pruebas en test unitarios"**

- Los candidatos de la Superficie 2 (bajo score / handoff / quejas) se vuelcan a un buffer
  (`tests/evals/goldens/sales/_candidates/`).
- **Revisión humana** (operador): corrige la `expected_outcome` (qué DEBIÓ responder el agente),
  redacta PII (teléfono/nombre), y **promueve** el caso al dataset curado
  (`tests/evals/goldens/sales/curated.json`).
- Ese golden ahora es parte de la Superficie 1 → cada deploy futuro se testea contra ese caso
  real. **El fallo de prod se convierte en test de regresión.** Loop cerrado.

```
   prod (WhatsApp)
        │  trazas gen_ai.* + session.id/episode.id
        ▼
   ┌─────────┐   Schedule 3×/día   ┌──────────────────┐  scores  ┌─────────┐
   │ SigNoz  │ ──────────────────► │ SalesEvalWorkflow │ ───────► │ SigNoz  │  ← tablero calidad
   │ (traces)│   muestreo          │  + activities     │  (OTel)  │ (metrics)│  + alertas drift
   └─────────┘                     │  DeepEval (juez)  │          └─────────┘
                                   └────────┬─────────┘
                                            │ candidatos (bajo score)
                                            ▼
                                   revisión humana + redacción PII
                                            │ promueve
                                            ▼
                                   golden dataset (repo, versionado)
                                            │
                                            ▼
                                   CI: `deepeval test run`  ← bloquea deploy si regresiona
```

---

## 3. Arquitectura propuesta (DEHA-native)

El harness es **otra instancia del patrón `reconcile.py`** (que ya corre en prod): un worker que
(a) asegura un Temporal Schedule al boot y (b) corre el loop de workflows que el Schedule dispara.

```
src/plugins/chats/agent/sales/evals/         ← NUEVO (dentro de chats: dueño del sales agent + shape de conversación)
  ├── metrics.py        ← define las ConversationalGEval/G-Eval de sales (criterios)
  ├── judge.py          ← wrapper DeepEvalBaseLLM sobre litellm (modelo juez)
  ├── reconstruct.py    ← conversación (vault o spans) → ConversationalTestCase  [PURO]
  ├── select.py         ← muestreo: qué session.id evaluar (consulta SigNoz)
  └── contracts.py      ← DTOs frozen (R-JSON) workflow↔activity
src/plugins/chats/agent/sales/activities/
  └── eval_activities.py ← run_sales_eval_activity (NO-DET: corre DeepEval) + heartbeat
src/plugins/chats/agent/sales/workflows/
  └── sales_eval.py      ← SalesEvalWorkflow (DET: ventana + fan-out + recolecta)
src/plugins/chats/workers/
  └── sales_eval.py      ← worker: _ensure_schedule(3×/día) + Worker(loop)  [copia de reconcile.py]
src/platform/observability/
  └── eval_metrics.py    ← emit_eval_score(): Meter global → métrica OTel a SigNoz  [genérico]
```

**Cumplimiento de R-rules** (crítico — el harness mismo se audita):
- **R-DET**: DeepEval = LLM-as-judge = I/O + no-determinismo → **vive en una activity**, jamás en
  el workflow. El `SalesEvalWorkflow` solo orquesta (elige ventana, fan-out a activities, recolecta).
  *Esta es la trampa #1: no importar deepeval en el módulo del workflow.*
- **R-JSON**: lo que cruza workflow↔activity (input: lista de `session_id`+ventana; output: scores)
  son `@dataclass(frozen=True)` serializables.
- **R-STATELESS**: la activity no cachea a nivel módulo; el juez/dataset se construyen vía
  `composition.py` con `@lru_cache`.
- **R-HEARTBEAT**: la activity de eval corre múltiples LLM-calls (puede tardar > 10s) → `@with_heartbeat`.
- **R-DIP**: si el harness vive **dentro de `chats`**, lee su propio metadata (sin importar siblings).
  El emisor de métricas (`platform/observability/eval_metrics.py`) es genérico (solo `opentelemetry`).
  → `lint-imports` queda verde.

**Por qué dentro de `chats` (no un plugin `evals` nuevo)**: el harness necesita el **shape de la
conversación del sales agent** (episodios, mensajes). Ponerlo en `chats` evita la gimnasia de
R-DIP (un plugin `evals` no podría importar `chats`). Si mañana evaluamos varios agentes, se
extrae a un plugin `observability`/`evals` con un contrato compartido en `platform`. (Decisión §6.)

---

## 4. Detalles que definen el build

### 4.1 El modelo juez (LLM-as-judge)
DeepEval necesita un LLM para casi toda métrica. Opciones:
- **(a) DeepSeek V4 Pro** (el mismo que el agente) vía `LiteLLMModel`/`DeepEvalBaseLLM` apuntando a
  nuestro proxy litellm. **Pro**: ya configurado, data se queda en infra. **Contra**: **self-preference
  bias** — un modelo tiende a aprobar su propio estilo; juez==evaluado es metodológicamente débil.
- **(b) Un modelo distinto/más fuerte como juez** (otro alias en litellm — p.ej. `gemini-backup`,
  o un frontier por API). **Pro**: juicio más confiable, sin self-preference. **Contra**: si es API
  externa, la PII sale de infra (ver §4.4); costo.
- **Recomendación**: juez = **modelo distinto al evaluado, servido por nuestro litellm** (data
  in-infra). Si hoy solo hay DeepSeek, arrancar con DeepSeek-as-judge para validar el pipeline,
  y cambiar el alias del juez cuando tengamos un segundo modelo fuerte. El juez es **un alias de
  config**, no código → cambiarlo es trivial.
- **Idioma**: las conversaciones son en **español (Colombia)**. Validar que el juez puntúa bien en
  español (los criterios de ConversationalGEval se escriben en español).

### 4.2 Qué métricas para SALES (criterios concretos)
Ancladas en `.hubara/specs/agents/sales-worker/spec.md` + nuestros gotchas ya documentados:

| Métrica (ConversationalGEval salvo nota) | Criterio (lenguaje natural, al juez) | Ancla |
|---|---|---|
| **Tono / voseo** | "El asistente usa **tuteo**, nunca voseo (no 'tenés/querés/podés')." | gotcha voseo (MEMORY) |
| **No alucina catálogo** | "El asistente no inventa precios, productos ni stock; solo afirma lo que vino de una tool." | backend-behavior gotcha |
| **Avance a conversión** | "El asistente mueve la charla hacia el cierre (cotiza, propone siguiente paso) sin ser agresivo." | spec sales funnel |
| **Handoff correcto** | "Si el cliente pide humano o hay frustración, escala (route=humano + tag=HUMANO)." | human-handoff invariant |
| **RoleAdherence** (built-in) | rol fijo = "Asesor de ventas Hubara". | — |
| **KnowledgeRetention** (built-in) | no re-pregunta datos que el cliente ya dio (nombre, pedido). | context-leak gotcha |
| **Relevancia** (built-in / GEval) | responde lo que el cliente preguntó. | — |

En **Superficie 1** (con golden) se suma **G-Eval con referencia** contra `expected_outcome`.
En **Superficie 2** (prod) solo van las **sin referencia** (las de arriba).

### 4.3 De dónde sale la conversación (fuente de los Turns)
Dos fuentes posibles; **recomendación = híbrido**:
- **Contenido** ← **vault `hubara_vault/wa_*/metadata.json`** (fuente de verdad de los mensajes,
  ya estructurado role/content). Reconstrucción de `Turn`s limpia y confiable **hoy**, sin parsear
  atributos anidados de spans. Se lee con `FilesystemMetadataStore` (platform).
- **Selección + correlación + writeback** ← **SigNoz** (qué `session.id` evaluar por costo/largo/
  handoff; y a dónde escribir los scores). Aprovecha el `session.id`/`episode.id` de HU-003.
- *(Futuro)* reconstrucción 100% desde spans (`gen_ai.prompt.*`) si querés cero dependencia del
  vault — más fiel al "evaluá exactamente lo que pasó" pero más frágil de parsear. Empezamos con
  vault por robustez.

### 4.4 PII / gobernanza (no opcional)
Las conversaciones son de **clientes reales** (teléfonos, nombres, direcciones).
- El **juez ve texto crudo**: si el juez corre **en nuestra infra** (litellm/DeepSeek self-hosted),
  la data no sale (mismo trust boundary que prod hoy). Si el juez es **API externa**, la PII sale →
  requiere decisión explícita + idealmente **redacción previa**.
- Los **goldens viven en el repo** (git) → **deben redactarse** (teléfono→`<PHONE>`, nombre→`<NAME>`)
  antes de commitear. Helper de redacción en `reconstruct.py`/curación.
- **Recomendación**: juez in-infra + redacción obligatoria antes de promover a golden.

### 4.5 Costo y muestreo (la eval cuesta LLM-calls)
N conversaciones × M métricas × tokens-de-juez por corrida. Sin tope, el costo de evaluar puede
rivalizar con el de producir. → **muestreo + presupuesto**:
- Cap por corrida (p.ej. ≤ 50 conversaciones × 3 corridas/día).
- Priorizar: caras + largas + handoff + muestra aleatoria (no solo las malas → evitás sesgo).
- El costo del juez **también** se ve en SigNoz (OpenLIT instrumenta el litellm del juez) → el
  harness se auto-mide. Tope configurable por env.

---

## 5. Qué tenemos ya vs qué falta

| Pieza | Estado | Detalle |
|---|---|---|
| SigNoz self-hosted + OTel | ✅ | corriendo (HU-003 A5). |
| Spans `gen_ai.*` con prompt/completion + tokens/costo | ✅ | OpenLIT (otel.py `_instrument_genai`). |
| `session.id`/`episode.id`/`whatsapp.number` en cada span | ✅ | BaggageSpanProcessor (HU-003 A7) — **la clave de agrupación**. |
| `MeterProvider` + `LoggerProvider` para emitir scores | ✅ | otel.py — solo falta el helper `emit_eval_score`. |
| Temporal Schedule (patrón) | ✅ | `orders/workers/reconcile.py` — copiar tal cual. |
| litellm + DeepSeek (juez candidato) + OpenLIT (juez inline alt) | ✅ | falta elegir alias del juez. |
| pytest + markers + fixtures de vault aislado | ✅ | agregar marker `eval`. |
| Contenido de conversación (vault metadata) | ✅ | fuente de Turns. |
| **`deepeval`** (paquete) | ➕ | dev-dep + extra runtime para el harness. |
| **Modelo juez** (decisión + config) | ➕ | §4.1 — alias litellm. |
| **Capa de lectura/selección** (SigNoz ClickHouse/Trace API + reconstruct) | ➕ | `select.py` + `reconstruct.py`. |
| **Harness DEHA** (workflow + activities + worker schedule) | ➕ | §3. |
| **Métricas de sales** (ConversationalGEval criterios) | ➕ | §4.2. |
| **Golden store + curación** (JSON versionado + redacción + revisión) | ➕ | §2 Superficie 3. |
| **Gate de CI** (`deepeval test run` / `pytest -m eval`) | ➕ | Superficie 1. |
| **Tablero "Calidad del LLM"** en SigNoz | ➕ | lo armo por API como los otros 4. |
| **Decisión PII** (juez in-infra, redacción) | ⚠️ | §4.4 — operador. |

**Veredicto**: con lo que tenemos **se puede** — el 80% es infra de HU-003 reutilizada. El 20%
nuevo es glue + las definiciones de calidad de sales + el loop de curación.

---

## 6. Decisiones para vos (definen el shape del build)

1. **Modelo juez**: ¿DeepSeek-as-judge (rápido, self-preference) para validar el pipeline, o
   esperamos a tener un 2º modelo fuerte como juez? *(Recom: arrancar con DeepSeek, alias
   cambiable.)*
2. **Fuente de conversación**: ¿vault metadata (robusto hoy) o reconstrucción pura desde spans
   (observability-purista)? *(Recom: vault para contenido + SigNoz para selección/writeback.)*
3. **Dónde vive el harness**: ¿dentro de `chats` (simple, R-DIP limpio) o plugin `evals`/
   `observability` nuevo (más limpio si crece cross-agente)? *(Recom: `chats` ahora, extraer
   después.)*
4. **Cadencia + presupuesto**: ¿cuántas corridas/día y tope de conversaciones por corrida?
   *(Recom: 3×/día, ≤50 conv/corrida, configurable por env.)*
5. **PII**: ¿confirmás juez **in-infra** + redacción obligatoria antes de commitear goldens?
   *(Recom: sí.)*
6. **OpenLIT inline evals** (complemento opcional): ¿activamos también los 11 evals inline de
   OpenLIT (hallucination/toxicity por-llamada → métricas OTel directas) además de DeepEval?
   *(Recom: no al inicio — DeepEval per-conversación + golden cubre el objetivo; OpenLIT inline
   es un add-on de bajo esfuerzo para después.)*

---

## 7. Plan incremental (fases independientes, cada una shippa valor)

> Cada fase es verificable y útil por sí sola. Orden = menor riesgo / mayor valor primero.

- **F0 — Spike (medio día).** Agregar `deepeval` (dev). Escribir 1 `ConversationalTestCase` a mano
  desde una conversación real + 1 `ConversationalGEval` ("usa tuteo") con DeepSeek-as-judge vía
  litellm. Correr `deepeval test run`. **Valida**: juez funciona en español, score sale. Sin esto
  no seguimos.
- **F1 — Superficie 1 (unit eval + golden seed).** `tests/evals/` + 5–10 goldens curados a mano
  (redactados) + `metrics.py` (criterios de sales §4.2) + marker `eval` + comando en
  project-context. **Valor inmediato**: red de regresión de calidad antes de cada deploy. **Sin
  tocar prod.**
- **F2 — Superficie 2 (eval asíncrona).** `eval_metrics.py` (emit a SigNoz) + `reconstruct.py` +
  `select.py` + `SalesEvalWorkflow` + activity + worker-schedule (copia de reconcile.py). Corre
  3×/día, escribe scores a SigNoz. **Valor**: visibilidad de calidad con tráfico real.
- **F3 — Tablero + alertas.** Dashboard "Calidad del LLM" en SigNoz (por API, como los 4 ya
  hechos) + alerta de drift. **Valor**: el tablero que pediste.
- **F4 — Superficie 3 (curación).** Buffer de candidatos + flujo de revisión/redacción/promoción
  (CLI o pestaña frontend) → goldens. **Valor**: el loop se cierra (prod → regresión).

Cada fase puede ser **una HU del pipeline** (`hu-hubara-pipeline`) o agruparse. F1 sola ya entrega
"unit test de eval"; F2+F3 entregan "eval asíncrona con SigNoz"; F4 entrega "golden dataset loop".

---

## 8. Riesgos / trampas (las que ya veo)

1. **DeepEval dentro de un workflow = R-DET roto.** El juez es I/O + no-determinista → **solo en
   activity**. No importar deepeval en el módulo del workflow. (Trampa #1, la más fácil de cometer.)
2. **Juez == evaluado (self-preference).** DeepSeek juzgando a DeepSeek infla scores. Mitigar con
   juez distinto cuando se pueda; documentar el sesgo mientras tanto.
3. **Eval flakiness (umbral).** El LLM-juez no es 100% estable → un golden puede oscilar cerca del
   umbral. Mitigar: umbrales con margen, `evaluate` con repeats, criterios bien específicos, y
   tratar el % agregado (no un caso suelto) como señal de regresión.
4. **PII en goldens / al juez.** §4.4 — redacción + juez in-infra. Bloqueante de gobernanza.
5. **Costo del juez.** §4.5 — muestreo + tope + auto-medición en SigNoz.
6. **Fidelidad de reconstrucción.** Reconstruir Turns desde spans es frágil (prompt incluye toda
   la ventana cada turno). Por eso F2 arranca leyendo vault (limpio).
7. **Calidad del juez en español.** Validar en F0; los criterios van en español.
8. **`deepeval` trae dependencias pesadas** (tiktoken, etc.) → ponerlo en un grupo/extra para no
   inflar la imagen de los workers de prod que no evalúan.

---

## 9. Verificación (cómo probamos cada superficie)

1. **F0/F1**: `cd hubara_agency && uv run deepeval test run tests/evals/test_sales_goldens.py` →
   scores por métrica; `uv run pytest -m eval` verde; `uv run lint-imports` (harness no importó
   siblings) + `uv run pytest -m architecture`.
2. **F2**: tras correr el Schedule (o disparar el workflow a mano), en SigNoz (`localhost:8080`)
   ver la métrica `gen_ai.eval.score` agrupada por `metric.name`/`session.id`, y un span/log por
   eval. `lint-imports` + `pytest -m architecture` siguen verdes (R-DET/R-DIP).
3. **F3**: el tablero "Calidad del LLM" muestra score en el tiempo + alerta dispara con drift
   simulado.
4. **F4**: un candidato de bajo score se promueve a golden (redactado) y aparece como caso nuevo
   en `deepeval test run`.

---

## 10. Refs (investigación)

- DeepEval — datasets/goldens, unit testing CI/CD, multi-turn (`ConversationalTestCase`/`Turn`),
  `ConversationalGEval`, juez custom (LiteLLM/DeepSeek/`DeepEvalBaseLLM`): https://deepeval.com/docs
- Confident AI (cloud; online evals + auto-curación; free tier / self-host Enterprise):
  https://www.confident-ai.com/pricing
- OpenLIT programmatic evals (11 tipos, emiten OTel con `collect_metrics=True`):
  https://docs.openlit.io/latest/openlit/evaluations/programmatic-evals
- SigNoz ClickHouse traces query (extraer `gen_ai.*`): https://signoz.io/docs/userguide/writing-clickhouse-traces-query/
- Temporal Schedules (Python, OSS): https://docs.temporal.io/develop/python/schedules
- Anclas internas: `src/platform/observability/otel.py`, `src/plugins/orders/workers/reconcile.py`,
  `.hubara/specs/agents/sales-worker/spec.md`, MEMORY (voseo, human-handoff, backend-behavior, context-leak).
