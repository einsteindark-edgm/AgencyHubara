#!/usr/bin/env python3
"""Inspecciona costs del vault sin depender del dashboard (HU-WA24H-001).

Lee `hubara_vault/wa_*/metadata.json` directamente y agrega `episodes[*].cost_summary`
para responder rápido a preguntas como "cuánto gasté este mes en marketing" o
"qué clientes me costaron más".

Sin dependencies externas — solo stdlib. Output ASCII para que funcione en
cualquier shell.

Uso típico (desde repo root):

    cd hubara_agency
    uv run python scripts/check_costs.py summary
    uv run python scripts/check_costs.py top --limit 10
    uv run python scripts/check_costs.py customer wa_+573001112233
    uv run python scripts/check_costs.py episodes --closing-tag COMPRA_EXITOSA
    uv run python scripts/check_costs.py marketing
    uv run python scripts/check_costs.py pending
    uv run python scripts/check_costs.py by-category
    uv run python scripts/check_costs.py by-channel

Vault dir: lee `WORKSPACE_VAULT_DIR` env var, fallback a `./hubara_vault`.
Filtro temporal: `--since 7d` filtra a episodes started_at_ms en últimos N días.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


# =============================================================================
# Vault iteration
# =============================================================================


def _default_vault_dir() -> Path:
    """Resuelve vault_dir desde env var o fallback."""
    raw = os.environ.get("WORKSPACE_VAULT_DIR", "./hubara_vault")
    return Path(raw).resolve()


def _iter_sessions(vault_dir: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (session_id, metadata) por cada wa_* dir en el vault.

    Skipea silenciosamente sesiones con metadata.json corrupto o ausente.
    """
    if not vault_dir.exists():
        print(f"WARNING: vault_dir {vault_dir} no existe", file=sys.stderr)
        return
    for session_dir in sorted(vault_dir.iterdir()):
        if not session_dir.is_dir() or not session_dir.name.startswith("wa_"):
            continue
        metadata_path = session_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        yield session_dir.name, metadata


def _filter_episodes_by_time(
    episodes: list[dict[str, Any]], since_ms: int | None
) -> list[dict[str, Any]]:
    """Si since_ms está seteado, filtra episodes con started_at_ms >= since_ms."""
    if since_ms is None:
        return episodes
    return [
        ep for ep in episodes if isinstance(ep.get("started_at_ms"), int)
        and ep["started_at_ms"] >= since_ms
    ]


def _parse_since(since_arg: str | None) -> int | None:
    """Parsea '7d', '24h', '30d' a epoch ms límite. None si no filtra."""
    if not since_arg:
        return None
    if since_arg.endswith("d"):
        days = int(since_arg[:-1])
        delta = timedelta(days=days)
    elif since_arg.endswith("h"):
        hours = int(since_arg[:-1])
        delta = timedelta(hours=hours)
    else:
        raise ValueError(f"--since invalid: {since_arg!r}. Usar Nd o Nh (ej 7d, 24h).")
    cutoff = datetime.now(timezone.utc) - delta
    return int(cutoff.timestamp() * 1000)


# =============================================================================
# Formatting
# =============================================================================


def _fmt_usd(micros: int) -> str:
    """USD micros (10^-6 USD per unit) → '$X.XXXX' con 4 decimales.

    $0.0125 marketing = 12500 micros; $0.0008 utility = 800 micros.
    Conversión: 1 USD = 1_000_000 micros.
    """
    return f"${micros / 1_000_000:.4f}"


