"""Temporal activities del dominio sales_whatsapp.

PR-E: ``activities.py`` (file) se convirtio en ``activities/`` (folder) para
permitir que cada activity viva en su propio modulo cuando crezcan. Hoy hay
solo una (``bootstrap_session``); los re-exports preservan el import path
publico ``from src.sales_whatsapp.activities import ...``.
"""
from __future__ import annotations

from src.sales_whatsapp.activities.bootstrap_session import (
    bootstrap_sales_session_activity,
    decide_ghosting_action,
)

__all__ = [
    "bootstrap_sales_session_activity",
    "decide_ghosting_action",
]
