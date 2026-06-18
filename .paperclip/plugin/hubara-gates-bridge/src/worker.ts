import { spawn } from "node:child_process";
import {
  definePlugin,
  runWorker,
  type PluginContext,
  type PluginEvent,
} from "@paperclipai/plugin-sdk";

// run-gates.sh lives in the AgencyHubara repo; the workspace cwd IS that checkout.
const GATES_CMD = "hubara-dev/hooks/scripts/run-gates.sh";

/**
 * Resolve the on-disk path where the gates should run. Mirrors the adapter's
 * precedence: an issue's execution workspace, else the project's primary
 * workspace. (Issues bound only via the agent's adapterConfig.cwd are not
 * resolvable from plugin context — see README "Requirements".)
 */
async function resolveWorkspacePath(
  ctx: PluginContext,
  issue: { executionWorkspaceId?: string | null; projectId?: string | null },
  companyId: string,
): Promise<string | null> {
  if (issue.executionWorkspaceId) {
    const ws = await ctx.executionWorkspaces.get(issue.executionWorkspaceId, companyId);
    if (ws?.path) return ws.path;
  }
  if (issue.projectId) {
    const all = await ctx.projects.listWorkspaces(issue.projectId, companyId);
    const primary = all.find((w) => w.isPrimary) ?? all[0];
    if (primary?.path) return primary.path;
  }
  return null;
}

function runGates(cwd: string): Promise<{ ok: boolean; code: number; tail: string }> {
  return new Promise((resolve) => {
    const child = spawn("bash", [GATES_CMD, "all"], { cwd });
    let out = "";
    child.stdout.on("data", (d) => (out += d.toString()));
    child.stderr.on("data", (d) => (out += d.toString()));
    child.on("close", (code) => {
      const tail = out.trim().split("\n").slice(-25).join("\n");
      resolve({ ok: code === 0, code: code ?? -1, tail });
    });
    child.on("error", (err) => resolve({ ok: false, code: -1, tail: String(err) }));
  });
}

const plugin = definePlugin({
  async setup(ctx) {
    ctx.logger.info("hubara-gates-bridge: subscribing to issue.updated");

    ctx.events.on("issue.updated", async (event: PluginEvent) => {
      const issueId = event.entityId;
      if (!issueId) return;

      const issue = await ctx.issues.get(issueId, event.companyId);
      if (!issue || issue.status !== "in_review") return;

      // Idempotency: run the panel once per in_review entry, not on every update.
      const stateKey = `gates-run:${issueId}`;
      const seen = await ctx.state.get(stateKey).catch(() => null);
      if (seen === "in_review") return;
      await ctx.state.set(stateKey, "in_review").catch(() => {});

      const cwd = await resolveWorkspacePath(ctx, issue, event.companyId);
      if (!cwd) {
        await ctx.issues.createComment(
          issueId,
          "🟡 **hubara-gates bridge** — could not resolve a workspace path " +
            "(no execution/project workspace; agent used adapterConfig.cwd). " +
            "Reviewer: run `/hubara-gates` manually.",
          event.companyId,
        );
        return;
      }

      ctx.logger.info(`hubara-gates-bridge: running panel in ${cwd} for ${issueId}`);
      const { ok, code, tail } = await runGates(cwd);

      const body = ok
        ? `🟢 **hubara-gates: PASS** (exit 0) — deterministic panel green in \`${cwd}\`.\n\n` +
          "Reviewer: this is the deterministic evidence; apply judgment and post the decision. " +
          "The bridge does not decide (PLUGIN_SPEC §15.2)."
        : `🔴 **hubara-gates: FAIL** (exit ${code}) — deterministic panel red in \`${cwd}\`.\n\n` +
          "```\n" + tail + "\n```\n\n" +
          "Reviewer: changes_requested — unless this is a known pre-existing fail or " +
          "allowlist/ratchet staleness (see harness L-15).";
      await ctx.issues.createComment(issueId, body, event.companyId);
    });
  },

  async onHealth() {
    return { status: "ok", message: "hubara-gates-bridge ready" };
  },
});

export default plugin;
runWorker(plugin, import.meta.url);
