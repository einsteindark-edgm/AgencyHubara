---
name: Hubara Ads Analytics
description: Trusted analytics pod for Hubara Click-to-WhatsApp campaigns. All metrics come from a deterministic golden-tested engine (the LLM never computes a number); a Data Engineer pulls Meta insights via the official Meta Ads MCP, the Analyst interprets, and a Numbers QA reviewer independently reconciles before any read-out is trusted (no self-review).
schema: agentcompanies/v1
slug: hubara-ads-analytics
category: software-development
key: paperclipai/optional/software-development/hubara-ads-analytics
manager: agents/analyst/AGENTS.md
includes:
  - agents/data-engineer/AGENTS.md
  - agents/qa-reviewer/AGENTS.md
  - projects/ads-analytics/PROJECT.md
defaultInstall: false
recommendedForCompanyTypes:
  - marketing
  - software
tags:
  - ads
  - meta
  - ctwa
  - whatsapp
  - unit-economics
  - deterministic
requiredSkills:
  - paperclipai/bundled/quality/qa-acceptance
  - paperclipai/bundled/paperclip-operations/task-planning
  - paperclipai/bundled/docs/doc-maintenance
---

# Hubara Ads Analytics

A three-role pod that turns Meta CTWA spend + manual WhatsApp sales into trustworthy
blended unit-economics (MER, Global CPA, Cost/Conversation, Drop-off, Win Rate).

The governing idea: **the arithmetic is code, not the LLM.** Every number is
produced by `ads_engine` (stdlib-only, golden-tested). The agents orchestrate the
fetch, interpret the result, and gate it — but never do the math in their heads.

- **Analyst** (manager) — Senior Growth Marketing Analyst. Pulls data via the Meta
  Ads MCP, runs the engine, writes the diagnosis. Never computes a metric.
- **Data Engineer** — acquires Meta insights (MCP) + ingests manual sales + runs the
  pipeline. Never edits a number; fixes the source.
- **QA Reviewer** — the no-self-review gate. Re-runs the engine on the same inputs,
  reconciles byte-for-byte, checks the golden tests are green, approves or rejects.
