"""API HTTP del plugin system_map.

Re-exporta el `router` para que `main.py` lo monte via `python_module`.
"""

from src.plugins.system_map.api.routes import router

__all__ = ["router"]
