# Harness de evaluación de calidad del Asesor de Ventas

Tres superficies del eval loop de LLMOps (ver `LLM_EVAL_HARNESS_PLAN.md` en la raíz).

## 1. Unit eval (esta carpeta) — regresión con referencia

```bash
# Unidades puras + métricas deterministas (sin juez, corren en la suite default):
cd hubara_agency && uv run pytest tests/evals/test_eval_harness_units.py -q

# Regresión completa con el LLM-juez (necesita proxy litellm + EVAL_JUDGE_MODEL):
cd hubara_agency && RUN_EVAL_TESTS=1 uv run --extra evals pytest -m eval -q
```

- `goldens/sales/curated.json` — golden dataset curado (`ConversationalGolden`s).
  La **fuente de verdad** de qué conversación es "ejemplar". Versionado en git.
- `goldens/sales/_candidates/` — buffer de candidatos auto-curados (ver §3). `.gitkeep`.

## 2. Online eval (Temporal Schedule) — sin referencia, tráfico real

El worker `chats/sales-eval` (`src/plugins/chats/workers/sales_eval.py`) asegura un
Temporal Schedule (08:00/14:00/20:00 hora Bogotá) que dispara `SalesEvalWorkflow`:
muestrea conversaciones reales del vault, las puntúa con DeepEval (juez), y emite
los scores a SigNoz (`platform/observability/eval_metrics.py`). Trigger manual:

```bash
# desde la Temporal UI/CLI, o re-arrancando el worker (crea el schedule):
cd hubara_agency && uv run --extra evals python -m src.plugins.chats.workers.sales_eval
```

Métricas emitidas a SigNoz: `gen_ai.eval.score` (por métrica) y
`gen_ai.eval.conversation` (promedio por conversación). Tablero:
`deploy/signoz/dashboards/05-calidad-llm.json`.

## 3. Curación de goldens (cierra 2 → 1) — auto-curación + revisión humana

Cuando una conversación puntúa por debajo de `candidate_threshold`, el harness:
1. el **juez redacta** el `expected_outcome` ideal (qué debió hacer el asesor),
2. escribe el candidato a `_candidates/<session>.json` (PII ya redactada).

El curador humano:
1. revisa `_candidates/<session>.json`, corrige el `expected_outcome` y **redacta**
   cualquier PII residual (nombres),
2. mueve/mergea la entry a `curated.json` (cambia `status` a `human_reviewed`),
3. borra el archivo de `_candidates/`.

Ese golden entra a la Superficie 1 → cada deploy futuro se testea contra ese caso
real. **El fallo de prod se convirtió en test de regresión.**

## Configuración

| Env | Default | Qué |
|---|---|---|
| `EVAL_JUDGE_MODEL` | `litellm_proxy/gemini-backup` | alias del proxy litellm para el juez (≠ modelo del agente → evita self-preference) |
| `EVAL_CANDIDATES_DIR` | `tests/evals/goldens/sales/_candidates` | dónde se escriben candidatos (en prod: path montado durable) |
| `SALES_EVAL_SCHEDULE_ENABLED` | `true` | off-switch del cron |
| `SALES_EVAL_SCHEDULE_CRON` | `0 8,14,20 * * *` | cron (tz America/Bogota) |
| `SALES_EVAL_LOOKBACK_HOURS` | `8` | ventana de muestreo |
| `SALES_EVAL_MAX_CONVERSATIONS` | `50` | tope por corrida (presupuesto de juez) |
| `RUN_EVAL_TESTS` | (unset) | habilita los tests `-m eval` (necesitan juez) |
