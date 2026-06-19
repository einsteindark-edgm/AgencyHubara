"""CLI: ``python -m ads_engine <command>``.

    ingest-sales <file>        load manual WhatsApp sales (JSON/CSV) into the store
    compute [--from-file f]    merge insights + sales → metrics → store
    report [--format md|json]  render the blended report from the store

Data acquisition is intentionally NOT in the CLI: insights come ONLY from the
official Meta Ads MCP (the agent calls `get_insights`, saves the raw JSON, and
feeds it to `compute --from-file`). There is no direct Graph API path — hitting
the Graph API with a raw token risks getting the ad account banned. No MCP, no data.

The math lives in the engine; this module is just plumbing + I/O.
"""

from __future__ import annotations

import argparse
import json
import sys

from .campaigns import campaign_breakdown, collapse_to_daily
from .ingest import load_manual_sales
from .merge import merge
from .meta_insights import load_meta_insights
from .report import render_markdown, to_dict
from .store import DEFAULT_DB_PATH, Store


def _cmd_ingest_sales(args: argparse.Namespace) -> int:
    sales = load_manual_sales(args.file)
    Store(args.db).upsert_sales(sales)
    print(f"ingested {len(sales)} manual-sales rows into {args.db}", file=sys.stderr)
    return 0


def _cmd_compute(args: argparse.Namespace) -> int:
    store = Store(args.db)
    if args.from_file:
        insights = load_meta_insights(args.from_file)
        store.upsert_insights(insights)
    else:
        insights = store.load_insights()
    sales = store.load_sales()
    result = merge(collapse_to_daily(insights), sales)
    store.replace_blended(result)
    print(f"computed {len(result.days)} blended days into {args.db}", file=sys.stderr)
    if result.meta_only_dates or result.sales_only_dates:
        print(
            "WARNING unmatched dates (excluded from blend) — "
            f"meta-only={[d.isoformat() for d in result.meta_only_dates]} "
            f"sales-only={[d.isoformat() for d in result.sales_only_dates]}",
            file=sys.stderr,
        )
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    store = Store(args.db)
    result = store.load_blended(since=args.since, until=args.until)
    campaigns = campaign_breakdown(store.load_insights())
    if args.format == "json":
        print(json.dumps(to_dict(result, campaigns), indent=2, ensure_ascii=False))
    else:
        print(render_markdown(result, campaigns))
    return 0


def _cmd_mcp_report(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .meta_mcp import parse_ad_entities
    from .report import render_mcp_markdown

    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    print(render_mcp_markdown(parse_ad_entities(payload)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ads-engine",
        description="Hubara Ads Analytics Engine — deterministic blended unit-economics",
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite path (default: ads_engine.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest-sales", help="load manual WhatsApp sales (JSON/CSV)")
    ingest.add_argument("file")
    ingest.set_defaults(func=_cmd_ingest_sales)

    compute = sub.add_parser(
        "compute", help="merge insights + sales → metrics → store (--from-file = raw MCP JSON)"
    )
    compute.add_argument(
        "--from-file",
        dest="from_file",
        help="raw Meta insights JSON saved from the Meta Ads MCP get_insights tool",
    )
    compute.set_defaults(func=_cmd_compute)

    report = sub.add_parser("report", help="render the blended report from the store")
    report.add_argument("--since")
    report.add_argument("--until")
    report.add_argument("--format", choices=["md", "json"], default="md")
    report.set_defaults(func=_cmd_report)

    mcp_report = sub.add_parser(
        "mcp-report",
        help="objective-aware report from official MCP ads_get_ad_entities JSON (Meta-only view)",
    )
    mcp_report.add_argument("file", help="JSON saved from the MCP ads_get_ad_entities tool")
    mcp_report.set_defaults(func=_cmd_mcp_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
