"""Guard: ningún alias del proxy litellm apunta a un id upstream de vida corta.

Hallazgo 2026-09-18 (lección L-23). El alias ``gemini-backup`` apuntaba a
``gemini/gemini-3.1-flash-lite-preview``. Según la tabla oficial de Google
(ai.google.dev/gemini-api/docs/deprecations, actualizada 2026-09-17) ese preview
salió el 2026-03-03 y se APAGÓ el 2026-05-25, con reemplazo
``gemini-3.1-flash-lite``; el aviso fue de ~2,5 semanas (changelog del
2026-05-07). Nadie se enteró, y no es casualidad — los consumidores del alias
tragan el error:

  1. es el FALLBACK del router para el agente de ventas cuando DeepSeek falla
     (``router_settings.fallbacks``): un fallback roto solo se ve el día que
     DeepSeek se cae, que es justo cuando hace falta;
  2. es el default de ``CUSTOMER_SUMMARY_MODEL``, cuyo consumidor
     (``customer_scoring/llm_summary.py``) es never-raises → falla en silencio.

Verificado contra PROD (por SSM, dentro del contenedor litellm v1.86.2, con la
key real): el id viejo HOY responde 200 porque Google lo redirige en silencio
al GA (``modelVersion=gemini-3.1-flash-lite``), aunque su propia tabla dice que
un modelo apagado "is completely turned off". Producción vivía de una cortesía
sin fecha de un id dado de baja.

Lo que fija este guard, sobre los archivos REALES que el deploy monta:

  - un id con marcador de vida corta (``preview`` / ``exp`` / ``experimental`` /
    ``latest`` / ``beta`` / ``alpha``) exige una excepción EXPLÍCITA y FECHADA,
    atada al id exacto y con vencimiento: pasada la fecha el guard falla hasta
    que alguien vuelva a mirar la tabla de deprecations;
  - un alias que atiende CLIENTES (toda la cadena de failover del router) no
    admite excepción: ahí el id tiene que ser estable;
  - el id va ESCRITO en el YAML: por env (``os.environ/…``) o con comodín no se
    puede auditar, y eso también es un problema;
  - los YAML no traen claves duplicadas (PyYAML se queda con la última sin
    avisar: el mismo typo sobre ``model:`` re-rutea un alias en silencio);
  - la tabla de precios de OpenLIT describe al modelo que el alias sirve HOY.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
_LITELLM_CONFIG = _REPO / "exoclaw-temporal" / "litellm_config.yaml"
_K8S_CONFIGMAP = (
    _REPO / "hubara_agency" / "k8s" / "aws-produccion" / "litellm-configmap.yaml"
)
_PRICING = _REPO / "hubara_agency" / "deploy" / "openlit" / "pricing.json"
_PLATFORM_CONFIG = _REPO / "hubara_agency" / "src" / "platform" / "config.py"

_DEPRECATIONS_URL = "https://ai.google.dev/gemini-api/docs/deprecations"

# Tokens del id upstream que delatan vida corta. Por TOKEN (no substring) para
# no marcar ids legítimos que contengan "exp" dentro de otra palabra.
_SHORT_LIVED_TOKENS = frozenset(
    {"preview", "exp", "experimental", "latest", "beta", "alpha"}
)
# Claves de failover de litellm. Las tres primeras son listas de {primario:
# [backups]}; `default_fallbacks` es una lista plana de alias.
_FALLBACK_MAP_KEYS = ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks")


@dataclass(frozen=True)
class PreviewException:
    """Permiso para que UN alias apunte a UN id de vida corta, con vencimiento."""

    upstream: str  # id EXACTO (con prefijo de provider): si cambia, deja de aplicar
    reviewed_on: dt.date  # cuándo se miró la tabla de deprecations
    review_by: dt.date  # pasada esta fecha el guard falla hasta re-revisar
    reason: str


# Única excepción vigente. Para renovarla: abrir _DEPRECATIONS_URL, confirmar que
# el id sigue SIN fecha de shutdown y mover las dos fechas (máximo 120 días). Si
# ya tiene fecha, no se renueva: se migra el alias.
PREVIEW_EXCEPTIONS: dict[str, PreviewException] = {
    "gemini-pro-judge": PreviewException(
        upstream="gemini/gemini-3.1-pro-preview",
        reviewed_on=dt.date(2026, 9, 18),
        review_by=dt.date(2026, 12, 18),
        reason=(
            "juez de los evals (golden del CI + scorecard online); preview a "
            "propósito — no atiende clientes — y sin fecha de shutdown en la "
            "tabla oficial al revisar"
        ),
    ),
}

_MAX_EXCEPTION_WINDOW_DAYS = 120


# ── carga estricta ─────────────────────────────────────────────────────────────


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader que rechaza claves duplicadas en vez de pisarlas en silencio."""


