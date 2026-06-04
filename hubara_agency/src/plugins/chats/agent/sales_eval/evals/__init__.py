"""Harness de evaluación de calidad del Asesor de Ventas (LLMOps eval loop).

Tres superficies que se retroalimentan (ver `LLM_EVAL_HARNESS_PLAN.md`):

  1. **Unit eval (pre-prod, con referencia)** — `tests/evals/` corre `assert_test`
     contra el golden dataset curado. Bloquea regresiones antes del deploy.
  2. **Online eval (prod, sin referencia)** — un Temporal Schedule muestrea
     conversaciones reales desde SigNoz, las puntúa con un LLM-juez (DeepEval) y
     emite los scores de vuelta a SigNoz (`platform/observability/eval_metrics`).
  3. **Curación de goldens (cierra 2→1)** — las conversaciones de bajo score se
     vuelven candidatos a golden; el juez **redacta** la respuesta corregida
     (auto-curación) y el humano aprueba.

REGLA CRÍTICA DEHA (R-DET): DeepEval = LLM-as-judge = I/O + no-determinismo →
vive SOLO en activities (`activities/eval_activities.py`), JAMÁS en el workflow.
Por eso `deepeval` se importa **lazy** (dentro de funciones) en todo este paquete:
los módulos deben ser importables sin el extra `evals` instalado (el gate
`pytest -m architecture` corre sin `--extra evals` y pytest-archon importa todo
`src/`).
"""