def _fmt_pct(num: int, denom: int) -> str:
    return f"{(num / denom * 100):.1f}%" if denom else "  N/A"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Tabla ASCII simple. Sin dependencias."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    sep = " │ "
    lines = [sep.join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("─" * (sum(widths) + len(sep) * (len(widths) - 1) + 2))
    for row in rows:
        lines.append(sep.join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(lines)


# =============================================================================
# Subcommand: summary
# =============================================================================


def cmd_summary(args: argparse.Namespace) -> int:
    """Total tenant spend + breakdown overall."""
    vault_dir = Path(args.vault_dir).resolve()
    since_ms = _parse_since(args.since)

    total_cents = 0
    total_messages = 0
    billable_count = 0
    free_count = 0
    pending_count = 0
    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "cents": 0})
    by_pricing_type: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "cents": 0})
    sessions_count = 0
    episodes_count = 0

    for _, metadata in _iter_sessions(vault_dir):
        sessions_count += 1
        episodes = _filter_episodes_by_time(metadata.get("episodes") or [], since_ms)
        for ep in episodes:
            episodes_count += 1
            summary = ep.get("cost_summary") or {}
            total_cents += summary.get("total_usd_micros", 0)
            total_messages += summary.get("messages_count", 0)
            billable_count += summary.get("messages_billable_count", 0)
            free_count += summary.get("messages_free_count", 0)
            pending_count += summary.get("messages_pending_count", 0)
            for cat, v in (summary.get("by_category") or {}).items():
                if isinstance(v, dict):
                    by_category[cat]["count"] += v.get("count", 0)
                    by_category[cat]["cents"] += v.get("usd_micros", 0)
            for pt, v in (summary.get("by_pricing_type") or {}).items():
                if isinstance(v, dict):
                    by_pricing_type[pt]["count"] += v.get("count", 0)
                    by_pricing_type[pt]["cents"] += v.get("usd_micros", 0)

    print(f"Vault: {vault_dir}")
    if since_ms is not None:
        print(f"Filtro: episodes started en últimos {args.since}")
    print()
    print(f"  Sessions:                {sessions_count}")
    print(f"  Episodes:                {episodes_count}")
    print(f"  Total spend:             {_fmt_usd(total_cents)} USD ({total_cents} micros)")
    print(f"  Messages total:          {total_messages}")
    print(f"  Messages billable:       {billable_count} ({_fmt_pct(billable_count, total_messages)})")
    print(f"  Messages free:           {free_count} ({_fmt_pct(free_count, total_messages)})")
    print(f"  Messages pending price:  {pending_count} ({_fmt_pct(pending_count, total_messages)})")
    print()

    if by_category:
        print("By category:")
        rows = [
            [cat, str(v["count"]), _fmt_usd(v["cents"])]
            for cat, v in sorted(by_category.items(), key=lambda kv: -kv[1]["cents"])
        ]
        print(_table(["category", "count", "spend"], rows))
        print()

    if by_pricing_type:
        print("By pricing_type:")
        rows = [
            [pt, str(v["count"]), _fmt_usd(v["cents"])]
            for pt, v in sorted(by_pricing_type.items(), key=lambda kv: -kv[1]["cents"])
        ]
        print(_table(["pricing_type", "count", "spend"], rows))

    if pending_count > 0:
        ratio = pending_count / total_messages if total_messages else 0
        if ratio > 0.10:
            print()
            print(f"⚠  ALERT: pending_count ratio {ratio:.1%} > 10% — webhook capture broken?")

    return 0


# =============================================================================
# Subcommand: top
# =============================================================================


def cmd_top(args: argparse.Namespace) -> int:
    """Top N customers por spend acumulado."""
    vault_dir = Path(args.vault_dir).resolve()
    since_ms = _parse_since(args.since)

    rows: list[tuple[str, int, int, int]] = []  # (session, total_cents, episodes, msgs)
    for session_id, metadata in _iter_sessions(vault_dir):
        episodes = _filter_episodes_by_time(metadata.get("episodes") or [], since_ms)
        total = sum(
            (ep.get("cost_summary") or {}).get("total_usd_micros", 0) for ep in episodes
        )
        msgs = sum(
            (ep.get("cost_summary") or {}).get("messages_count", 0) for ep in episodes
        )
        if total > 0 or msgs > 0:
            rows.append((session_id, total, len(episodes), msgs))

    rows.sort(key=lambda r: -r[1])
    rows = rows[: args.limit]

    print(f"Top {len(rows)} customers by spend:")
    if since_ms is not None:
        print(f"(episodes started en últimos {args.since})")
    print()
    table_rows = [
        [session, _fmt_usd(cents), str(eps), str(msgs)]
        for session, cents, eps, msgs in rows
    ]
    if not table_rows:
        print("(sin datos)")
        return 0
    print(_table(["session", "spend", "episodes", "messages"], table_rows))
    return 0


# =============================================================================
# Subcommand: customer
# =============================================================================


