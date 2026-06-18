---
name: AgencyHubara Engineering
description: Trusted engineering pod that ships AgencyHubara user stories under the hubara-dev harness (TDD red then green then refactor plus the deterministic architecture gates) governed by Paperclip atomic checkout, per-agent budgets, and a no-self-review approval stage.
schema: agentcompanies/v1
slug: agencyhubara-engineering
category: software-development
key: paperclipai/optional/software-development/agencyhubara-engineering
manager: agents/architect/AGENTS.md
includes:
  - agents/implementer/AGENTS.md
  - agents/reviewer/AGENTS.md
  - projects/agencyhubara/PROJECT.md
defaultInstall: false
recommendedForCompanyTypes:
  - software
  - product
tags:
  - agencyhubara
  - tdd
  - deha
  - feature-sliced
  - architecture-gates
requiredSkills:
  - paperclipai/bundled/software-development/github-pr-workflow
  - paperclipai/bundled/quality/qa-acceptance
  - paperclipai/bundled/paperclip-operations/task-planning
  - paperclipai/bundled/docs/doc-maintenance
---

# AgencyHubara Engineering

A drop-in engineering pod for developing **AgencyHubara** (the Temporal/DEHA Python
backend + Feature-Sliced React/TS dashboard + plugin system + Platform SDK) with
agents you can trust. It marries two layers:

- **Per-agent discipline** comes from the `hubara-dev` Claude Code harness, which is
  active in the workspace these agents run in. It enforces **TDD (red → green →
  refactor)**, the hard architecture rules, the deterministic gate panel
  (`/hubara-gates`), and plugin certification. The agents do not invent this — the
  harness ships it (skill + subagents + hooks).
- **Fleet governance** comes from Paperclip: **atomic task checkout** (one HU = one
  issue, `409` = never retry), **per-agent budgets**, **config revisioning +
  rollback**, and a **no-self-review approval stage** — the executor's `done` is
  intercepted into `in_review`, and a *different* agent signs off.

> The trust statement: **correct-by-construction (TDD) × verified (gate panel) ×
> governed (Paperclip checkout + budget + approval) × auditable (execution
> decisions + cost events).** No single layer is trusted alone.

## Roles

- **architect** — team root + engineering manager. Refines incoming HUs against the
  capability specs, breaks them into well-scoped child issues, and holds the final
  merge sign-off. Maps to the pipeline's tech-refiner + plugin-planner.
- **implementer** — primary coder. Picks up one child issue, implements it strictly
  **test-first** under `hubara-dev`, and ships when `/hubara-gates` is green. Never
  marks its own work approved.
- **reviewer** — independent verification. On `in_review`, re-runs the deterministic
  panel, audits the diff against the hard rules + lessons, and **verifies observable
  behavior against the live Docker stack** (tests green ≠ feature alive). Posts
  `approved` or `changes_requested` with evidence.

## Workspace contract

Each agent runs the `claude_local` adapter against an **AgencyHubara checkout** that
has the `hubara-dev` plugin installed and the repo hooks active. The harness'
`SessionStart` hook injects the TDD law every wake; `/hubara-gates` is the shared
definition-of-done. See `.paperclip/README.md` for the operator runbook (install,
budgets, execution policy, low-trust preset).

## Skill rationale

- `github-pr-workflow` — logical commits, branch hygiene, merge discipline (composes
  with Paperclip's checkout lock and the repo's `architecture-change` label flow).
- `task-planning` — lets the architect turn an HU into bounded child issues.
- `qa-acceptance` — gives the reviewer a structured pass/fail format the implementer
  can act on.
- `doc-maintenance` — keeps capability specs and `.hubara/` conventions aligned with
  shipped behavior.

The AgencyHubara-specific architecture knowledge (DEHA R-rules, FSD anti-patterns,
plugin contract, SDK certification) is **not** duplicated here — it lives in the
`hubara-dev` harness and `ARCHITECTURE_FINAL_fable.md`, which the workspace exposes.
