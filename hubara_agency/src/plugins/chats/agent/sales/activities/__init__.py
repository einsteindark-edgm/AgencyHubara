"""Temporal activities del dominio sales_whatsapp.

PR-E: ``activities.py`` (file) se convirtio en ``activities/`` (folder) para
permitir que cada activity viva en su propio modulo cuando crezcan. Los
re-exports preservan el import path publico
``from src.plugins.chats.agent.sales.activities import ...``.

Notese que ``persist_assistant_message_activity`` NO vive aca: por R-DIP #10
(agentes independientes), las activities cross-domain viven en
``src.platform.session_history.activities``.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.activities.bootstrap_session import (
    bootstrap_sales_session_activity,
    decide_ghosting_action,
    read_and_clear_pending_handoff_activity,
    read_idle_timeout_seconds_activity,
)
from src.plugins.chats.agent.sales.activities.flush_ui_intents import (
    flush_pending_ui_intents_activity,
)
from src.plugins.chats.agent.sales.activities.inject_context import (
    compute_bogota_context_activity,
)
from src.plugins.chats.agent.sales.activities.transcribe_audio import (
    transcribe_audio_activity,
)

__all__ = [
    "bootstrap_sales_session_activity",
    "compute_bogota_context_activity",
    "decide_ghosting_action",
    "read_and_clear_pending_handoff_activity",
    "read_idle_timeout_seconds_activity",
    "flush_pending_ui_intents_activity",
    "transcribe_audio_activity",
]
