"""API del plugin agents_admin. ``src/main.py`` importa ``router`` desde aquí."""
from src.plugins.agents_admin.api.evals import router as _evals_router
from src.plugins.agents_admin.api.routes import router

# Cast evals (F5): el plano de gestión sirve /api/agents/evals/* — ver
# src/plugins/agents_admin/api/evals.py + el bloque `consumes:` del manifest.
router.include_router(_evals_router)

__all__ = ["router"]