def cmd_customer(args: argparse.Namespace) -> int:
    """Detalle de UN cliente — episodes con sus outcomes y costs."""
    vault_dir = Path(args.vault_dir).resolve()
    metadata_path = vault_dir / args.session_id / "metadata.json"
    if not metadata_path.exists():
        print(f"ERROR: {metadata_path} no existe", file=sys.stderr)
        return 1
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    print(f"Customer: {args.session_id}")
    print(f"  active_route:    {metadata.get('active_route', 'unknown')}")
    print(f"  tag:             {metadata.get('tag', 'unknown')}")
    origin = metadata.get("origin") or {}
    print(f"  origin channel:  {origin.get('channel', 'unknown')}")
    print()

    episodes = metadata.get("episodes") or []
    if not episodes:
        print("(sin episodes)")
        return 0

    rows = []
    grand_total = 0
    for ep in episodes:
        summary = ep.get("cost_summary") or {}
        total = summary.get("total_usd_micros", 0)
        grand_total += total
        marketing_cents = (summary.get("by_category", {}).get("marketing") or {}).get(
            "usd_micros", 0
        )
        utility_cents = (summary.get("by_category", {}).get("utility") or {}).get(
            "usd_micros", 0
        )
        rows.append(
            [
                ep.get("episode_id", "?"),
                ep.get("closing_tag") or "ACTIVE",
                str(summary.get("messages_count", 0)),
                _fmt_usd(total),
                _fmt_usd(marketing_cents),
                _fmt_usd(utility_cents),
                ep.get("order_id") or "—",
            ]
        )

    print(_table(
        ["episode", "tag", "msgs", "total", "marketing", "utility", "order"],
        rows,
    ))
    print()
    print(f"  Grand total spend: {_fmt_usd(grand_total)} USD")

    if args.outbounds:
        print()
        print("Outbound details:")
        for ep in episodes:
            outbounds = ep.get("outbound_messages") or []
            if not outbounds:
                continue
            print(f"\n  Episode {ep.get('episode_id')}:")
            for ob in outbounds:
                cost = ob.get("cost_usd_micros")
                cost_str = _fmt_usd(cost) if cost is not None else "pending"
                pt = (ob.get("pricing") or {}).get("pricing_type", "?")
                cat = (ob.get("pricing") or {}).get("category", "?")
                tmpl = ob.get("template_name") or ob.get("kind", "?")
                print(f"    {ob.get('wa_message_id', '?'):30s}  {tmpl:30s}  {cat:10s}  {pt:25s}  {cost_str}")

    return 0


# =============================================================================
# Subcommand: episodes (filter by closing_tag)
# =============================================================================


def cmd_episodes(args: argparse.Namespace) -> int:
    """Lista episodes filtrados por closing_tag con su spend."""
    vault_dir = Path(args.vault_dir).resolve()
    since_ms = _parse_since(args.since)

    rows = []
    total_spend = 0
    won_spend = 0
    lost_spend = 0
    for session_id, metadata in _iter_sessions(vault_dir):
        episodes = _filter_episodes_by_time(metadata.get("episodes") or [], since_ms)
        for ep in episodes:
            tag = ep.get("closing_tag")
            if args.closing_tag and tag != args.closing_tag:
                continue
            if not args.closing_tag and tag is None:
                continue  # Solo cerrados si no especifica filtro
            summary = ep.get("cost_summary") or {}
            total = summary.get("total_usd_micros", 0)
            total_spend += total
            if tag == "COMPRA_EXITOSA":
                won_spend += total
            elif tag in ("RECHAZO", "TIMEOUT"):
                lost_spend += total
            rows.append(
                [
                    session_id,
                    ep.get("episode_id", "?"),
                    tag or "?",
                    _fmt_usd(total),
                    ep.get("order_id") or "—",
                ]
            )

    rows.sort(key=lambda r: r[2])  # group by tag

    filter_label = f"closing_tag={args.closing_tag}" if args.closing_tag else "all closed"
    print(f"Episodes ({filter_label}, {len(rows)} found):")
    if since_ms is not None:
        print(f"(started en últimos {args.since})")
    print()
    if not rows:
        print("(sin datos)")
        return 0
    print(_table(["session", "episode", "tag", "spend", "order"], rows))
    print()
    print(f"  Total in filter:    {_fmt_usd(total_spend)} USD")
    if won_spend > 0 or lost_spend > 0:
        print(f"  Won (COMPRA_EXITOSA): {_fmt_usd(won_spend)} USD")
        print(f"  Lost (RECHAZO+TIMEOUT): {_fmt_usd(lost_spend)} USD")
        won_episodes = sum(1 for r in rows if r[2] == "COMPRA_EXITOSA")
        if won_episodes > 0:
            avg_cac = won_spend // won_episodes
            print(f"  Avg CAC per won:    {_fmt_usd(avg_cac)} USD")

    return 0