def _construct_mapping_without_duplicates(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"clave duplicada {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_without_duplicates,
)


def _load_yaml_strict(text: str) -> Any:
    return yaml.load(text, Loader=_StrictLoader)  # noqa: S506 — subclase de SafeLoader


_COMPOSE = "compose (prod + local)"
_K8S = "k8s configmap"
_SOURCES = (_COMPOSE, _K8S)


def _proxy_config_text(source: str) -> str:
    """El YAML del proxy tal como lo monta cada despliegue."""
    if source == _COMPOSE:
        return _LITELLM_CONFIG.read_text(encoding="utf-8")
    configmap = _load_yaml_strict(_K8S_CONFIGMAP.read_text(encoding="utf-8"))
    return configmap["data"]["config.yaml"]


def _proxy_config(source: str) -> dict[str, Any]:
    """Carga TOLERANTE: cada test reporta SU problema aunque haya claves repetidas."""
    return yaml.safe_load(_proxy_config_text(source))


# ── el chequeo (puro: se prueba con configs sintéticas más abajo) ──────────────


def _aliases(config: dict[str, Any]) -> dict[str, str]:
    return {
        entry["model_name"]: entry["litellm_params"]["model"]
        for entry in config["model_list"]
    }


def _is_short_lived(upstream: str) -> bool:
    return not _SHORT_LIVED_TOKENS.isdisjoint(re.split(r"[-_./:@]", upstream.lower()))


def _is_auditable(upstream: str) -> bool:
    """Falso si el id real no está escrito en el repo (env o comodín)."""
    return not upstream.startswith("os.environ/") and "*" not in upstream


def _customer_facing_aliases(config: dict[str, Any]) -> set[str]:
    """Alias que pueden contestarle a un cliente: toda la cadena de failover."""
    out: set[str] = set()
    for section in ("router_settings", "litellm_settings"):
        settings = config.get(section) or {}
        for key in _FALLBACK_MAP_KEYS:
            for rule in settings.get(key) or []:
                for primary, backups in rule.items():
                    out.add(primary)
                    out.update(backups)
        out.update(settings.get("default_fallbacks") or [])
    return out


def short_lived_id_problems(
    config: dict[str, Any],
    exceptions: dict[str, PreviewException],
    *,
    today: dt.date,
) -> list[str]:
    """Problemas de vida corta de un config del proxy. Lista vacía = sano."""
    problems: list[str] = []
    aliases = _aliases(config)
    customer_facing = _customer_facing_aliases(config)
    for alias, upstream in sorted(aliases.items()):
        if not _is_auditable(upstream):
            problems.append(
                f"`{alias}` → `{upstream}`: el id real no está en el repo (env o "
                "comodín), así que no se puede auditar; escribir el id explícito"
            )
            continue
        if not _is_short_lived(upstream):
            continue
        exc = exceptions.get(alias)
        if alias in customer_facing:
            problems.append(
                f"`{alias}` → `{upstream}`: este alias atiende CLIENTES (failover "
                "del router) y no admite excepción; usar un id estable"
            )
        elif exc is None or exc.upstream != upstream:
            problems.append(
                f"`{alias}` → `{upstream}`: id de vida corta sin excepción fechada "
                f"para ese id exacto. Migrar a un id estable ({_DEPRECATIONS_URL}) "
                "o declarar la excepción en PREVIEW_EXCEPTIONS"
            )
        elif today > exc.review_by:
            problems.append(
                f"`{alias}` → `{upstream}`: la excepción venció el {exc.review_by} "
                f"(revisada el {exc.reviewed_on}). Abrir {_DEPRECATIONS_URL}: si el "
                "id sigue sin fecha de shutdown, renovar las dos fechas; si ya "
                "tiene fecha, migrar el alias"
            )
    return problems


