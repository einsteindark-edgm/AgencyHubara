"""G-CONTRACT — el contrato I/O a nivel AGENTE (`contract: {inputs, outputs}`, ext key).

La regla de oro (L-12): ningún campo nuevo del manifest sin su check. `contract:`
es lo que habilita conectar/desconectar nodos desde el explorer validando
compatibilidad (como las tools ya lo hacen con `tool.yaml` inputs/outputs):

- el wiring `inputs:` de un miembro solo puede nombrar inputs DECLARADOS del
  contrato del agente referenciado (espejo de G-BIND with↔tool.yaml),
- y debe cablear TODOS los inputs `required` del contrato.

Negativo primero: se fabrica el manifest roto y el check lo caza. Un agente sin
`contract:` queda fuera del alcance del check (backwards-compat: los manifests
existentes no se rompen por no declarar).
"""
from __future__ import annotations

from pathlib import Path

from sdk.manifest_model import EXT_KEYS, load_manifest, native_subset
from sdk.testkit.checks import check_agent_contract_wiring, run_checks

MEMBER = """\
name: miembro
archetype: extractor
contract:
  inputs:
    payload: {type: object, required: true, description: "el JSON crudo"}
    modo: {type: string, required: false}
  outputs:
    sales: {type: array}
"""

SUP_TMPL = """\
name: pod
archetype: supervisor
strategy: sequential
agents:
  - uses: agent://miembro@1
    inputs:
{inputs}
"""


def _ga_root(tmp_path: Path, sup_inputs: str, member: str = MEMBER) -> Path:
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests" / "miembro.agent.yaml").write_text(member, encoding="utf-8")
    (tmp_path / "manifests" / "pod.taskgraph.yaml").write_text(
        SUP_TMPL.format(inputs=sup_inputs), encoding="utf-8"
    )
    return tmp_path


def _sup(tmp_path: Path):
    return load_manifest(tmp_path / "manifests" / "pod.taskgraph.yaml")


def test_contract_parsea_y_es_ext_key(tmp_path):
    """El contrato parsea tipado (FieldSpec) y NO se filtra a `agentspan deploy`."""
    root = _ga_root(tmp_path, "      payload: $state.raw")
    m = load_manifest(root / "manifests" / "miembro.agent.yaml")
    assert m.contract is not None
    assert m.contract.inputs["payload"].type == "object"
    assert m.contract.inputs["payload"].required is True
    assert m.contract.inputs["modo"].required is False
    assert m.contract.outputs["sales"].type == "array"
    assert "contract" in EXT_KEYS
    assert "contract" not in native_subset(m)


def test_wiring_con_clave_desconocida_rompe(tmp_path):
    """NEGATIVO: el wiring nombra un input que el contrato del agente NO declara."""
    root = _ga_root(tmp_path, "      nope: $state.x\n      payload: $state.raw")
    errs = check_agent_contract_wiring(_sup(root), root)
    assert any("G-CONTRACT" in e and "nope" in e for e in errs), errs


def test_wiring_sin_required_rompe(tmp_path):
    """NEGATIVO: el wiring omite un input `required` del contrato."""
    root = _ga_root(tmp_path, "      modo: $state.x")  # falta `payload` (required)
    errs = check_agent_contract_wiring(_sup(root), root)
    assert any("G-CONTRACT" in e and "payload" in e for e in errs), errs


def test_wiring_correcto_pasa(tmp_path):
    root = _ga_root(tmp_path, "      payload: $state.raw\n      modo: $state.m")
    assert check_agent_contract_wiring(_sup(root), root) == []


def test_router_no_exige_wiring_de_required(tmp_path):
    """Un supervisor router/manual despacha el input CRUDO (G-WIRE lo exime de wiring):
    G-CONTRACT no puede exigirle cablear los required del contrato del miembro."""
    root = _ga_root(tmp_path, "")  # sin inputs
    sup = (tmp_path / "manifests" / "pod.taskgraph.yaml")
    sup.write_text(
        "name: pod\narchetype: supervisor\nstrategy: router\nagents:\n  - uses: agent://miembro@1\n",
        encoding="utf-8",
    )
    assert check_agent_contract_wiring(_sup(root), root) == []


def test_agente_sin_contract_no_opina(tmp_path):
    """Backwards-compat: sin `contract:` declarado, el check no exige nada."""
    member = "name: miembro\narchetype: extractor\n"
    root = _ga_root(tmp_path, "      lo-que-sea: $state.x", member=member)
    assert check_agent_contract_wiring(_sup(root), root) == []


def test_run_checks_incluye_g_contract(tmp_path):
    """El check corre en la cert (run_checks) — no es una etiqueta suelta (L-19)."""
    root = _ga_root(tmp_path, "      nope: $state.x\n      payload: $state.raw")
    res = run_checks(_sup(root), root)
    assert any("G-CONTRACT" in e for e in res["errors"]), res


def test_graph_expone_contratos(tmp_path):
    """El grafo del explorer lleva el contrato de agentes y tools (la UI valida con eso)."""
    from sdk.graph import build_graph

    root = _ga_root(tmp_path, "      payload: $state.raw")
    g = build_graph(root)
    agent = next(n for n in g["nodes"] if n["id"] == "agent:miembro")
    assert agent["contract"]["inputs"]["payload"]["type"] == "object"
    assert agent["contract"]["outputs"]["sales"]["type"] == "array"
    sup = next(n for n in g["nodes"] if n["id"] == "agent:pod")
    assert sup["contract"] is None  # sin declarar → None honesto, no {} fabricado