# =============================================================================
# Subcommand: marketing
# =============================================================================


def cmd_marketing(args: argparse.Namespace) -> int:
    """Solo gastos en templates marketing."""
    vault_dir = Path(args.vault_dir).resolve()
    since_ms = _parse_since(args.since)

    total_cents = 0
    total_count = 0
    by_session: dict[str, int] = defaultdict(int)
    by_template: dict[str, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "cents": 0}
    )

    for session_id, metadata in _iter_sessions(vault_dir):
        episodes = _filter_episodes_by_time(metadata.get("episodes") or [], since_ms)
        for ep in episodes:
            outbounds = ep.get("outbound_messages") or []
            for ob in outbounds:
                pricing = ob.get("pricing") or {}
                if pricing.get("category") != "marketing":
                    continue
                cost = ob.get("cost_usd_micros") or 0
                total_cents += cost
                total_count += 1
                by_session[session_id] += cost
                template = ob.get("template_name") or "(unknown)"
                by_template[template]["count"] += 1
                by_template[template]["cents"] += cost

    print("Marketing spend:")
    if since_ms is not None:
        print(f"(últimos {args.since})")
    print()
    print(f"  Total:    {_fmt_usd(total_cents)} USD ({total_count} sends)")
    print()

    if by_template:
        print("By template:")
        rows = [
            [tmpl, str(v["count"]), _fmt_usd(v["cents"])]
            for tmpl, v in sorted(by_template.items(), key=lambda kv: -kv[1]["cents"])
        ]
        print(_table(["template", "count", "spend"], rows))
        print()

    if by_session:
        top_n = sorted(by_session.items(), key=lambda kv: -kv[1])[:10]
        print(f"Top {len(top_n)} customers by marketing spend:")
        rows = [[sid, _fmt_usd(cents)] for sid, cents in top_n]
        print(_table(["session", "marketing spend"], rows))

    return 0


# =============================================================================
# Subcommand: pending
# =============================================================================


def cmd_pending(args: argparse.Namespace) -> int:
    """Cuántos messages tienen pricing pending — indica health del webhook capture."""
    vault_dir = Path(args.vault_dir).resolve()

    total_msgs = 0
    pending = 0
    pending_details = []  # (session, episode, wa_message_id, sent_at_ms)

    for session_id, metadata in _iter_sessions(vault_dir):
        for ep in metadata.get("episodes") or []:
            summary = ep.get("cost_summary") or {}
            total_msgs += summary.get("messages_count", 0)
            pending += summary.get("messages_pending_count", 0)
            for ob in ep.get("outbound_messages") or []:
                if ob.get("cost_usd_micros") is None and ob.get("pricing") is None:
                    pending_details.append(
                        (
                            session_id,
                            ep.get("episode_id"),
                            ob.get("wa_message_id"),
                            ob.get("sent_at_ms"),
                        )
                    )

    print(f"Pending pricing webhooks: {pending} of {total_msgs} total messages")
    if total_msgs:
        ratio = pending / total_msgs
        print(f"Ratio: {ratio:.1%}")
        if ratio > 0.10:
            print("⚠  ALERT: > 10% sostenido indica webhook capture roto.")
        elif ratio > 0.02:
            print("⚠  WARN: > 2% — investigar si steady-state.")
        else:
            print("✓ healthy (< 2%).")
    print()

    if pending_details and args.detail:
        # Sort by sent_at_ms — los más viejos primero (los que más probable están perdidos).
        pending_details.sort(key=lambda x: x[3] or 0)
        print(f"Top {min(20, len(pending_details))} oldest pending:")
        rows = []
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        for sid, eid, wamid, sent_at in pending_details[:20]:
            age_min = (now_ms - (sent_at or now_ms)) // 60000
            rows.append([sid or "?", eid or "?", wamid or "?", f"{age_min} min"])
        print(_table(["session", "episode", "wa_message_id", "age"], rows))

    # Tambien orphan dead-letter
    orphan_path = vault_dir / "_orphan_delivery_statuses.jsonl"
    if orphan_path.exists():
        try:
            orphan_count = sum(1 for line in orphan_path.read_text().splitlines() if line.strip())
            print()
            print(f"Orphan dead-letters: {orphan_count} in {orphan_path}")
        except OSError:
            pass

    return 0


