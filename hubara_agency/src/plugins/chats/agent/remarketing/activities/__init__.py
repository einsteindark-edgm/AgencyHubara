"""Temporal activities del dominio remarketing_whatsapp.

PR-E (ADR-2026-05-06-11): ``activities.py`` (file) se convirtio en
``activities/`` (folder) para alinear con el layout de sales_whatsapp y
permitir que cada activity viva en su propio modulo cuando crezcan.
Modulos actuales:
  * ``bootstrap_session.py``     — bootstrap + trigger prompt builder.
  * ``watchdog_activities.py``   — HU-WA24H-001 Sprint 2: eligibility,
    MOCK send, outcome persistence.

Los re-exports preservan el import path publico
``from src.plugins.chats.agent.remarketing.activities import ...``.
"""
from __future__ import annotations

from src.plugins.chats.agent.remarketing.activities.bootstrap_session import (
    bootstrap_remarketing_session_activity,
    build_remarketing_trigger_activity,
)
from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
    check_watchdog_eligibility_activity,
    persist_watchdog_outcome_activity,
    send_watchdog_template_activity,
)

__all__ = [
    "bootstrap_remarketing_session_activity",
    "build_remarketing_trigger_activity",
    "check_watchdog_eligibility_activity",
    "persist_watchdog_outcome_activity",
    "send_watchdog_template_activity",
]