def stale_exception_problems(
    configs: list[dict[str, Any]], exceptions: dict[str, PreviewException]
) -> list[str]:
    """Excepciones que ya no tapan nada, o que se dieron un plazo sin límite."""
    problems: list[str] = []
    for alias, exc in sorted(exceptions.items()):
        if not any(_aliases(config).get(alias) == exc.upstream for config in configs):
            problems.append(
                f"la excepción de `{alias}` → `{exc.upstream}` ya no corresponde a "
                "ningún config: borrarla (una excepción huérfana es un permiso en "
                "blanco para el próximo que reuse el nombre)"
            )
        window = (exc.review_by - exc.reviewed_on).days
        if not 0 < window <= _MAX_EXCEPTION_WINDOW_DAYS:
            problems.append(
                f"la excepción de `{alias}` dura {window} días; el máximo es "
                f"{_MAX_EXCEPTION_WINDOW_DAYS} (un preview se apaga con semanas de aviso)"
            )
    return problems


# ── los archivos reales ────────────────────────────────────────────────────────


@pytest.mark.parametrize("source", _SOURCES)
def test_proxy_config_has_no_duplicate_keys(source: str) -> None:
    """Carga estricta: una clave repetida dentro de un mapping es un error."""
    try:
        _load_yaml_strict(_proxy_config_text(source))
    except yaml.constructor.ConstructorError as exc:
        pytest.fail(f"{source}: {exc.problem} ({exc.problem_mark})")


@pytest.mark.parametrize("source", _SOURCES)
def test_no_alias_carries_a_null_litellm_param(source: str) -> None:
    """Un ``clave:`` sin valor llega a litellm como kwarg ``None``: es basura."""
    nulls = {
        entry["model_name"]: sorted(
            key for key, value in entry["litellm_params"].items() if value is None
        )
        for entry in _proxy_config(source)["model_list"]
    }
    assert {alias: keys for alias, keys in nulls.items() if keys} == {}


@pytest.mark.parametrize("source", _SOURCES)
def test_no_alias_points_to_a_short_lived_upstream_id(source: str) -> None:
    problems = short_lived_id_problems(
        _proxy_config(source), PREVIEW_EXCEPTIONS, today=dt.date.today()
    )
    assert problems == [], "\n".join(problems)


def test_every_exception_still_covers_a_real_alias_and_has_a_bounded_window() -> None:
    configs = [_proxy_config(source) for source in _SOURCES]
    problems = stale_exception_problems(configs, PREVIEW_EXCEPTIONS)
    assert problems == [], "\n".join(problems)


def test_router_fallbacks_only_name_registered_aliases() -> None:
    """Un fallback hacia un alias inexistente falla igual de callado que un 404."""
    config = _proxy_config(_COMPOSE)
    assert _customer_facing_aliases(config) <= set(_aliases(config))


def test_customer_summary_default_is_a_registered_alias() -> None:
    """``llm_summary`` es never-raises: un alias colgado se vería como resumen vacío."""
    source = _PLATFORM_CONFIG.read_text(encoding="utf-8")
    match = re.search(
        r'"CUSTOMER_SUMMARY_MODEL",\s*"litellm_proxy/([A-Za-z0-9._-]+)"', source
    )
    assert match, "no encontré el default de CUSTOMER_SUMMARY_MODEL en platform/config.py"
    assert match.group(1) in _aliases(_proxy_config(_COMPOSE))


# ── comportamiento: qué id SALE cuando el primario se cae ─────────────────────
# El YAML es schema; el gotcha #1 del repo es que el schema verde miente. Mismo
# patrón que test_llm_thinking_disabled.py: el proxy litellm ES un Router, así que
# se monta el model_list + fallbacks REALES contra upstreams falsos y se mira la
# request que sale hacia Google cuando DeepSeek responde 500.


class _FakeUpstream:
    """Servidor HTTP local: registra cada (path, body) y contesta lo configurado."""

    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — API de BaseHTTPRequestHandler
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    received = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    received = {}
                outer.requests.append((self.path, received))
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:  # silencio en pytest
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


_GEMINI_OK = {
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"text": "respuesta del fallback"}]},
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 3,
        "candidatesTokenCount": 4,
        "totalTokenCount": 7,
    },
}


