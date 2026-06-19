"""Hola mundo de GraphAgents — el agente `greeter` usa la tool `hello` del
catálogo y corre sobre el runtime port. Es la plantilla mínima que ejercita TODA
la estructura: tool (contrato + impl + adapters + test) + agente (manifest +
capability) + runtime + recovery.

Corre con el LocalRuntime: NO necesita API key ni el server AgentSpan. Es el
`CMD` por defecto del contenedor `graphagents` del docker-compose.
"""
from __future__ import annotations

import json
from pathlib import Path

from sdk.loader import build_runnable
from sdk.manifest_model import load_manifest
from sdk.registry import agent_index
from sdk.registry import index as tool_index
from sdk.runtime import LocalRuntime

GA = Path(__file__).resolve().parent


def main() -> None:
    print("== catálogo de tools (palette) ==")
    for t in tool_index(GA):
        print(f"  {t['id']}@{t['version']}  [{t['side_effect']}]  {', '.join(t['tags'])}")

    print("== catálogo de agentes ==")
    for a in agent_index(GA):
        extra = " ·as-tool" if a["exposes_as_tool"] else ""
        pub = f" ·publish:{a['publish']}" if a["publish"] else ""
        print(f"  {a['id']}  [{a['archetype']}]{extra}{pub}")

    print("\n== hola mundo: greeter usa la tool hello, sobre el runtime ==")
    greeter = load_manifest(GA / "manifests" / "greeter.agent.yaml")
    runnable = build_runnable(greeter, GA)  # el loader resuelve hello del catálogo
    rt = LocalRuntime()
    ex = rt.run(runnable, {"name": "mundo"})
    print(f"  execution {ex.id}: {ex.status} -> {json.dumps(ex.output, ensure_ascii=False)}")

    print("\n== durabilidad: crash simulado + recovery por execution-id ==")
    eid = rt.start_durable(runnable, {"name": "otra vez"})
    print(f"  {eid}: {rt.get(eid).status} (quedó a medias)")
    ex2 = rt.resume(eid)
    print(f"  resume {ex2.id}: {ex2.status} -> {json.dumps(ex2.output, ensure_ascii=False)}")

    print(
        "\nOK ✅  Para correrlo sobre el AgentSpan real: `docker compose up -d agentspan`"
        " + GRAPHAGENTS_RUNTIME=agentspan (cablear AgentSpanRuntime, G1+)."
    )


if __name__ == "__main__":
    main()
