"""Guard: los alias del proxy litellm solo apuntan a ids upstream REVISADOS y vigentes.

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
sin fecha de un id dado de baja. Misma clase, 8 días antes y SIN marcador en el
nombre: ``deepseek-v4-flash`` retirado y redirigido (ver
``test_llm_thinking_disabled.py``). Por eso este guard no mira solo el nombre.

Lo que fija, sobre los archivos REALES que el deploy monta:

  - TODO id upstream tiene una constancia FECHADA de que alguien miró la tabla
    de ciclo de vida del proveedor (``REVIEWED_UPSTREAM_IDS``). Elegir un id es
    el momento en que se decide cuánto va a durar: ahí se mira;
  - si la tabla publica fecha de apagado, el guard se pone rojo 60 días antes;
  - un id con marcador de vida corta (``preview`` / ``exp`` / ``latest`` / …)
    solo entra con permiso EXPLÍCITO: para ese alias, con razón y con
    VENCIMIENTO (pasada la fecha el guard falla hasta volver a mirar la tabla);
  - un alias que atiende CLIENTES (toda la cadena de failover del router) no
    admite id de vida corta, con permiso o sin él;
  - el id va ESCRITO en el YAML: por env (``os.environ/…``) o con comodín no se
    puede auditar, y eso también es un problema;
  - los YAML no traen claves duplicadas (PyYAML se queda con la última sin
    avisar: el mismo typo sobre ``model:`` re-rutea un alias en silencio);
  - la tabla de precios de OpenLIT describe al modelo que el alias sirve HOY.

LÍMITE HONESTO: un guard estático no ve un apagado que el proveedor anuncie
DESPUÉS de la revisión. Acota el daño (constancia fechada + vencimientos), no
reemplaza mirar la tabla ni un chequeo en vivo de qué modelo contesta.

Corre en CI (``architecture-gates.yml`` → "Guards del proxy LLM"): ``tests/platform``
completo NO corre ahí, así que sin ese step este archivo solo valdría en local.
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

_LIFECYCLE_GOOGLE = "https://ai.google.dev/gemini-api/docs/deprecations"
_LIFECYCLE_DEEPSEEK = "https://api-docs.deepseek.com/quick_start/pricing"
_LIFECYCLE_OPENAI = "https://developers.openai.com/api/docs/deprecations"

# Marcadores de vida corta. Se comparan por TOKEN (no substring, para no marcar
# "expert"/"export") y toleran dígitos pegados: `exp1206`, `rc1`, `preview2`.
_SHORT_LIVED_TOKEN = re.compile(
    r"^(preview|exp|experimental|latest|beta|alpha|nightly|rc|dev)\d*$"
)
# Claves de failover de litellm. Las tres primeras son listas de {primario:
# [backups]}; `default_fallbacks` es una lista plana de alias.
_FALLBACK_MAP_KEYS = ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks")

_SHUTDOWN_WARNING_DAYS = 60
_MAX_SHORT_LIVED_WINDOW_DAYS = 120


@dataclass(frozen=True)
class UpstreamReview:
    """Constancia de que alguien miró el ciclo de vida de UN id upstream."""

    reviewed_on: dt.date  # cuándo se miró `source`
    source: str  # la tabla de ciclo de vida del proveedor
    shutdown: dt.date | None = None  # fecha de apagado PUBLICADA, si la hay
    # Solo para ids de vida corta: el permiso es por alias, con razón y vence.
    allowed_aliases: frozenset[str] = frozenset()
    review_by: dt.date | None = None
    reason: str = ""


# Un id nuevo entra acá DESPUÉS de mirar su tabla. Para renovar un permiso de
# vida corta: abrir `source`, confirmar que el id sigue SIN fecha de apagado y
# mover `reviewed_on` + `review_by` (máximo 120 días). Si ya tiene fecha, no se
# renueva: se migra el alias.
REVIEWED_UPSTREAM_IDS: dict[str, UpstreamReview] = {
    # Brazo C del laboratorio (alias `openrouter-perception`): snapshot con fecha,
    # sin apagado publicado (solo sus variantes de audio/realtime/transcribe).
    "openrouter/openai/gpt-4o-mini-2024-07-18": UpstreamReview(
        reviewed_on=dt.date(2026, 9, 23), source=_LIFECYCLE_OPENAI
    ),
    "deepseek/deepseek-flash": UpstreamReview(
        reviewed_on=dt.date(2026, 9, 18), source=_LIFECYCLE_DEEPSEEK
    ),
    "gemini/gemini-3.5-flash-lite": UpstreamReview(
        reviewed_on=dt.date(2026, 9, 18), source=_LIFECYCLE_GOOGLE
    ),
    "gemini/gemini-2.5-flash-lite": UpstreamReview(
        reviewed_on=dt.date(2026, 9, 18), source=_LIFECYCLE_GOOGLE
    ),
    "gemini/gemini-3.1-pro-preview": UpstreamReview(
        reviewed_on=dt.date(2026, 9, 18),
        source=_LIFECYCLE_GOOGLE,
        allowed_aliases=frozenset({"gemini-pro-judge"}),
        review_by=dt.date(2026, 12, 18),
        reason=(
            "juez de los evals (golden del CI + scorecard online); preview a "
            "propósito — no atiende clientes — y sin fecha de apagado publicada"
        ),
    ),
}


# ── carga estricta ─────────────────────────────────────────────────────────────


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader que rechaza claves duplicadas en vez de pisarlas en silencio."""