async def test_when_the_primary_fails_the_request_leaves_for_a_stable_gemini_id() -> None:
    """DeepSeek 500 → el failover REAL del YAML pide un id estable a Google."""
    import litellm
    from litellm import Router

    litellm.drop_params = True
    litellm.suppress_debug_info = True
    config = _proxy_config(_COMPOSE)
    primary = "deepseek-v4-flash"
    deepseek = _FakeUpstream(500, {"error": {"message": "upstream caído (test)"}})
    gemini = _FakeUpstream(200, _GEMINI_OK)
    model_list = []
    for entry in config["model_list"]:
        params = dict(entry["litellm_params"])
        params["api_key"] = "sk-fake-para-el-upstream-falso"
        params["api_base"] = (
            deepseek.base_url if params["model"].startswith("deepseek/") else gemini.base_url
        )
        model_list.append({"model_name": entry["model_name"], "litellm_params": params})
    try:
        router = Router(
            model_list=model_list,
            fallbacks=config["router_settings"]["fallbacks"],
            num_retries=0,
        )
        response = await router.acompletion(
            model=primary, messages=[{"role": "user", "content": "hola"}]
        )
    finally:
        deepseek.shutdown()
        gemini.shutdown()

    assert deepseek.requests, "el primario nunca se intentó: el test no prueba el failover"
    assert response.choices[0].message.content == "respuesta del fallback"
    assert len(gemini.requests) == 1
    path = gemini.requests[0][0]
    requested = re.search(r"/models/([^:/?]+):generateContent", path)
    assert requested, f"no reconozco la ruta que litellm le pidió a Google: {path!r}"
    assert not _is_short_lived(requested.group(1)), (
        f"el failover del agente pide `{requested.group(1)}` a Google: id de vida corta"
    )
    assert requested.group(1) == _aliases(config)["gemini-backup"].split("/", 1)[-1]


# ── precios: la tabla describe al modelo que el alias sirve HOY ────────────────


def _pricing_chat_table() -> dict[str, dict[str, float]]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [key for key, _ in pairs]
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        # Dos ramas que agregan la misma clave en líneas distintas mergean LIMPIO
        # y json se queda con la última: el precio que gana lo decide el orden.
        assert not repeated, f"pricing.json repite claves: {repeated}"
        return dict(pairs)

    data = json.loads(
        _PRICING.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates
    )
    return data["chat"]


def test_priced_aliases_are_priced_as_the_model_they_serve_today() -> None:
    """Re-apuntar un alias sin tocar la tabla atribuye el costo de OTRO modelo."""
    prices = _pricing_chat_table()
    drift: dict[str, str] = {}
    for alias, upstream in _aliases(_proxy_config(_COMPOSE)).items():
        if alias not in prices:
            continue
        upstream_id = upstream.split("/", 1)[-1]
        if upstream_id not in prices:
            drift[alias] = f"falta el precio del id upstream `{upstream_id}`"
        elif prices[upstream_id] != prices[alias]:
            drift[alias] = (
                f"el alias cobra {prices[alias]} pero `{upstream_id}` cobra "
                f"{prices[upstream_id]}"
            )
    assert drift == {}


def test_gemini_backup_is_priced_at_the_official_list_price() -> None:
    """USD por 1000 tokens. gemini-3.5-flash-lite: $0.30 in / $2.50 out por 1M
    (ai.google.dev/gemini-api/docs/pricing, revisado 2026-09-18)."""
    assert _pricing_chat_table()["gemini-backup"] == {
        "promptPrice": 0.0003,
        "completionPrice": 0.0025,
    }


# ── el chequeo mismo (un guard que nunca se vio fallar no protege nada) ────────

_TODAY = dt.date(2026, 9, 18)


def _config(aliases: dict[str, str], fallbacks: list[dict[str, list[str]]] | None = None):
    return {
        "model_list": [
            {"model_name": name, "litellm_params": {"model": upstream}}
            for name, upstream in aliases.items()
        ],
        "router_settings": {"fallbacks": fallbacks or []},
    }


def _exception(upstream: str, *, review_by: dt.date = dt.date(2026, 12, 18)):
    return PreviewException(
        upstream=upstream, reviewed_on=_TODAY, review_by=review_by, reason="test"
    )


def test_checker_passes_stable_ids() -> None:
    config = _config(
        {
            "a": "gemini/gemini-3.5-flash-lite",
            "b": "deepseek/deepseek-flash",
            # "expert"/"export" contienen "exp" pero no son el TOKEN exp.
            "c": "acme/expert-export-v2",
        }
    )
    assert short_lived_id_problems(config, {}, today=_TODAY) == []


@pytest.mark.parametrize(
    "upstream",
    [
        "gemini/gemini-3.1-flash-lite-preview",
        "gemini/gemini-2.5-flash-preview-09-2025",
        "gemini/gemini-2.0-flash-exp",
        "gemini/gemini-flash-lite-latest",
        "vertex_ai/some-model-experimental",
        "xai/grok-beta",
        "acme/chat-alpha-2",
    ],
)
def test_checker_flags_short_lived_ids_without_exception(upstream: str) -> None:
    problems = short_lived_id_problems(_config({"x": upstream}), {}, today=_TODAY)
    assert len(problems) == 1 and "sin excepción fechada" in problems[0]


