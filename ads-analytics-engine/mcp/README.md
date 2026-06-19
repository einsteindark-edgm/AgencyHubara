# Meta Ads MCP — wiring

The engine does the math; **the MCP only fetches data**. The agent calls the
official Meta Ads MCP tool to pull daily insights, saves the raw JSON, and feeds
it to `ads-engine compute --from-file`.

## The only supported path: official Meta Ads MCP (`mcp.facebook.com/ads`)

Meta's first-party MCP (open beta since Apr 2026): Claude-native, OAuth direct
with Meta, free during the beta. It is the sanctioned, safe way to read ad data.

1. In Claude → **Settings → Connectors → Add custom connector** → URL
   `https://mcp.facebook.com/ads`.
2. Sign in with the Meta account that owns the ad account (OAuth).
3. Verify: if tools return `is_ads_mcp_enabled: false`, your account isn't in the
   beta yet (phased rollout). **Then you cannot pull data yet — by design.** Wait
   for access; do not improvise another route.

**Tool to call:** `get_insights` for the ad account / campaign, per day.

## Why there is no fallback

> ⚠️ **Do NOT pull insights with a raw Graph API token or a third-party MCP
> broker.** Calling the Graph API directly — or routing your Meta token through a
> third party — outside Meta's sanctioned path can get the **ad account banned**.
> This engine ships **no** direct-Graph client and **no** token field on purpose.
> No properly-configured official MCP → no data. Full stop.

## The `get_insights` contract (what the engine expects)

Request, per day:

| Param | Value |
|---|---|
| `level` | `account` (or `campaign`) |
| `time_increment` | `1` (one row per day) |
| `fields` | `spend,inline_link_clicks,actions` |

The engine reads, from each daily row:
- `spend` → `spend_cop` (rounded to whole COP)
- `inline_link_clicks` → clicks
- `actions[]` where `action_type == onsite_conversion.messaging_conversation_started_7d`
  → `messaging_conversations_started`
- if you fetched `level=campaign`: `campaign_id` + `campaign_name` per row → the report
  adds a per-campaign funnel table; `level=account` omits them → account blend only.

**Choosing what to analyze:** `level=account` = all CTWA campaigns blended (default).
`level=campaign` = one row per campaign per day → per-campaign funnel breakdown. To
target ONE campaign, resolve its id first with the MCP's `get_campaigns` tool and filter.

Save the tool's raw `{"data": [...], "account_currency": "COP"}` response to a file,
then: `ads-engine compute --from-file <that-file>.json`.

> COP-only MVP: if `account_currency` isn't `COP`, the engine refuses (mixing
> currencies in MER/CPA is the #1 bug). Dated FX is a Phase-2 concern.
