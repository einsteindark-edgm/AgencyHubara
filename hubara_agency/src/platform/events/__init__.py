"""Bus de eventos del dashboard (in-process) — ver `bus.py`."""

from src.platform.events.bus import (
    DashboardEvent,
    DashboardEventBus,
    get_dashboard_event_bus,
)

__all__ = ["DashboardEvent", "DashboardEventBus", "get_dashboard_event_bus"]
