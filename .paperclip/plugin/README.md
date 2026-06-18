# hubara-gates-bridge — the deterministic gate bridge (Paperclip plugin)

> **Status: source, ready to build & install — NOT yet built/installed/live.** The
> code is written against the real `@paperclipai/plugin-sdk` API (verified against
> `packages/plugins/plugin-workspace-diff` + `sdk/src/types.ts`), but it has not been
> compiled or installed into a running Paperclip. Build + install steps below.

## What it does (and the hard constraint that shaped it)

When an issue enters **`in_review`**, this plugin:
1. resolves the issue's workspace path (`ctx.executionWorkspaces.get` → `.path`, else
   the project's primary workspace),
2. runs the deterministic panel there — `bash hubara-dev/hooks/scripts/run-gates.sh all`
   (a real child process; PLUGIN_SPEC §13/§15 explicitly allow plugins to resolve
   workspace paths and spawn processes/git),
3. posts the **verdict as an issue comment** (🟢 PASS / 🔴 FAIL + the failing gates).

**It does NOT post the approval/review decision.** `PLUGIN_SPEC §15.2` forbids plugins
from making *approval decisions* (also: budget override, auth bypass, checkout-lock
override, direct DB). So the bridge produces the **deterministic evidence**; the
**reviewer agent (or a human) posts the actual decision** from it. This is the right
split: the gate run becomes guaranteed + uniform (not dependent on the reviewer agent
remembering to run it), while the decision stays inside governance.

```
issue → in_review ──(event)──▶ hubara-gates-bridge
                                 ├─ resolve workspace .path
                                 ├─ run /hubara-gates (real panel)
                                 └─ createComment(🟢/🔴 verdict)   ← evidence, not a decision
                               reviewer agent reads the comment ──▶ approved / changes_requested
```

## Files

- `src/manifest.ts` — `PaperclipPluginManifestV1` (id `agencyhubara.hubara-gates-bridge`, capabilities verified vs §15.1).
- `src/worker.ts` — `definePlugin({setup})` + `ctx.events.on("issue.updated", …)` + the gate run + `ctx.issues.createComment`.

## Requirements / caveats

- **Needs a project-bound workspace.** The bridge resolves the cwd from the issue's
  execution/project workspace. If an agent is bound only via `adapterConfig.cwd` (like
  the throwaway verification run), the plugin can't see that path and posts a "run it
  manually" note instead. So pair this with the **`project-workspace` binding**
  (`sourceType: local_path` / `git_repo`), not a per-agent `adapterConfig.cwd`.
- The gates run in a real checkout, so that checkout must have its deps installed
  (`uv sync` / `npm ci`) or the gates will report environment failures (correctly red).

## Build

The canonical build tooling comes from the official scaffolder (it pins the SDK + the
esbuild/rollup presets). Easiest path:

```sh
# in the Paperclip clone (so @paperclipai/plugin-sdk resolves):
cd /Users/edgm/Documents/Projects/paperclip
pnpm dlx @paperclipai/create-paperclip-plugin hubara-gates-bridge --template default
# then copy this dir's src/manifest.ts + src/worker.ts over the scaffold's, and:
cd hubara-gates-bridge && pnpm install && pnpm typecheck && pnpm build   # → dist/worker.js
```

(Or add `@paperclipai/plugin-sdk` + `esbuild`/`typescript` as deps here and
`esbuild src/worker.ts --bundle --platform=node --format=esm --packages=external
--outfile=dist/worker.js`.)

## Install into the running Paperclip

```sh
cd /Users/edgm/Documents/Projects/paperclip
pnpm paperclipai plugin --help                 # confirm the exact verb/flags on your version
pnpm paperclipai plugin install <path-to-built-plugin> -C <companyId>
```

The host validates the manifest + capabilities at install; if a capability name is
rejected, reconcile it against `doc/plugins/PLUGIN_SPEC.md §15.1`. After install the
worker starts and subscribes to `issue.updated`; move an issue to `in_review` to see the
verdict comment.

## Why this is optional

The trust loop already works **without** this plugin: the **reviewer agent** runs
`/hubara-gates` + `hubara-gate-reviewer` and posts the decision (verified end-to-end).
This bridge is an *upgrade*: it makes the deterministic gate run **guaranteed and
uniform** on every `in_review`, independent of the reviewer agent's behavior.
