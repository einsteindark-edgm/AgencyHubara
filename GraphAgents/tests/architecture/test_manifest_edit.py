"""`sdk.manifest_edit` — conectar/desconectar nodos del grafo EDITANDO los manifests
(la verdad sigue siendo el YAML, git-visible; el explorer es solo el frontend).

Contrato del módulo:
- la edición es TEXTUAL y quirúrgica: los comentarios del manifest (densos, load-bearing)
  SOBREVIVEN a un connect/disconnect;
- red de seguridad: tras editar se recarga el manifest y corren los checks (run_checks);
  si algo rompe → ROLLBACK (el archivo queda byte-idéntico) y el error se reporta;
- `validate_connection` es PURA (no escribe): es el gate de compatibilidad que la UI
  consulta antes de dibujar la línea (G-BIND / G-BIND-AGENT / G-CONTRACT / tipos).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sdk.manifest_edit import connect, delete_node, disconnect, validate_connection
from sdk.manifest_model import load_manifest

GA_ROOT = Path(__file__).resolve().parents[2]

SUP_A = """\
# supervisor de catálogo (fixture de ciclo) — rutea, no compone (sin wiring).
name: sup-a
archetype: supervisor
strategy: router
agents:
  - uses: agent://sup-b@1
"""
SUP_B = """\
name: sup-b
archetype: supervisor
strategy: router
agents:
  - uses: agent://greeter@1
"""
SOLO = """\
name: solo
archetype: supervisor
strategy: router
agents:
  - uses: agent://greeter@1
"""
TYPED_PRODUCER = """\
name: productor
archetype: extractor
contract:
  inputs:
    payload: {type: object}
  outputs:
    sales: {type: array}
"""
TYPED_CONSUMER = """\
name: consumidor
archetype: analyzer
contract:
  inputs:
    x: {type: object, required: true}
  outputs:
    y: {type: object}
"""
TYPED_POD = """\
name: pod-tipado
archetype: supervisor
strategy: sequential
agents:
  - uses: agent://productor@1
    inputs:
      payload: $state.raw
