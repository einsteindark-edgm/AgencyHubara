# Agent coordination — agentic OS shared awareness layer

This directory is the **shared substrate** for multiple Claude agents working on the same project. It lets every agent know what other agents are doing, recently did, or decided — without a daemon, a database, or any framework.

The convention was introduced by the `exoclaw-temporal-expert` plugin but is intentionally generic: any agent (from this plugin or elsewhere) that follows the protocol below participates in the agentic OS.

## Files

- **`active_work.md`** — table of work currently `in_progress`. Mutable. Agents update their own row.
- **`activity_log.md`** — append-only history. Agents prepend new entries on completion or handoff.
- **`decisions.md`** — lightweight ADRs (Architectural Decision Records). Agents add an entry per architectural decision.

## Protocol (every participating agent runs this)

1. **On start**:
   - Read `active_work.md` to see what other agents are doing.
   - Read the last ~10 entries of `activity_log.md` for recent context.

2. **Register**:
   - Append a row to `active_work.md` with: agent name, UTC timestamp, task summary, status `in_progress`, files/branch.

3. **On meaningful architectural decision**:
   - Add a 5-10 line entry to `decisions.md` (template inside that file).

4. **On completion**:
   - Update your row in `active_work.md` to `done @ HH:MM`.
   - Prepend an entry to `activity_log.md` describing outcome and any handoff.

5. **Coordination**:
   - Before duplicating work, check `active_work.md`. If another agent is on the same files/feature, hand off via an entry in `activity_log.md` instead of acting in parallel.

## Discoverability

The project's auto-memory `MEMORY.md` should contain a single line pointing here. The `exoclaw-temporal-expert` plugin auto-creates this directory and adds the index line when an agent starts and the directory does not yet exist.

## Why this works

- **No daemon, no infra**: just markdown files in the auto-memory directory.
- **Append-only history + mutable status**: history is robust (git-trackable if committed), status is fast to update.
- **Plugin-agnostic**: any agent that respects the protocol participates. No coupling to one plugin.
- **Self-healing**: an agent that crashes mid-task leaves a stale `in_progress` row; the next agent sees it, decides to clean up or continue.
- **Composable with git**: commit this directory if you want a permanent audit trail; gitignore it for ephemeral coordination.
