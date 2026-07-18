"""Comportamiento del empaquetador de graph agents (acktospkg/1 — F2).

Contrato exigido:

- ``plan_export`` resuelve la clausura de un agente: manifest + capability
  (``graphs.<mod>:build`` → archivo), tools por ``uses: <id>@<major>``
  (dir ``tools/<id_undescored>/``), tests (golden/build/tool) y los fixtures
  que esos tests referencian. Un taskgraph arrastra sus ``agent://`` refs
  como unidades propias (deps primero).
- ``build_package`` produce el MISMO formato acktospkg/1 que el CLI hubara
  (package.yaml + units/ + checksums) con unidades kind=graphagent, y
  ``stage_units`` permite pre-stagear en un staging compartido (F4).
- ``plan_install`` clasifica new/overwrite contra un GraphAgents root
  destino; ``install_package`` copia los archivos file-level (graphs/ y
  manifests/ son dirs compartidos — acá NO se reemplaza el dir entero,
  solo los paths de la unidad; tools/<id>/ sí se reemplaza completo).
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from sdk.packaging import (
    build_package,
    install_package,
    plan_export,
    plan_install,
    read_package,
)

REAL_GA_ROOT = Path(__file__).resolve().parents[2]


def _mini_ga(tmp_path: Path) -> Path:
    root = tmp_path / "ga_origen"
    (root / "manifests").mkdir(parents=True)
    (root / "graphs").mkdir()
    (root / "tools" / "my_tool").mkdir(parents=True)
    (root / "tests" / "graphs").mkdir(parents=True)
    (root / "tests" / "tools").mkdir(parents=True)
    (root / "fixtures").mkdir()

    (root / "manifests" / "scout.agent.yaml").write_text(
        textwrap.dedent(
            """\
            name: scout
            description: agente de prueba
            archetype: analyzer
            certification: C2
            capability: graphs.scout:build
            tools:
              - uses: my-tool@1
                with: {payload: $state.payload}
            consumes:
              - llm
            """
        ),
        encoding="utf-8",
    )
    (root / "manifests" / "team.taskgraph.yaml").write_text(
        textwrap.dedent(
            """\
            name: team
            description: equipo de prueba
            archetype: supervisor
            strategy: sequential
            certification: C2
            agents:
              - uses: agent://scout@1
            """
        ),
        encoding="utf-8",
    )
    (root / "graphs" / "scout.py").write_text(
        "def build(*, llm=None):\n    return None\n", encoding="utf-8"
    )
    (root / "tools" / "my_tool" / "tool.yaml").write_text(
        "id: my-tool\nversion: 1.0.0\nside_effect: pure\nimpl: tools.my_tool.impl:run\n",
        encoding="utf-8",
    )
    (root / "tools" / "my_tool" / "impl.py").write_text(
        "def run(payload):\n    return {}\n", encoding="utf-8"
    )
    (root / "tests" / "graphs" / "test_scout_golden.py").write_text(
        'GA = None\nFIX = "fixtures/scout_snapshot.json"\n', encoding="utf-8"
    )
    (root / "tests" / "tools" / "test_my_tool.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (root / "fixtures" / "scout_snapshot.json").write_text("{}\n", encoding="utf-8")
    return root


def _target_ga(tmp_path: Path, name: str = "ga_destino") -> Path:
    root = tmp_path / name
    for rel in ("manifests", "graphs", "tools", "tests/graphs", "fixtures"):
        (root / rel).mkdir(parents=True)
    return root


def test_plan_export_clausura_de_un_agente(tmp_path: Path) -> None:
    root = _mini_ga(tmp_path)
    plan = plan_export(["scout"], ga_root=root)

    (unit,) = plan.units
    assert unit.agent_id == "scout"
    assert unit.kind_file == "agent"
    rels = {f.as_posix() for f in unit.files}
    assert "manifests/scout.agent.yaml" in rels
    assert "graphs/scout.py" in rels
    assert "tests/graphs/test_scout_golden.py" in rels
    assert "fixtures/scout_snapshot.json" in rels, "fixture referenciado por el golden"
    assert "tests/tools/test_my_tool.py" in rels
    assert unit.tool_dirs == ("tools/my_tool",)
    assert unit.ports == ("llm",)


def test_plan_export_taskgraph_arrastra_sus_agentes(tmp_path: Path) -> None:
    root = _mini_ga(tmp_path)
    plan = plan_export(["team"], ga_root=root)
    ids = [u.agent_id for u in plan.units]
    assert ids == ["scout", "team"], "deps (agent:// refs) primero"
    team = plan.units[-1]
    assert team.kind_file == "taskgraph"
    assert {f.as_posix() for f in team.files} == {"manifests/team.taskgraph.yaml"}


def test_plan_export_id_inexistente_falla_limpio(tmp_path: Path) -> None:
    root = _mini_ga(tmp_path)
    try:
        plan_export(["nope"], ga_root=root)
    except Exception as exc:  # noqa: BLE001
        assert "nope" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("agente inexistente debe fallar con mensaje claro")


def test_build_e_install_roundtrip(tmp_path: Path) -> None:
    origen = _mini_ga(tmp_path)
    out = tmp_path / "team.acktospkg"
    build_package(plan_export(["team"], ga_root=origen), ga_root=origen, out_path=out)

    info = read_package(out)
    assert info.format == "acktospkg/1"
    assert sorted(u.unit_id for u in info.units) == ["scout", "team"]
    assert all(u.kind == "graphagent" for u in info.units)

    destino = _target_ga(tmp_path)
    iplan = plan_install(out, ga_root=destino)
    assert {u.unit_id: u.action for u in iplan.units} == {"scout": "new", "team": "new"}

    result = install_package(out, ga_root=destino)
    assert (destino / "manifests" / "scout.agent.yaml").exists()
    assert (destino / "manifests" / "team.taskgraph.yaml").exists()
    assert (destino / "graphs" / "scout.py").exists()
    assert (destino / "tools" / "my_tool" / "tool.yaml").exists()
    assert (destino / "fixtures" / "scout_snapshot.json").exists()
    assert sorted(result.installed) == ["scout", "team"]

    # re-plan tras instalar: contenido idéntico = unchanged (idempotencia)
    vecino = destino / "graphs" / "otro.py"
    vecino.write_text("# de otro agente\n", encoding="utf-8")
    iplan2 = plan_install(out, ga_root=destino)
    assert {u.unit_id: u.action for u in iplan2.units} == {
        "scout": "unchanged",
        "team": "unchanged",
    }
    # divergir el graph instalado → overwrite real, que NO arrasa vecinos
    (destino / "graphs" / "scout.py").write_text("# divergido\n", encoding="utf-8")
    iplan3 = plan_install(out, ga_root=destino)
    assert {u.unit_id: u.action for u in iplan3.units}["scout"] == "overwrite"
    install_package(out, ga_root=destino)
    assert vecino.exists(), "install file-level: los dirs compartidos no se arrasan"


def test_plan_export_incluye_cases_del_viewer(tmp_path: Path) -> None:
    """Los ⚡ cases replayables (fixtures/cases/) viajan con su agente — sin
    ellos el agente instalado queda mudo en el catálogo de Studio."""
    root = _mini_ga(tmp_path)
    (root / "fixtures" / "cases").mkdir()
    (root / "fixtures" / "cases" / "scout-basico.case.yaml").write_text(
        textwrap.dedent(
            """\
            id: scout-basico
            title: scout básico
            target: agent:scout
            seed:
              payload: { $ref: fixtures/scout_snapshot.json }
            golden: { $ref: fixtures/cases/scout-basico.golden.json }
            """
        ),
        encoding="utf-8",
    )
    (root / "fixtures" / "cases" / "scout-basico.golden.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (root / "fixtures" / "cases" / "de-otro.case.yaml").write_text(
        "id: de-otro\ntarget: agent:otro\n", encoding="utf-8"
    )

    plan = plan_export(["scout"], ga_root=root)
    (unit,) = plan.units
    rels = {f.as_posix() for f in unit.files}
    assert "fixtures/cases/scout-basico.case.yaml" in rels
    assert "fixtures/cases/scout-basico.golden.json" in rels, "$ref del golden"
    assert "fixtures/scout_snapshot.json" in rels, "$ref del seed"
    assert "fixtures/cases/de-otro.case.yaml" not in rels, "cases ajenos NO viajan"


def test_plan_export_taskgraph_incluye_cases_flow(tmp_path: Path) -> None:
    root = _mini_ga(tmp_path)
    (root / "fixtures" / "cases").mkdir()
    (root / "fixtures" / "cases" / "team-flujo.case.yaml").write_text(
        "id: team-flujo\ntarget: flow:team\nseed: {}\n", encoding="utf-8"
    )
    plan = plan_export(["team"], ga_root=root)
    team = next(u for u in plan.units if u.agent_id == "team")
    assert "fixtures/cases/team-flujo.case.yaml" in {f.as_posix() for f in team.files}


def test_version_del_manifest_viaja(tmp_path: Path) -> None:
    """`version:` opcional del manifest = la versión de release del agente."""
    root = _mini_ga(tmp_path)
    manifest = root / "manifests" / "scout.agent.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "version: 1.2.0\n", encoding="utf-8"
    )
    out = tmp_path / "scout.acktospkg"
    build_package(plan_export(["scout"], ga_root=root), ga_root=root, out_path=out)
    unit = next(u for u in read_package(out).units if u.unit_id == "scout")
    assert unit.version == "1.2.0"
    assert unit.fingerprint and len(unit.fingerprint) >= 12


def test_unchanged_bump_pending_y_ledger(tmp_path: Path) -> None:
    origen = _mini_ga(tmp_path)
    out1 = tmp_path / "r1.acktospkg"
    build_package(
        plan_export(["scout"], ga_root=origen),
        ga_root=origen,
        out_path=out1,
        name="scout-pack",
    )
    destino = _target_ga(tmp_path)
    r1 = install_package(out1, ga_root=destino)
    assert r1.installed == ("scout",)

    # ledger del destino con la entrada del install
    import yaml

    ledger_path = destino / "installed-packages.yaml"
    ledger = yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
    (entry,) = ledger["installs"]
    assert entry["unit"] == "scout" and entry["kind"] == "graphagent"
    assert entry["package"] == "scout-pack" and entry["fingerprint"]

    # reinstalar lo MISMO = no-op declarado, sin duplicar ledger
    plan = plan_install(out1, ga_root=destino)
    assert {u.unit_id: u.action for u in plan.units} == {"scout": "unchanged"}
    r2 = install_package(out1, ga_root=destino)
    assert r2.skipped_unchanged == ("scout",) and r2.installed == ()
    assert len(yaml.safe_load(ledger_path.read_text(encoding="utf-8"))["installs"]) == 1

    # mejora en el origen SIN bump de versión → overwrite + bump_pending
    graph = origen / "graphs" / "scout.py"
    graph.write_text(graph.read_text(encoding="utf-8") + "# mejora\n", encoding="utf-8")
    out2 = tmp_path / "r2.acktospkg"
    build_package(plan_export(["scout"], ga_root=origen), ga_root=origen, out_path=out2)
    plan2 = plan_install(out2, ga_root=destino)
    (scout,) = plan2.units
    assert scout.action == "overwrite" and scout.bump_pending is True


def test_plan_export_order_sentinel_repo_real() -> None:
    plan = plan_export(["order-sentinel"], ga_root=REAL_GA_ROOT)
    (unit,) = plan.units
    rels = {f.as_posix() for f in unit.files}
    assert "manifests/order-sentinel.agent.yaml" in rels
    assert "graphs/order_sentinel.py" in rels
    assert "fixtures/order_sentinel_snapshot.json" in rels
    assert "fixtures/cases/order-sentinel-en-camino.case.yaml" in rels, (
        "los cases reales del viewer viajan"
    )
    assert unit.ports == ("llm",)
