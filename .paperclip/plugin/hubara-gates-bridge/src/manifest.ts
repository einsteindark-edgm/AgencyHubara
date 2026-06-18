import type { PaperclipPluginManifestV1 } from "@paperclipai/plugin-sdk";

/**
 * Hubara Gates Bridge — runs the deterministic `/hubara-gates` panel in an issue's
 * workspace the moment it enters `in_review`, and posts the verdict as an issue
 * COMMENT (evidence). It deliberately does NOT post the review/approval decision:
 * PLUGIN_SPEC §15.2 forbids plugins from making approval decisions. The reviewer
 * agent (or a human) reads the verdict comment and posts the actual decision.
 *
 * Capability strings verified against PLUGIN_SPEC §15.1 + plugin-workspace-diff.
 */
const manifest: PaperclipPluginManifestV1 = {
  id: "agencyhubara.hubara-gates-bridge",
  apiVersion: 1,
  version: "0.1.0",
  displayName: "Hubara Gates Bridge",
  description:
    "On issue in_review, runs the AgencyHubara /hubara-gates deterministic panel in the issue workspace and posts the pass/fail verdict as a comment. Does not decide (PLUGIN_SPEC §15.2) — the reviewer decides from the verdict.",
  author: "AgencyHubara",
  categories: ["automation"],
  capabilities: [
    "events.subscribe",          // subscribe to issue.updated
    "issues.read",               // ctx.issues.get
    "issue.comments.create",     // ctx.issues.createComment (the verdict)
    "execution.workspaces.read", // ctx.executionWorkspaces.get -> .path
    "project.workspaces.read",   // ctx.projects.listWorkspaces -> primary .path
    "plugin.state.read",         // idempotency marker
    "plugin.state.write",
  ],
  entrypoints: {
    worker: "./dist/worker.js",
  },
};

export default manifest;
