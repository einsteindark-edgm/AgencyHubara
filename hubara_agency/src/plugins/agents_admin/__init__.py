"""Plugin `agents_admin` — UI de gestión de agentes.

Plugin frontend-only por ahora. NO aporta routers FastAPI ni workers Temporal.
Los datos de `entities/agent` (frontend) provienen de `/api/dashboard/agents`,
endpoint que sigue registrado por el plugin `chats` (vía `legacy_routers` →
`src.plugins.chats.api.dashboard`).

Si en el futuro se necesitan endpoints CRUD propios para agent metadata
(prompts, schedules, etc.), se crea `api/__init__.py` con un `router` y se
declara `api.python_module: src.plugins.agents_admin.api` en el manifest.

Manifest del plugin: `frontend_dashboard/src/plugins/agents_admin/plugin.yaml`.
"""
