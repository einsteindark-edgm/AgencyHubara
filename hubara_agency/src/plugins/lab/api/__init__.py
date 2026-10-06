"""API del plugin lab — routers DELGADOS (el perfil lo audita).

Todo `/api/lab/*` es el cast `lab.py` al contrato `lab@v1` de chats (bloque
`consumes:` del manifest); la validación de segmentos vive en ``domain/``.
"""
from src.plugins.lab.api.lab import router

__all__ = ["router"]
