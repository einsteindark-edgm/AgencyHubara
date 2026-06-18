# AgencyHubara × Paperclip — operator runbook

How to run **AgencyHubara development through Paperclip** with agents you can trust at
100%. This directory is config-as-code (it mirrors `.archon/` and `.hubara/`): the
[team package](team/) is the org chart + roles + trust loop; this README is the
operator runbook.

> **The trust statement.** No single layer is trusted alone:
> **correct-by-construction (TDD red→green, per-agent `hubara-dev`)
> × verified (the deterministic `/hubara-gates` panel)
> × governed (Paperclip atomic checkout + per-agent budgets + a no-self-review review stage)
> × auditable (execution decisions + cost events + activity log).**

```
 Paperclip (the company)                         hubara-dev (the discipline)
 ─────────────────────────                       ────────────────────────────
 issue checkout (409 = never retry)              TDD law: no prod code w/o a red test
 per-agent budgets (80% soft / 100% stop)        hard rules + golden 3-legs
 executor 'done' → INTERCEPTED → in_review   ⇄   DoD = /hubara-gates VERDE
 reviewer ≠ executor (no self-review)            reviewer subagent + Docker visual check
 config revisions + rollback                     certification C0–C3 (cli certify)
        claude_local adapter runs Claude Code in the AgencyHubara checkout
        └────────────── where hubara-dev (repo-local .claude/) is active ───────────────┘
```

Paths below are absolute under the Paperclip clone at
`/Users/edgm/Documents/Projects/paperclip`. Run-from-source uses the wrapper
`pnpm paperclipai <cmd>` (root `package.json` script). The published binary is
`paperclipai` (`cli/package.json` bin). **Verify any flag with
`pnpm paperclipai <cmd> --help`** — several governance bodies are passed as
`--payload-json '{…}'`.

---

## 0. How hubara-dev actually activates (and the one thing to commit)

The `claude_local` adapter runs the **real `claude` CLI in print mode**, in the
AgencyHubara checkout, with the host environment — verbatim
(`packages/adapters/claude-local/src/server/execute.ts:691`):

```
claude --print - --output-format stream-json --verbose <perms> \
  --append-system-prompt-file <agent persona> --add-dir <paperclip skill bundle> …
# cwd = your AgencyHubara checkout · stdin = the wake prompt
```

So a Paperclip run is **the same as you running `claude --print` by hand in that repo.**
hubara-dev activates through three orthogonal layers:

