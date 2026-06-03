"""Agente Temporal del plugin orders.

Contiene el `OrderReconciliationWorkflow` (disparado por un Temporal Schedule
periódico) y la activity que envuelve el barrido idempotente de pedidos
pendientes. La LÓGICA vive en `platform/orders/reconciliation.py` +
`plugins/orders/reconcile_runner.py`; esta capa solo la hace durable.
"""
