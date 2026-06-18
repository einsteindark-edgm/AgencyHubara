---
name: Harness Pulse
slug: harness-pulse
assignee: architect
project: agencyhubara
recurring: true
---

Recurring architect-owned check-in that keeps the trust loop healthy.

- Confirm `main` is green on the deterministic panel (`/hubara-gates all`). A red gate
  on `main` blocks all delivery — open an issue and assign it before anything else.
- Triage the backlog: surface blockers, confirm the next deliverable, and reassign any
  child issue that has stalled in `in_progress` or `in_review`.
- Check the fleet: any agent paused on a budget hard-stop, any checkout lock held by a
  non-terminal run, any `architecture-change` issue waiting on an ADR or approval.
- Post a short status comment with the top three items for the upcoming cycle.