def _construct_mapping_without_duplicates(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            continue  # `<<: *ancla` no es una clave; pisar una heredada es legítimo
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"clave duplicada {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)  # resuelve los merge keys


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


def _deployments(config: dict[str, Any]) -> list[tuple[str, str]]:
    """TODOS los (alias, id upstream). litellm admite N deployments por alias:
    un dict por alias escondería al resto detrás del último."""
    return [
        (entry["model_name"], entry["litellm_params"]["model"])
        for entry in config["model_list"]
    ]


def _is_short_lived(upstream: str) -> bool:
    return any(
        _SHORT_LIVED_TOKEN.match(token)
        for token in re.split(r"[-_./:@]", upstream.lower())
    )


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
    out.discard("*")  # comodín de litellm ("cualquier alias"): no es un alias
    return out


def _short_lived_permit_problem(
    alias: str, review: UpstreamReview, today: dt.date
) -> str | None:
    if alias not in review.allowed_aliases:
        return "id de vida corta sin permiso para ESTE alias (`allowed_aliases`)"
    if review.review_by is None or not review.reason.strip():
        return "el permiso de vida corta exige `review_by` y `reason`"
    if today > review.review_by:
        return (
            f"el permiso venció el {review.review_by} (revisado el "
            f"{review.reviewed_on}). Abrir {review.source}: si el id sigue sin fecha "
            "de apagado, renovar las dos fechas; si ya tiene fecha, migrar el alias"
        )
    window = (review.review_by - review.reviewed_on).days
    if window > _MAX_SHORT_LIVED_WINDOW_DAYS:
        return (
            f"el permiso dura {window} días; el máximo es "
            f"{_MAX_SHORT_LIVED_WINDOW_DAYS} (un preview se apaga con semanas de aviso)"
        )
    return None


def upstream_id_problems(
    config: dict[str, Any],
    reviews: dict[str, UpstreamReview],
    *,
    today: dt.date,
) -> list[str]:
    """Problemas de ciclo de vida de un config del proxy. Lista vacía = sano."""
    problems: list[str] = []
    customer_facing = _customer_facing_aliases(config)
    for alias, upstream in _deployments(config):
        where = f"`{alias}` → `{upstream}`"
        if not _is_auditable(upstream):
            problems.append(
                f"{where}: el id real no está en el repo (env o comodín), así que "
                "no se puede auditar; escribir el id explícito"
            )
            continue
        short_lived = _is_short_lived(upstream)
        if short_lived and alias in customer_facing:
            problems.append(
                f"{where}: este alias atiende CLIENTES (cadena de failover del "
                "router) y no admite un id de vida corta; usar un id estable"
            )
            continue
        review = reviews.get(upstream)
        if review is None:
            problems.append(
                f"{where}: id sin revisión de ciclo de vida. Mirar la tabla del "
                "proveedor (fecha de apagado, reemplazo) y registrarlo en "
                "REVIEWED_UPSTREAM_IDS"
            )
            continue
        if review.reviewed_on > today:
            problems.append(
                f"{where}: `reviewed_on` {review.reviewed_on} está en el futuro"
            )
        if review.shutdown is not None and today >= review.shutdown - dt.timedelta(
            days=_SHUTDOWN_WARNING_DAYS
        ):
            problems.append(
                f"{where}: el proveedor apaga este id el {review.shutdown} "
                f"(faltan ≤{_SHUTDOWN_WARNING_DAYS} días o ya pasó). Migrar el alias"
            )
        if short_lived:
            permit_problem = _short_lived_permit_problem(alias, review, today)
            if permit_problem:
                problems.append(f"{where}: {permit_problem}")
    return problems


def orphan_review_problems(
    configs: list[dict[str, Any]], reviews: dict[str, UpstreamReview]
) -> list[str]:
    """Constancias de ids que ya nadie usa: un permiso en blanco para el próximo."""
    used = {upstream for config in configs for _, upstream in _deployments(config)}
    return [
        f"`{upstream}` está en REVIEWED_UPSTREAM_IDS pero ningún config lo usa: borrarlo"
        for upstream in sorted(set(reviews) - used)
    ]


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
def test_every_upstream_id_is_reviewed_and_alive(source: str) -> None:
    problems = upstream_id_problems(
        _proxy_config(source), REVIEWED_UPSTREAM_IDS, today=dt.date.today()
    )
    assert problems == [], "\n".join(problems)


def test_no_review_outlives_the_id_it_vouches_for() -> None:
    configs = [_proxy_config(source) for source in _SOURCES]
    problems = orphan_review_problems(configs, REVIEWED_UPSTREAM_IDS)
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("source", _SOURCES)
def test_router_fallbacks_only_name_registered_aliases(source: str) -> None:
    """Un fallback hacia un alias inexistente falla igual de callado que un 404."""
    config = _proxy_config(source)
    registered = {alias for alias, _ in _deployments(config)}
    assert _customer_facing_aliases(config) <= registered


@pytest.mark.parametrize("source", _SOURCES)
def test_customer_summary_default_is_a_registered_alias(source: str) -> None:
    """``llm_summary`` es never-raises: un alias colgado se vería como resumen vacío."""
    code = _PLATFORM_CONFIG.read_text(encoding="utf-8")
    match = re.search(
        r'"CUSTOMER_SUMMARY_MODEL",\s*"litellm_proxy/([A-Za-z0-9._-]+)"', code
    )
    assert match, "no encontré el default de CUSTOMER_SUMMARY_MODEL en platform/config.py"
    assert match.group(1) in {alias for alias, _ in _deployments(_proxy_config(source))}


# ── comportamiento: qué id SALE cuando el primario se cae ─────────────────────
# El YAML es schema; el gotcha #1 del repo es que el schema verde miente. Mismo
# patrón que test_llm_thinking_disabled.py: el proxy litellm ES un Router, así que
# se monta el model_list + fallbacks REALES contra upstreams falsos y se mira la
# request que sale hacia Google cuando DeepSeek responde 500.
#
# OJO versiones: esto corre con el litellm del uv.lock, que NO es el del proxy de
# prod (imagen ghcr.io/berriai/litellm pineada en infra/compose). Prueba el
# CABLEADO del failover y el id que sale; la compatibilidad del modelo con el
# litellm de prod se midió a mano dentro del contenedor (ver L-23).


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


async def test_when_the_primary_fails_the_request_leaves_for_a_stable_gemini_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek 500 → el failover REAL del YAML pide un id estable a Google."""
    import litellm
    from litellm import Router

    monkeypatch.setattr(litellm, "drop_params", True)
    monkeypatch.setattr(litellm, "suppress_debug_info", True)
    # Con HTTP(S)_PROXY en el env el cliente mandaría el loopback por el proxy.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

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
            timeout=20,
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
    backups = {u for alias, u in _deployments(config) if alias == "gemini-backup"}
    assert {f"gemini/{requested.group(1)}"} == backups


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
    for alias, upstream in _deployments(_proxy_config(_COMPOSE)):
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


def test_perception_alias_pins_the_dated_openai_snapshot_with_strict_routing() -> None:
    """Brazo C del laboratorio (plan §1.3): OpenAI con logprobs por OpenRouter.

    Snapshot con fecha (no el alias móvil `gpt-4o-mini`). Preferencias de
    proveedor fijas en el alias: solo OpenAI (el único que sirve logprobs de
    este modelo), sin fallback a otro proveedor, `require_parameters` (si
    OpenAI dejara de dar logprobs, OpenRouter RECHAZA en vez de ignorar el
    parámetro sin avisar) y sin retención de datos para entrenar. El alias no
    le contesta a clientes: no entra a la cadena de failover del router."""
    config = _proxy_config(_COMPOSE)
    entry = next(e for e in config["model_list"] if e["model_name"] == "openrouter-perception")
    params = entry["litellm_params"]

    assert params["model"] == "openrouter/openai/gpt-4o-mini-2024-07-18"
    assert params["api_key"] == "os.environ/OPENROUTER_API_KEY"
    assert params["extra_body"]["provider"] == {
        "order": ["openai"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
    }
    assert "openrouter-perception" not in _customer_facing_aliases(config)


def test_perception_alias_is_priced_at_the_openai_list_price() -> None:
    """USD por 1000 tokens. gpt-4o-mini: $0.15 in / $0.60 out por 1M
    (openrouter.ai/openai/gpt-4o-mini-2024-07-18, revisado 2026-09-23)."""
    assert _pricing_chat_table()["openrouter-perception"] == {
        "promptPrice": 0.00015,
        "completionPrice": 0.0006,
    }


# ── el chequeo mismo (un guard que nunca se vio fallar no protege nada) ────────

_TODAY = dt.date(2026, 9, 18)
_STABLE = "gemini/gemini-3.5-flash-lite"
_PREVIEW = "gemini/gemini-3.1-pro-preview"


def _config(
    deployments: list[tuple[str, str]] | dict[str, str],
    fallbacks: list[dict[str, list[str]]] | None = None,
) -> dict[str, Any]:
    pairs = list(deployments.items()) if isinstance(deployments, dict) else deployments
    return {
        "model_list": [
            {"model_name": alias, "litellm_params": {"model": upstream}}
            for alias, upstream in pairs
        ],
        "router_settings": {"fallbacks": fallbacks or []},
    }


def _reviewed(**overrides: Any) -> UpstreamReview:
    return UpstreamReview(**{"reviewed_on": _TODAY, "source": "https://proveedor", **overrides})


def _permit(alias: str = "judge", **overrides: Any) -> UpstreamReview:
    return _reviewed(
        **{
            "allowed_aliases": frozenset({alias}),
            "review_by": dt.date(2026, 12, 18),
            "reason": "test",
            **overrides,
        }
    )


def test_checker_passes_reviewed_stable_ids() -> None:
    config = _config(
        {
            "a": _STABLE,
            "b": "deepseek/deepseek-flash",
            # "expert"/"export" contienen "exp" pero no son el TOKEN exp.
            "c": "acme/expert-export-v2",
        }
    )
    reviews = {
        _STABLE: _reviewed(),
        "deepseek/deepseek-flash": _reviewed(),
        "acme/expert-export-v2": _reviewed(),
    }
    assert upstream_id_problems(config, reviews, today=_TODAY) == []


def test_checker_flags_an_id_nobody_reviewed() -> None:
    """Sin marcador en el nombre también: así entró `deepseek-v4-flash` retirado."""
    problems = upstream_id_problems(_config({"x": _STABLE}), {}, today=_TODAY)
    assert len(problems) == 1 and "sin revisión de ciclo de vida" in problems[0]


def test_checker_warns_before_a_published_shutdown() -> None:
    """El caso `gemini-3.1-flash-lite`: estable en el nombre, apagado el 2027-05-07."""
    upstream = "gemini/gemini-3.1-flash-lite"
    config = _config({"x": upstream})
    reviews = {upstream: _reviewed(shutdown=dt.date(2027, 5, 7))}

    assert upstream_id_problems(config, reviews, today=dt.date(2027, 3, 7)) == []
    for day in (dt.date(2027, 3, 8), dt.date(2027, 6, 1)):
        problems = upstream_id_problems(config, reviews, today=day)
        assert len(problems) == 1 and "apaga este id el 2027-05-07" in problems[0]


def test_checker_rejects_a_review_dated_in_the_future() -> None:
    config = _config({"x": _STABLE})
    reviews = {_STABLE: _reviewed(reviewed_on=dt.date(2027, 6, 1))}
    problems = upstream_id_problems(config, reviews, today=_TODAY)
    assert len(problems) == 1 and "está en el futuro" in problems[0]


def test_checker_audits_every_deployment_of_an_alias() -> None:
    """litellm balancea entre N deployments del mismo alias: se miran TODOS."""
    config = _config(
        [("backup", "gemini/gemini-3.1-flash-lite-preview"), ("backup", _STABLE)]
    )
    problems = upstream_id_problems(config, {_STABLE: _reviewed()}, today=_TODAY)
    assert len(problems) == 1 and "gemini-3.1-flash-lite-preview" in problems[0]


@pytest.mark.parametrize(
    "upstream",
    [
        "gemini/gemini-3.1-flash-lite-preview",
        "gemini/gemini-2.5-flash-preview-09-2025",
        "gemini/gemini-2.0-flash-exp",
        "gemini/gemini-exp1206",
        "gemini/gemini-flash-lite-latest",
        "vertex_ai/some-model-experimental",
        "xai/grok-beta",
        "acme/chat-alpha-2",
        "cohere/command-nightly",
        "acme/chat-v3-rc1",
        "acme/chat-preview2",
    ],
)
def test_checker_demands_a_permit_for_short_lived_ids(upstream: str) -> None:
    """Estar revisado no alcanza: vida corta exige permiso para ese alias."""
    problems = upstream_id_problems(
        _config({"x": upstream}), {upstream: _reviewed()}, today=_TODAY
    )
    assert len(problems) == 1 and "sin permiso para ESTE alias" in problems[0]


def test_checker_accepts_a_live_permit_for_that_alias_and_id() -> None:
    config = _config({"judge": _PREVIEW})
    assert upstream_id_problems(config, {_PREVIEW: _permit()}, today=_TODAY) == []


def test_checker_permit_does_not_cover_another_alias_or_another_id() -> None:
    other_alias = upstream_id_problems(
        _config({"multimodal": _PREVIEW}), {_PREVIEW: _permit("judge")}, today=_TODAY
    )
    assert len(other_alias) == 1 and "sin permiso para ESTE alias" in other_alias[0]

    other_id = upstream_id_problems(
        _config({"judge": "gemini/gemini-4-pro-preview"}),
        {_PREVIEW: _permit("judge")},
        today=_TODAY,
    )
    assert len(other_id) == 1 and "sin revisión de ciclo de vida" in other_id[0]


def test_checker_permit_needs_an_expiry_and_a_reason() -> None:
    config = _config({"judge": _PREVIEW})
    for broken in (_permit(review_by=None), _permit(reason="  ")):
        problems = upstream_id_problems(config, {_PREVIEW: broken}, today=_TODAY)
        assert len(problems) == 1 and "exige `review_by` y `reason`" in problems[0]


def test_checker_fails_once_the_permit_expires() -> None:
    config = _config({"judge": _PREVIEW})
    reviews = {_PREVIEW: _permit(review_by=dt.date(2026, 12, 18))}
    assert upstream_id_problems(config, reviews, today=dt.date(2026, 12, 18)) == []
    problems = upstream_id_problems(config, reviews, today=dt.date(2026, 12, 19))
    assert len(problems) == 1 and "venció" in problems[0]


def test_checker_caps_how_long_a_permit_can_last() -> None:
    config = _config({"judge": _PREVIEW})
    reviews = {_PREVIEW: _permit(review_by=dt.date(2028, 1, 1))}
    problems = upstream_id_problems(config, reviews, today=_TODAY)
    assert len(problems) == 1 and "el máximo es" in problems[0]


def test_checker_refuses_short_lived_ids_on_customer_facing_aliases() -> None:
    """El caso de este incidente: el fallback del agente NUNCA puede ser preview."""
    upstream = "gemini/gemini-3.1-flash-lite-preview"
    config = _config(
        {"agent": "deepseek/deepseek-flash", "backup": upstream},
        fallbacks=[{"agent": ["backup"]}],
    )
    reviews = {"deepseek/deepseek-flash": _reviewed(), upstream: _permit("backup")}
    problems = upstream_id_problems(config, reviews, today=_TODAY)
    assert len(problems) == 1 and "atiende CLIENTES" in problems[0]


@pytest.mark.parametrize("upstream", ["os.environ/BACKUP_MODEL_ID", "gemini/*"])
def test_checker_flags_upstream_ids_it_cannot_audit(upstream: str) -> None:
    """Un id por env o un comodín esquivan el guard: el id real no está en el repo."""
    problems = upstream_id_problems(_config({"x": upstream}), {}, today=_TODAY)
    assert len(problems) == 1 and "no se puede auditar" in problems[0]


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("router_settings", "fallbacks", [{"agent": ["backup"]}]),
        ("router_settings", "fallbacks", [{"*": ["backup"]}]),
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
    upstream = "gemini/gemini-3.1-flash-lite-preview"
    config = _config({"agent": "deepseek/deepseek-flash", "backup": upstream})
    config["router_settings"] = {}
    config[section] = {key: value}
    reviews = {"deepseek/deepseek-flash": _reviewed(), upstream: _permit("backup")}
    problems = upstream_id_problems(config, reviews, today=_TODAY)
    assert len(problems) == 1 and "atiende CLIENTES" in problems[0]
    assert "*" not in _customer_facing_aliases(config)


def test_checker_reports_reviews_nobody_uses() -> None:
    config = _config({"judge": "gemini/gemini-3.5-pro"})
    reviews = {"gemini/gemini-3.5-pro": _reviewed(), _PREVIEW: _permit()}
    problems = orphan_review_problems([config], reviews)
    assert len(problems) == 1 and _PREVIEW in problems[0]


def test_strict_loader_rejects_duplicate_keys() -> None:
    with pytest.raises(yaml.constructor.ConstructorError, match="clave duplicada"):
        _load_yaml_strict("a:\n  model: x\n  api_key: k\n  api_key: k\n")
    assert _load_yaml_strict("a:\n  model: x\n  api_key: k\n") == {
        "a": {"model": "x", "api_key": "k"}
    }


def test_strict_loader_accepts_merge_keys_and_their_overrides() -> None:
    """`<<: *ancla` + pisar una clave heredada es YAML legítimo, no un duplicado."""
    text = (
        "base: &base\n  api_key: k\n  timeout: 10\n"
        "a:\n  <<: *base\n  model: x\n  timeout: 30\n"
    )
    assert _load_yaml_strict(text)["a"] == {"api_key": "k", "model": "x", "timeout": 30}
