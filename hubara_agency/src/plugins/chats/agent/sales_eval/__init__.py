"""Agente DEHA `sales_eval` — harness de evaluación de calidad del Asesor de Ventas.

NO es un agente conversacional: es el eval loop (LLMOps) que puntúa las
conversaciones del agente `sales` con DeepEval (LLM-juez) y emite los scores a
SigNoz. Su worker (`src/plugins/chats/workers/sales_eval.py`) corre un Temporal
Schedule (08/14/20 hora Bogotá). Lógica en `evals/`; golden dataset + unit eval
en `tests/evals/`. Es un agente separado (no parte de `agent/sales/`) para que
el worker de ventas en el hot-path NO arrastre `deepeval` ni la cadencia de eval.
"""
