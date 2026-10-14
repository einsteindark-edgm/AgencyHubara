"""Workflows del agente sales_eval (evaluación de calidad).

Sin re-exports: cada consumidor importa su submódulo. Re-exportar
`SalesEvalWorkflow` acá hacía que tomar un id liviano (`lab_launch`) desde la API
arrastrara las activities del eval y con ellas deepeval (memoria de la caja de
app, 2026-10-14). Guard: tests/plugins/chats/test_api_boot_without_deepeval.py.
"""
