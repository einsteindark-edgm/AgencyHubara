"""La API del viewer — **stdlib `http.server`, cero deps nuevas**.

El consumidor es **Acktos Studio** (la extensión de VS Code en `vscode-hubara/`),
que habla con `api_route()` vía el puente stdio `viewer/bridge.py`; este server
HTTP expone la MISMA función para uso standalone/Docker. (El viejo visor web
`index.html` se eliminó — la extensión lo reemplaza.) La API lee el registry
en vivo — cada request re-proyecta el sistema; editás un manifest y se refleja:

    GET  /api/graph          -> el grafo del sistema {nodes, edges} (sdk.graph)
    GET  /api/health         -> {ok: true}
    POST /api/run            -> corre un agente tool-only por el LocalRuntime
                               body: {"agent": "greeter", "input": {"name": "..."}}
    GET  /api/cases[?node=]  -> los casos replayables del catálogo (el select del panel "Probar")
    POST /api/replay         -> corre un caso (seed+ports-fixture) y compara con el golden
                               body: {"case": "diagnose-scale"} -> {output, golden, matches, inputs}
    GET  /api/legend         -> el glosario de convenciones (niveles + reglas G-*/T-*/CASE-*)
    POST /api/run-durable    -> corre el caso en AgentSpan (durable) -> {execution_id} para el trace
                               body: {"case": "dia-del-padre-flujo"}  (a diferencia de /api/replay)
    POST /api/validate-connection -> el gate PURO de compatibilidad de una conexión (contratos)
                               body: {"source": "agent:x", "target": "tool:y"|"agent:z",
                                      "binding"|"inputs": {...}?}  (sin binding = modo informativo)
    POST /api/connect        -> valida Y persiste la conexión en el manifest YAML (con rollback)
    POST /api/disconnect     -> remueve la relación del manifest  body: {source, target, kind}
    POST /api/delete-node    -> borra un nodo ENTERO (tool o agente) + cascade-desconecta a
                               quienes lo usan  body: {"node_id": "tool:x"|"agent:y", "cascade": bool?}
                               (sin cascade y con dependientes: 200 + needs_confirmation + lista)
    GET  /api/production-status -> {saved, dirty, snapshot} — ¿el wiring matchea lo bendecido?
    POST /api/save           -> 'guardar producción': checks de TODO el sistema + snapshot
                               (production.yaml, raíz del subsistema); 422 si algo está roto
    POST /api/publish        -> 'publicar': despliegue del wiring bendecido — commit quirúrgico
                               (manifests/ + production.yaml) + push + PR (gh); exige save al día
                               body: {"push": bool?, "pr": bool?} (default true)

Por qué stdlib y no FastAPI: la API es read-mostly + un endpoint de run; con
la stdlib corre en la imagen slim sin sincronizar deps pesadas, igual que el
`LocalRuntime`. Si crece (auth, websockets) se hace el swap — el core ruteable
(`api_route`) ya está aislado del transporte.

Correr:  `python -m viewer.server`  (desde GraphAgents/)  ·  PORT=8900 por defecto.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

GA_ROOT = Path(__file__).resolve().parent.parent

# que `import sdk` resuelva tanto con `-m viewer.server` como con `python viewer/server.py`
if str(GA_ROOT) not in sys.path:
    sys.path.insert(0, str(GA_ROOT))


def run_agent(ga_root: Path, agent_id: str, input_dict: dict, runtime: str = "local") -> dict:
    """Corre un agente tool-only. `runtime`: `'local'` (LocalRuntime in-process) o
    `'agentspan'` (server durable :6767 — el run aparece en su UI). Los agentes que
    `consumes:` un port se rechazan: necesitan un vendor inyectado (Fixture en
    tests/integration, no desde la UI)."""
    from sdk.manifest_model import iter_nodes, load_manifest

    manifests = ga_root / "manifests"
    cand = sorted(manifests.glob(f"{agent_id}.agent.yaml")) + sorted(
        manifests.glob(f"{agent_id}.taskgraph.yaml")
    )
    if not cand:
        return {"status": "failed", "error": f"no existe el agente '{agent_id}'", "runtime": runtime}
    m = load_manifest(cand[0])
    consumed = sorted({p for n in iter_nodes(m) for p in n.consumes})
    if consumed:
        return {
            "status": "failed",
            "runtime": runtime,
            "error": (
                f"'{agent_id}' consume ports {consumed}: necesita vendors inyectados "
                "(corré por tests/integration con un Fixture, no desde la UI)."
            ),
        }
    if runtime == "agentspan":
        return _run_on_agentspan(ga_root, m, input_dict or {})
    return _run_local(ga_root, m, input_dict or {})


def _run_local(ga_root: Path, m, input_dict: dict) -> dict:
    from sdk.loader import build_runnable
    from sdk.runtime import LocalRuntime

    try:
        ex = LocalRuntime().run(build_runnable(m, ga_root), input_dict)
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "error": str(e), "runtime": "local"}
    return {"id": ex.id, "status": ex.status, "output": ex.output, "error": ex.error, "runtime": "local"}


def _run_on_agentspan(ga_root: Path, m, input_dict: dict) -> dict:
    """Compila la capability a su `CompiledStateGraph` (`build()`) y la corre sobre el
    server durable. Degrada con un error claro si falta `build()` real, langgraph o
    el server — la UI NUNCA crashea."""
    from sdk.loader import build_agent
    from sdk.runtime import AgentSpanRuntime

    try:
        graph = build_agent(m, ga_root)  # CompiledStateGraph — requiere build() real + langgraph
    except NotImplementedError:
        return {
            "status": "failed",
            "runtime": "agentspan",
            "error": f"'{m.name}' todavía no tiene build() real (G1+); por ahora solo greeter corre en AgentSpan.",
        }
    except Exception as e:  # noqa: BLE001 — langgraph ausente / import roto
        return {
            "status": "failed",
            "runtime": "agentspan",
            "error": f"no pude compilar el grafo (¿langgraph? corré el explorer en el container): {e}",
        }
    try:
        ex = AgentSpanRuntime().run(graph, _durable_input(m, input_dict))  # supervisor → {acc: seed}
    except (Exception, SystemExit) as e:  # noqa: BLE001 — AgentRuntime levanta SystemExit si :6767 cae (auto_start=false)
        return {
            "status": "failed",
            "runtime": "agentspan",
            "error": f"el run en AgentSpan falló (¿server :6767 arriba? ¿agentspan instalado?): {e}",
        }
    return {"id": ex.id, "status": ex.status, "output": _durable_output(m, ex.output),
            "error": ex.error, "runtime": "agentspan"}


def _durable_input(m, seed: dict) -> dict:
    """La forma del estado inicial para AgentSpan según el grafo. Un SUPERVISOR compila a
    `_SupervisorState{acc}` (+ `patches` en parallel) → el seed va ENVUELTO en `acc`; pasarlo
    crudo hace que el 1er nodo reciba `acc` como string y reviente con `dict(<string>)` (el bug
    que tumbaba 'probar durable' del flujo). Una capability sola (greeter) toma el seed crudo —
    su State tiene las claves directo."""
    if not getattr(m, "is_supervisor", False):
        return seed
    state = {"acc": seed}
    if m.strategy == "parallel":
        state["patches"] = []  # el canal operator.add del fan-out (L-14)
    return state


def _durable_output(m, out):
    """El estado final de un supervisor viene como `{acc: <state>}` → devolvemos el state (lo que
    el resto del sistema y el panel esperan). Una capability sola ya devuelve su output."""
    if getattr(m, "is_supervisor", False) and isinstance(out, dict) and "acc" in out:
        return out["acc"]
    return out


def _case_node_id(case) -> str:
    """El id del NODO del grafo al que pertenece un caso (para filtrar el select por nodo).
    `tool:<id>`→`tool:<id>` · `agent:<id>`→`agent:<id>` · `flow:<id>`→`agent:<id>` (un
    supervisor es un nodo `agent:` en el grafo)."""
    kind, cid = case.target.split(":", 1)
    return f"{'agent' if kind in ('agent', 'flow') else 'tool'}:{cid}"


def replay_case_route(ga_root: Path, case_id: str) -> tuple[int, dict]:
    """Replayea un caso del catálogo y compara con su golden. Es lo que /api/run NO puede:
    inyecta los Fixtures del caso (ports + llm). Degrada limpio — la UI nunca crashea."""
    from sdk.case_model import discover_cases, resolve
    from sdk.replay import replay_case

    case = next((c for c in discover_cases(ga_root) if c.id == case_id), None)
    if case is None:
        return 404, {"error": f"no existe el caso '{case_id}'"}
    try:
        out = replay_case(case, ga_root)
        golden = resolve(case.golden, ga_root)
        # el TRIPLE expandido (lo que el panel "Probar" muestra como input que entró):
        inputs = {"seed": resolve(case.seed, ga_root), "ports": resolve(case.ports, ga_root)}
    except Exception as e:  # noqa: BLE001 — capability/loader roto = no confiable, no crash
        return 422, {"error": f"el replay del caso '{case_id}' falló: {e}", "case": case_id}
    return 200, {
        "case": case.id, "target": case.target, "title": case.title,
        "matches": out == golden, "output": out, "golden": golden, "inputs": inputs,
    }


def run_local_route(ga_root: Path, case_id: str) -> tuple[int, dict]:
    """La ejecución SENCILLA: corre el caso EN PROCESO con sus fixtures (ports del caso, cero
    AgentSpan/Conductor) y devuelve TODO lo que el detalle de ejecución necesita — los steps del
    plan, el I/O por-tool reconstruido, el acc después de cada nodo y el output final del pod.
    Determinista (G-DET): mismo `run()` por nodo que el durable, con los vendors reemplazados por
    los Fixtures del caso. Es el camino sin infraestructura (local hoy, AWS mañana: el durable es
    OPCIONAL para probar un flujo)."""
    from sdk.case_model import discover_cases, resolve
    from sdk.graph import execution_plan
    from sdk.replay import _target_node, build_ports

    case = next((c for c in discover_cases(ga_root) if c.id == case_id), None)
    if case is None:
        return 404, {"error": f"no existe el caso '{case_id}'"}
    if case.kind == "tool":
        return 422, {"error": f"'{case.target}' es un tool — corré '▶ Replay' (compara contra su golden); "
                     "la ejecución local es para agentes/flows."}
    try:
        import sdk.tooltrace as tooltrace  # via módulo: monkeypatcheable en tests (L-19)

        node = _target_node(case, ga_root)
        seed = resolve(case.seed, ga_root)
        res = tooltrace.replay_flow_with_trace(node, ga_root, seed, ports=build_ports(case, ga_root))
        plan = execution_plan(node, ga_root)
    except Exception as e:  # noqa: BLE001 — capability/loader roto: degradá, la UI nunca crashea
        return 422, {"error": f"la ejecución local del caso '{case_id}' falló: {e}", "case": case_id}
    return 200, {
        "case": case.id, "target": case.target, "title": case.title,
        "agent": case.target_id, "strategy": plan.get("strategy"), "seed": seed,
        "steps": plan.get("steps", []), "output": res["output"],
        "node_traces": res["node_traces"], "node_accs": res.get("node_accs", {}),
        "runtime": "local",
    }


def _durable_ports() -> dict:
    """Los vendors REALES que el durable inyecta a los miembros que `consumes:` un port. Hoy: el
    `llm` → `LiteLLMProxy` (deepseek-v4-flash vía el proxy LiteLLM del central). Es LAZY — solo pega
    cuando un nodo lo invoca, y `build_runnable` filtra por `consumes` (un miembro que no declara el
    port no lo recibe). Config del endpoint por `LITELLM_PROXY_URL` (default `localhost:4000`, SOLO
    dev): en un deploy multi-caja (el proxy vive en OTRA EC2) hay que setearla al endpoint alcanzable,
    si no el nodo pega a localhost y revienta con `[Errno 99]` (L-26). Si el proxy está caído/mal
    configurado, el reporte se emite IGUAL (G-DET) y la narrativa degrada VISIBLE (marcador + el error
    en el trace) — honesto, no silencioso, no crashea el nodo. (Meta sigue sin vendor real — G2 — su
    port se rechaza.)"""
    from sdk.connectorkit.ports import LiteLLMProxy

    return {"llm": LiteLLMProxy()}


def _start_durable(ga_root: Path, m, input_dict: dict) -> str:
    """Compila el agente (inyectando los vendors reales de los ports que consume, `_durable_ports`)
    y lo submitea ASÍNCRONO a AgentSpan (`start`, no `run`) → execution-id al instante (~0.4s), los
    workers completan en background. El supervisor va envuelto en `{acc: seed}`. Devuelve el eid."""
    from sdk.loader import build_agent
    from sdk.runtime import AgentSpanRuntime

    graph = build_agent(m, ga_root, ports=_durable_ports())
    return AgentSpanRuntime().start(graph, _durable_input(m, input_dict))


def run_durable_route(ga_root: Path, case_id: str) -> tuple[int, dict]:
    """Submitea el TARGET de un caso al runtime DURABLE (AgentSpan) y devuelve el execution-id AL
    INSTANTE (status 'running', NO espera a que complete) — así el viewer pollea el trace y ve los
    nodos activarse EN VIVO (pending→running→done). A diferencia de /api/replay (en proceso), SÍ
    submitea a Conductor (:6767). Un tool no es un agente durable. Un agente DIRECTO con port se
    rechaza (no hay fixture en el durable). Un supervisor que REFERENCIA un agente-con-port (ninguno
    hoy) NO lo caza el guard estructural, pero falla CERRADO en runtime (`ports={}`→KeyError antes
    del vendor → task FAILED, sin llamada a Meta)."""
    from sdk.case_model import discover_cases, resolve
    from sdk.manifest_model import iter_nodes, load_manifest

    case = next((c for c in discover_cases(ga_root) if c.id == case_id), None)
    if case is None:
        return 404, {"error": f"no existe el caso '{case_id}'"}
    if case.kind == "tool":
        return 422, {"error": f"'{case.target}' es un tool — no corre en el runtime DURABLE "
                     "(no es un agente de AgentSpan). Usá 'probar' (en proceso)."}
    manifests = ga_root / "manifests"
    cand = sorted(manifests.glob(f"{case.target_id}.agent.yaml")) + sorted(
        manifests.glob(f"{case.target_id}.taskgraph.yaml"))
    if not cand:
        return 404, {"error": f"no existe el agente '{case.target_id}' en manifests/"}
    m = load_manifest(cand[0])
    consumed = sorted({p for n in iter_nodes(m) for p in n.consumes})
    unprovidable = [p for p in consumed if p not in _durable_ports()]  # llm SÍ tiene vendor real ahora
    if unprovidable:
        return 422, {"error": f"'{case.target_id}' consume ports {unprovidable} sin vendor real en el "
                     "durable (ej. Meta — G2): corré por tests/integration con un Fixture."}
    try:
        eid = _start_durable(ga_root, m, resolve(case.seed, ga_root))
    except (Exception, SystemExit) as e:  # noqa: BLE001 — AgentRuntime levanta SystemExit (no Exception)
        # si :6767 cae con auto_start_server=false → la UI degrada a 422, no muere el request thread.
        return 422, {"error": f"no pude submitear a AgentSpan (¿server :6767 arriba? ¿langgraph/agentspan?): {e}",
                     "agent": case.target_id, "runtime": "agentspan"}
    return 200, {"execution_id": eid, "agent": case.target_id, "status": "running", "runtime": "agentspan"}


def api_route(method: str, path: str, params: dict, body: dict | None, ga_root: Path = GA_ROOT):
    """Core ruteable y testeable (sin socket). Devuelve `(status:int, payload:dict)`."""
    from sdk.graph import build_graph

    if method == "GET" and path == "/api/health":
        return 200, {"ok": True}
    if method == "GET" and path == "/api/graph":
        return 200, build_graph(ga_root)
    if method == "GET" and path == "/api/plan":
        # El ORDEN DE EJECUCIÓN de un supervisor (o agente): lo que el explorer dibuja como
        # "flujo". `params` viene de parse_qs → valores en lista (?agent=ads-analytics).
        from sdk.graph import execution_plan
        from sdk.manifest_model import load_manifest

        agent_id = (params.get("agent") or [None])[0]
        if not agent_id:
            return 400, {"error": "falta 'agent' en el query (?agent=<id>)"}
        manifests = ga_root / "manifests"
        cand = sorted(manifests.glob(f"{agent_id}.agent.yaml")) + sorted(
            manifests.glob(f"{agent_id}.taskgraph.yaml")
        )
        if not cand:
            return 404, {"error": f"no existe el agente '{agent_id}'"}
        return 200, execution_plan(load_manifest(cand[0]), ga_root)
    if method == "GET" and path == "/api/runs":
        # las ejecuciones recientes de AgentSpan (los 'threads' de Studio). Degrada limpio
        # si el server :6767 no está — la UI nunca crashea.
        from sdk.trace import fetch_runs

        try:
            limit = int((params.get("limit") or ["25"])[0])
        except ValueError:
            limit = 25
        running = (params.get("running") or ["0"])[0] in ("1", "true")  # ?running=1 → poll de flota
        try:
            return 200, {"runs": fetch_runs(limit=limit, running_only=running)}
        except Exception as e:  # noqa: BLE001 — server caído / agentspan ausente
            return 502, {"error": f"no pude listar ejecuciones (¿server :6767 arriba?): {e}", "runs": []}
    if method == "GET" and path == "/api/trace":
        # el TRACE en vivo: el execution_plan anotado con el estado por-nodo de Conductor.
        from sdk.graph import execution_plan
        from sdk.manifest_model import load_manifest
        from sdk.trace import build_trace, fetch_workflow

        eid = (params.get("execution_id") or [None])[0]
        if not eid:
            return 400, {"error": "falta 'execution_id' en el query (?execution_id=<id>)"}
        try:
            wf = fetch_workflow(eid)
        except Exception as e:  # noqa: BLE001
            return 502, {"error": f"no pude leer la ejecución (¿server :6767 arriba?): {e}"}
        agent_id = wf.get("workflowName")
        manifests = ga_root / "manifests"
        cand = sorted(manifests.glob(f"{agent_id}.agent.yaml")) + sorted(
            manifests.glob(f"{agent_id}.taskgraph.yaml")
        )
        if not cand:
            return 404, {"error": f"la ejecución es del agente '{agent_id}', ausente del catálogo"}
        return 200, build_trace(execution_plan(load_manifest(cand[0]), ga_root), wf)
    if method == "GET" and path == "/api/node-state":
        # lazy: el acc (estado acumulador) DESPUÉS de un nodo — el 'ver por dentro', al click.
        from sdk.trace import fetch_node_state

        eid = (params.get("execution_id") or [None])[0]
        tid = (params.get("task_id") or [None])[0]
        if not eid or not tid:
            return 400, {"error": "faltan 'execution_id' y 'task_id' en el query"}
        try:
            return 200, {"acc": fetch_node_state(eid, tid)}
        except Exception as e:  # noqa: BLE001
            return 502, {"error": f"no pude leer el estado del nodo: {e}"}
    if method == "GET" and path == "/api/flow-trace":
        # RECONSTRUYE el I/O por-tool de un run durable replayeando el pod desde su seed (G-DET) —
        # SIN tocar la ejecución ni persistir nada en Conductor. El supervisor durable corre el
        # MISMO `run()` por nodo (build_runnable), así que replayear con tools trazadas reproduce
        # exactamente lo que pasó. Es la honestidad amarrada por la cert (conformance declared==real).
        from sdk.manifest_model import load_manifest
        from sdk.tooltrace import replay_flow_with_trace
        from sdk.trace import _root_seed, fetch_workflow

        eid = (params.get("execution_id") or [None])[0]
        if not eid:
            return 400, {"error": "falta 'execution_id' en el query (?execution_id=<id>)"}
        try:
            wf = fetch_workflow(eid)
        except Exception as e:  # noqa: BLE001
            return 502, {"error": f"no pude leer la ejecución (¿server :6767 arriba?): {e}"}
        agent_id = wf.get("workflowName")
        manifests = ga_root / "manifests"
        cand = sorted(manifests.glob(f"{agent_id}.agent.yaml")) + sorted(
            manifests.glob(f"{agent_id}.taskgraph.yaml"))
        if not cand:
            return 404, {"error": f"la ejecución es del agente '{agent_id}', ausente del catálogo"}
        seed = _root_seed(wf)
        if seed is None:  # sin seed recuperable (fixture trimmeado / run viejo) → no inventa, degrada
            return 200, {"node_traces": {}, "reconstructed": False,
                         "reason": "el seed no viajó en este run — no se puede reconstruir"}
        try:
            res = replay_flow_with_trace(load_manifest(cand[0]), ga_root, seed)
        except Exception as e:  # noqa: BLE001 — capability/binding roto: degradá, la UI nunca crashea
            return 422, {"error": f"no pude reconstruir el trace por-tool: {e}",
                         "node_traces": {}, "reconstructed": False}
        return 200, {"node_traces": res["node_traces"], "reconstructed": True}
    if method == "GET" and path == "/api/inspect":
        # los FILES + los CHECKS de un nodo o de una RELACIÓN (la línea entre nodos).
        from sdk.graph import build_graph
        from sdk.inspect import inspect_edge, inspect_node

        g = build_graph(ga_root)
        node_id = (params.get("node") or [None])[0]
        if node_id:
            n = next((x for x in g["nodes"] if x["id"] == node_id), None)
            if n is None:
                return 404, {"error": f"no existe el nodo '{node_id}'"}
            return 200, inspect_node(n, ga_root)
        src = (params.get("source") or [None])[0]
        tgt = (params.get("target") or [None])[0]
        if src and tgt:
            kind = (params.get("kind") or [None])[0]
            e = next((x for x in g["edges"] if x["source"] == src and x["target"] == tgt
                      and (not kind or x["kind"] == kind)), None)
            if e is None:
                return 404, {"error": f"no existe la arista {src} → {tgt}"}
            return 200, inspect_edge(e, ga_root)
        return 400, {"error": "falta 'node' o ('source' y 'target') en el query"}
    if method == "GET" and path == "/api/checks":
        # 'correr las certs en el viewer': veredicto por nodo y por arista para el overlay.
        from sdk.graph import build_graph
        from sdk.inspect import system_checks

        return 200, system_checks(build_graph(ga_root), ga_root)
    if method == "GET" and path == "/api/cases":
        # el catálogo de CASOS replayables (lo que el panel "Probar" lista en un select). Con
        # `?node=<id>` filtra a los del nodo enfocado (un caso flow:X pertenece al nodo agent:X).
        from sdk.case_model import discover_cases

        cases = discover_cases(ga_root)
        node = (params.get("node") or [None])[0]
        if node:
            cases = [c for c in cases if _case_node_id(c) == node]
        return 200, {"cases": [{"id": c.id, "target": c.target, "title": c.title} for c in cases]}
    if method == "GET" and path == "/api/legend":
        # el glosario de convenciones (niveles C0–C3 + reglas G-*/T-*/CASE-*) — el botón
        # "convenciones". Fuente única en sdk.glossary; la UI no reimplementa el vocabulario.
        from sdk.glossary import glossary

        return 200, glossary()
    if method == "POST" and path == "/api/run":
        body = body or {}
        agent_id = body.get("agent")
        if not agent_id:
            return 400, {"error": "falta 'agent' en el body"}
        runtime = body.get("runtime", "local")
        if runtime not in ("local", "agentspan"):
            return 400, {"error": f"runtime inválido '{runtime}' (usá local|agentspan)"}
        res = run_agent(ga_root, agent_id, body.get("input") or {}, runtime=runtime)
        return (200 if res.get("status") == "completed" else 422), res
    if method == "POST" and path == "/api/replay":
        # corre un CASO del catálogo (seed + ports-fixture + golden) — el determinismo hecho
        # botón. A diferencia de /api/run, inyecta los Fixtures del caso (ports con $ref).
        body = body or {}
        case_id = body.get("case")
        if not case_id:
            return 400, {"error": "falta 'case' en el body"}
        return replay_case_route(ga_root, case_id)
    if method == "POST" and path == "/api/run-local":
        # la ejecución SENCILLA en proceso (fixtures del caso, sin AgentSpan) — ver run_local_route.
        body = body or {}
        case_id = body.get("case")
        if not case_id:
            return 400, {"error": "falta 'case' en el body"}
        return run_local_route(ga_root, case_id)
    if method == "POST" and path == "/api/run-durable":
        # corre el caso en el runtime DURABLE (AgentSpan) → execution-id para seguir el trace
        # nodo-por-nodo. A diferencia de /api/replay (en proceso), submitea a Conductor (:6767).
        case_id = (body or {}).get("case")
        if not case_id:
            return 400, {"error": "falta 'case' en el body"}
        return run_durable_route(ga_root, case_id)
    if method == "GET" and path == "/api/production-status":
        # ¿el wiring actual matchea el snapshot bendecido? — el header pinta ✓ / ● sin guardar.
        from sdk.production import production_status

        return 200, production_status(ga_root)
    if method == "POST" and path == "/api/save":
        # 'guardar producción': corre TODOS los checks y bendice el wiring actual como final
        # (production.yaml, raíz del subsistema). Se rehúsa 422 si algo está roto — no se bendice
        # producción rota. Los manifests siguen siendo la verdad; esto solo registra el hito.
        from sdk.production import save_production

        try:
            res = save_production(ga_root)
        except Exception as e:  # noqa: BLE001 — manifest ilegible / IO: degradá, la UI nunca crashea
            return 422, {"ok": False, "errors": [f"el save falló: {e}"], "snapshot": None}
        return (200 if res["ok"] else 422), res
    if method == "POST" and path == "/api/publish":
        # 'publicar producción': el DESPLIEGUE del wiring bendecido — commit quirúrgico
        # (manifests/ + production.yaml) → push → PR con `gh`. Exige snapshot al día
        # (save previo = checks verdes). body: {"push": bool?, "pr": bool?} (default true).
        # Degrada honesto si no hay git/gh en el runtime (p. ej. el container slim).
        from sdk.production import publish_production

        body = body or {}
        try:
            res = publish_production(ga_root, push=body.get("push", True), pr=body.get("pr", True))
        except Exception as e:  # noqa: BLE001 — IO/git inesperado: degradá, la UI nunca crashea
            return 422, {"ok": False, "errors": [f"publicar falló: {e}"], "steps": [],
                         "branch": None, "commit": None, "pr_url": None}
        return (200 if res["ok"] else 422), res
    if method == "POST" and path == "/api/delete-node":
        # borrar un nodo ENTERO del catálogo (tool o agente), no solo una relación:
        # cascade-desconecta a quienes lo usan + borra el archivo/dir. Sin cascade y con
        # dependientes devuelve 200 + needs_confirmation (la UI pide confirmar el blast radius).
        # La certificación completa que corre "Guardar & certificar" es el gate real del PR.
        from sdk.manifest_edit import delete_node

        body = body or {}
        node_id = body.get("node_id")
        if not node_id:
            return 400, {"error": "falta 'node_id' en el body (ids 'kind:id')"}
        try:
            res = delete_node(ga_root, node_id, cascade=bool(body.get("cascade", False)))
        except Exception as e:  # noqa: BLE001 — IO/manifest inesperado: degradá, la UI nunca crashea
            return 422, {"ok": False, "needs_confirmation": False, "dependents": [],
                         "disconnected": [], "deleted": [], "warnings": [],
                         "errors": [f"el borrado falló: {e}"]}
        if res.get("ok") or res.get("needs_confirmation"):
            return 200, res  # needs_confirmation NO es un error HTTP — es la lista para confirmar
        return 422, res
    if method == "POST" and path in ("/api/validate-connection", "/api/connect", "/api/disconnect"):
        # el MODO EDICIÓN del explorer (estilo n8n): validar/conectar/desconectar relaciones
        # editando los manifests (sdk.manifest_edit). La verdad sigue siendo el YAML: validate
        # es PURO (el gate de compatibilidad por contrato); connect/disconnect editan quirúrgico
        # (comentarios intactos) con recarga + checks + ROLLBACK si algo rompe. 200=ok · 422=el
        # gate rechazó (errors visibles) · 400=request malformado. La UI nunca crashea.
        from sdk.manifest_edit import connect, disconnect, validate_connection

        body = body or {}
        source, target = body.get("source"), body.get("target")
        if not source or not target:
            return 400, {"error": "faltan 'source' y/o 'target' en el body (ids 'kind:id')"}
        try:
            if path == "/api/validate-connection":
                res = validate_connection(ga_root, source, target,
                                          binding=body.get("binding"), inputs=body.get("inputs"))
                return 200, res  # el veredicto (ok true/false) ES el payload — no es un error HTTP
            if path == "/api/connect":
                res = connect(ga_root, source, target,
                              binding=body.get("binding"), inputs=body.get("inputs"))
            else:
                kind = body.get("kind")
                if not kind:
                    return 400, {"error": "falta 'kind' en el body (uses|agent|consumes)"}
                res = disconnect(ga_root, source, target, kind=kind)
        except Exception as e:  # noqa: BLE001 — manifest ilegible / IO: degradá, la UI nunca crashea
            return 422, {"ok": False, "errors": [f"la edición falló: {e}"], "warnings": []}
        return (200 if res.get("ok") else 422), res
    return 404, {"error": f"no existe la ruta {method} {path}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "GraphAgentsExplorer/0.1"

    def _send(self, status: int, payload, ctype: str = "application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path.startswith("/api/"):
            status, payload = api_route("GET", u.path, parse_qs(u.query), None)
            return self._send(status, payload)
        # sin visor estático: la UI es Acktos Studio (vscode-hubara/) — este
        # server expone SOLO la API JSON.
        self._send(404, {"error": f"no existe {u.path} — la UI es Acktos Studio; la API vive bajo /api/*"})

    def do_POST(self) -> None:
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return self._send(400, {"error": "body no es JSON válido"})
        status, payload = api_route("POST", u.path, parse_qs(u.query), body)
        self._send(status, payload)

    def log_message(self, *_args) -> None:  # silencio (no ensuciar stdout)
        pass


def serve(host: str = "0.0.0.0", port: int = 8900) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"GraphAgents explorer → http://localhost:{port}  (Ctrl-C para parar)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    import os
    import sys

    # puerto por argv (1er arg) o env PORT o 8900 — el argv permite un server de preview
    # en otro puerto sin chocar con el :8900 del container vivo.
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8900"))
    serve(port=port)
