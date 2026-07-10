"""GraphAgentsKit — el bridge hubara ↔ caja GraphAgents, para plugins.

Fachada SDK (P-28/P-3) sobre `src.platform.graphagents`: el Launcher port
(despertar la caja, despachar un run, resumir HITL, pollear), su vendor real
boto3 (EC2 + SSM — el backend NUNCA se conecta directo a la caja) y el
`interpret` PURO del JSON de Conductor.

Historia: el bridge nació como código privado del plugin `ads` (el buzón de
análisis); promovido a platform en WS-B0 (plan Window Strategist) para que
otros plugins (`reengagement`) lo usen sin imports cross-plugin.

Uso canónico (activity de un plugin)::

    from src.sdk.graphagentskit import Boto3Launcher, interpret

    launcher = Boto3Launcher()
    launcher.start_box()
    eid = launcher.dispatch("window-strategist", snapshot, run_id=run_id)
    ...
    state = interpret(launcher.fetch_status(eid))
    if state["status"] == "completed":
        output = state["result"]

El transporte es POLL-based, hubara-initiated: no existe push/callback desde
la caja. Idempotencia cross-run = fingerprint del envío (no el event_id).
"""
from __future__ import annotations

from src.platform.graphagents.boto3_launcher import (
    Boto3Launcher as Boto3Launcher,
)
from src.platform.graphagents.conductor import (
    extract_agent_result as extract_agent_result,
)
from src.platform.graphagents.conductor import (
    interpret as interpret,
)
from src.platform.graphagents.launcher import (
    Launcher as Launcher,
)
