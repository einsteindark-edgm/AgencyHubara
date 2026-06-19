"""Backend VIVO del explorer — **stdlib `http.server`, cero deps nuevas**.

Sirve el visor estático (`index.html`) y una API JSON mínima que lee el registry
en vivo (cada request re-proyecta el sistema; editás un manifest y refrescás):

    GET  /api/graph          -> el grafo del sistema {nodes, edges} (sdk.graph)
    GET  /api/health         -> {ok: true}
    POST /api/run            -> corre un agente tool-only por el LocalRuntime
                               body: {"agent": "greeter", "input": {"name": "..."}}

Por qué stdlib y no FastAPI: el explorer es read-mostly + un endpoint de run; con
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
VIEWER = Path(__file__).resolve().parent

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
        ex = AgentSpanRuntime().run(graph, input_dict)
    except Exception as e:  # noqa: BLE001 — server caído / agentspan ausente
        return {
            "status": "failed",
            "runtime": "agentspan",
            "error": f"el run en AgentSpan falló (¿server :6767 arriba? ¿agentspan instalado?): {e}",
        }
    return {"id": ex.id, "status": ex.status, "output": ex.output, "error": ex.error, "runtime": "agentspan"}


def api_route(method: str, path: str, params: dict, body: dict | None, ga_root: Path = GA_ROOT):
    """Core ruteable y testeable (sin socket). Devuelve `(status:int, payload:dict)`."""
    from sdk.graph import build_graph

    if method == "GET" and path == "/api/health":
        return 200, {"ok": True}
    if method == "GET" and path == "/api/graph":
        return 200, build_graph(ga_root)
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
    return 404, {"error": f"no existe la ruta {method} {path}"}


_STATIC = {"/": "index.html", "/index.html": "index.html"}


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
        fname = _STATIC.get(u.path)
        if fname:
            f = VIEWER / fname
            if f.exists():
                return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        self._send(404, {"error": f"no existe {u.path}"})

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

    serve(port=int(os.environ.get("PORT", "8900")))
