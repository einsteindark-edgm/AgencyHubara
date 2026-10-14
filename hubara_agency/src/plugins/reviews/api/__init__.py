"""API del plugin reviews — routers DELGADOS (el perfil lo audita).

La lógica vive en ``domain/`` (pura, sin I/O); este módulo solo traduce
HTTP ↔ dominio. Los datos de OTROS plugins entran por cast declarado
(``consumes:`` del manifest), jamás importando a un sibling (P-3).
"""
from fastapi import APIRouter

from src.plugins.reviews.domain.logic import health_payload

router = APIRouter()


@router.get("/health")
def health() -> dict:
    """Smoke del plugin — reemplazar por endpoints reales."""
    return health_payload()
