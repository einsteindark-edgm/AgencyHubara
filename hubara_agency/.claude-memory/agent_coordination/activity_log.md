# Activity log

Append-only chronological history of agent work. **Newest entries at the top.**

Each entry follows the template below.

```
## YYYY-MM-DDTHH:MM · <agent-name>
- **Outcome**: <one-line summary of what was achieved>
- **Findings**: <optional, if relevant>
- **Handoff**: <→ next agent, or "none">
- **Files touched**: <comma-separated paths or "none (read-only)">
```

---

## 2026-04-28 · exoclaw-temporal-expert (workflow-architect)
- **Outcome**: F7 done — Sales workflow now mirrors Remarketing's bootstrap-activity pattern. `HubaraSalesSessionWorkflow.run` accepts `SalesSessionInput(session_id, turn_count=0)` and the first activity is `bootstrap_sales_session_activity`, which builds the full `SessionInput` (LLMConfig + WorkspaceConfig + ToolRegistry + tool_definitions_json). Callers (`service.py`, `dispatcher_activities.py`) no longer construct `SessionInput`. Tests: 44/44 green (41 baseline + 3 new bootstrap-sales).
- **Findings**: `SalesSessionInput` only needs `session_id` + `turn_count`. The seed message is delivered via `signal(send_message, ...)` after `start_workflow`, so it does not need to ride on the input DTO. `continue_as_new` now carries `SalesSessionInput(session_id=session.session_id, turn_count=turn_count)` (smaller payload than the previous `SessionInput`). Sales fixture regenerated (v1, pre-cloud — no historical history to preserve); first activity event is now `bootstrap_sales_session_activity`. The pre-existing F401 on `parse_whatsapp_inbound` in `service.py` is unrelated to F7 and left untouched.
- **Handoff**: none. Future PRs F8 (ports) and F9 (use_cases) build on this as planned.
- **Files touched**: src/domains/sales_whatsapp/contracts.py (new), src/domains/sales_whatsapp/activities.py, src/domains/sales_whatsapp/workflows/sales_session.py, src/domains/sales_whatsapp/service.py, src/domains/sales_whatsapp/worker.py, src/core/infrastructure/temporal/dispatcher_activities.py, tests/test_bootstrap_sales_activity.py (new), tests/fixtures/generate_fixtures.py, tests/fixtures/history_sales_session_v1.json (regenerated), tests/fixtures/history_remarketing_session_v1.json (regenerated, no shape change), tests/fixtures/README.md.

_(Log starts here. Add new entries above this line.)_
