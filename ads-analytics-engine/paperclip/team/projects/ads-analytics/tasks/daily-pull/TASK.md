---
name: Daily Pull
slug: daily-pull
assignee: data-engineer
project: ads-analytics
recurring: true
---

# Daily Pull

Recurring routine — keep the blended unit-economics current.

Each run:
1. **Data Engineer** pulls yesterday's Meta insights via the official Meta Ads MCP
   (`get_insights`, `time_increment=1`), saves the raw JSON, and ingests any
   new manual sales the operator dropped in.
2. `ads-engine compute` then `ads-engine report` — the engine does the math and
   updates the SQLite store (idempotent: re-running a date overwrites, so the
   history accumulates cleanly for trend/backfill — useful for the SENA Fondo
   Emprender funding story).
3. **Analyst** writes the short read-out for the new day(s); flags from the
   deterministic diagnosis (scale / rotate / review).
4. **QA Reviewer** reconciles and approves (no self-review).

If Meta data for the day isn't available yet, record nothing rather than guessing —
the engine reports "—" for undefined metrics by design.
