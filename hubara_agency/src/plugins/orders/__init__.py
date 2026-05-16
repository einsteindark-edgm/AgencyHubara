"""Plugin `orders` — tablero kanban de órdenes.

Plugin frontend-only por ahora. NO aporta routers FastAPI ni workers Temporal.
Los datos de `entities/order` (frontend) son mocks; cuando se necesite CRUD
real se agrega `api/__init__.py` con un router.

Manifest: `frontend_dashboard/src/plugins/orders/plugin.yaml`.
"""
