"""API del plugin agents_admin. ``src/main.py`` importa ``router`` desde aquí."""
from src.plugins.agents_admin.api.routes import router

__all__ = ["router"]
