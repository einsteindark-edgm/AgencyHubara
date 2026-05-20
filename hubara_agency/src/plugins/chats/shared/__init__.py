"""Cross-sub-agent shared types for the ``chats`` plugin.

Anything importable from ``src.plugins.chats.shared.*`` is fair game for
**any** sub-agent under ``chats/agent/``. This is where DTOs and completion
events live when they need to be referenced by more than one sibling agent
(e.g. ``sales`` emits an event consumed by the orchestration dispatcher which
routes to ``remarketing`` — both sides reference the same event class without
importing each other).

R-DIP #10 — siblings stay isolated:
    - sales/ NEVER imports remarketing/
    - remarketing/ NEVER imports sales/
    - both/ import from shared/ — OK (shared/ has no sibling-specific deps)

Subdirectories:
    contracts/  — frozen @dataclass DTOs (events, inputs, decisions)
"""