# =============================================================================
# Subcommand: by-channel (referral source)
# =============================================================================


def cmd_by_channel(args: argparse.Namespace) -> int:
    """Spend agregado por canal de origen (CTWA ad vs web_referral vs direct)."""
    vault_dir = Path(args.vault_dir).resolve()
    since_ms = _parse_since(args.since)

    by_channel: dict[str, dict[str, int]] = defaultdict(
        lambda: {"sessions": 0, "episodes": 0, "won": 0, "spend": 0}
    )

    for _, metadata in _iter_sessions(vault_dir):
        origin = metadata.get("origin") or {}
        channel = origin.get("channel") or "unknown"
        by_channel[channel]["sessions"] += 1
        episodes = _filter_episodes_by_time(metadata.get("episodes") or [], since_ms)
        for ep in episodes:
            by_channel[channel]["episodes"] += 1
            if ep.get("closing_tag") == "COMPRA_EXITOSA":
                by_channel[channel]["won"] += 1
            summary = ep.get("cost_summary") or {}
            by_channel[channel]["spend"] += summary.get("total_usd_micros", 0)

    print("Spend by origin channel:")
    if since_ms is not None:
        print(f"(episodes started en últimos {args.since})")
    print()
    rows = []
    for ch, v in sorted(by_channel.items(), key=lambda kv: -kv[1]["spend"]):
        win_rate = _fmt_pct(v["won"], v["episodes"])
        rows.append(
            [
                ch,
                str(v["sessions"]),
                str(v["episodes"]),
                str(v["won"]),
                win_rate,
                _fmt_usd(v["spend"]),
            ]
        )
    if not rows:
        print("(sin datos)")
        return 0
    print(_table(["channel", "sessions", "episodes", "won", "win_rate", "spend"], rows))
    return 0


# =============================================================================
# Argparse
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspecciona costs del vault HU-WA24H-001.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--vault-dir",
        default=str(_default_vault_dir()),
        help="Override del vault dir (default: $WORKSPACE_VAULT_DIR o ./hubara_vault)",
    )

    subs = parser.add_subparsers(dest="cmd", required=True)

    p_summary = subs.add_parser("summary", help="Total tenant spend + breakdown")
    p_summary.add_argument("--since", help="Filtrar episodes started últimos Nd/Nh (ej 7d)")
    p_summary.set_defaults(func=cmd_summary)

    p_top = subs.add_parser("top", help="Top N customers por spend")
    p_top.add_argument("--limit", type=int, default=10)
    p_top.add_argument("--since")
    p_top.set_defaults(func=cmd_top)

    p_customer = subs.add_parser("customer", help="Detalle de UN cliente por session_id")
    p_customer.add_argument("session_id", help="ej: wa_+573001112233")
    p_customer.add_argument("--outbounds", action="store_true", help="Detalle de outbound_messages")
    p_customer.set_defaults(func=cmd_customer)

    p_eps = subs.add_parser("episodes", help="Episodes filtrados por closing_tag")
    p_eps.add_argument("--closing-tag", help="ej COMPRA_EXITOSA, RECHAZO, TIMEOUT")
    p_eps.add_argument("--since")
    p_eps.set_defaults(func=cmd_episodes)

    p_mkt = subs.add_parser("marketing", help="Solo gastos en templates marketing")
    p_mkt.add_argument("--since")
    p_mkt.set_defaults(func=cmd_marketing)

    p_pending = subs.add_parser("pending", help="Pending pricing webhooks (health check)")
    p_pending.add_argument("--detail", action="store_true", help="Lista top 20 más viejos")
    p_pending.set_defaults(func=cmd_pending)

    p_chan = subs.add_parser("by-channel", help="Spend agregado por origin channel")
    p_chan.add_argument("--since")
    p_chan.set_defaults(func=cmd_by_channel)

    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
