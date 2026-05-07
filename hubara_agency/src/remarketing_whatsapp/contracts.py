"""Boundary DTOs del dominio Remarketing.

Aplicacion de R-JSON: cualquier valor que cruce `workflow.execute_workflow` /
`workflow.run` es un dataclass plano JSON-serializable.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RemarketingSessionInput:
    session_id: str
    motivo: str
