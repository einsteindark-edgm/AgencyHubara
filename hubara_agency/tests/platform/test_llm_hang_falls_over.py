"""Guard: un DeepSeek que no contesta tiene que caer a ``gemini-backup`` a tiempo.

INCIDENTE 2026-10-15 01:30 UTC (20:30 Bogotá). DeepSeek se saturó y en prod las
llamadas no recibieron un 503: se COLGARON. 26 de 28 intentos de ``llm_chat``
se cortaron exactamente a los 5 min (``start_to_close`` de ``_LLM_OPTIONS``);
una conversación nueva que venía de un anuncio quedó 12 min sin respuesta hasta
que el operador entró a mano, y fallaron 7 de 10 toques de remarketing. El
respaldo ``gemini-backup`` estaba en ``router_settings.fallbacks``, pero el
router solo cae al respaldo cuando hay un ERROR, y una conexión colgada nunca lo
produce: el proxy no tenía ``timeout`` y litellm espera 600 s por defecto.

CÓMO SE VE DEEPSEEK SATURADO — medido contra la API real el 2026-10-15 ~02:00
UTC, con una petición no-streaming: el ``200`` y los headers llegan a los 0,4 s;
después, UNA línea vacía cada 60,0 s (13 de 13, máximo 60,04 s); el JSON llegó
a los 806 s, pasados los 10 min que promete la doc
(api-docs.deepseek.com/quick_start/rate_limit). El ``timeout`` de litellm es
POR LECTURA (aiohttp ``sock_read``): cada byte lo reinicia. O sea, un tope de 60
s o más NUNCA vence contra un DeepSeek saturado; uno por debajo vence antes de
la primera línea vacía. Por eso este guard prueba los dos cuelgues:

  * ``silencio``: acepta la conexión y no manda nada;
  * ``lineas-vacias``: ``200`` + una línea vacía cada ``_DEEPSEEK_KEEPALIVE_TICK_S``.

Y el LÍMITE (``test_the_limit_a_keepalive_faster_than_the_timeout_is_never_cut``):
DeepSeek no documenta los 60 s; si los baja por debajo del tope, el tope no
vence nunca. La defensa completa sería un tope total en ``llm_chat`` (seguimiento).

Mismo patrón que ``test_llm_thinking_disabled.py``: el proxy litellm ES un
Router, así que se monta el ``model_list`` y el ``router_settings`` REALES del
YAML (filtrados como los filtra el proxy) contra upstreams falsos. Los tiempos
se escalan juntos (tope del YAML → ``_SCALED_DEADLINE_S``, y la línea vacía en la
misma proporción) para que la prueba tarde segundos; los valores reales los fija
``test_the_deepseek_deadline_sits_between_a_healthy_turn_and_the_keepalive``.

OJO versiones: corre con el litellm del uv.lock (1.82.6), no con el del proxy de
prod (1.86.2, pineado en infra/compose). Los dos cuelgues se probaron a mano con
las DOS versiones y dan lo mismo (2026-10-15).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.platform.temporal.retry_policies import _LLM_OPTIONS

_LITELLM_CONFIG = (
    Path(__file__).resolve().parents[3] / "exoclaw-temporal" / "litellm_config.yaml"
)

#: Los dos alias de DeepSeek que el router manda a ``gemini-backup``.
_DEEPSEEK_ALIASES = ("deepseek-v4-flash", "deepseek-v4-flash-thinking")

#: ``start_to_close_timeout`` de ``_LLM_OPTIONS`` (hoy 5 min): pasado esto
#: Temporal mata el intento de ``llm_chat`` y el cliente sigue sin respuesta.
_ACTIVITY_ATTEMPT_S = _LLM_OPTIONS["start_to_close_timeout"].total_seconds()
#: El respaldo tiene que entrar antes de la MITAD del intento: queda margen para
#: el respaldo mismo y para la segunda llamada del turno.
_BACKUP_MUST_START_BY_S = _ACTIVITY_ATTEMPT_S / 2

#: Cada cuánto manda DeepSeek saturado una línea vacía (medido 2026-10-15).
_DEEPSEEK_KEEPALIVE_TICK_S = 60.0
#: El tope tiene que vencer con holgura antes de esa primera línea vacía.
_MAX_DEADLINE_S = 0.75 * _DEEPSEEK_KEEPALIVE_TICK_S
#: La llamada sana más lenta de un turno de ventas: 9,45 s entre 1.323
#: ``llm_chat`` del simulador contra DeepSeek real (2026-10-14 15:03 → 10-15
#: 01:00 UTC; prompt p50 29.736 tokens, máx 65.144; p99,9 4,7 s).
_SLOWEST_HEALTHY_TURN_S = 9.45
#: El tope deja pasar con holgura a la llamada sana más lenta.
_MIN_DEADLINE_S = 3 * _SLOWEST_HEALTHY_TURN_S

#: Tope al que se escala el del YAML para que la prueba dure segundos. No más
#: chico: con 45 s contra 60 s (``-thinking``) el margen escalado es un tercio de
#: esto, y en una máquina cargada pedir el respaldo tarda unas décimas.
_SCALED_DEADLINE_S = 3.0
#: Si a esta altura no contestó nadie, sigue colgado.
_STILL_HUNG_AFTER_S = 20.0

_DEEPSEEK_OK = {
    "id": "chatcmpl-fake",
    "object": "chat.completion",
    "created": 1,
    "model": "deepseek-flash",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "respuesta de deepseek"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
}

_GEMINI_OK = {
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"text": "respuesta del respaldo"}]},
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4, "totalTokenCount": 7},
}


class _Upstream:
    """Servidor HTTP local que contesta (``payload``, tras ``delay_s``) o cuelga.

    ``hang``: ``None`` contesta; ``"silencio"`` no manda nada; ``"lineas-vacias"``
    manda ``200`` y una línea vacía cada ``tick_s``, sin JSON nunca.
    """

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        hang: str | None = None,
        delay_s: float = 0.0,
        tick_s: float = 0.0,
    ) -> None:
        self.requests = 0
        #: ``time.monotonic()`` de cada petición recibida (lado servidor).
        self.arrivals: list[float] = []
        self._release = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 — API de BaseHTTPRequestHandler
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests += 1
                outer.arrivals.append(time.monotonic())
                if hang == "silencio":
                    outer._release.wait()
                    return
                if hang == "lineas-vacias":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    while not outer._release.wait(tick_s):
                        try:
                            self.wfile.write(b"\n")
                            self.wfile.flush()
                        except OSError:
                            return
                    return
                if outer._release.wait(delay_s):
                    return
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:  # silencio en pytest
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def shutdown(self) -> None:
        self._release.set()
        self._server.shutdown()
        self._server.server_close()


def _config() -> dict[str, Any]:
    return yaml.safe_load(_LITELLM_CONFIG.read_text(encoding="utf-8"))


def _deadline(config: dict[str, Any], alias: str) -> float | None:
    for entry in config["model_list"]:
        if entry["model_name"] == alias:
            return entry["litellm_params"].get("timeout")
    pytest.fail(f"el alias '{alias}' no está en {_LITELLM_CONFIG}")


def _router_settings(config: dict[str, Any]) -> dict[str, Any]:
    """``router_settings`` filtrado como lo filtra el proxy (proxy_server.py)."""
    from litellm import Router

    valid = set(Router.get_valid_args()) - {"model_list"}
    return {k: v for k, v in (config.get("router_settings") or {}).items() if k in valid}


def _retries_on_timeout(settings: dict[str, Any], alias: str) -> int:
    """Cuántas veces reintenta el router un deployment de ``alias`` que venció su tope."""
    for policy in (
        (settings.get("model_group_retry_policy") or {}).get(alias) or {},
        settings.get("retry_policy") or {},
    ):
        if policy.get("TimeoutErrorRetries") is not None:
            return int(policy["TimeoutErrorRetries"])
    return int(settings.get("num_retries") or 0)


def _router(config: dict[str, Any], deepseek: _Upstream, gemini: _Upstream) -> Any:
    """El Router del proxy con DeepSeek → ``deepseek`` y Gemini → ``gemini``."""
    from litellm import Router

    model_list = []
    for entry in config["model_list"]:
        params = dict(entry["litellm_params"])
        params["api_key"] = "sk-fake-para-el-upstream-falso"
        is_deepseek = params["model"].startswith("deepseek/")
        params["api_base"] = deepseek.base_url if is_deepseek else gemini.base_url
        if is_deepseek and params.get("timeout") is not None:
            params["timeout"] = _SCALED_DEADLINE_S
        model_list.append({"model_name": entry["model_name"], "litellm_params": params})
    return Router(model_list=model_list, **_router_settings(config))


@pytest.fixture
def _quiet_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm

    monkeypatch.setattr(litellm, "drop_params", True)
    monkeypatch.setattr(litellm, "suppress_debug_info", True)
    # Con HTTP(S)_PROXY en el env el cliente mandaría el loopback por el proxy.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.mark.parametrize("alias", _DEEPSEEK_ALIASES)
def test_the_deepseek_deadline_sits_between_a_healthy_turn_and_the_keepalive(
    alias: str,
) -> None:
    """El tope REAL: vence antes de la línea vacía, deja pasar un turno sano y cabe en 5 min."""
    config = _config()
    deadline = _deadline(config, alias)
    assert deadline is not None, (
        f"'{alias}' no tiene `timeout`: un DeepSeek colgado espera hasta que Temporal "
        "corta la activity a los 5 min, y gemini-backup nunca entra (2026-10-15)"
    )
    assert deadline <= _MAX_DEADLINE_S, (
        f"'{alias}': timeout {deadline} s. DeepSeek saturado manda una línea vacía "
        f"cada {_DEEPSEEK_KEEPALIVE_TICK_S:.0f} s y el timeout de litellm es por "
        f"lectura: tiene que vencer antes, con holgura (≤ {_MAX_DEADLINE_S:.0f} s)"
    )
    assert deadline >= _MIN_DEADLINE_S, (
        f"'{alias}': timeout {deadline} s corta turnos sanos; la llamada sana más "
        f"lenta medida tardó {_SLOWEST_HEALTHY_TURN_S} s (≥ {_MIN_DEADLINE_S:.1f} s)"
    )
    attempts = 1 + _retries_on_timeout(_router_settings(config), alias)
    assert deadline * attempts <= _BACKUP_MUST_START_BY_S, (
        f"'{alias}': {attempts} intento(s) × {deadline} s antes del respaldo; el "
        f"respaldo tiene que entrar antes de {_BACKUP_MUST_START_BY_S:.0f} s"
    )


@pytest.mark.usefixtures("_quiet_litellm")
@pytest.mark.parametrize("hang", ["silencio", "lineas-vacias"])
@pytest.mark.parametrize("alias", _DEEPSEEK_ALIASES)
async def test_a_hung_deepseek_is_answered_by_the_backup(alias: str, hang: str) -> None:
    """DeepSeek colgado → contesta ``gemini-backup`` al vencer el tope, no a los 5 min."""
    config = _config()
    real_deadline = _deadline(config, alias)
    scale = _SCALED_DEADLINE_S / real_deadline if real_deadline else 1.0
    tick_s = _DEEPSEEK_KEEPALIVE_TICK_S * scale
    deepseek = _Upstream(hang=hang, tick_s=tick_s)
    gemini = _Upstream(_GEMINI_OK)
    try:
        router = _router(config, deepseek, gemini)
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                router.acompletion(model=alias, messages=[{"role": "user", "content": "hola"}]),
                timeout=_STILL_HUNG_AFTER_S,
            )
        except asyncio.TimeoutError:
            pytest.fail(
                f"DeepSeek '{alias}' colgado ({hang}): a los {_STILL_HUNG_AFTER_S:.0f} s "
                f"nadie contestó (timeout del YAML: {real_deadline!r}). En prod el cliente "
                "espera hasta que Temporal corta la activity a los 5 min y "
                "gemini-backup nunca entra (2026-10-15 01:30 UTC)."
            )
        elapsed = time.monotonic() - started
    finally:
        deepseek.shutdown()
        gemini.shutdown()

    assert response.choices[0].message.content == "respuesta del respaldo"
    assert gemini.requests == 1
    attempts = 1 + _retries_on_timeout(_router_settings(config), alias)
    assert deepseek.requests == attempts, (
        f"el router intentó DeepSeek {deepseek.requests} veces; el presupuesto de "
        f"tiempo supone {attempts}"
    )
    assert elapsed < _SCALED_DEADLINE_S * attempts + 3, (
        f"el respaldo contestó a los {elapsed:.1f} s; con el tope escalado a "
        f"{_SCALED_DEADLINE_S} s × {attempts} intento(s) debía llegar antes"
    )
    if hang == "lineas-vacias":
        # Medido en los servidores: de la petición a DeepSeek a la de Gemini.
        waited = gemini.arrivals[0] - deepseek.arrivals[0]
        assert waited < tick_s, (
            f"el respaldo se pidió a los {waited:.2f} s, después de la primera línea "
            f"vacía ({tick_s:.2f} s escalados = {_DEEPSEEK_KEEPALIVE_TICK_S:.0f} s "
            "reales): el tope tiene que vencer ANTES, o la línea lo reinicia"
        )


@pytest.mark.usefixtures("_quiet_litellm")
async def test_the_limit_a_keepalive_faster_than_the_timeout_is_never_cut() -> None:
    """LÍMITE del arreglo: con una línea vacía más seguido que el timeout, no corta.

    El ``timeout`` de litellm es por lectura. El arreglo funciona porque DeepSeek
    manda la línea vacía cada 60 s y el tope es de 30 s. Ese intervalo DeepSeek NO
    lo documenta (lo medimos: 13 de 13, 2026-10-15). Si un día lo baja por debajo
    del tope (acá: cada 15 s reales), el tope nunca vence y vuelve el cuelgue del
    2026-10-15. La defensa completa sería un tope TOTAL en la activity ``llm_chat``
    que caiga al respaldo (seguimiento, no implementado). Si este caso se pone
    rojo, litellm empezó a cortar por tiempo total: revisar si el seguimiento sobra.
    """
    config = _config()
    alias = "deepseek-v4-flash"
    real_deadline = _deadline(config, alias)
    assert real_deadline is not None, f"'{alias}' no tiene `timeout`"
    deepseek = _Upstream(hang="lineas-vacias", tick_s=_SCALED_DEADLINE_S / 2)
    gemini = _Upstream(_GEMINI_OK)
    try:
        router = _router(config, deepseek, gemini)
        call = asyncio.ensure_future(
            router.acompletion(model=alias, messages=[{"role": "user", "content": "hola"}])
        )
        done, _ = await asyncio.wait({call}, timeout=_SCALED_DEADLINE_S * 3)
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
    finally:
        deepseek.shutdown()
        gemini.shutdown()

    assert not done, (
        "con una línea vacía cada medio tope, la llamada terminó: el timeout de "
        "litellm ya no es solo por lectura. Revisar si el tope total en llm_chat "
        "(seguimiento) sigue haciendo falta."
    )
    assert gemini.requests == 0


@pytest.mark.usefixtures("_quiet_litellm")
@pytest.mark.parametrize("alias", _DEEPSEEK_ALIASES)
async def test_the_slowest_healthy_turn_is_still_answered_by_deepseek(alias: str) -> None:
    """El tope no le quita a DeepSeek un turno sano lento: no hay respaldo de más."""
    config = _config()
    real_deadline = _deadline(config, alias)
    assert real_deadline is not None, f"'{alias}' no tiene `timeout`"
    scale = _SCALED_DEADLINE_S / real_deadline
    deepseek = _Upstream(_DEEPSEEK_OK, delay_s=_SLOWEST_HEALTHY_TURN_S * scale)
    gemini = _Upstream(_GEMINI_OK)
    try:
        router = _router(config, deepseek, gemini)
        response = await asyncio.wait_for(
            router.acompletion(model=alias, messages=[{"role": "user", "content": "hola"}]),
            timeout=_STILL_HUNG_AFTER_S,
        )
    finally:
        deepseek.shutdown()
        gemini.shutdown()

    assert response.choices[0].message.content == "respuesta de deepseek"
    assert (deepseek.requests, gemini.requests) == (1, 0)
