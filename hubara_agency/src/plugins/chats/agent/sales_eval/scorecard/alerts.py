"""Alertas del scorecard (HU-SC-6)."""
from __future__ import annotations

from typing import Any


async def notify_failure(record: dict[str, Any]) -> bool:
    return False
