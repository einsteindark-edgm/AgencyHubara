"""`decisions check` / `decisions schema`: el certificador de paquetes de
decisión en la terminal (PAQUETES_DE_DECISION.md §6, docs/_sdk/17).

`check` sin rutas certifica todos los paquetes del repo
(`src/plugins/**/decisions/bundles/<paquete>/bundle.yaml`); el catálogo de
builtins por defecto es el `builtins.yaml` junto a la carpeta del paquete.
"""
from __future__ import annotations

import json
from pathlib import Path

CAPABILITY_SCHEMA = "decision-capability.schema.json"
BUNDLE_SCHEMA = "decision-bundle.schema.json"
TURN_SCHEMA = "decision-turn.schema.json"


def discover_bundles(repo_root: Path) -> list[Path]:
    plugins = repo_root / "hubara_agency" / "src" / "plugins"
    return sorted(p.parent for p in plugins.glob("**/decisions/bundles/*/bundle.yaml"))


def cmd_decisions_check(args, repo_root: Path) -> int:  # noqa: ANN001 — argparse.Namespace
    from src.sdk.decisionkit import check_bundle

    bundles = [Path(p) for p in args.bundles] or discover_bundles(repo_root)
    if not bundles:
        print("decisions check: no hay paquetes")
        return 1
    failed = 0
    for bundle_dir in bundles:
        catalog = Path(args.catalog) if args.catalog else bundle_dir.parent / "builtins.yaml"
        diagnostics = check_bundle(bundle_dir, catalog)
        name = _bundle_name(bundle_dir)
        if diagnostics:
            failed += 1
            print(f"FALLA {name}")
            for d in diagnostics:
                print(f"  {d}")
        else:
            count = len(list((bundle_dir / "capabilities").glob("*.yaml")))
            turn = " y su turno" if (bundle_dir / "turn.yaml").is_file() else ""
            print(f"OK {name} ({count} {'capacidad' if count == 1 else 'capacidades'}{turn})")
    return 1 if failed else 0


def cmd_decisions_schema(args) -> int:  # noqa: ANN001
    from src.sdk.decisionkit import Bundle, Capability, Turn

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for filename, model in ((CAPABILITY_SCHEMA, Capability), (BUNDLE_SCHEMA, Bundle), (TURN_SCHEMA, Turn)):
        schema = model.model_json_schema(by_alias=True)
        (out / filename).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"escrito {out / filename}")
    return 0


def _bundle_name(bundle_dir: Path) -> str:
    try:
        import yaml

        head = yaml.safe_load((bundle_dir / "bundle.yaml").read_text(encoding="utf-8")) or {}
        return f"{head.get('id', bundle_dir.name)}@{head.get('version', '?')}"
    except Exception:  # noqa: BLE001 — el nombre es solo para el reporte
        return bundle_dir.name