1. **Persona** → injected via `--append-system-prompt-file` (the agent's AGENT.md). Tells
   the agent to follow the harness; DoD = `/hubara-gates`.
2. **Paperclip-managed skills** (the catalog skills: `github-pr-workflow`, …) → mounted
   into a prompt bundle and passed via `--add-dir` (`execute.ts:437,462,712`).
3. **The hubara-dev plugin itself** (skill auto-trigger + the 3 hooks + the 3 subagents)
   → loaded by Claude Code's **native config discovery** from the cwd + host config,
   exactly as in your own sessions. Hooks fire in `--print` mode; subagents (Task) work.

**Local vs remote — the nuance:** for **local execution** (`sourceType: local_path`, the
recommended posture) the run inherits your full host env and `~/.claude`, so a
user-global hubara-dev install is active and `CLAUDE_CONFIG_DIR` is **not** overridden
(`execute.ts:466-472,544`). Only **remote/sandbox** targets get a managed config that
seeds just 5 files from `~/.claude` (`claude-config.ts:8-14`) — there, a user-global
plugin does **not** travel.

**Therefore, to be robust in both postures: enable hubara-dev at the PROJECT level** —
commit the repo-root marketplace + the enablement/hooks in `AgencyHubara/.claude/`
(committed `settings.json`), so the plugin travels with the repo (it syncs as part of the
workspace in remote mode) instead of depending on a user-global install. Sanity check:
run `claude --print` by hand in that checkout and confirm the SessionStart TDD banner +
`/hubara-gates` resolve — if they do for you, they do for the agent.

---

## 1. Run Paperclip locally

```sh
cd /Users/edgm/Documents/Projects/paperclip
pnpm install           # engines: node>=20, pnpm@9.15.4; auto-provisions embedded Postgres
pnpm build             # preflight workspace links + pnpm -r build
cp .env.example .env   # then edit (see below)
```

Env that matters for **`local_trusted`** (loopback, no login — `doc/DEPLOYMENT-MODES.md` §3):

| Var | Value | Why |
|---|---|---|
| `PORT` | `3100` | API port; default 3100 (`cli/src/commands/onboard.ts:234`) |
| `DATABASE_URL` | **unset** | unset ⇒ embedded Postgres on `54329` (`onboard.ts:139,216`) |
| `PAPERCLIP_AGENT_JWT_SECRET` | any 32+ char secret | signs agent JWTs; `doctor --repair` writes it (`cli/src/checks/agent-jwt-secret-check.ts`) |
| `ANTHROPIC_API_KEY` | optional | if set ⇒ `api` billing; unset ⇒ Claude subscription (`claude-local/.../execute.ts:130`) |

Onboard, self-check, run:

```sh
pnpm paperclipai onboard --yes          # trusted-local, loopback bind
pnpm paperclipai doctor --repair -y     # 9 checks (config/auth/jwt/secrets/storage/db/llm/log/port)
pnpm paperclipai run                     # serves http://localhost:3100
```

---

## 2. Company + board context

```sh
# local_trusted needs no login. (authenticated installs: `pnpm paperclipai auth bootstrap-ceo`)
pnpm paperclipai company create --payload-json '{"name":"AgencyHubara Inc"}'   # company.ts:1179
pnpm paperclipai company list
pnpm paperclipai connect                                         # saves an API profile (doc/CLI.md:78)
pnpm paperclipai context set --api-base http://localhost:3100 --company-id <companyId>
```

Global client flags on every command: `--api-base <url>`, `--api-key <token>`,
`-C, --company-id <id>`, `--json`, `-d, --data-dir <path>` (`cli/src/commands/client/common.ts`).
There is **no `--server` flag** — use `--api-base`.

---

## 3. Install the AgencyHubara Engineering team

Two routes — pick by whether you keep a Paperclip checkout/fork:

### Path A — catalog + `teams install` (validated, server-supported)

The [team/](team/) package is in the shipped-catalog format (`agentcompanies/v1`) and is
**validated green** by Paperclip's own `buildCatalogManifest()` (0 errors). Drop it into the
catalog, rebuild the manifest, install:

```sh
cp -R /Users/edgm/Documents/Projects/AgencyHubara/.claude/worktrees/tender-vaughan-8c43c9/.paperclip/team \
  /Users/edgm/Documents/Projects/paperclip/packages/teams-catalog/catalog/optional/software-development/agencyhubara-engineering
cd /Users/edgm/Documents/Projects/paperclip
pnpm --filter @paperclipai/teams-catalog build:manifest      # regenerate generated/catalog.json
pnpm paperclipai teams install agencyhubara-engineering -C <companyId> \
  --adapter-override architect=claude_local \
  --adapter-override implementer=claude_local \
  --adapter-override reviewer=claude_local
```

`--adapter-override <slug>=<type>` forces the imported agents onto `claude_local`
(`cli/src/commands/client/teams.ts:212`). The server renders the team into a company
package at install time (`server/src/services/teams-catalog.ts:663`).

### Path B — `company import` (portable, no fork)

`teams install` only resolves **app-shipped** catalog refs, so for a fully portable package
(no Paperclip fork) use `company import`. Confirmed required shape
(`server/src/services/company-portability.ts:190,2065`): **`COMPANY.md` (required)** +
`.paperclip.yaml` (`schema: paperclip/v1`) + `agents/<slug>/AGENT.md` (singular). Agent
frontmatter may carry `adapterType: "claude_local"`.

```sh
pnpm paperclipai company import ./agencyhubara-company \
  --target existing -C <companyId> --include company,agents,projects \
  --agents all --collision rename --yes                       # company.ts:1385-1464
# GitHub source also supported: company import <owner/repo> --ref main --target new …
```

> The `team/` here is in the **catalog** format (`AGENTS.md`, `TEAM.md`). The
> `company import` format differs (`AGENT.md`, `COMPANY.md`). If you want Path B, ask me
> to generate the `company import` variant — it needs one validation pass against a
> running Paperclip (its validator pulls server deps, unlike the catalog one I already ran).

---

## 4. Bind the agents to the AgencyHubara repo (the crux)

The `claude_local` cwd resolves, in order: **(1) the project workspace `cwd`/`repoUrl`**,
else **(2) `adapterConfig.cwd`**, else `process.cwd()`
(`packages/adapters/claude-local/src/server/execute.ts:140-169`). For a local single-machine
loop, bind a **project workspace** to your existing checkout:

```sh
pnpm paperclipai project create -C <companyId> --name "AgencyHubara Dev" --lead-agent-id <architectId>

# Claude runs IN your existing clone (recommended for local_trusted):
pnpm paperclipai project-workspace create <projectId> --payload-json '{
  "name":"agencyhubara-main",
  "sourceType":"local_path",
  "cwd":"/abs/path/to/AgencyHubara",
  "isPrimary":true
}'                                                            # workspace.ts:69-104; validators/project.ts:43-89
```

(Or `"sourceType":"git_repo","repoUrl":"https://github.com/you/AgencyHubara","repoRef":"main"`
to clone/worktree instead. Fallback: pin `adapterConfig.cwd` directly on each agent via
`agent update --payload-json`.) Coherence contract: when git is expected the cwd must be a
valid repo root (`doc/execution-semantics.md` §"Adapter-backed workspace coherence").

**Verify the binding** — the heartbeat debug stream prints the adapter invoke (`cwd`,
`command`, args), so you can confirm Claude launches in the AgencyHubara checkout:

```sh
pnpm paperclipai heartbeat run --agent-id <implementerId> --source on_demand --trigger manual --debug
```

---

## 5. Budgets (the cost-governance layer)

```sh
pnpm paperclipai budget company:update -C <companyId> --payload-json '{"budgetMonthlyCents":500000}'   # $5,000/mo
pnpm paperclipai budget agent:update <architectId>   --payload-json '{"budgetMonthlyCents":150000}'
pnpm paperclipai budget agent:update <implementerId> --payload-json '{"budgetMonthlyCents":250000}'
pnpm paperclipai budget agent:update <reviewerId>    --payload-json '{"budgetMonthlyCents":100000}'
pnpm paperclipai budget overview -C <companyId>
pnpm paperclipai cost by-agent -C <companyId>
```

(`cli/src/commands/client/cost.ts:68-101`.) 80% → soft alert, 100% → **hard stop** (agent
auto-paused, no more heartbeats). This is the fleet-level equivalent of your
LiteLLM-key-per-tenant cost tracking — now enforced.

---

## 6. The no-self-review gate (the bridge to `/hubara-gates`)

This is where the deterministic panel becomes **governance**. An issue's
`executionPolicy.stages[]` makes the executor's `done` flip to `in_review` and route to a
**different** agent (the runtime excludes the original executor —
`docs/guides/execution-policy.md:96-103`). The reviewer persona then runs `/hubara-gates`
+ the `hubara-gate-reviewer` subagent + the live-Docker check before posting `approved`.

**No typed CLI flag sets this** (`issue create` has no `--execution-policy-json`) — use the
HTTP API or the board UI. Ready-to-POST body in
[governance/issue-policy.example.json](governance/issue-policy.example.json):

```sh
curl -sS -X POST "http://localhost:3100/api/companies/<companyId>/issues" \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d @/Users/edgm/Documents/Projects/AgencyHubara/.claude/worktrees/tender-vaughan-8c43c9/.paperclip/governance/issue-policy.example.json
```

Stages = `[{review → reviewer agent}, {approval → board user}]`. Because
`reviewerId ≠ implementerId`, self-review is structurally impossible. UI path: New-Issue
dialog → Reviewer/Approver fields (`docs/guides/execution-policy.md:248`).

---

## 7. Approval gate toggle (the principled replacement for the Archon human gate)

Your pipeline's hard-coded human gates become an **auditable, toggleable** board approval
(`cli/src/commands/client/approval.ts:113-196`):

```sh
pnpm paperclipai approval list   -C <companyId> --status pending
pnpm paperclipai approval approve <approvalId> --decision-note "ship"
pnpm paperclipai approval reject  <approvalId> --decision-note "blocked: <reason>"
# Make a forbidden action raise an approval instead of a raw 403:
pnpm paperclipai teams install <ref> -C <companyId> --request-approval-on-forbidden
```

On resolution the woken run sees `PAPERCLIP_APPROVAL_ID` / `PAPERCLIP_APPROVAL_STATUS`
(`execute.ts:225-230`). Toggle: include an `{type:"approval"}` stage (§6) to require a human;
omit it to run gate-only auto-merge once you trust the loop (your `pipeline_auto_chain` goal).

---

## 8. Low-trust preset — two trust postures, pick by threat model

```sh
pnpm paperclipai agent permissions:update <implementerId> --payload-json '{
  "trustPreset":"low_trust_review",
  "authorizationPolicy":{"trustPreset":"low_trust_review",
    "trustBoundary":{"mode":"low_trust_review","companyId":"<companyId>","projectIds":["<projectId>"]}}
}'                                                            # agent.ts:436-446; trust-policy.ts
```

**Tradeoff (decide deliberately).** `low_trust_review` **fails closed unless** the run uses
the **`sandbox` driver + `isolated_workspace`** (`doc/LOW-TRUST-PRESETS.md` §Runtime
Containment) — which is **mutually exclusive** with the §4 host-local `cwd` binding (the
implementer then runs sandboxed, not against your raw host clone).

- **Posture A — host-local + review gate (recommended for your own repo).** Trust comes
  from TDD + `/hubara-gates` + the independent reviewer + budgets + checkout. No sandbox.
  This already gives "100% trust" for *your* code on *your* machine.
- **Posture B — sandboxed low-trust.** Add containment when the agent or its inputs are
  less trusted (untrusted PRs, scaling to many agents). Create a sandbox environment
  (`environment create --payload-json '{"driver":"sandbox",…}'`) and set the project's
  `executionWorkspacePolicy` to `isolated_workspace`.

---

## 9. Run one HU end-to-end + verify the loop

```sh
pnpm paperclipai goal create  -C <companyId> --title "Ship AgencyHubara feature"
# Architect decomposes → child issue for the implementer, in the repo-bound project.
# (Add the review/approval stages from §6 via the API body.)
pnpm paperclipai issue create -C <companyId> --title "Implement HU-X" \
  --assignee-agent-id <implementerId> --project-id <projectId> --status todo

pnpm paperclipai heartbeat run --agent-id <implementerId> --source on_demand --trigger manual --debug
pnpm paperclipai run live -C <companyId>     # watch todo→in_progress→in_review→done
pnpm paperclipai run log <runId>
pnpm paperclipai issue get <issueId>
```

The loop is healthy when: the implementer's run cwd is the AgencyHubara checkout
(`--debug`), it ships with `/hubara-gates` green, the issue lands in `in_review` (not
`done`), the **reviewer** agent picks it up and re-runs the panel + the live-Docker check,
and only then does it advance. The recurring `harness-pulse` routine keeps `main` green and
triages the fleet.

---

## 10. Honest gaps (verify against your Paperclip version)

| Step | Surface | Note |
|---|---|---|
| Install custom team | `company import` (Path B) | `teams install` is catalog-only; Path A needs the team in the cloned catalog. |
| Execution-policy stages (§6) | **API / UI only** | no typed CLI flag on `issue create/update`. |
| Budget 80/100% thresholds | `budget policy:upsert` | numeric thresholds are policy-driven, not direct CLI flags. |
| Low-trust (§8) | CLI + sandbox env | forces sandbox/isolated workspace; not the host-local cwd. |

Everything here is extracted from the Paperclip source at the clone path above and cited
inline. Re-confirm exact flags with `pnpm paperclipai <cmd> --help` on your version.
