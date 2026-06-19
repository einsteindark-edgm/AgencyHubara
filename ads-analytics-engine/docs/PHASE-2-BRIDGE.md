# Phase 2 — bridge to the live AgencyHubara system

The MVP is standalone (manual sales + Meta MCP, COP-only). When you want to wire it
into the live system, these reuse targets already exist in the repo (paths verified
present). **Nothing here is built yet** — this is the map.

## 1. Real sales instead of manual JSON

- **Reuse:** `hubara_agency/src/platform/orders/query_port.py` — `OrderQueryPort`
  with `OrderSummaryDTO.total_cop` (COP major units) + `created_at_ms`.
- **How:** a `SalesSource` adapter that lists closed orders per day from Medusa and
  emits the same `ManualSale` shape the engine already consumes. The engine's merge
  / metrics don't change — only the ingestion source.

## 2. Spend/clicks into the existing ads dashboard

- **Reuse:** `hubara_agency/src/plugins/ads/aggregation.py` —
  `list_ads_campaigns()` already aggregates WhatsApp conversations + revenue per ad;
  `spend` / `impressions` / `clicks` are `None`, waiting for Meta data.
- **How:** feed this engine's Meta insights (joined on the ad/`source_id`) to fill
  those `None` fields, so the dashboard shows true blended CAC/ROAS per campaign.
- **Frontend formatting:** `frontend_dashboard/src/plugins/ads/frontend/lib/format.ts`
  (`fmtMoney` for COP, `fmtUsd` for sub-cent USD) — reuse so COP and USD stay
  visually distinguished.

## 3. The feedback loop (higher leverage than any dashboard)

- **Reuse:** `hubara_agency/src/platform/whatsapp/capi.py` —
  `build_purchase_event()` + `ctwa_clid` + 7-day attribution window, COP default.
- **How:** when a CTWA conversation closes a sale, send a **Purchase** event back to
  Meta via CAPI. That trains Meta's optimizer on real revenue — it improves the ads
  themselves, not just our reporting. This is the single highest-leverage extension.

## 4. Dated FX (only when USD costs join)

The moment WhatsApp message costs (`usd_micros`, 10⁻⁶ USD) enter the blend, the
COP-only guard must give way to an **explicit, dated FX rate** (`META_FX_USD_TO_COP`,
sourced + timestamped). Convert with integer arithmetic, keep every intermediate
tagged with its unit. Until then, the engine deliberately refuses mixed currencies.

## Sequencing

Do them in leverage order: **(3) CAPI feedback** first (it makes the ads better),
then **(1) real sales** (kills manual entry), then **(2) dashboard fill** (visibility),
then **(4) FX** (only if/when USD costs are folded in).
