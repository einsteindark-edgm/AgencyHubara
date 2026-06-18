# AgencyHubara Engineering — team package

A Paperclip team (`schema: agentcompanies/v1`) that deploys a 3-agent pod —
**architect → implementer → reviewer** — to develop AgencyHubara under the `hubara-dev`
harness with a no-self-review approval stage.

This directory is the importable team package. Drop it under
`packages/teams-catalog/catalog/optional/software-development/agencyhubara-engineering/`
in a Paperclip checkout to ship it in the catalog, or import it into a running company
with the CLI (see `../README.md` for the operator runbook).

```
team/
├── TEAM.md                     # manifest: roster, manager, requiredSkills
├── .paperclip.yaml             # per-agent permission overlay (paperclip/v1)
├── agents/
│   ├── architect/AGENTS.md     # manager + refiner/planner + merge sign-off
│   ├── implementer/AGENTS.md   # test-first coder under hubara-dev
│   └── reviewer/AGENTS.md      # independent gate panel + live-stack verification
└── projects/agencyhubara/
    ├── PROJECT.md              # the HU backlog
    └── tasks/harness-pulse/TASK.md   # recurring architect check-in (routine)
```

The architecture knowledge the agents rely on (DEHA, FSD, plugin contract, SDK
certification, the deterministic gate panel, the L-0..L-15 lessons) is **not**
duplicated here — it ships in the `hubara-dev` Claude Code plugin that is active in
each agent's workspace. This package only wires the org chart, the roles, and the trust
loop on top of it.
