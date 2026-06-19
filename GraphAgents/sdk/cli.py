"""CLI del SDK de GraphAgents — verbos deterministas. Una fuente (el TestKit),
varios frontends; este es el CLI. No implementa reglas: delega en
`sdk.testkit.checks` (manifests) y `sdk.testkit.tool_checks` (tools).

    uv run python -m sdk.cli check [<id>...]        # compilador rápido de manifests
    uv run python -m sdk.cli certify [<id>...]      # manifests: nivel (exit 1 si < C2)
    uv run python -m sdk.cli list-tools             # el palette de tools
    uv run python -m sdk.cli list-agents            # el catálogo de agentes
    uv run python -m sdk.cli search <term>          # buscar tools en el catálogo
    uv run python -m sdk.cli certify-tool [<id>...] # tools: nivel (exit 1 si < C2)
    uv run python -m sdk.cli graph [--format mermaid|json]  # serializa el sistema a grafo
    uv run python -m sdk.cli create <id>            # G2: scaffold que nace C2
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sdk.manifest_model import load_manifest
from sdk.registry import agent_index
from sdk.registry import index as tool_index
from sdk.registry import search as tool_search
from sdk.testkit.checks import level_of, run_checks
from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool

ROOT = Path(".")
MANIFESTS = ROOT / "manifests"
TOOLS = ROOT / "tools"


def _resolve(ids: list[str]) -> list[Path]:
    if not ids:
        return sorted(MANIFESTS.glob("*.yaml"))
    out: list[Path] = []
    for i in ids:
        out += sorted(MANIFESTS.glob(f"{i}.yaml")) + sorted(MANIFESTS.glob(f"{i}.*.yaml"))
    return out


def cmd_check(args: argparse.Namespace) -> int:
    paths = _resolve(args.ids)
    if not paths:
        print("No hay manifests en manifests/ (¿corriste desde GraphAgents/?).")
        return 0
    failed = 0
    for p in paths:
        try:
            m = load_manifest(p)
        except Exception as e:  # noqa: BLE001
            print(f"error[C0-SCHEMA] {p.name}: {e}")
            failed = 1
            continue
        res = run_checks(m, ROOT)
        for err in res["errors"]:
            print(f"error {p.name}: {err}")
            failed = 1
        for w in res["warnings"]:
            print(f"warn  {p.name}: {w}")
        if not res["errors"]:
            print(f"ok    {p.name}  ({m.archetype}, nivel {level_of(m, ROOT)})")
    return failed


def cmd_certify(args: argparse.Namespace) -> int:
    paths = _resolve(args.ids)
    failed = 0
    for p in paths:
        try:
            m = load_manifest(p)
        except Exception as e:  # noqa: BLE001
            print(f"{p.name}: none ({e})")
            failed = 1
            continue
        lvl = level_of(m, ROOT)
        print(f"{m.name}: {lvl}")
        if lvl not in ("C2", "C3"):
            failed = 1
    if failed:
        print("\nAlgún agente < C2 — no mergeable / no componible (G-CERT).")
    return failed


def cmd_list_tools(args: argparse.Namespace) -> int:
    rows = tool_index(ROOT)
    if not rows:
        print("catálogo de tools vacío (no hay tools/*/tool.yaml).")
        return 0
    print("# el palette — tools agnósticas del catálogo")
    for r in rows:
        appr = " ⚠approval" if r["approval_required"] else ""
        print(f"  {r['id']}@{r['version']}  [{r['side_effect']}]{appr}  ({', '.join(r['tags'])})")
        print(f"      {r['description']}")
    return 0


def cmd_list_agents(args: argparse.Namespace) -> int:
    rows = agent_index(ROOT)
    if not rows:
        print("catálogo de agentes vacío (no hay manifests/*.agent.yaml).")
        return 0
    print("# el catálogo de agentes — referenciables por agent://<id>")
    for r in rows:
        ex = " ·as-tool" if r["exposes_as_tool"] else ""
        pub = f" ·publish:{r['publish']}" if r["publish"] else ""
        print(f"  {r['id']}  [{r['archetype']}]{ex}{pub}")
        print(f"      {r['description']}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    rows = tool_search(ROOT, args.term)
    if not rows:
        print(f"sin resultados para '{args.term}'.")
        return 0
    for r in rows:
        print(f"  {r['id']}@{r['version']}  ({', '.join(r['tags'])}) — {r['description']}")
    return 0


def cmd_certify_tool(args: argparse.Namespace) -> int:
    paths = sorted(TOOLS.glob("*/tool.yaml"))
    failed = 0
    seen = 0
    for p in paths:
        c = load_tool(p)
        if args.ids and c.id not in args.ids:
            continue
        seen += 1
        lvl = tool_level(c, ROOT)
        print(f"{c.id}@{c.version}: {lvl}")
        if lvl not in ("C2", "C3"):
            for e in run_tool_checks(c, ROOT)["errors"]:
                print(f"    {e}")
            failed = 1
    if args.ids and seen == 0:
        print(f"no encontré tool(s): {', '.join(args.ids)}")
        return 1
    return failed


def cmd_run(args: argparse.Namespace) -> int:
    import json

    from sdk.loader import build_runnable
    from sdk.manifest_model import iter_nodes
    from sdk.runtime import LocalRuntime

    cand = sorted(MANIFESTS.glob(f"{args.id}.agent.yaml")) + sorted(
        MANIFESTS.glob(f"{args.id}.taskgraph.yaml")
    )
    if not cand:
        print(f"no encontré el agente '{args.id}' en manifests/")
        return 1
    m = load_manifest(cand[0])
    consumed = sorted({p for n in iter_nodes(m) for p in n.consumes})
    if consumed:
        print(
            f"'{args.id}' consume ports {consumed} — necesita vendors inyectados. "
            "`run` corre agentes tool-only (ej. greeter); para el resto usá un runner "
            "con un FixtureXxx (ver tests/integration)."
        )
        return 1
    inp = json.loads(args.input) if args.input else {}
    ex = LocalRuntime().run(build_runnable(m, ROOT), inp)
    print(f"execution {ex.id}: {ex.status}")
    print(json.dumps(ex.output, ensure_ascii=False, indent=2))
    return 0 if ex.status == "completed" else 1


def cmd_graph(args: argparse.Namespace) -> int:
    import json

    from sdk.graph import build_graph, to_mermaid

    g = build_graph(ROOT)
    if args.format == "json":
        print(json.dumps(g, ensure_ascii=False, indent=2))
    else:
        print(to_mermaid(g), end="")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    print("TODO (G2): scaffold de un manifest/tool que nace C2 y corre su golden.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="graphagents", description="CLI del SDK de GraphAgents")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="manifests: schema + archetype + refs + binding")
    c.add_argument("ids", nargs="*")
    c.set_defaults(fn=cmd_check)

    ce = sub.add_parser("certify", help="manifests: nivel C0–C3 (exit 1 si < C2)")
    ce.add_argument("ids", nargs="*")
    ce.set_defaults(fn=cmd_certify)

    lt = sub.add_parser("list-tools", help="el palette del catálogo de tools")
    lt.set_defaults(fn=cmd_list_tools)

    la = sub.add_parser("list-agents", help="el catálogo de agentes (agent://<id>)")
    la.set_defaults(fn=cmd_list_agents)

    se = sub.add_parser("search", help="buscar tools en el catálogo (id/tag/descripción)")
    se.add_argument("term")
    se.set_defaults(fn=cmd_search)

    ct = sub.add_parser("certify-tool", help="tools: nivel C0–C3 (exit 1 si < C2)")
    ct.add_argument("ids", nargs="*")
    ct.set_defaults(fn=cmd_certify_tool)

    rn = sub.add_parser("run", help="corre un agente tool-only por el LocalRuntime")
    rn.add_argument("id")
    rn.add_argument("--input", default="", help='input JSON, ej. \'{"name":"mundo"}\'')
    rn.set_defaults(fn=cmd_run)

    g = sub.add_parser("graph", help="serializa el sistema a grafo (mermaid|json)")
    g.add_argument("--format", choices=["mermaid", "json"], default="mermaid")
    g.set_defaults(fn=cmd_graph)

    cr = sub.add_parser("create", help="scaffold (G2)")
    cr.add_argument("ids", nargs="*")
    cr.set_defaults(fn=cmd_create)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
