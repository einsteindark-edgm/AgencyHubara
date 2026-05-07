"""Temporal activities del dominio remarketing_whatsapp.

PR-E (ADR-2026-05-06-11): ``activities.py`` (file) se convirtio en
``activities/`` (folder) para alinear con el layout de sales_whatsapp y
permitir que cada activity viva en su propio modulo cuando crezcan. Hoy hay
solo dos (``bootstrap_remarketing_session_activity`` y
``build_remarketing_trigger_activity``); ambas viven en ``bootstrap_session.py``.
Los re-exports preservan el import path publico
``from src.remarketing_whatsapp.activities import ...``.
"""
from __future__ import annotations

from src.remarketing_whatsapp.activities.bootstrap_session import (
    bootstrap_remarketing_session_activity,
    build_remarketing_trigger_activity,
)

__all__ = [
    "bootstrap_remarketing_session_activity",
    "build_remarketing_trigger_activity",
]
