"""Toda integración hubara→GraphAgents tiene su COSTURA declarada en
`vscode-hubara/seams.yaml` — la línea cross-sistema del workspace de Acktos
Studio. Sin la costura, el operador ve el plugin y el agente como islas.

1. Todo `launcher.dispatch(..., "<agent-id>", ...)` LITERAL en
   `src/plugins/<plugin>/` exige la seam `hub:plugin:<plugin> →
   ga:agent:<agent-id>` (los dispatch dinámicos — caso ads, el agente viene
   del request — se declaran a mano y los cubre el chequeo 2).
2. Toda seam declarada RESUELVE contra el sistema vivo: el plugin existe en
   `frontend_dashboard/src/plugins/` y el agente tiene manifest en
   `GraphAgents/manifests/` — nada aspiracional (el visor las reporta rotas,
   pero acá se ponen rojas en CI).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

HUBARA = Path(__file__).resolve().parents[2]
REPO = HUBARA.parent
SEAMS_PATH = REPO / "vscode-hubara" / "seams.yaml"
PLUGINS_SRC = HUBARA / "src" / "plugins"
PLUGINS_MANIFESTS = REPO / "frontend_dashboard" / "src" / "plugins"
GA_MANIFESTS = REPO / "GraphAgents" / "manifests"

_DISPATCH_RE = re.compile(r"\.dispatch[,(]\s*[\"']([a-z0-9-]+)[\"']")


def _seams() -> list[dict]:
    data = yaml.safe_load(SEAMS_PATH.read_text(encoding="utf-8")) or {}
    return data.get("seams") or []


def _literal_dispatches() -> set[tuple[str, str]]:
    """{(plugin_id, agent_id)} por cada dispatch con id literal en un plugin."""
    found: set[tuple[str, str]] = set()
    for py in PLUGINS_SRC.glob("*/**/*.py"):
        plugin_id = py.relative_to(PLUGINS_SRC).parts[0]
        for agent_id in _DISPATCH_RE.findall(py.read_text(encoding="utf-8")):
            found.add((plugin_id, agent_id))
    return found


def test_todo_dispatch_literal_tiene_su_costura():
    declared = {(s.get("from"), s.get("to")) for s in _seams()}
    missing = [
        f"hub:plugin:{plugin} → ga:agent:{agent}"
        for plugin, agent in sorted(_literal_dispatches())
        if (f"hub:plugin:{plugin}", f"ga:agent:{agent}") not in declared
    ]
    assert missing == [], (
        f"integraciones hubara→GraphAgents SIN costura en vscode-hubara/seams.yaml: "
        f"{missing} — sin la seam, Acktos Studio dibuja el plugin y el agente como "
        "islas en el workspace. Declarala (from/to/label/kind) apuntando al "
        "call-site real del dispatch."
    )


def test_toda_costura_resuelve_contra_el_sistema_vivo():
    broken: list[str] = []
    for seam in _seams():
        frm, to = seam.get("from", ""), seam.get("to", "")
        m_from = re.fullmatch(r"hub:plugin:([a-z0-9_]+)", frm)
        m_to = re.fullmatch(r"ga:agent:([a-z0-9-]+)", to)
        if not m_from or not (PLUGINS_MANIFESTS / m_from.group(1) / "plugin.yaml").exists():
            broken.append(f"{seam.get('id')}: from={frm!r} no resuelve a un plugin real")
        if not m_to or not any(
            (GA_MANIFESTS / f"{m_to.group(1)}{ext}").exists()
            for ext in (".agent.yaml", ".taskgraph.yaml")
        ):
            broken.append(f"{seam.get('id')}: to={to!r} no resuelve a un agente/taskgraph real")
    assert broken == [], broken
