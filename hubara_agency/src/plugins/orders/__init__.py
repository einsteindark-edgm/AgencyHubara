"""Plugin `orders` — tablero kanban de órdenes.

Backend: expone `api/__init__.py` con un router FastAPI que sirve los
endpoints `/api/orders/orders` (list + detail) consumiendo Medusa v2 via
`platform/orders/medusa_order_query.py`.

Frontend: `frontend_dashboard/src/plugins/orders/frontend/` (kanban +
inspector + filtros).

Manifest: `frontend_dashboard/src/plugins/orders/plugin.yaml`.
"""
