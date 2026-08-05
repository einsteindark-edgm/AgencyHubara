"""Guard del modo de razonamiento del agente contra el proxy litellm.

CONTEXTO (2026-08-05). El agente de ventas corría con thinking ENCENDIDO. La
evidencia dice que para un chat con restricciones duras (no vosear, no inventar
productos fuera del catálogo, guion por etapa) el razonamiento explícito
DEGRADA el seguimiento de instrucciones — "When Thinking Fails" (NeurIPS 2025):
13 de 14 modelos empeoran en IFEval con CoT porque el razonamiento desvía la
atención de los tokens de la restricción. Además nos costó un incidente real:
turnos que cerraban con ``reasoning_content`` y ``content`` vacío dejaban al
bot "pensando" hasta que saltaba el ghosting (ver el manejo en
``workflow_helpers`` alrededor de la respuesta vacía).

LA TRAMPA QUE ESTE TEST EXISTE PARA CAZAR — medido contra la API real:

  * ``thinking`` AUSENTE del body  → DeepSeek razona IGUAL (el default del
    servidor es ON). Quitar ``reasoning_effort`` es un PLACEBO.
  * ``thinking: {"type": "enabled"}``  → razona.
  * ``thinking: {"type": "disabled"}`` → NO razona (``reasoning_tokens=None``).

Y ``litellm`` NO PUEDE emitir ``disabled`` por la vía normal: su
``DeepSeekChatConfig.map_openai_params`` tiene una whitelist que sólo deja pasar
``{"type": "enabled"}`` y descarta cualquier otro valor de ``thinking`` /
``reasoning_effort``. El ÚNICO canal que llega crudo al upstream es
``extra_body``, que no pasa por el mapper de params.

Por eso el interruptor vive en ``litellm_params.extra_body`` del proxy y no en
el código de la app. Este test NO asierta el YAML (eso sería schema, y el
gotcha #1 del repo es justamente que el schema verde miente): construye el
``litellm.Router`` con el model_list REAL del archivo que el deploy monta en el
proxy, lo apunta a un upstream falso, y asierta el BODY QUE SALE. Si un upgrade
de litellm deja de reenviar ``extra_body``, o alguien borra el bloque del YAML,
esto se pone rojo.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

_LITELLM_CONFIG = (
    Path(__file__).resolve().parents[3] / "exoclaw-temporal" / "litellm_config.yaml"
)

# El alias que consume el agente de ventas (DEFAULT_LLM_MODEL=deepseek/<este>).
_AGENT_ALIAS = "deepseek-v4-flash"
# El alias hermano que CONSERVA el thinking — brazo del A/B del golden eval y
# rollback instantáneo por env, sin redeploy del proxy.
_THINKING_ALIAS = "deepseek-v4-flash-thinking"


class _FakeUpstream:
    """Servidor que hace de api.deepseek.com y registra el body recibido."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — API de BaseHTTPRequestHandler
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    outer.bodies.append(json.loads(self.rfile.read(length) or b"{}"))
                except json.JSONDecodeError:
                    outer.bodies.append({})
                body = json.dumps(
                    {
                        "id": "chatcmpl-fake",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "deepseek-v4-flash",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 2,
                            "total_tokens": 12,
                        },
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:  # silencio en pytest
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _model_list() -> list[dict]:
    return yaml.safe_load(_LITELLM_CONFIG.read_text(encoding="utf-8"))["model_list"]


def _entry(alias: str) -> dict:
    for entry in _model_list():
        if entry["model_name"] == alias:
            return entry
    pytest.fail(f"el alias '{alias}' no está en el model_list de {_LITELLM_CONFIG}")


async def _body_sent_upstream(alias: str, **call_kwargs) -> dict:
    """Corre el alias REAL por un Router de litellm y devuelve el body upstream.

    El proxy litellm ES un Router — ejercitar el Router con el mismo model_list
    que el deploy monta es la aproximación fiel sin levantar el container.
    """
    import litellm
    from litellm import Router

    litellm.drop_params = True
    entry = _entry(alias)
    upstream = _FakeUpstream()
    params = dict(entry["litellm_params"])
    params["api_key"] = "sk-fake-para-el-upstream-falso"
    params["api_base"] = upstream.base_url
    try:
        router = Router(model_list=[{"model_name": alias, "litellm_params": params}])
        await router.acompletion(
            model=alias,
            messages=[{"role": "user", "content": "hola"}],
            **call_kwargs,
        )
    finally:
        upstream.shutdown()

    assert upstream.bodies, f"el Router nunca llegó al upstream para '{alias}'"
    return upstream.bodies[-1]


async def test_agent_alias_disables_thinking_in_the_upstream_body():
    """El agente de ventas NO debe razonar: body con thinking disabled explícito."""
    body = await _body_sent_upstream(_AGENT_ALIAS)
    assert body.get("thinking") == {"type": "disabled"}, (
        "el body que sale hacia DeepSeek debe llevar thinking DESHABILITADO "
        "explícito. OJO: omitir el campo NO alcanza — el default del servidor "
        f"es razonar. Body observado: {body!r}"
    )


async def test_reasoning_effort_from_the_app_cannot_re_enable_thinking():
    """Regresión: un caller que mande reasoning_effort no debe reactivar el thinking.

    ``build_default_llm_config`` mandó ``reasoning_effort='high'`` por meses.
    Si vuelve (o lo manda otro caller), ``extra_body`` tiene que ganar — si no,
    el thinking se reenciende en silencio y nadie se entera.
    """
    body = await _body_sent_upstream(_AGENT_ALIAS, reasoning_effort="high")
    assert body.get("thinking") == {"type": "disabled"}, (
        "extra_body del proxy debe GANAR sobre el reasoning_effort del caller; "
        f"body observado: {body!r}"
    )


async def test_thinking_alias_still_reasons_for_the_ab_arm():
    """El alias hermano conserva el thinking (brazo B del golden eval / rollback)."""
    body = await _body_sent_upstream(_THINKING_ALIAS)
    assert body.get("thinking") != {"type": "disabled"}, (
        "el alias de thinking existe para comparar contra el default y para "
        f"revertir por env sin redeploy; body observado: {body!r}"
    )
