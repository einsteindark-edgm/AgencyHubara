# Architecture

## Why split the math from the LLM

LLMs hallucinate arithmetic. A "Senior Analyst" agent that fetches data **and**
computes MER/CPA in prose can silently emit a wrong number, and you'd never know.
So the system has a hard boundary:

```
        ┌─────────────────────────── Orchestration (Paperclip) ───────────────────────────┐
        │  Analyst (interpret) · Data Engineer (acquire) · QA (reconcile, no self-review)   │
        └───────────────┬───────────────────────────────────────────────┬──────────────────┘
                        │ calls MCP / CLI                                 │ reads report
                        ▼                                                 ▼
   ┌──── Acquisition ────┐        ┌──────────────── Deterministic core (no LLM) ────────────┐
   │ Official Meta Ads   │  JSON  │ meta_insights.parse → merge → metrics → diagnosis →      │
   │ MCP (get_insights)  ├───────▶│ report   +   store (SQLite, idempotent by date)          │
   │ OAuth · sanctioned  │  raw   │ pure functions · Decimal exact · golden-tested           │
   └─────────────────────┘        └──────────────────────────────────────────────────────────┘
```

The agents move data and write narrative. **Numbers cross the boundary in only one
direction: out of the core.** Nothing an agent types becomes a metric.

## No direct Graph API — by design

Data comes **only** from the official Meta Ads MCP (`mcp.facebook.com/ads`, OAuth).
There is no direct Graph API client and no token field anywhere in this project:
hitting the Graph API with a raw token — or routing it through a third-party broker
— outside Meta's sanctioned path can get the **ad account banned**. If the official
MCP isn't available for the account, the engine simply can't fetch (and that's the
correct, safe behavior). See [`../mcp/README.md`](../mcp/README.md).

## Data flow

1. **Acquire** — the agent calls the official Meta Ads MCP `get_insights`; the raw
   `{"data":[...]}` response is saved to JSON. `meta_insights.parse_meta_insights`
   turns rows into `MetaDailyInsight` (the one place untrusted external shape meets
   typed data — fixture-tested).
2. **Ingest** — `ingest.load_manual_sales` parses the operator's JSON/CSV into
   `ManualSale`.
3. **Merge** — `merge.merge` inner-joins by date; **unmatched dates are surfaced**,
   not dropped silently (a lesson borrowed from the AgencyHubara harness:
   silent truncation reads as "covered everything").
4. **Compute** — `metrics.compute_metrics` (pure, `Decimal`, zero-denominator →
   `None`). `diagnosis.diagnose` applies the fixed threshold table.
5. **Persist** — `store` keeps RAW facts only; metrics are recomputed on read, so
   the store can never drift from the math.
6. **Report** — `report.render_markdown` / `to_dict` format numbers (rounding at
   presentation only). Period rows aggregate raw totals first, then compute — the
   correct blended way, not an average of daily ratios.

## Per-campaign breakdown (what's attributable, what isn't)

When insights are fetched at `level=campaign`, `campaigns.campaign_breakdown` produces
one funnel row per campaign (spend, clicks, conversations, drop-off, cost/conversation)
and `campaigns.collapse_to_daily` sums them into account-per-date rows so the blend still
works. The split is principled:

- **Per campaign:** only the Meta-side funnel (drop-off, cost/conversation). These are
  attributable, and they drive the "which creative to rotate" decision.
- **Account-only:** revenue, MER, Global CPA, win rate. Manual WhatsApp sales can't be
  tied to a campaign (the attribution gap), so splitting revenue per campaign would be a
  fabricated number — the engine refuses. Profitability is reported blended.

`level=account` data has no `campaign_id`; the breakdown is then empty and only the
account blend renders. Mixed campaign/account rows raise (malformed → fail loudly).

## Why COP-only (for now)

Hubara's ad account and sales are both COP, so the MVP needs no FX. The models
**refuse** any non-COP currency (`_check_currency`) — mixing currencies in MER/CPA
is the single most likely bug (a lesson from the AgencyHubara `usd_micros` vs COP
history). Dated FX enters only in Phase 2, when WhatsApp message costs (`usd_micros`)
join the blend — see [`PHASE-2-BRIDGE.md`](PHASE-2-BRIDGE.md).

## Diagnosis decision table

| MER | Drop-off | Recommendation |
|---|---|---|
| ≥ 2.0 | any | `scale_budget` (high-friction still flagged) |
| < 2.0 | > 40% | `rotate_creative` (friction is the bottleneck) |
| < 2.0 | ≤ 40% | `review_targeting_or_pricing` |
| undefined (no clicks/spend) | — | `insufficient_data` |

Thresholds live in `diagnosis.py` (`HIGH_FRICTION_DROP_OFF`, `MIN_HEALTHY_MER`).

## Testing strategy

- **Golden tests** (`@pytest.mark.golden`) pin every metric to a value computed by
  hand in the test, independent of the engine — this is the anti-hallucination
  guarantee.
- **Edge cases**: zero clicks / conversations / orders → `None`; currency mismatch
  → raises; duplicate date → raises; unmatched dates → surfaced.
- **Round-trip** (`@pytest.mark.functional`): store → reload reproduces identical
  numbers.
