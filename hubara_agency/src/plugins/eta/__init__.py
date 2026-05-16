"""Plugin `eta` — visualización del estado de envíos.

Plugin frontend-only por ahora. NO aporta routers FastAPI ni workers Temporal.
Los datos de `entities/tracked-order` (frontend) provienen de endpoints
existentes (mocks por ahora).

Si en el futuro se necesitan endpoints propios (ej. CRUD de tracked orders,
webhook de transportistas), se crea `api/__init__.py` con un `router` y se
declara en el manifest.

Manifest: `frontend_dashboard/src/plugins/eta/plugin.yaml`.
"""
