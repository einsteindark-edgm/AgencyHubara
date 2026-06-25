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
    uv run python -m sdk.cli cases [--check]         # los casos replayables (el catálogo del viewer)
    uv run python -m sdk.cli create tool <id>       # scaffold determinista (nace C2 + golden rojo)
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
    if getattr(args, "input_file", ""):
        inp = json.loads(Path(args.input_file).read_text(encoding="utf-8"))
    else:
        inp = json.loads(args.input) if args.input else {}
    ex = LocalRuntime().run(build_runnable(m, ROOT), inp)
    print(f"execution {ex.id}: {ex.status}")
    print(json.dumps(ex.output, ensure_ascii=False, indent=2))
    return 0 if ex.status == "completed" else 1


def cmd_resume(args: argparse.Namespace) -> int:
    """HITL: completa la HUMAN task de un execution-id con la decisión (vía
    `AgentSpanRuntime.resume` → `respond`). Lo corre el buzón por SSM en la caja:
    `python -m sdk.cli resume <eid> --decision '{"approved": true, "by": "ed"}'`."""
    import json

    from sdk.runtime import AgentSpanRuntime

    decision = json.loads(args.decision) if args.decision else None
    ex = AgentSpanRuntime().resume(args.execution_id, decision=decision)
    print(f"execution {ex.id}: {ex.status}")
    print(json.dumps(ex.output, ensure_ascii=False, indent=2))
    return 0 if ex.status != "failed" else 1


def cmd_cases(args: argparse.Namespace) -> int:
    from sdk.case_model import discover_cases

    cases = discover_cases(ROOT)
    if not cases:
        print("no hay casos en fixtures/cases/ (*.case.yaml).")
        return 0
    if args.check:  # replayea cada caso y verifica su golden (el guard del catálogo)
        from sdk.testkit.case_checks import run_case_checks

        failed = 0
        for c in cases:
            res = run_case_checks(c, ROOT)
            mark = "✓" if not res["errors"] else "✗"
            print(f"{mark} {c.id}  [{c.target}]")
            for e in res["errors"]:
                print(f"    {e}")
                failed = 1
        if failed:
            print("\nAlgún caso no replayea a su golden (CASE-*) — el catálogo no es confiable.")
        return failed
    print("# casos de prueba — el triple input fijo (seed + ports + golden) que el viewer lista")
    for c in cases:
        title = f"  — {c.title}" if c.title else ""
        print(f"  {c.id}  [{c.target}]{title}")
    return 0


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
    if args.kind == "tool":
        from sdk.scaffold import ScaffoldError, create_tool

        try:
            paths = create_tool(
                args.id, ROOT, description=args.description, side_effect=args.side_effect
            )
        except ScaffoldError as e:  # noqa: BLE001
            print(f"error: {e}")
            return 1
        mod = args.id.replace("-", "_")
        print(f"creada tool '{args.id}' (contrato C2; su golden nace ROJO):")
        for p in paths:
            print(f"  {p}")
        print(
            f"\nseguí (TDD): implementá tools/{mod}/impl.py + completá el golden en "
            f"tests/tools/test_{mod}.py → verificá `python -m sdk.cli certify-tool {args.id}`."
        )
        return 0
    # capability/agent/connector: scaffold pendiente (ver docs/cli-design-guide.md).
    print(
        f"create {args.kind}: todavía no implementado (ver docs/cli-design-guide.md). "
        "Disponible hoy: `create tool <id>`."
    )
    return 1


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

    rn = sub.add_parser("run", help="corre un agente o un TASK GRAPH (supervisor) por el LocalRuntime")
    rn.add_argument("id")
    rn.add_argument("--input", default="", help='input/seed JSON inline, ej. \'{"name":"mundo"}\'')
    rn.add_argument(
        "--input-file", dest="input_file", default="",
        help="seed JSON desde archivo (para task graphs con payloads grandes, ej. el JSON de Meta)",
    )
    rn.set_defaults(fn=cmd_run)

    rs = sub.add_parser("resume", help="HITL: completa la HUMAN task de un execution-id con la decisión (AgentSpan)")
    rs.add_argument("execution_id")
    rs.add_argument("--decision", default="", help='decisión JSON inline, ej. \'{"approved": true, "by": "ed"}\'')
    rs.set_defaults(fn=cmd_resume)

    cs = sub.add_parser("cases", help="lista los casos de prueba replayables (el catálogo del viewer)")
    cs.add_argument("--check", action="store_true", help="además, replayea cada caso y verifica su golden")
    cs.set_defaults(fn=cmd_cases)

    g = sub.add_parser("graph", help="serializa el sistema a grafo (mermaid|json)")
    g.add_argument("--format", choices=["mermaid", "json"], default="mermaid")
    g.set_defaults(fn=cmd_graph)

    cr = sub.add_parser("create", help="scaffold determinista (tool|capability|agent|connector)")
    cr.add_argument("kind", choices=["tool", "capability", "agent", "connector"])
    cr.add_argument("id")
    cr.add_argument("--description", default="", help="descripción de la unidad")
    cr.add_argument(
        "--side-effect",
        dest="side_effect",
        default="pure",
        choices=["pure", "read", "outward"],
        help="pure | read (lee externo) | outward (muta — nace approval_required)",
    )
    cr.set_defaults(fn=cmd_create)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
