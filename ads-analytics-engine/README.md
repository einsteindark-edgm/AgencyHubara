# Hubara Ads Analytics Engine

Blended unit-economics for **Click-to-WhatsApp (CTWA)** campaigns, where the click
(Meta) and the sale (manual WhatsApp) can't be tied together deterministically.
It joins daily ad spend with daily manual sales and computes the numbers Hubara
needs to scale and to back funding applications (e.g. SENA Fondo Emprender).

## The one idea that makes it trustworthy

> **The LLM never computes a metric.** Every number comes from a deterministic,
> stdlib-only, **golden-tested** engine. The agent orchestrates the fetch and
> writes the narrative; the arithmetic is code.

The original spec's Definition of Done was *"performs the math without
hallucinating numbers."* An LLM doing arithmetic in prose can't guarantee that.
Splitting computation (code) from interpretation (LLM) turns the hope into a
CI-enforced guarantee — `tests/test_metrics.py` pins every metric to a
hand-computed value.

## Three layers

| Layer | What | Where |
|---|---|---|
| **Deterministic core** | parse → merge → metrics → diagnosis → report | `src/ads_engine/` (pure, no deps) |
| **Acquisition** | pull Meta insights — **official Meta Ads MCP only** (no direct Graph API) | `mcp/` |
| **Orchestration** | Analyst + Data Engineer + QA pod, daily routine, no-self-review | `paperclip/` |

## Metrics (COP-only MVP)

| Metric | Formula | Undefined when |
|---|---|---|
| WhatsApp Drop-off | `1 − conversations / clicks` | clicks = 0 |
| Cost per Conversation | `spend / conversations` | conversations = 0 |
| MER | `revenue / spend` | spend = 0 |
| Global CPA | `spend / orders` | orders = 0 |
| Global Win Rate | `orders / conversations` | conversations = 0 |

Undefined → reported as `—`, **never** a guessed number. Diagnosis is a fixed table:
drop-off > 40% = high friction, MER < 2.0 = poor profitability → `scale_budget` /
`rotate_creative` / `review_targeting_or_pricing` / `insufficient_data`.

## Quickstart

```bash
cd ads-analytics-engine
./scripts/setup.sh                 # venv + dev tools (+ the `ads-engine` CLI)
./scripts/verify.sh                # ruff + golden tests — must be green

# dry end-to-end on the bundled fixtures (no credentials needed):
ads-engine ingest-sales tests/fixtures/manual_sales.json
ads-engine compute --from-file tests/fixtures/meta_insights.json
ads-engine report
```

Going live needs real Meta data via the **official Meta Ads MCP only** (no raw
Graph API token — that risks an account ban): see [`mcp/README.md`](mcp/README.md)
and the operator guide
[`paperclip/RUNBOOK.md`](paperclip/RUNBOOK.md).

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the 3 layers + why deterministic math.
- [`docs/PHASE-2-BRIDGE.md`](docs/PHASE-2-BRIDGE.md) — how to plug into the live
  AgencyHubara system (real Medusa sales + Meta CAPI feedback loop) later.

## Status

Standalone MVP. Core engine: **built + verified** (31 tests green, ruff clean).
Meta MCP wiring + Paperclip pod: **delivered**. Live data needs your Meta ad
account + official MCP access — everything else runs on fixtures today.
