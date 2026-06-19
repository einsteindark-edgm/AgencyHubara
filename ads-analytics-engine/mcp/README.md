# Meta Ads MCP — wiring

The engine does the math; **the MCP only fetches data**. The agent calls the
official Meta Ads MCP, saves the raw JSON, and feeds it to `ads-engine mcp-report`
(or `compute`). For the full read-only analytical toolbox + analysis recipes, see
[`../docs/MCP-TOOLBOX.md`](../docs/MCP-TOOLBOX.md).

## The only supported path: official Meta Ads MCP (`mcp.facebook.com/ads`)

Meta's first-party MCP (open beta since Apr 2026): Claude-native, OAuth direct with
Meta, free during the beta. The sanctioned, safe way to read ad data.

1. In Claude → **Settings → Connectors → Add custom connector** → URL
   `https://mcp.facebook.com/ads` (or CLI: `claude mcp add --transport http --scope
   user meta-ads https://mcp.facebook.com/ads`, then `/mcp` → authenticate).
2. Sign in with the Meta account that owns the ad account (OAuth). The token caches
   at host level, so a headless `claude --print` (e.g. a Paperclip agent) inherits it.
3. `ads_get_ad_accounts` → confirm `is_ads_mcp_enabled: true` and `is_queryable: true`.
   If `false`, your account isn't in the beta yet → **you cannot pull data, by design.**

## Why there is no fallback

> ⚠️ **Do NOT pull data with a raw Graph API token or a third-party MCP broker.**
> Calling the Graph API directly — or routing your Meta token through a third party —
> outside Meta's sanctioned path can get the **ad account banned**. This engine ships
> **no** direct-Graph client and **no** token field on purpose. No official MCP → no data.

## The real tool: `ads_get_ad_entities` (NOT `get_insights`)

The official MCP does **not** have a `get_insights` tool (that's the community servers).
The workhorse is **`ads_get_ad_entities`**:

| Param | Value |
|---|---|
| `ad_account_id` | e.g. `1010393601284112` (Hubara) |
| `level` | `account` (blend) or `campaign` (per-campaign breakdown) |
| `time_increment` | `"1"` for a daily breakdown (omit for a period aggregate) |
| `fields` | `["id","name","objective","amount_spent","actions:link_click","results","cost_per_result"]` |

(If a field errors with "Unsupported field", verify names with `ads_get_field_context`.)

**Important — the MCP returns human-formatted strings, not raw numbers**, and `results`
is objective-dependent. The engine's adapter (`meta_mcp.parse_ad_entities`) parses them
deterministically:
- `amount_spent: "$ 896.823 COP"` → `896823`
- `actions:link_click: "571"` / `"Not available"` → `571` / `0`
- `results: {"value": "205 (Messaging conversations started)"}` → `205` + the result type
  (only `Messaging conversations started` = a CTWA funnel campaign; `Meta purchases`,
  `Link clicks`, etc. are reported in the report's "otras campañas" section)
- `cost_per_result: {"value": "$ 4.375 COP (...)"}` → `4375`

## Feed the engine

Save the **raw tool response** (the `{"ad_entities": "...", "summary": {...}}` envelope)
to a file, then:
```bash
ads-engine mcp-report <that-file>.json      # objective-aware Meta view (funnel + sales)
```
For the account blend with manual WhatsApp sales joined by day, also:
```bash
ads-engine ingest-sales <sales.json>        # date, total_orders, total_revenue (COP)
ads-engine compute --from-file <...> && ads-engine report
```

> COP-only MVP: if the account `currency` isn't `COP`, the engine refuses (mixing
> currencies in MER/CPA is the #1 bug). Dated FX is a Phase-2 concern.