def test_checker_accepts_a_live_exception_for_that_exact_id() -> None:
    config = _config({"judge": "gemini/gemini-3.1-pro-preview"})
    exceptions = {"judge": _exception("gemini/gemini-3.1-pro-preview")}
    assert short_lived_id_problems(config, exceptions, today=_TODAY) == []


def test_checker_exception_does_not_cover_a_different_preview_id() -> None:
    """Cambiar el id bajo el mismo alias no hereda el permiso del anterior."""
    config = _config({"judge": "gemini/gemini-4-pro-preview"})
    exceptions = {"judge": _exception("gemini/gemini-3.1-pro-preview")}
    problems = short_lived_id_problems(config, exceptions, today=_TODAY)
    assert len(problems) == 1 and "sin excepción fechada" in problems[0]


def test_checker_fails_once_the_exception_expires() -> None:
    config = _config({"judge": "gemini/gemini-3.1-pro-preview"})
    exceptions = {
        "judge": _exception(
            "gemini/gemini-3.1-pro-preview", review_by=dt.date(2026, 12, 18)
        )
    }
    assert short_lived_id_problems(config, exceptions, today=dt.date(2026, 12, 18)) == []
    problems = short_lived_id_problems(
        config, exceptions, today=dt.date(2026, 12, 19)
    )
    assert len(problems) == 1 and "venció" in problems[0]


def test_checker_refuses_exceptions_on_customer_facing_aliases() -> None:
    """El caso de este incidente: el fallback del agente NUNCA puede ser preview."""
    config = _config(
        {
            "agent": "deepseek/deepseek-flash",
            "backup": "gemini/gemini-3.1-flash-lite-preview",
        },
        fallbacks=[{"agent": ["backup"]}],
    )
    exceptions = {"backup": _exception("gemini/gemini-3.1-flash-lite-preview")}
    problems = short_lived_id_problems(config, exceptions, today=_TODAY)
    assert len(problems) == 1 and "atiende CLIENTES" in problems[0]


@pytest.mark.parametrize("upstream", ["os.environ/BACKUP_MODEL_ID", "gemini/*"])
def test_checker_flags_upstream_ids_it_cannot_audit(upstream: str) -> None:
    """Un id por env o un comodín esquivan el guard: el id real no está en el repo."""
    problems = short_lived_id_problems(_config({"x": upstream}), {}, today=_TODAY)
    assert len(problems) == 1 and "no se puede auditar" in problems[0]


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("router_settings", "fallbacks", [{"agent": ["backup"]}]),
        ("router_settings", "context_window_fallbacks", [{"agent": ["backup"]}]),
        ("router_settings", "content_policy_fallbacks", [{"agent": ["backup"]}]),
        ("router_settings", "default_fallbacks", ["backup"]),
        ("litellm_settings", "fallbacks", [{"agent": ["backup"]}]),
        ("litellm_settings", "default_fallbacks", ["backup"]),
    ],
)
def test_checker_treats_every_failover_form_as_customer_facing(
    section: str, key: str, value: list[Any]
) -> None:
    config = _config(
        {
            "agent": "deepseek/deepseek-flash",
            "backup": "gemini/gemini-3.1-flash-lite-preview",
        }
    )
    config["router_settings"] = {}
    config[section] = {key: value}
    exceptions = {"backup": _exception("gemini/gemini-3.1-flash-lite-preview")}
    problems = short_lived_id_problems(config, exceptions, today=_TODAY)
    assert len(problems) == 1 and "atiende CLIENTES" in problems[0]


def test_checker_reports_orphan_and_unbounded_exceptions() -> None:
    config = _config({"judge": "gemini/gemini-3.5-pro"})
    orphan = {"judge": _exception("gemini/gemini-3.1-pro-preview")}
    assert "ya no corresponde" in stale_exception_problems([config], orphan)[0]

    config = _config({"judge": "gemini/gemini-3.1-pro-preview"})
    forever = {
        "judge": _exception(
            "gemini/gemini-3.1-pro-preview", review_by=dt.date(2028, 1, 1)
        )
    }
    assert "el máximo es" in stale_exception_problems([config], forever)[0]


def test_strict_loader_rejects_duplicate_keys() -> None:
    with pytest.raises(yaml.constructor.ConstructorError, match="clave duplicada"):
        _load_yaml_strict("a:\n  model: x\n  api_key: k\n  api_key: k\n")
    assert _load_yaml_strict("a:\n  model: x\n  api_key: k\n") == {
        "a": {"model": "x", "api_key": "k"}
    }
