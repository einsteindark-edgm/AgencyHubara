"""CLI ``hubara`` — el ciclo de vida del plugin system hecho comandos.

Invocación (módulo, sin [project.scripts] — el proyecto raíz no es package
instalable; ADR-2026-06-12 §6)::

    cd hubara_agency && uv run python -m src.sdk.cli <verbo>

Verbos:

- ``check [<id>...]``    — el "compilador" rápido: corre el TCK estático y
  imprime violaciones estilo rustc (error[P-x] + fix + ref). Exit 1 si falla.
- ``certify [<id>...|--all]`` — check + escribe los reportes JSON de
  certificación (`.hubara/certification/`) + tabla de niveles. Exit 1 si
  algún plugin < C2.
- ``explain <código>``   — imprime el diagnóstico completo de un código.
- ``graph``              — el grafo del sistema derivado de los manifests
  (deps + transitions), en mermaid o JSON.
- ``create plugin <id> --archetype <a>`` — scaffold completo que NACE C2
  (genera desde los perfiles — INV-5).
- ``package plan|build|inspect|plan-install|install`` — export/install de
  plugins entre repos Hubara-shaped (formato acktospkg/1 — docs/_sdk/13).

Diseñado para humanos Y para los skills del pipeline (salida estable,
exit codes claros: 0 ok · 1 violaciones · 2 uso inválido).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _all_plugin_ids() -> list[str]:
    from src.platform.plugin_manifest import all_manifests

    return [pid for pid, _ in all_manifests()]


# ---------------------------------------------------------------------------
# check / certify
# ---------------------------------------------------------------------------

def _render_report(report, *, verbose_warnings: bool = True) -> str:
    from src.sdk.diagnostics import format_diagnostic

    lines: list[str] = []
    for r in report.failed:
        lines.append(format_diagnostic(r.code, r.detail, location=r.location))
        lines.append("")
    if verbose_warnings:
        for r in report.warnings:
            lines.append(f"warning[{r.code}]: {r.detail}")
    return "\n".join(lines).rstrip()


def _resolve_plugin_ids(requested: list[str]) -> list[str]:
    """Valida ids ANTES de correr nada — typo = error limpio, no traceback."""
    known = _all_plugin_ids()
    if not requested:
        return known
    unknown = sorted(set(requested) - set(known))
    if unknown:
        raise SystemExit(
            f"error: plugin(s) inexistente(s): {', '.join(unknown)}. "
            f"Conocidos: {', '.join(known)}"
        )
    return requested


def cmd_check(args: argparse.Namespace) -> int:
    from src.sdk.testkit import run_conformance

    plugin_ids = _resolve_plugin_ids(args.plugins)
    failed_any = False
    for pid in plugin_ids:
        report = run_conformance(pid)
        status = "✓" if not report.failed else "✗"
        warn_note = f" ({len(report.warnings)} warning/s)" if report.warnings else ""
        print(f"{status} {pid} [{report.archetype}] → {report.level}{warn_note}")
        body = _render_report(report, verbose_warnings=args.verbose)
        if body:
            print(body)
            print()
        failed_any |= bool(report.failed)
    if failed_any:
        print("check: FAIL — corré `... explain <código>` para el detalle de una regla")
        return 1
    print(f"check: OK ({len(plugin_ids)} plugin/s)")
    return 0


def cmd_certify(args: argparse.Namespace) -> int:
    from src.sdk.testkit import run_conformance, write_report

    if args.all and args.plugins:
        print("certify: --all es incompatible con una lista de ids", file=sys.stderr)
        return 2
    plugin_ids = _resolve_plugin_ids(args.plugins)
    rows: list[tuple[str, str, str, int, int, str]] = []
    worst_fail = False
    for pid in plugin_ids:
        report = run_conformance(pid)
        path = write_report(report)
        rows.append(
            (
                pid,
                report.archetype or "—",
                report.level,
                len(report.failed),
                len(report.warnings),
                str(path.relative_to(_repo_root())),
            )
        )
        worst_fail |= report.level != "C2"
        if report.failed and args.verbose:
            print(_render_report(report))
    width = max(len(r[0]) for r in rows)
    print(f"{'plugin':<{width}}  arquetipo    nivel  fail  warn  reporte")
    for pid, arch, level, nfail, nwarn, path in rows:
        print(f"{pid:<{width}}  {arch:<11}  {level:<5}  {nfail:<4}  {nwarn:<4}  {path}")
    if worst_fail:
        print("\ncertify: FAIL — hay plugins < C2 (el merge gate los bloquea)")
        return 1
    print("\ncertify: OK — todos C2")
    return 0


# ---------------------------------------------------------------------------
# explain / graph / create
# ---------------------------------------------------------------------------

def cmd_explain(args: argparse.Namespace) -> int:
    from src.sdk.diagnostics import UnknownDiagnosticError, format_diagnostic

    try:
        print(format_diagnostic(args.code))
        return 0
    except UnknownDiagnosticError as exc:
        print(f"explain: {exc}", file=sys.stderr)
        return 2


def cmd_graph(args: argparse.Namespace) -> int:
    from src.platform.plugin_manifest import all_manifests
    from src.sdk.manifest_model import parse_manifest

    nodes: list[dict] = []
    edges: list[dict] = []
    for pid, raw in all_manifests():
        typed = parse_manifest(raw, source=pid)
        nodes.append({"id": pid, "archetype": typed.archetype})
        for dep in typed.depends_on:
            edges.append({"from": pid, "to": dep, "kind": "depends_on"})
        for w in typed.agent.workers if typed.agent else []:
            for t in w.transitions:
                target = t.action.target_plugin or pid
                if target != pid:
                    edges.append(
                        {"from": pid, "to": target, "kind": f"event:{t.on_event}"}
                    )
    if args.format == "json":
        print(json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2))
    else:
        print("graph LR")
        for n in nodes:
            print(f'  {n["id"]}["{n["id"]} ({n["archetype"]})"]')
        seen: set[tuple[str, str, str]] = set()
        for e in edges:
            key = (e["from"], e["to"], e["kind"])
            if key in seen:
                continue
            seen.add(key)
            arrow = "-.->" if e["kind"] == "depends_on" else "-->"
            print(f'  {e["from"]} {arrow}|{e["kind"]}| {e["to"]}')
    return 0


def cmd_create_plugin(args: argparse.Namespace) -> int:
    from src.sdk.cli.scaffold import ScaffoldError, create_plugin
    from src.sdk.testkit import run_conformance

    try:
        created = create_plugin(
            args.id, args.archetype, display_name=args.display_name
        )
    except ScaffoldError as exc:
        print(f"create: {exc}", file=sys.stderr)
        return 2
    root = _repo_root()
    print(f"create: {len(created)} archivos generados:")
    for path in created:
        print(f"  + {path.relative_to(root)}")
    report = run_conformance(args.id)
    print(f"\nTCK del recién nacido: nivel {report.level}", end="")
    print(" ✓ (nace certificado)" if report.level == "C2" else " ✗")
    if report.failed:
        print(_render_report(report))
        return 1
    print(
        "\nPróximos pasos:\n"
        "  1. cd frontend_dashboard && npm run plugins:sync   (registra el frontend)\n"
        "  2. completá domain/ + entities (el Page de ejemplo es un placeholder)\n"
        "  3. cd hubara_agency && uv run python -m src.sdk.cli certify "
        f"{args.id}\n"
        "  (workers Temporal: F-SDK-3b — por ahora seguí docs/_sdk/05-arquetipos.md)"
    )
    return 0


# ---------------------------------------------------------------------------
# package — export/install de plugins entre repos Hubara-shaped (acktospkg/1)
# ---------------------------------------------------------------------------

def _unit_requires_json(unit) -> dict:
    return {
        "plugins": list(unit.depends_on),
        "env_vars": list(unit.env_vars),
        "secrets": list(unit.secrets),
    }


def cmd_package_plan(args: argparse.Namespace) -> int:
    from src.sdk.packaging import PackagingError, plan_export

    try:
        plan = plan_export(args.plugins, repo_root=Path(args.repo))
    except PackagingError as exc:
        print(f"package plan: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "requested": list(plan.requested),
                    "units": [
                        {
                            "kind": "plugin",
                            "id": u.plugin_id,
                            "version": u.version,
                            "archetype": u.archetype,
                            "requires": _unit_requires_json(u),
                        }
                        for u in plan.units
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"plan de export ({', '.join(plan.requested)}):")
    for u in plan.units:
        extra = " (dependencia)" if u.plugin_id not in plan.requested else ""
        print(f"  + {u.plugin_id} [{u.archetype}] v{u.version}{extra}")
        if u.env_vars:
            print(f"      env vars: {', '.join(u.env_vars)}")
        if u.secrets:
            print(f"      secrets:  {', '.join(u.secrets)}")
    return 0


def cmd_package_build(args: argparse.Namespace) -> int:
    from src.sdk.packaging import PackagingError, build_package, plan_export

    try:
        plan = plan_export(args.plugins, repo_root=Path(args.repo))
        if args.units:
            plan = plan.only([s.strip() for s in args.units.split(",") if s.strip()])
        pkg = build_package(
            plan,
            repo_root=Path(args.repo),
            out_path=Path(args.out),
            name=args.name,
            staging_dir=Path(args.staging) if args.staging else None,
        )
    except PackagingError as exc:
        print(f"package build: {exc}", file=sys.stderr)
        return 2
    print(f"package build: {pkg} ({len(plan.units)} unidad/es plugin)")
    return 0


def cmd_package_inspect(args: argparse.Namespace) -> int:
    from src.sdk.packaging import PackagingError, read_package

    try:
        info = read_package(Path(args.package))
    except PackagingError as exc:
        print(f"package inspect: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "format": info.format,
                    "name": info.name,
                    "source": info.source,
                    "units": [
                        {
                            "kind": u.kind,
                            "id": u.unit_id,
                            "version": u.version,
                            "archetype": u.archetype,
                            "dir": u.dir,
                            "requires": {
                                "plugins": list(u.requires_plugins),
                                "env_vars": list(u.requires_env_vars),
                                "secrets": list(u.requires_secrets),
                            },
                        }
                        for u in info.units
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"{info.name} [{info.format}] — commit {info.source.get('commit') or '?'}")
    for u in info.units:
        print(f"  {u.kind}:{u.unit_id} v{u.version} [{u.archetype}]")
    return 0


def cmd_package_plan_install(args: argparse.Namespace) -> int:
    from src.sdk.packaging import PackagingError, plan_install

    try:
        plan = plan_install(Path(args.package), repo_root=Path(args.repo))
    except PackagingError as exc:
        print(f"package plan-install: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "units": [
                        {
                            "id": u.unit_id,
                            "kind": u.kind,
                            "action": u.action,
                            "version": u.version,
                            "target_version": u.target_version,
                            "downgrade": u.downgrade,
                        }
                        for u in plan.units
                    ],
                    "missing_plugins": list(plan.missing_plugins),
                    "post_steps": list(plan.post_steps),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    for u in plan.units:
        upgrade = f"  ({u.target_version} → {u.version})" if u.target_version else ""
        flag = "  ⚠ DOWNGRADE" if u.downgrade else ""
        print(f"  {u.action:<9} {u.kind}:{u.unit_id}{upgrade}{flag}")
    if plan.missing_plugins:
        print(f"  ⚠ dependencias faltantes en el destino: {', '.join(plan.missing_plugins)}")
    print("pasos post-install:")
    for step in plan.post_steps:
        print(f"  · {step}")
    return 0


def cmd_package_install(args: argparse.Namespace) -> int:
    from src.sdk.packaging import PackagingError, install_package

    only = (
        [s.strip() for s in args.units.split(",") if s.strip()] if args.units else None
    )
    try:
        result = install_package(
            Path(args.package), repo_root=Path(args.repo), units=only
        )
    except PackagingError as exc:
        print(f"package install: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "installed": list(result.installed),
                    "replaced": list(result.replaced),
                    "skipped": list(result.skipped),
                    "written": len(result.written),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    for pid in result.installed:
        print(f"  + instalado {pid}")
    for pid in result.replaced:
        print(f"  ~ reemplazado {pid}")
    for pid in result.skipped:
        print(f"  - salteado {pid}")
    print(f"package install: OK ({len(result.written)} archivos)")
    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hubara",
        description="Platform SDK CLI — check/certify/explain/graph/create "
        "(docs/_sdk/06-cli.md)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="el compilador rápido (TCK estático)")
    p_check.add_argument("plugins", nargs="*", help="ids (default: todos)")
    p_check.add_argument("-v", "--verbose", action="store_true", help="muestra warnings")
    p_check.set_defaults(fn=cmd_check)

    p_cert = sub.add_parser("certify", help="check + reportes JSON + niveles")
    p_cert.add_argument("plugins", nargs="*", help="ids (default: todos)")
    p_cert.add_argument("--all", action="store_true", help="(explícito) todos")
    p_cert.add_argument("-v", "--verbose", action="store_true")
    p_cert.set_defaults(fn=cmd_certify)

    p_explain = sub.add_parser("explain", help="diagnóstico completo de un código")
    p_explain.add_argument("code", help="ej. P-27, C1-DEPS")
    p_explain.set_defaults(fn=cmd_explain)

    p_graph = sub.add_parser("graph", help="grafo del sistema desde los manifests")
    p_graph.add_argument("--format", choices=("mermaid", "json"), default="mermaid")
    p_graph.set_defaults(fn=cmd_graph)

    p_create = sub.add_parser("create", help="scaffolds que nacen certificados")
    create_sub = p_create.add_subparsers(dest="what", required=True)
    p_cp = create_sub.add_parser("plugin", help="plugin nuevo desde un arquetipo")
    p_cp.add_argument("id", help="plugin id (^[a-z][a-z0-9_]*$)")
    p_cp.add_argument(
        "--archetype",
        required=True,
        choices=("api_only", "full_stack", "agentic", "notifier", "sync"),
    )
    p_cp.add_argument("--display-name", default=None)
    p_cp.set_defaults(fn=cmd_create_plugin)

    p_pkg = sub.add_parser(
        "package", help="export/install de plugins entre repos (acktospkg/1)"
    )
    pkg_sub = p_pkg.add_subparsers(dest="pkg_command", required=True)

    def _add_repo(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--repo",
            default=str(_repo_root()),
            help="repo root Hubara-shaped (default: este repo)",
        )

    p_pp = pkg_sub.add_parser("plan", help="clausura depends_on + requirements")
    p_pp.add_argument("plugins", nargs="+", help="ids a exportar")
    p_pp.add_argument("--json", action="store_true")
    _add_repo(p_pp)
    p_pp.set_defaults(fn=cmd_package_plan)

    p_pb = pkg_sub.add_parser("build", help="stagea la clausura y sella el .acktospkg")
    p_pb.add_argument("plugins", nargs="+", help="ids a exportar")
    p_pb.add_argument("-o", "--out", required=True, help="path del .acktospkg")
    p_pb.add_argument("--name", default=None, help="nombre del paquete")
    p_pb.add_argument(
        "--units", default=None, help="recorte de la clausura (ids separados por coma)"
    )
    p_pb.add_argument(
        "--staging",
        default=None,
        help="staging dir persistente — conserva unidades foráneas pre-stageadas "
        "(p.ej. graphagent-* del CLI de GraphAgents) y las sella en el paquete",
    )
    _add_repo(p_pb)
    p_pb.set_defaults(fn=cmd_package_build)

    p_pi = pkg_sub.add_parser("inspect", help="package.yaml + verificación de integridad")
    p_pi.add_argument("package", help="path al .acktospkg")
    p_pi.add_argument("--json", action="store_true")
    p_pi.set_defaults(fn=cmd_package_inspect)

    p_ppi = pkg_sub.add_parser(
        "plan-install", help="clasifica unidades contra el destino (new/overwrite)"
    )
    p_ppi.add_argument("package", help="path al .acktospkg")
    p_ppi.add_argument("--json", action="store_true")
    _add_repo(p_ppi)
    p_ppi.set_defaults(fn=cmd_package_plan_install)

    p_pin = pkg_sub.add_parser(
        "install", help="escribe las unidades plugin en sus paths single-owner"
    )
    p_pin.add_argument("package", help="path al .acktospkg")
    p_pin.add_argument(
        "--units", default=None, help="instalar solo estos ids (separados por coma)"
    )
    p_pin.add_argument("--json", action="store_true")
    _add_repo(p_pin)
    p_pin.set_defaults(fn=cmd_package_install)

    args = parser.parse_args(argv)
    return args.fn(args)
