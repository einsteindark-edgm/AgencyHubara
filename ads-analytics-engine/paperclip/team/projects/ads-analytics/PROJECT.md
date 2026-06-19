---
name: Ads Analytics
slug: ads-analytics
description: Rolling blended unit-economics for Hubara CTWA campaigns — daily Meta spend + manual WhatsApp sales joined by date, metrics computed by the deterministic engine, interpreted by the Analyst and gated by Numbers QA.
owner: analyst
---

# Ads Analytics

The standing project for Hubara's ad unit-economics. Work arrives as analysis
requests (a date range to audit) or via the recurring `daily-pull` routine.

Every deliverable is an engine-produced report (Markdown table + diagnosis) plus
the Analyst's narrative, signed off by the QA reviewer. Numbers are never authored
by an agent — only by `ads_engine`.