"""


@pytest.fixture()
def ga(tmp_path: Path) -> Path:
    """Una copia REAL del catálogo (manifests/ + tools/) para mutar sin miedo."""
    shutil.copytree(GA_ROOT / "manifests", tmp_path / "manifests")
    shutil.copytree(GA_ROOT / "tools", tmp_path / "tools")
    for name, text in (("sup-a.agent.yaml", SUP_A), ("sup-b.agent.yaml", SUP_B),
                       ("solo.taskgraph.yaml", SOLO), ("productor.agent.yaml", TYPED_PRODUCER),
                       ("consumidor.agent.yaml", TYPED_CONSUMER), ("pod-tipado.taskgraph.yaml", TYPED_POD)):
        (tmp_path / "manifests" / name).write_text(text, encoding="utf-8")
    return tmp_path


def _text(ga: Path, fname: str) -> str:
    return (ga / "manifests" / fname).read_text(encoding="utf-8")


# ------------------------------------------------------------------ agente → tool

def test_connect_tool_persiste_binding_y_preserva_comentarios(ga):
    before = _text(ga, "ctwa-insights.agent.yaml")
    res = connect(ga, "agent:ctwa-insights", "tool:diagnose", binding={"payload": "$state.payload"})
    assert res["ok"], res
    m = load_manifest(ga / "manifests" / "ctwa-insights.agent.yaml")
    assert any(t.ref_id == "diagnose" and t.binding == {"payload": "$state.payload"} for t in m.tools)
    after = _text(ga, "ctwa-insights.agent.yaml")
    for line in before.splitlines():  # ningún comentario original se pierde
        if line.strip().startswith("#"):
            assert line in after, f"se perdió el comentario: {line!r}"


def test_connect_tool_duplicada_rechaza_sin_tocar_el_archivo(ga):
    before = _text(ga, "ctwa-insights.agent.yaml")
    res = connect(ga, "agent:ctwa-insights", "tool:meta-ads-insights", binding={"payload": "$state.payload"})
    assert not res["ok"]
    assert any("ya" in e for e in res["errors"]), res
    assert _text(ga, "ctwa-insights.agent.yaml") == before


def test_connect_tool_binding_desconocido_rechaza(ga):
    """El `with:` nombra un input que el contrato de la tool NO declara → ni se escribe."""
    before = _text(ga, "ctwa-insights.agent.yaml")
    res = connect(ga, "agent:ctwa-insights", "tool:diagnose", binding={"nope": "$state.x"})
    assert not res["ok"]
    assert any("nope" in e for e in res["errors"]), res
    assert _text(ga, "ctwa-insights.agent.yaml") == before


def test_connect_tool_sin_required_rechaza(ga):
    """Falta cablear un input required del contrato de la tool."""
    res = connect(ga, "agent:ctwa-insights", "tool:diagnose", binding={})
    assert not res["ok"]
    assert any("payload" in e for e in res["errors"]), res


def test_disconnect_tool_remueve_el_binding(ga):
    res = disconnect(ga, "agent:ctwa-insights", "tool:meta-ads-insights", kind="uses")
    assert res["ok"], res
    m = load_manifest(ga / "manifests" / "ctwa-insights.agent.yaml")
    assert all(t.ref_id != "meta-ads-insights" for t in m.tools)
    # el header del archivo (comentarios de doc) sobrevive
    assert _text(ga, "ctwa-insights.agent.yaml").startswith("# Agente de catálogo")


def test_connect_disconnect_roundtrip_byte_identico(ga):
    """Conectar y desconectar la MISMA relación deja el archivo byte-idéntico
    (incluido el newline final — el residuo que ensucia el diff de git)."""
    before = _text(ga, "roas-cac.agent.yaml")
    assert connect(ga, "agent:roas-cac", "tool:diagnose", binding={"payload": "$state.payload"})["ok"]
    assert disconnect(ga, "agent:roas-cac", "tool:diagnose", kind="uses")["ok"]
    assert _text(ga, "roas-cac.agent.yaml") == before


# ---------------------------------------------------------------- agente → agente

def test_connect_agente_en_supervisor_compone_con_wiring(ga):
    res = connect(ga, "agent:ads-analytics", "agent:roas-cac",
                  inputs={"adsets": "$state.adsets", "total_budget": "$state.total_budget"})
    assert res["ok"], res
    m = load_manifest(ga / "manifests" / "ads-analytics.taskgraph.yaml")
    assert any(a.ref_agent_id == "roas-cac" for a in m.agents)
    assert m.agents[-1].inputs == {"adsets": "$state.adsets", "total_budget": "$state.total_budget"}


def test_connect_agente_sin_wiring_en_supervisor_que_compone_rechaza(ga):
    """G-WIRE en el gate: componer (sequential) exige inputs:."""
    res = connect(ga, "agent:ads-analytics", "agent:roas-cac")
    assert not res["ok"]
    assert any("inputs" in e for e in res["errors"]), res


def test_connect_agente_desde_no_supervisor_rechaza(ga):
    res = connect(ga, "agent:ctwa-insights", "agent:sales-ledger")
    assert not res["ok"]
    assert any("supervisor" in e for e in res["errors"]), res


def test_connect_ciclo_rechaza(ga):
    res = connect(ga, "agent:sup-b", "agent:sup-a")
    assert not res["ok"]
    assert any("ciclo" in e.lower() for e in res["errors"]), res


def test_connect_agente_a_router_sin_wiring_pasa(ga):
    """Un router despacha el input crudo: conectar un miembro con contrato required
    NO exige wiring (espejo de G-WIRE/G-CONTRACT)."""
    res = connect(ga, "agent:sup-a", "agent:consumidor")
    assert res["ok"], res
    m = load_manifest(ga / "manifests" / "sup-a.agent.yaml")
    assert any(a.ref_agent_id == "consumidor" for a in m.agents)


def test_disconnect_miembro_remueve_solo_ese(ga):
    res = disconnect(ga, "agent:ads-analytics", "agent:numbers-qa", kind="agent")
    assert res["ok"], res
    m = load_manifest(ga / "manifests" / "ads-analytics.taskgraph.yaml")
    ids = [a.ref_agent_id for a in m.agents]
    assert "numbers-qa" not in ids
    assert ids == ["ctwa-insights", "sales-ledger", "ctwa-campaign-funnel", "blended-economics", "ctwa-report"]


def test_disconnect_ultimo_miembro_rollback(ga):
    """Dejar un supervisor sin miembros rompe SUP → rollback byte-idéntico."""
    before = _text(ga, "solo.taskgraph.yaml")
    res = disconnect(ga, "agent:solo", "agent:greeter", kind="agent")
    assert not res["ok"]
    assert _text(ga, "solo.taskgraph.yaml") == before


def test_disconnect_port_no_editable(ga):
    res = disconnect(ga, "agent:ctwa-report", "port:llm", kind="consumes")
    assert not res["ok"]
    assert any("port" in e for e in res["errors"]), res


# ------------------------------------------------------- validate (puro, pre-línea)

def test_validate_tool_devuelve_contrato_y_sugerencia(ga):
    v = validate_connection(ga, "agent:roas-cac", "tool:diagnose")
    assert v["ok"], v
    assert v["kind"] == "uses"
    assert v["target_contract"]["inputs"]["payload"]["type"] == "object"
    assert v["suggested"] == {"payload": "$state.payload"}


def test_validate_no_escribe(ga):
    before = _text(ga, "roas-cac.agent.yaml")
    validate_connection(ga, "agent:roas-cac", "tool:diagnose")
    assert _text(ga, "roas-cac.agent.yaml") == before


def test_validate_tipos_incompatibles_rechaza(ga):
    """La entrada NO acepta la salida: el productor emite `sales: array`, el consumidor
    exige `x: object` — cablear x ← $state.sales es incompatible por contrato."""
    v = validate_connection(ga, "agent:pod-tipado", "agent:consumidor",
                            inputs={"x": "$state.sales"})
    assert not v["ok"]
    assert any("array" in e and "object" in e for e in v["errors"]), v


def test_validate_tipos_compatibles_pasa(ga):
    v = validate_connection(ga, "agent:pod-tipado", "agent:consumidor",
                            inputs={"x": "$state.raw"})
    # $state.raw no es producido por ningún miembro (viene del seed) → warning, no error
    assert v["ok"], v
    v2 = validate_connection(ga, "agent:pod-tipado", "agent:consumidor",
                             inputs={"x": "$state.y"})
    assert v2["ok"], v2  # nadie produce `y` todavía → seed-warning; no bloquea


def test_connect_tipos_incompatibles_no_escribe(ga):
    before = _text(ga, "pod-tipado.taskgraph.yaml")
    res = connect(ga, "agent:pod-tipado", "agent:consumidor", inputs={"x": "$state.sales"})
    assert not res["ok"]
    assert _text(ga, "pod-tipado.taskgraph.yaml") == before


# ------------------------------------------------------------- delete_node (borrar nodo)

def test_delete_tool_con_dependientes_pide_confirmacion_sin_tocar_nada(ga):
    """Borrar una tool que agentes USAN, sin cascade, NO borra: pide confirmación y
    enumera los dependientes (blast radius) para que la UI lo muestre."""
    res = delete_node(ga, "tool:meta-ads-insights")
    assert res["ok"] is False
    assert res["needs_confirmation"] is True
    assert set(res["dependents"]) == {"ctwa-insights", "ctwa-campaign-funnel"}
    assert res["deleted"] == [] and res["disconnected"] == []
    # nada se tocó: el dir de la tool y los manifests siguen intactos
    assert (ga / "tools" / "meta_ads_insights").is_dir()
    assert any(t.ref_id == "meta-ads-insights"
               for t in load_manifest(ga / "manifests" / "ctwa-insights.agent.yaml").tools)


def test_delete_tool_cascade_desconecta_dependientes_y_borra_el_dir(ga):
    res = delete_node(ga, "tool:meta-ads-insights", cascade=True)
    assert res["ok"] is True, res
    assert set(res["disconnected"]) == {"ctwa-insights", "ctwa-campaign-funnel"}
    # el directorio de la tool ya no existe → sale del catálogo
    assert not (ga / "tools" / "meta_ads_insights").exists()
    from sdk.registry import discover_tool_ids
    assert "meta-ads-insights" not in discover_tool_ids(ga)
    # ningún agente la referencia ya
    for a in ("ctwa-insights", "ctwa-campaign-funnel"):
        assert all(t.ref_id != "meta-ads-insights"
                   for t in load_manifest(ga / "manifests" / f"{a}.agent.yaml").tools)


def test_delete_agent_cascade_borra_manifest_y_desconecta_supervisores(ga):
    """greeter lo componen los fixtures sup-b y solo → cascade lo saca de ambos y
    borra su manifest del catálogo."""
    res = delete_node(ga, "agent:greeter", cascade=True)
    assert res["ok"] is True, res
    assert set(res["disconnected"]) == {"sup-b", "solo"}
    assert not (ga / "manifests" / "greeter.agent.yaml").exists()
    from sdk.registry import discover_agent_ids
    assert "greeter" not in discover_agent_ids(ga)


def test_delete_tool_sin_dependientes_borra_directo(ga):
    """Una tool que NO usa ningún agente → borra sin pedir confirmación (todas las tools
    del catálogo real tienen ≥1 dependiente, así que sembramos una huérfana)."""
    orphan = ga / "tools" / "orphan_tool"
    orphan.mkdir()
    (orphan / "tool.yaml").write_text(
        "id: orphan-tool\nversion: 1.0.0\ndescription: huérfana\nside_effect: pure\n"
        "idempotent: true\napproval_required: false\ncredentials: []\n"
        "inputs: {}\noutputs: {}\nimpl: tools.orphan_tool.impl:run\n",
        encoding="utf-8")
    res = delete_node(ga, "tool:orphan-tool")
    assert res["ok"] is True, res
    assert res["dependents"] == [] and res["disconnected"] == []
    assert not orphan.exists()


def test_delete_node_inexistente_reporta_error(ga):
    res = delete_node(ga, "tool:no-existe")
    assert res["ok"] is False and res["needs_confirmation"] is False
    assert any("no-existe" in e for e in res["errors"]), res


def test_delete_node_id_malformado_reporta_error(ga):
    res = delete_node(ga, "basura")
    assert res["ok"] is False
    assert res["errors"], res
